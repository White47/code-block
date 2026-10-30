# B4 Safe Source Excerpts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Process amendment (2026-10-15):** process is governed by the repository `AGENTS.md`. Do not load `subagent-driven-development` or `executing-plans`; do not run a per-task spec/quality review pair. Technical requirements below are unchanged.

**Goal:** 提供基于明确同意、实际解析字节身份、原生安全句柄和有界片段的本地只读源码 API，以及检测新增、删除、变化和无法验证文件的完整 freshness 核验。

**Architecture:** B3 提供不可变分析事实；B4 将授权策略、纯文本坐标转换、严格完整目录扫描和原生安全读取分开。片段只在目标文件可信、范围合法、秘密筛查通过且取消/期限检查通过后签发元数据证据；完整项目 freshness 是独立的时间区间内观察，不是磁盘事务或持续保证。工具计次唯一归属B5 dispatch，源码/token计量唯一归属C每次实际发送前的预留，B4不重复扣减。

**Tech Stack:** Python 3.11+ 标准库、frozen/slots dataclass、tokenize、hashlib、unittest、现有 source_handles、BudgetLedger、EvidenceRegistry；无新依赖、模型 SDK、网络或项目代码执行。

---

## 1. 审查结论、范围与前置条件

依据：正式批准规格 `docs/superpowers/specs/2026-10-11-code-understanding-agent-design.md` 的 §5–8；批准 B3 计划 `docs/superpowers/plans/2026-10-12-code-agent-analysis-snapshot.md`；当前 `src/codeviz/source_handles.py`、`src/codeviz/analysis/discovery.py`、`src/codeviz/analysis/python.py`、`src/codeviz/analysis/project.py`、`src/codeviz/persistence/fingerprint.py` 和 assistant 的 contracts/budget/evidence。仓库没有独立的 parser.py；Python parser 的实际文件是 `analysis/python.py`。

本计划写作时 B1/B2 已完成，B3 测试正在进行。B4 实施必须在 B3 公共 DTO/构建测试通过后开始；不得推断 B3 已实现或已通过。此次 architect 工作只写本文件，不修改生产代码、测试、进度文档，也不执行 Git。执行者按本计划完成后交主代理和独立 reviewer；不加入自动提交步骤。

架构结论：下述合同可以实施，不需要新的产品问题。必须解决的实现前置条件是 B3 完整 inventory accessor 和严格 discovery；它们是已有规格所要求的完整核验，不是可选优化。不得通过 1,000 项查询、旧 fingerprint 或捕获异常后返回空集合替代。

### 1.1 已有代码的真实边界

- `read_source_bytes(root, relative, max_bytes=...)` 返回 `SourceRead(raw_bytes, content_hash)`；hash 是读取的原始字节 SHA-256。Windows 的 CreateFileW 使用 GENERIC_READ / FILE_SHARE_READ / OPEN_EXISTING / OPEN_REPARSE_POINT，并核验句柄路径、属性及前后观察；保持此共享模式。不得增加共享写/删除，不加 Path.read_text/read_bytes/open 作为失败回退。
- B1 明确不提供恶意 ABA 防护或阻塞 I/O deadline；B4 不能把此组件改述为全文件系统沙箱。Linux 有自己的 fd/no-follow 验证；真实 Windows 句柄验收不能由模拟 Windows 的 Linux 测试代替。
- `parse_python_file` 使用 tokenize.detect_encoding、strict decode、CRLF/CR→LF，再 ast.parse；line_count 为 `max(1, count(LF) + not endswith(LF))`。hash 始终是上述解码前的实际解析字节，不是规范化文本 hash。
- `ParsedFile.was_parsed` 尊重 parsed_success，因此缓存中的 tree=None、parsed_success=True 是成功文件。B4 仅看 B3 FileRecord.was_parsed，不读 AST，不重新解析项目。
- 现有 discovery 使用 os.walk(followlinks=False)，没有 onerror；os.walk 会吞掉 scandir OSError。Path.is_symlink 的失败也不能证明无链接；Windows junction/reparse 不能仅凭 symlink 判定。其 sorted tuple 并不携带“完整扫描成功”证明，现有 fingerprint 同样继承此风险。
- A 的 EvidenceRegistry 只验证结构和请求归属，不验证磁盘真实性；hash/坐标/授权门禁必须在 B4 先完成。A 的 BudgetLedger 是不可退款累计预留，反复片段、外传历史和重试必须再次计费。

### 1.2 职责与实施文件

| 文件 | 职责/改动 |
| --- | --- |
| Create `src/codeviz/assistant/source_policy.py` | 严格路径、同意 DTO、保护路径和固定错误；不做 I/O |
| Create `src/codeviz/assistant/source_text.py` | Python 编码、LF 文本、AST UTF-8 边界、Monaco UTF-16、前缀裁剪、秘密筛查；不做 I/O |
| Create `src/codeviz/assistant/freshness.py` | 完整 inventory/hash 核验、限额和本地报告；不产生源码证据 |
| Create `src/codeviz/assistant/source_excerpts.py` | 目标文件授权、安全读取、预算、证据签发及状态门禁 |
| Modify `src/codeviz/analysis/discovery.py` | 新增独立严格扫描入口，复用同一默认 ignore 集合及字面前缀规则；既有 discover_python_files API/行为保持兼容 |
| Modify `src/codeviz/assistant/snapshot.py` | 仅新增 file_inventory 属性，直接返回已经复制、排序的完整 tuple；无 I/O、无新投影字段 |
| Create `tests/test_assistant_source_policy.py` | 路径、保护和授权边界 |
| Create `tests/test_assistant_source_text.py` | 编码、坐标、裁剪、秘密等价类 |
| Create `tests/test_assistant_freshness.py` | 完整扫描、身份、错误与时点 |
| Create `tests/test_assistant_source_excerpts.py` | 有序门禁、预算、证据、原生集成 |
| Modify `tests/test_discovery.py` | 严格 scanner 错误/限额/链接测试，旧 API 回归 |
| Modify `tests/test_assistant_snapshot.py` | file_inventory 超过 1,000 项且不截断、不可变、无 I/O |

不修改 parser、cache schema、核心 IR、前端/Monaco 组件、bridge、Provider、网络配置或旧 read_source。源码 DTO 不持久化；registry 只持有已有 EvidenceRecord 元数据。future service 负责外传、配置 HTTPS 验证、撤销时取消/clear 和会话总预算；B4 只提供本地门禁。

## 2. 冻结公共值和接口

以下是本批精确字段和签名，不允许测试与实现分别猜测名字。全部公开记录 frozen=True, slots=True。所有 int 必须 type(x) is int（拒绝 bool/float），str 必须 type(x) is str 且可 UTF-8 编码；tuple 严格 tuple。枚举严格对应枚举类型。公开构造器验证局部字段，跨对象绑定由 reader/checker 验证。固定异常均 `from None`，不包含原路径、源码、底层 exception、key 或请求内容。

### 2.1 policy 模块

```python
from dataclasses import dataclass
from typing import Literal
from codeviz.assistant.snapshot import SnapshotBinding

SourceErrorCode = Literal[
    "invalid_input", "scope_mismatch", "consent_required", "path_denied",
    "source_untrusted", "source_changed", "source_unavailable",
    "decode_failed", "invalid_range", "secret_blocked", "verification_failed",
]

class SourceExcerptError(ValueError):
    # code: SourceErrorCode; message: str; only the fixed table in §8.
    def __init__(self, code: SourceErrorCode) -> None: ...

@dataclass(frozen=True, slots=True)
class SourceConsent:
    binding: SnapshotBinding
    consent_id: str
    provider_origin: str
    allowed_files: tuple[str, ...]

def validate_source_path(relative_path: str) -> str: ...
def is_protected_source_path(relative_path: str) -> bool: ...
```

签名中的 `...` 是声明语法；行为完整规定如下，不是延期设计。consent_id 采用 RequestScope ID 的 ASCII opaque 格式。provider_origin 是可信配置层已确认的 HTTPS origin（非任意 Base URL），本层仍做 urlsplit 词法检查：scheme=https、非空 hostname、无 userinfo/query/fragment，path 仅空或 `/`，有效端口，无控制/空白字符；存储原规范 origin，比较严格相等。不发请求、不 DNS 查询。SourceConsent 不能由模型或工具 payload 构造，不能自动生成或扩大 allowed_files；可信 UI 同意适配器才创建它。

binding 精确等于 snapshot.binding（含 root/ignore/epoch/generation）。重新分析需要新的绑定/有效状态检查，不能靠老 consent_id 授权新文件。allowed_files 是排序、去重、严格 canonical 项目相对文件名 tuple，不允许目录、glob 或“全部”占位符；空 tuple 是无源码许可。授予范围可以包含随后被保护规则拒绝的路径，但不可因此读它；授权本身不证明 inventory/解析成功/哈希存在。

严格路径采用 `/` 分隔，拒绝空、NUL/控制字符、反斜线、绝对/UNC/drive、`.`/`..`/空 component、ADS `:`、Windows 设备名及任意 component 尾点/尾空格。在所有平台使用同一保守 Windows 安全规则。不能借 SourceAnchor 的规范化把 `a//b.py` 或 `a/./b.py` 变成许可路径；验证后返回输入原值。白名单 membership 是 canonical 字符串精确相等；Windows casefold 相同但拼写不同的 inventory/consent 组合拒绝 `invalid_input`，不隐式合并。严格 scanner 同样拒绝此类候选冲突。

保护规则 casefold 后逐 component 检查：`.git`、`.hg`、`.svn`、`.ssh`、`.aws`、`.azure`、`.gnupg`、`.codex`；任意 basename 为 `.env` 或以 `.env.` 开头；任意 component 为 `secrets`、`credentials`、`private_keys`；basename 为 `id_rsa`、`id_dsa`、`id_ecdsa`、`id_ed25519` 或以其加 `.` 开头；basename 为 `credentials`、`credentials.json`、`secrets.json`、`secrets.py`、`credentials.py`；扩展名 `.pem/.key/.p12/.pfx/.crt/.cer/.der/.jks/.keystore`。这些是首版具体固定 deny 规则，用户许可不能覆盖；额外组织规则后续只能收窄。非 `.py` 文件即使允许也无分析 inventory，不读。

### 2.2 text 模块

```python
from dataclasses import dataclass
from codeviz.assistant.snapshot import AnchorRecord

@dataclass(frozen=True, slots=True)
class MonacoRange:
    start_line: int
    start_column: int
    end_line: int
    end_column: int

@dataclass(frozen=True, slots=True)
class ExcerptLimits:
    max_file_bytes: int = 5_000_000
    max_lines: int = 200
    max_characters: int = 4_000

@dataclass(frozen=True, slots=True)
class TextSelection:
    text: str
    returned_anchor: AnchorRecord
    monaco_range: MonacoRange
    truncated: bool

def decode_python_source(raw_bytes: bytes) -> str: ...
def select_source_text(source: str, anchor: AnchorRecord,
                       *, limits: ExcerptLimits = ExcerptLimits()) -> TextSelection: ...
def contains_suspected_secret(source: str) -> bool: ...
```

ExcerptLimits 三项严格正整数，允许调小但不得超过上述硬上限。decode 只接受 bytes，detect_encoding(BytesIO(raw).readline)、strict decode，再 LF 规范化；SyntaxError/UnicodeError/LookupError→decode_failed。不调用 compile/ast.parse/import/eval；标准库编码解码不执行项目模块，不能改变 sys.path 或注册项目 codec。解码后必须能 UTF-8 strict encode；禁止替换非法字节。

### 2.3 freshness 模块

```python
from dataclasses import dataclass
from enum import StrEnum
from collections.abc import Callable
from codeviz.assistant.contracts import RequestScope
from codeviz.assistant.snapshot import AnalysisSnapshot, SnapshotBinding

class FreshnessStatus(StrEnum):
    FRESH = "Fresh"
    NOT_FRESH = "notFresh"

class FreshnessReason(StrEnum):
    SCOPE_MISMATCH = "scope_mismatch"
    ROOT_UNVERIFIED = "root_unverified"
    SCAN_INCOMPLETE = "scan_incomplete"
    INVENTORY_CHANGED = "inventory_changed"
    CONTENT_CHANGED = "content_changed"
    IDENTITY_MISSING = "identity_missing"
    FILE_UNVERIFIED = "file_unverified"
    PROTECTED_FILE = "protected_file"
    LIMIT_EXCEEDED = "limit_exceeded"

@dataclass(frozen=True, slots=True)
class FreshnessLimits:
    max_entries: int = 200_000
    max_directories: int = 20_000
    max_files: int = 200_000
    max_file_bytes: int = 5_000_000
    max_total_bytes: int = 50_000_000

@dataclass(frozen=True, slots=True)
class FreshnessReport:
    binding: SnapshotBinding
    scope: RequestScope
    status: FreshnessStatus
    reasons: tuple[FreshnessReason, ...]
    observed_hashes: tuple[tuple[str, str], ...]
    scan_complete: bool
    started_ns: int
    checked_ns: int

def verify_snapshot_freshness(
    snapshot: AnalysisSnapshot, *, scope: RequestScope,
    limits: FreshnessLimits = FreshnessLimits(),
    checkpoint: Callable[[], None] | None = None,
    clock_ns: Callable[[], int] = monotonic_ns,
) -> FreshnessReport: ...
```

需实际导入 time.monotonic_ns。limits strict 正整数，允许调低不得放大默认硬上限。默认总字节 50MB 是每次完整核验的本地工作上限，与 A 的 12,000 字符外传预算不同；超过则 notFresh/limit_exceeded，不跳过剩余文件后标 Fresh。entries 包括所有枚举项，非 Python 文件也计；directories 包含 root；files 计可分析候选。所有输出 hash 是本地元数据，禁止将报告/绝对 root/未授权路径直接作为模型 tool result。

报告 reasons 去重并按枚举声明顺序排序。Fresh 当且仅当扫描前后均完整、集合相同且等于完整 B3 inventory、每个 inventory 文件有分析 hash、安全读取 hash 一致、根验证通过、未超限。notFresh 必须非空 reasons；scan_complete 仅表示两次完整枚举及 root 验证通过，不意味着 hash 核验成功。started_ns<=checked_ns 严格非负 int，checked_ns 在最终扫描和根后验证之后获取。时钟回退/非法返回→verification_failed，不制造时间值。

observed_hashes 仅包含成功安全读取的文件，排序、去重；notFresh 报告里的 hash 不能修补 B3，不得用于许可 Fresh 或 lookup 点击。报告不持有 raw_bytes 或源码。checkpoint 是可信本地取消/deadline 门禁，在目录循环、每个文件读前后、终点执行；AgentError(cancelled/timeout) 原样传播，取消不是普通磁盘 notFresh。未提供 callback 时仍实行固定工作限额，但不声称能打断阻塞 OS I/O。

### 2.4 discovery 的新增严格入口和 B3 唯一扩展

```python
def discover_python_files_strict(
    project_root: Path, ignore_paths: tuple[str, ...] = (), *,
    max_entries: int = 200_000, max_directories: int = 20_000,
    max_files: int = 200_000,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[str, ...]: ...

class DiscoveryScanError(ValueError):
    # code only: ROOT_UNVERIFIED / SCAN_INCOMPLETE / LIMIT_EXCEEDED
    def __init__(self, code: str) -> None: ...

# AnalysisSnapshot, additive API (not a new DTO):
@property
def file_inventory(self) -> tuple[FileRecord, ...]:
    return self._files  # use B3's actual private storage name; no second copy.
```

私有存储名不属于合同；实现以 B3 最终存储名为准，公共名字固定 file_inventory。不能通过重复 files(limit=1000) 取全量，不能修改 B3 查询 limit 或引用 ProjectAnalysis。属性值直接为不可变完整排序 tuple。

严格扫描具体算法见 §5。DiscoveryScanError 固定消息 `Project root could not be verified.` / `Project source scan is incomplete.` / `Project source scan limit exceeded.`，不保留 OSError 对象或原目录字段。

### 2.5 reader 模块

```python
from collections.abc import Callable
from dataclasses import dataclass
from codeviz.assistant.budget import BudgetLedger
from codeviz.assistant.contracts import RequestScope
from codeviz.assistant.evidence import EvidenceRegistry
from codeviz.assistant.snapshot import AnalysisSnapshot, AnchorRecord
from codeviz.assistant.source_policy import SourceConsent
from codeviz.assistant.source_text import ExcerptLimits, MonacoRange

@dataclass(frozen=True, slots=True)
class SourceReadState:
    scope: RequestScope
    consent: SourceConsent | None
    cancelled: bool

@dataclass(frozen=True, slots=True)
class ExcerptRequest:
    anchor: AnchorRecord

@dataclass(frozen=True, slots=True)
class SourceExcerpt:
    evidence_id: str
    scope: RequestScope
    relative_path: str
    content_hash: str
    text: str
    requested_anchor: AnchorRecord
    returned_anchor: AnchorRecord
    monaco_range: MonacoRange
    truncated: bool

class SourceExcerptReader:
    def __init__(
        self, snapshot: AnalysisSnapshot, *, scope: RequestScope,
        consent: SourceConsent, ledger: BudgetLedger, registry: EvidenceRegistry,
        current_state: Callable[[], SourceReadState],
        limits: ExcerptLimits = ExcerptLimits(),
    ) -> None: ...

    def read_excerpt(self, request: ExcerptRequest) -> SourceExcerpt: ...
```

这是 trusted Python 服务 API，不是直接模型 JSON schema。未来模型工具只能提供相对文件+整数范围，由服务重建 ExcerptRequest；scope/consent/root/registry/hash/confidence/证据 ID 都不由模型输入。不新增 symbol_id/code_edge_id/semantic_id 参数；B4 只签发源码证据，B5 再基于快照权威映射绑定静态事实。EvidenceRecord.confidence 固定 Confidence.EXACT，含义仅为字节和位置已验证，不把自然语言或业务顺序升级为 Exact。

SourceReadState 的 scope 必须等于完整 RequestScope（含 request_id），consent 必须等于初始化 SourceConsent；cancelled strict bool。state 回调异常或错类型→verification_failed；scope 不同→scope_mismatch；consent None/变更→consent_required；cancelled→AgentError(cancelled)。状态检查是观察时点，不是与撤销动作之间的原子事务；未来 service 必须在实际发送前在自己的任务/授权锁下再次核对并预留发送，网络等待不能持该锁。

## 3. 授权与安全读取的精确顺序

每次调用按以下顺序，前一阶段拒绝不得进入后一阶段。源文件读取均只经 `source_handles.read_source_bytes`，不依赖 monkeypatched platform 伪装原生支持。

1. 校验对象与 request/anchor 的严格字段及所存canonical路径。B3的AnchorRecord按已批准合同规范化，因此B4 trusted Python API不能恢复或声称拒绝构造前已经消失的原始拼写。接受原始字符串的B5 dispatch及C上下文适配器必须先validate_source_path(raw_path)，再构造AnchorRecord/SourceAnchor；拒绝别名时不得查文件或读取，已接纳的工具尝试仍按B5计一次。检查 snapshot.matches_scope(scope)、consent.binding==snapshot.binding；构造时检查 registry/ledger 类型，registry 的实际 scope 归属由 register 再强制确认。
2. current_state 检查，ledger.reserve(BudgetReservation())仅检查取消/期限。B4是本地读取原语，不是计次入口；B5的dispatch在每次工具尝试（包括非法参数、unknown name及权限不足）前唯一预留tool_calls=1，不能让C和reader再次计次。取消/期限先于dispatch计次检查。
3. snapshot.get_file(path) 必须存在，路径不命中 protected deny，path 精确在 allowed_files，且不命中 snapshot.binding.ignore_paths 的字面前缀。顺序固定先 path_denied，再 consent_required，再 source_untrusted；不把 unknown 文件当作可读取探针。不做 resolve/stat/open 之前必须完成此授权阶段。
4. FileRecord.was_parsed 必须 True 且 content_hash 是 B3 验证过的非空 SHA-256。成功缺 hash→source_untrusted；失败文件无论有无 hash 均 source_untrusted，不读取、不引用。源文件 line_count>=1，请求行在该范围内。
5. root 由可信 binding 提供的绝对路径创建 Path；运行时 lstat root、拒绝非目录和 symlink/reparse，resolve(strict=True) 结果须等于 binding 的原 canonical 路径，不能 resolve 后“修正”绑定。B3 的词法 root 校验本身不证明 canonical，B4 允许并必须做 I/O。
6. 安全读取全部目标原始字节，max_bytes=limits.max_file_bytes；核验返回 bytes/SHA-256 结构，实际重新计算 sha256(raw).hexdigest()==SourceRead.content_hash==FileRecord.content_hash，错误不返回部分数据。B1 reader 已原生验证句柄前后路径/身份/大小。hash 比较不使用 mtime 代替。
7. decode_python_source；line_count 必须等于 FileRecord.line_count，否则 source_changed。秘密筛查整个 LF 文件，然后 select_source_text；不能先裁剪后检测，不能通过指定非秘密行绕过同文件阻断。
8. 完成截取后 current_state 和 ledger.reserve(BudgetReservation()) 再检查。再安全读取同一文件一次并比较 raw hash 与 snapshot/首读 hash。此第二读不 decode、不缓存；它证明截取后的再次观察，不证明连续未变或未来不变。
9. ledger.reserve(BudgetReservation())再检查期限与状态，然后 registry.register(EvidenceRecord(scope, SourceAnchor 对应 returned_anchor, actual raw hash, Confidence.EXACT, truncated=selection.truncated))。不保存请求的完整范围为已传输证据；不保留源码到 registry。失败无 SourceExcerpt 返回。本地读取不预留source_fragments；C必须将最终HTTP body中每个源码出现位置标记并在实际发送前唯一预留，每次重复发送或历史中的重复源码都再次累计。
10. 签发后、返回前再检查 current_state/ledger deadline；若此阶段失败，registry.clear() 撤销本请求证据并抛固定错误。正常返回 SourceExcerpt。B4不发送数据，不把本地取得片段说成外传；一次工具读取、两次发送在集成时应计1次工具、2份源码，不能重复扣减reader或把重复发送视为免费。

Reader 不强制把任意 unrelated failed/null 文件升级为可引用。Partial 项目可以在本地取得成功部分的片段/候选证据；完整 freshness 如果含缺 hash 文件则必为 notFresh，future service 不得把该报告解释为整个项目 Fresh 或用它启用点击。没有新的缺失 hash 身份设计前，完整最终结果/引用门禁保守失败并明确覆盖缺口；不悄悄借旧缓存指纹补缺。B4 不实现缩小依赖集合的策略。

## 4. 文本、范围、裁剪及秘密合同

### 4.1 行与边界

LF 行拆分只用 `split("\n")`，不用 splitlines（后者会把 Python 行内 U+2028/U+0085 等分成额外行）。若 source 非空且以 LF 结尾，删除 split 的最后一个虚拟空项；空文件保留 `[""]`。行内容不含 LF；行数与 parser 公式完全相同。validate 请求完整范围后才裁剪；不得用小上限遮蔽非法 end 行/列。

anchor 行 1-based、结束行包含；列 None 或严格非负 int，列是该解码 LF 行 UTF-8 字节偏移、结束列排他。两端有列分别验证，哪端缺列哪端扩大到该行完整边界，已给出的非法列不能因另一端 None 而忽略；只要任一端缺列，实际选择及 Monaco 都定位 start/end 的整行，两端实际列分别 0 和末行 UTF-8 长度。源内每个边界必须可在 UTF-8 strict decode 后对应 Unicode codepoint，不得落在中文/emoji 中间，也不得超过行 UTF-8 长度。

核心边界转换可直接采用下述完整纯函数（异常转换成 invalid_range）：

```python
def utf8_boundary_to_utf16(line: str, offset: int) -> int:
    if type(offset) is not int or offset < 0:
        raise SourceExcerptError("invalid_range") from None
    encoded = line.encode("utf-8")
    if offset > len(encoded):
        raise SourceExcerptError("invalid_range") from None
    try:
        prefix = encoded[:offset].decode("utf-8", errors="strict")
    except UnicodeError:
        raise SourceExcerptError("invalid_range") from None
    return len(prefix.encode("utf-16-le")) // 2 + 1
```

MonacoRange start/end 都 1-based，end_column 排他。例如行 `a😀中z` 的 UTF-8 偏移 1..8 选择 `😀中`，Monaco 列 2..5；偏移 2/7 非法。组合字符按 UTF-16 code units 计，不做 grapheme 聚合或 Unicode normalize。允许零宽范围（同一合法边界），text="" 且不截断，可签发位置证据但不表示覆盖文本的事实；B5仍计工具尝试，C外传此文件元数据仍受披露文件数限制。

多行文本为首行 suffix + 中间完整行 + 末行 prefix，行间单 LF；选择文件末行内容时不添加文件尾 LF。returned_anchor 总是有显式 UTF-8 start/end columns；零宽也有精确列。requested_anchor 保留原不可变值；returned_anchor 为实际输出前缀，不可用原 end 表示省略区域。

### 4.2 固定裁剪

完整请求先合法化，按源顺序只取连续前缀。最多 max_lines 个已覆盖的真实行、max_characters 个 Python Unicode codepoints（含内嵌 LF）；不添加省略号、标签、行号或伪造文本，truncated 单独表示。有多行时若字符截止恰好在 LF 后，返回前缀可含该 LF，returned_anchor 的 end 为下一真实行的列 0；此末行也计 max_lines，若超行限则删除边界 LF 并以此前真实行末为 end。不能创建末尾 LF 后的虚拟行。字符截断绝不截断 Unicode codepoint；重新从实际终点计算 UTF-8 end offset 和 Monaco UTF-16 end。

returned_anchor/Monaco 描述实际 text；truncated iff 实际字符序列/终点未达到完整请求终点，零宽请求不截断。不能把 RequestScope 的余量自动变成 ExcerptLimits；默认 4,000 字符、200 行单片段。B5/C承担总预算8文件/12,000字符/8工具，发送前累计额度不足则budget_exceeded，不再次悄悄裁剪。重复text每次实际发送累计，B4本地重复读取不计外传源码。

### 4.3 固定秘密筛查

contains_suspected_secret 以完整 LF 文本作无 I/O、无网络、无日志判定，不执行 AST 或项目内容。固定首版规则：

```python
import re

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bBearer[ \t]+[A-Za-z0-9._~+/-]{16,}={0,2}"),
    re.compile(
        r"(?im)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
        r"client[_-]?secret|password|passwd|secret[_-]?key)\b"
        r"[\"']?[ \t]*(?:=|:)[ \t]*[\"'][^\"'\n]{8,}[\"']"
    ),
    re.compile(r"(?i)\b(?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://"
               r"[^\s/:@]+:[^\s/@]+@"),
)

def contains_suspected_secret(source: str) -> bool:
    return any(pattern.search(source) is not None for pattern in SECRET_PATTERNS)
```

模式使用受文件硬限额约束的输入，避免嵌套无界量词。碰到疑似凭据阻断整次片段，不替换成 `[REDACTED]` 后签发原范围，不返回匹配内容/行号/类型样本。注释/字符串也筛查。测试全部用合成假凭据，不能复制真实 key。规则存在漏报和误报，UI/future service 只能说“疑似凭据阻断”，不能声称已可靠清除全部秘密。混淆/分段动态字符串不承诺识别；不以此扩大用户授权。

## 5. 严格扫描与完整 freshness 算法

### 5.1 严格扫描

保持分析的默认 `_IGNORED_DIRECTORIES`（目录名 casefold）和 `_matches_ignore` 的精确字面前缀语义，以及 `.py` 后缀 casefold。不把 allowed_files 或 protected policy 当作 discovery ignore；否则新增/删除被隐藏。`.git` 等默认 ignore 已排除，但 `secrets.py` 如实际进入分析 inventory，仍要出现在集合比较中。

采用显式 os.scandir 栈、`with os.scandir(directory)`，每个 entry 先计 entries 限额、checkpoint，然后获取 `entry.stat(follow_symlinks=False)`；禁止 is_file/is_dir 返回 False 的异常吞掉路径。root 和每个实际进入的目录在枚举前后 lstat；验证目录类型、非 link/reparse、非零 dev/ino，比较 dev/ino/mtime_ns/ctime_ns，Windows 仅同 pathname API 比较，不混用 B1 fstat 的 ctime。

对明确默认/自定义 ignore 目录不递归；其名字枚举仍计 entries。已确认 symlink/reparse 的 entry 不跟随，作为分析规则排除；无法判断 entry 属性的任何 OSError 必须 SCAN_INCOMPLETE。Python 后缀候选若非 regular（FIFO/device/socket）则 SCAN_INCOMPLETE，不打开。根是 reparse/symlink 或身份不可确认→ROOT_UNVERIFIED。Windows junction 一律不递归。同一 canonical path 重复、Windows casefold 冲突、非法候选路径→SCAN_INCOMPLETE。

scan 返回 sorted tuple 仅在所有待扫描目录成功后；OSError、迭代中失败、root 验证失败、任何限额失败不返回 partial tuple。Directory 在扫描前后发生变更，包括非源码变化，保守 SCAN_INCOMPLETE；不自动重试。不得声称读不到的目录内一定没有 Python 文件。旧 discover_python_files 继续使用旧行为，严格入口由 B4 显式调用，避免未经本批验证改变缓存/既有分析入口。

检查 root 是运行时 I/O：绝对和 lexical 检查无法证明目录存在/身份，不能实现“绝对路径 stat 永不发生”测试。未授权片段必须在任何 root I/O 前拒绝；完整 freshness 是 trusted 本地检查，明确允许遍历整个分析范围，但不外传未授权元数据。不是模型可随意启动的工具。

### 5.2 完整核验

1. 验证 snapshot/scope/limits/callback；合法但 snapshot.matches_scope=False 返回 notFresh/scope_mismatch，零文件 I/O。clock/checkpoint 输入不合法抛固定错误。
2. 保存 started_ns，验证 canonical root 与 root pathname 身份；严格 scan A，记录完整候选集合。扫描错误映射对应 notFresh reason，无部分结果被称为完整。
3. 全量 inventory 来自 file_inventory，不按 was_parsed/allowed_files 过滤。scan A 与 inventory path 集合比较；新增、删除、改名都 INVENTORY_CHANGED。两种差集只供本地内部比较，不把未授权文件名写进模型错误。
4. 对 inventory 按路径遍历：hash=None→IDENTITY_MISSING（成功和失败文件都如此），不计算新 hash 当其分析身份；protected→PROTECTED_FILE，不读取其字节；失败但有合法 hash 的普通文件也经 B1 安全读并比较 hash，虽然不能 cite。hash=None 文件无法验证但仍参与前后集合扫描，不因跳过 hash 而标 Fresh。
5. 每个可验证 inventory 文件读取前计算 `remaining = limits.max_total_bytes - accepted_bytes`；remaining<=0 且仍有待验证文件时标 LIMIT_EXCEEDED 并停止 hash 工作。传给 B1 的 `max_bytes=min(limits.max_file_bytes, remaining)`，成功后累加实际 raw 长度。FILE_TOO_LARGE 在剩余总额度限制了本次读取时映射 LIMIT_EXCEEDED，并停止 hash 工作；由单文件限额触发则 FILE_UNVERIFIED。不把统计归入外传 max_files。此为成功接收原始字节的逻辑上限；保留 B1 至多一个额外字节的溢出探测，不宣称绝对 OS I/O 字节或 heap 上限。其他安全读取失败→FILE_UNVERIFIED；SOURCE_CHANGED→CONTENT_CHANGED（至少 notFresh）；读后 raw SHA-256 不等于解析 hash→CONTENT_CHANGED。对同尺寸同 mtime 变化照常重算原始字节，停止 hash 后仍在许可范围完成 scan B。
6. 无论出现已知内容变化或身份缺失，都完成 scan B（除 cancel/deadline/无法继续扫描的根错误）；scan B 须完整、与 A 以及 inventory 同集合。A/B 任意不完整→SCAN_INCOMPLETE，集合不同→INVENTORY_CHANGED。限额已用尽导致无法完整扫描→scan_complete False，同时保留 LIMIT_EXCEEDED。entries/directories/files 是每次扫描的上限；两次各有固定上限，hash max_total_bytes 是本次 verify 总计。
7. 最终 root 再验证并与初始 root pathname 身份/状态比较，获取 checked_ns。成功 hash 观察存入本地 observed_hashes；任何 reason→notFresh。empty inventory 只有 root 和 A/B 两次完整空扫描都成功才 Fresh，不能把空集合短路为无 I/O成功。

没有 analysis hash 的失败/null文件或成功合成文件必 notFresh，不能因为当前可以读取而判新鲜。失败但有 hash且字节/集合相同可以 Fresh（文件身份未变，解析覆盖仍 Partial），但 reader 坚决 cannotcite。若原 discovery 曾吞错误，现在严格扫描仍遇错则 notFresh；若错误已消失、漏掉的可分析文件可见则 INVENTORY_CHANGED。若当前完整集合与 inventory 相同，只证明当前观察与已存分析身份一致，不能倒推历史扫描曾完整，也不推断动态/未解析事实不存在。

fingerprint_project/outdated_reasons 可继续用于现有 UI，但 B4 不调用它们作可信验证，不能利用 cache fingerprint 补 FileRecord.content_hash。验证依据是实际 parsedbytes hash；same fingerprint 不是 missing hash 的证明。

完整 scan A→逐文件 read→scan B 是有界串行观察，文件可能在它最后一次读后、报告返回前或返回后改变；同一路径变更又恢复也可能逃过观测。FreshnessReport.started_ns/checked_ns 显示核验区间与完成时点，不能标为“此时整个磁盘原子快照”或 continuous guarantee。未来发送/完成/点击各自重新执行 gate；navigation 还要目标安全再读/hash 检查且核对 epoch/generation。checked_ns 属进程 monotonic 域，UI 若需墙上时间由 service 在本地附加，不能跨重启比较。

## 6. 有界任务与具体验收步骤

工作路由：test_writer 负责真实 RED 和风险组 fixture；core_implementer 负责跨模块源身份、安全与坐标实现；不由普通 implementer 修改这些边界。主代理读本计划后才能下发实现；B3 accessor 的小改动与当前 B3 工作协调，避免并行写同文件。复杂失败交 debugger；实现后独立 reviewer，不自动上升 final_auditor。

测试用 unittest/subTest、临时目录、假 state/clock、mock checkpoint、真实 source_handles。纯 DTO/权限测试用 B3 typed synthetic facts，不为每个非法输入调用整个 analyzer。parser 编码集成用 parse_python_file + build_snapshot。每个测试模块 setUp 首先 assertTrue(hasattr(module, public_name))；缺 API 的 RED 必须明确缺合同，不把 collection import error 当有效行为失败。

### Task 1：完整 inventory 访问器与严格扫描

- [ ] test_writer 为 B3 创建 1,001 个合法 FileRecord 的 analysis fixture，build_snapshot 后断言 len(snapshot.file_inventory)==1001、排序且 tuple/frozen；同时 snapshot.files(limit=1000) 仍仅 1000；patch 文件系统入口为异常验证 accessor 无 I/O。
- [ ] tests/test_discovery.py 新增 test_strict_scan_does_not_swallow_scandir_error：临时 root 中 safe.py 与 blocked 子目录，在该目录的 scandir 注入带私有路径的 PermissionError，断言 DiscoveryScanError.code==SCAN_INCOMPLETE 且无返回值/路径泄漏；对迭代第二 entry 的异常再测一次。
- [ ] 新增 scanner 默认/自定义 ignore、`.PY`、symlink/junction、不规则 Python candidate、entry.stat 异常、零 identity、目录前后改变、casefold 冲突和三限额边界；取消回调必须向上传播。
- [ ] 运行 RED：`.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_discovery.py -v`，以及 snapshot 定向命令；观察失败原因对应缺 accessor/strict scanner。
- [ ] core_implementer 实现 §2.4/§5.1，使用如下异常边界（不得变成返回空 tuple），再运行相同测试 GREEN。

```python
try:
    with os.scandir(directory) as entries:
        for entry in entries:
            if checkpoint is not None:
                checkpoint()
            info = entry.stat(follow_symlinks=False)
            # 在此应用 §5.1 的计数、目录/regular/reparse 和 ignore 判定。
except OSError:
    raise DiscoveryScanError("SCAN_INCOMPLETE") from None
```

### Task 2：严格同意与保护路径

- [ ] test_writer 为每个路径/授权风险组提供 subTest：绝对/UNC/drive/ADS/设备/尾点/尾空格/控制字符/dot/重复分隔符、canonical case 变体、allowed tuple 排序重复、origin credentials/query/fragment/http、binding/root/ignore/generation 不匹配；验证 bool 不能伪装 generation/列。
- [ ] 使用 SourceConsent(snapshot.binding, "consent-1", "https://provider.example", ("app.py",))；给 `.env.py`、`pkg/secrets/token.py`、`secrets.py` 即使都获准且有分析 hash，is_protected_source_path 仍 True。合法空白名单不能读取。
- [ ] 运行 `.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_source_policy.py -v`，记录 RED；core 实现 §2.1 并重复 GREEN。
- [ ] 纯 policy 测试 patch scandir/stat/open/native reader/network 为 raise 哨兵，确认零 I/O；reader 授权拒绝另于 Task 5 验证。

### Task 3：Python 文本、UTF-8/UTF-16与裁剪

- [ ] test_writer 精确 fixture：raw=`b"# coding: latin-1\r\nname = '\xe9'\r\n"`，decode 后为 LF、保留 é；UTF-8 BOM 合法而 BOM/cookie 冲突失败。原 raw hash 与 LF UTF-8 hash 不同，reader 后续必须保留 raw hash。
- [ ] anchor Unicode 测试直接用 §4.1 示例，至少这些独立断言：

```python
selection = select_source_text(
    "a😀中z", AnchorRecord("app.py", 1, 1, 1, 8)
)
self.assertEqual(selection.text, "😀中")
self.assertEqual(selection.monaco_range, MonacoRange(1, 2, 1, 5))
for offset in (2, 3, 4, 6, 7):
    with self.subTest(offset=offset), self.assertRaises(SourceExcerptError):
        select_source_text("a😀中z", AnchorRecord("app.py", 1, offset, 1, 8))
```

- [ ] 空文件、最终 LF、不带 LF、CR-only、CRLF、U+2028/U+0085、跨行/同一行、两端 None/单端 None、已给列非法、零宽、倒置、过长 end line、bool/float、非 UTF-8 边界全部覆盖。None endpoint 与精确 endpoint 不能制造两套坐标。
- [ ] 裁剪断言使用 `a\nb\nc` 选择三行：max_lines=2 返回 `a\nb`、末端 line2 byte1；max_characters=2/max_lines=3 返回 `a\n`、末端 line2 byte0；max_characters=2/max_lines=1 返回 `a`、末端 line1 byte1。text、returned_anchor、Monaco、truncated 四项同时断言，非法原 end 即使裁剪也拒绝。
- [ ] 运行 `.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_source_text.py -v` 得 RED；core 实现 §2.2/§4.1–2，再 GREEN。不得用执行源码获取 Unicode 范围。

### Task 4：秘密阻断与稳定错误

- [ ] 在 source_text 测试中对每个 SECRET_PATTERNS 提供合成阳性、相近但无匹配阴性；评论/字符串也阳性。用 `"sk-" + "A" * 24`，不引入真实密钥。秘密在 200 行以后/4,000 字符以后，requested anchor 选择正常第一行，reader 仍 secret_blocked。
- [ ] 秘密检测前后源不变，失败没有文本/ID/registry entry；B4直接调用不改变tool_calls/files/source_characters，通过B5 dispatch时唯一计工具尝试。错误不含合成 token 或 root。输入达到 max_file_bytes 后不会被额外 scanner/network 隐式发送。
- [ ] core 加入 §4.3 固定模式、§8 固定消息，运行 source_text/source_excerpts 定向 RED→GREEN。不增加自动脱敏开关或用户 bypass。

### Task 5：Reader 有序门禁、原始身份与证据

- [ ] test_writer fixture：临时磁盘 `app.py`=`b"x = 1\r\n"`，parse_python_file 获取实际 hash，build_snapshot，创建 scope/consent/ledger/registry/state；read_excerpt(ExcerptRequest(AnchorRecord("app.py", 1, None, 1, None))) 返回 `x = 1`、MonacoRange(1,1,1,6)，返回 hash 等于 raw hash，registry.lookup(expected_file_hash=rawhash) 的 anchor 等于 returned_anchor、Confidence.EXACT，无 IDs、truncated=False。
- [ ] patch source_excerpts 模块所引用的 read_source_bytes 记录调用：未授权/保护/unknown/失败/成功缺hash全部零源文件 I/O；成功两次且 max_bytes 相同，首读 hash 不匹配不进行第二读、不注册；非法 UTF-8、行数不一致、非边界、不规则 root、unsupported 平台全部固定错误。
- [ ] B4直接重复读取保持ledger计数不变，但取消/期限始终受ledger约束；后续B5/C集成必须测试一次工具读取两次外传计1次工具/2份源码及8文件/12k/8工具累计拒绝。registry满、closed、scope错配均失败且无SourceExcerpt；关闭不会被重建registry绕过。
- [ ] state 序列分别在首读前、首读后、第二读前后、预算后、register 后切 epoch/generation/request/consent 或 cancelled；断言不会返回源/证据，最后阶段已签发则 registry.clear。deadline 用假 monotonic、绝不 sleep。
- [ ] 第二读 hash 变化/路径被替换/原生 reader 报 SOURCE_CHANGED→source_changed，不忽略第一读已合法。两个同 raw hash 观察也不声称连续保证。
- [ ] 运行 `.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_source_excerpts.py -v` 得 RED；core 按 §3 实现（原有 A 接口如下，不得改名/改计数语义），再 GREEN。

```python
ledger.reserve(BudgetReservation())
record = EvidenceRecord(
    scope=scope, anchor=SourceAnchor(
        path, selection.returned_anchor.start_line,
        selection.returned_anchor.start_column,
        selection.returned_anchor.end_line,
        selection.returned_anchor.end_column,
    ), content_hash=verified.content_hash, confidence=Confidence.EXACT,
    truncated=selection.truncated,
)
evidence_id = registry.register(record)
```

### Task 6：完整 freshness、失败/null 与分析身份

- [ ] test_writer 在真实临时树上形成 B3 snapshot：unchanged→Fresh/空 reasons，改为同长度字节并恢复 mtime→CONTENT_CHANGED；新增 `.py`、删除、rename→INVENTORY_CHANGED。新增 ignored 路径不影响 inventory，新增非 ignored 非 Python 文件仅在扫描期间变更时保守拒绝，稳定时不视为分析集合变化。
- [ ] >1,000 inventory 中最后一个文件变更必发现；不能用 mock 返回前 1,000 项掩盖。file limits 边界另用小上限避免巨大 I/O。白名单只含 app.py 时改变 other.py 仍 notFresh；未来模型不能看到 other.py 的源或路径。
- [ ] 成功缺 hash→IDENTITY_MISSING；失败/null 缺 hash 同样 notFresh；失败有 hash且相同→可 Fresh 但 reader 不可 cite，失败有 hash且变更→CONTENT_CHANGED；cached tree=None/parsed_success=True 成功记录正常验证。用错误“相同 saved fingerprint”哨兵，确认从未用于补 hash。
- [ ] scan A/B 任意 error/不完整/root 不存在、reparse、根身份/目录状态变化、非法候选、权限拒绝、迭代报错→notFresh；发生新增/删除于 A/B 之间也拒绝。inject checkpoint 在确定调用计数改变树，不 sleep，不依赖线程比赛；如 root timestamp 检查先发现变化，接受 reasons 包含 SCAN_INCOMPLETE，但必须 notFresh。
- [ ] 原始读错误、超文件/总字节、entries/dirs/files限额、protected inventory（零字节读）等类别断言 notFresh；用小额度测试总长度恰好上限成功，以及下一文件超过 remaining 时 native reader 获得收紧后的 max_bytes、返回 LIMIT_EXCEEDED 且停止 hash 工作，scan B 仍完成；剩余额度为零有待验证文件时不得额外读取。空 inventory 在 root error 时不能 Fresh。取消/deadline 抛 A 固定错误；非法 clock 返回/回退抛 verification_failed。
- [ ] 时间点验收：fake clock 10→20，unchanged 报 started_ns=10/checked_ns=20；返回后修改文件，旧 report 不自动改变，下次 verify notFresh。此测试刻意证明报告是观测值，不是持续订阅或连续承诺。
- [ ] 运行 `.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_freshness.py -v` 观察 RED；core 按 §5.2 实现、再 GREEN。

### Task 7：原生集成、独立审查与门禁

- [ ] Windows 本机使用真实 source_handles reader 运行 source_excerpts 与 freshness 临时树集成；保留 B1 `test_windows_read_handle_blocks_write_delete_and_releases_after_read` 的真实共享读验收。绝不调用应用终端去执行 fixture 项目，不发 API、不安装 SDK。
- [ ] 权限/无外传哨兵：fixture 内容包含 `raise RuntimeError`/恶意注释与提示词，不会执行；patch socket/subprocess 和持久化入口为异常，B4 仍能读取安全 fixture；registry/export 检查没有 raw/source 字段，未授权完整 freshness hashes 只留本地。
- [ ] 独立 reviewer 按 §7 风险组和规格 §5–8 复审顺序、权限、hash 事实、未知身份和 freshness 的实际保证；tests passed 不替代 review。重大未解决架构疑问才另请 architect/final_auditor，正常失败先 debugger。
- [ ] 定向回归命令：

```powershell
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p 'test_assistant*.py' -v
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_discovery.py -v
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_source_handles.py -v
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_parser.py -v
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p 'test_persistence*.py' -v
.desktop-venv/Scripts/python.exe -B -m ruff check src tests
.desktop-venv/Scripts/python.exe -B -m mypy src/codeviz
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -v
```

预期每条 exit 0；unittest 明确报告当前实际 counts/skips，不复制 B1/B2 历史计数。Linux 按现有环境运行同等 Python/typecheck 门禁并明确 native Windows skips，环境不可用明确 skipped；不得声称 Windows-only run 等于 Linux 已验证。未改前端不重复 UI/SDK 接入门禁，不宣称完成整个 Agent 或最终 EXE 验收。

- [ ] 实施结束由主代理按现有工作流检查实际 diff 并更新进度；本次计划任务不执行 Git，也不编辑本文件外的任何文件。B5 接续搜索/邻域、权威 evidence 映射及真正模型 tool DTO，后续 service 接续授权撤销/发送/完成/点击门禁。

## 7. 风险组验收矩阵（test_writer/core/reviewer 共用）

| 组 | 必须达到的可观察结果 |
| --- | --- |
| R1 值类型/绑定 | frozen/slots；strict str/int/bool/tuple；epoch/generation/request/root/ignore 不混用；非法输入固定错误 |
| R2 路径权限 | 白名单∩inventory∩未 ignore∩未 protected；逃逸/ADS/设备/alias 拒绝，授权拒绝先于全部文件 I/O |
| R3 解析身份 | rawbytes SHA-256；was_parsed 正确；成功缺hash不能 cite，失败即使有hash不能 cite，fingerprint 不补身份 |
| R4 原生句柄 | 唯一 B1 reader、Windows shared-read、前后身份/路径一致、异常无普通读回退、资源关闭 |
| R5 编码/LF | PEP263/BOM/Latin1 严格解码、非法编码固定失败；LF 与 parser 同行数；原 hash 保留 |
| R6 范围/导航 | 所有 AST UTF-8 边界检查、Monaco UTF-16 code units、None 整行、零宽、多行/end exclusive 正确 |
| R7 裁剪 | 请求完整校验先行；固定连续前缀；行/字符双限；真实 returned_anchor/Monaco/truncated，省略区不被证据覆盖 |
| R8 秘密 | 整文件筛查先裁剪，合成凭据阻断无数据/ID；无可靠脱敏夸大或 bypass |
| R9 预算/状态 | B4空预留检查deadline/cancel/撤销/跨请求；不重复扣工具/源码，唯一计次及wire累计由B5/C验收；单片段本地硬限额不放宽 |
| R10 证据 | registry 只持元数据；可信 hash/实际返回 anchor；满/关闭/归属错误不返回片段；签发后状态失效清 registry |
| R11 完整扫描 | 旧 os.walk 吞错不用于 Fresh；严格 entry/iterator/scandir/reparse 判定；完整 inventory >1000、双扫描 |
| R12 集合 freshness | 新增/删除/rename/未授权文件变化均检测；ignore 相同；scan limit/error 不能以空或 partial 作完整 |
| R13 未知/失败身份 | failed/null/hash=None 保留集合并 notFresh；failed knownhash 仍核验；protected 不读但 notFresh；空 inventory 仍核验 root |
| R14 时间/资源/无执行 | 明确 started/checked 观察区间；report 返回后变化不保证；有界本地 bytes/scan；无执行/持久化/网络/收费 API |

## 8. 固定错误与外传边界

SourceExcerptError 消息表（未知 code 构造回退 invalid_input，不 echo 输入）：

| code | message |
| --- | --- |
| invalid_input | Invalid source excerpt input. |
| scope_mismatch | Source request scope changed. |
| consent_required | Source permission is unavailable. |
| path_denied | Source path is not permitted. |
| source_untrusted | Source analysis identity is unavailable. |
| source_changed | Source differs from the analysis snapshot. |
| source_unavailable | Source could not be safely read. |
| decode_failed | Source encoding could not be decoded. |
| invalid_range | Source range is invalid. |
| secret_blocked | Source excerpt blocked by suspected credentials. |
| verification_failed | Source verification could not be completed. |

B1 SOURCE_CHANGED→source_changed；FILE_TOO_LARGE/READ_ERROR/NOT_REGULAR_FILE→source_unavailable；UNSUPPORTED_PLATFORM/SOURCE_VERIFICATION_FAILED/SYMLINK_UNSUPPORTED→verification_failed；INVALID_SOURCE_PATH→invalid_input。known budget/registry AgentError 原样传播固定 A 消息。不要宽泛捕获 KeyboardInterrupt/SystemExit，不把 unexpected implementation defects 全部静默标 Fresh；在 provider service 尚未实现时，局部入口仍要将预期 OS/codec/state 错误清理成固定异常，不记录源码。

未来模型仅可见获准 SourceExcerpt 的相对路径、text、受限范围、evidence_id、truncated；不直接 serialize consent/binding/root/observed_hashes、原异常或未授权路径。完整 freshness 读 unconsented 普通 inventory 是本地身份验证，不构成上传许可；protected inventory 不读并保守 notFresh。Reader 的当前状态回调不替代发送时的原子授权门禁，本批也不宣称已提供取消网络请求的能力。

## 9. 架构自审与接续结论

规格覆盖：§5 只读有界工具→Task2/5；§6 原始 hash、范围、可信证据→Task3/5；§7 changed/added/removed、时点和 failed/null→Task1/6；§8 白名单交集、保护与秘密、不执行/持久化/外传→Task2/4/7。模型请求预算/token、UI dirty 状态、Provider HTTPS/TLS 和最终 navigation 按正式规格留给后续批次，不在 B4 伪称完成。

B3 DTO 名称/字段完全沿用批准计划：SnapshotBinding、FileRecord.relative_path/line_count/was_parsed/content_hash、AnchorRecord.file_path/start_line/start_column/end_line/end_column；没有把 analysis 前后 fingerprint 当分析字节 hash。唯一新增 B3 访问器 file_inventory 不影响投影语义或原查询上限。

没有产品决策阻塞。工程前置为完成 B3 并协调 additive inventory accessor；实施者必须落实 strict scanner 和未知身份保守拒绝，不能降级完整 Fresh 合同。此次架构审查不证明上述代码已经实现，也不替代主代理批判性阅读及独立实施 review。
