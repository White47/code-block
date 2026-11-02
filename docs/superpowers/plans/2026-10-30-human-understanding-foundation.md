# 可读项目理解基础实施计划

执行方式：遵守 AGENTS.md，自主按队列执行；核心交给 core_implementer，界面由主代理完成。未经用户要求不提交或推送。

**Goal:** 为可读功能图建立可缓存、有来源、不会把作者说明冒充行为证明的本地事实基础。
**Architecture:** AST 存在时提取有界摘要事实，随 ProjectAnalysis 保存；视图只消费持久化事实。原始符号、调用图和语义图保持权威，摘要不写回它们。
**Tech Stack:** 现有 Python dataclass、AST、SymbolIndex、FlowAnalysis、白名单 JSON codec；后续复用 React/React Flow，不加依赖。

依据：[总设计](../specs/2026-10-30-human-readable-understanding-design.md)、[nanoGPT 验收](../specs/2026-10-30-nanogpt-readability-acceptance.md)。

## 阶段与边界

H1 仅交付分析/缓存基础，不声称界面改版完成。H2 接 Overview 职责卡与源码下钻；H3 接有控制证据的主流程及 nanoGPT 模型/训练/生成验收；H4 接当前有效 AI 结果的同图增强。
H2–H4 在各自开始前按实际消费者制定独立实施切片；不能用 H1 的门禁代替后续功能或真机验收。
默认队列从 H1 开始，按阶段连续推进；上下文不足时在阶段末留下断点。

## H1 文件边界

| 文件 | 责任 |
|---|---|
| `src/codeviz/analysis/human_understanding.py` | 有界事实值对象、分析时提取、作者句及确证操作来源 |
| `src/codeviz/analysis/project.py` | 添加末尾可选字段并在 AST 可用时构建 |
| `src/codeviz/persistence/codec.py` | 新类型白名单，严格解码，不序列化 AST |
| `src/codeviz/persistence/fingerprint.py` | ANALYSIS_VERSION 4 → 5，旧缓存重分析 |
| `src/codeviz/persistence/store.py` | 校验摘要 owner、callsite、target、anchor 均来自同一分析 |
| `tests/test_human_understanding.py` | 职责来源、身份、词法作用域、边界、稳定 ID |
| `tests/test_persistence_codec.py` | 冷暖事实等价及无源码副本 |
| `tests/test_persistence_store.py` | 篡改引用拒绝、v4 提前拒绝、其他状态不变 |
| `tests/test_analysis_version.py` | 既有版本断言随已批准的 4 → 5 变更更新，保留 Parser/Semantic 不变断言 |

禁止改 Parser/Resolver 的既有解析行为、原始 CodeGraph、AI 请求/预算/授权、View State、Annotations 或打包依赖。

### 已完成的 H1 架构决定

复用已有整体架构评审；2026-10-30 的 H1 合同复核决定 GO，明确只做基础事实，不做任务/流程关系。

```python
class HumanOperationKind(StrEnum):
    RETURN_VALUE = "return_value"
    RETURN_EMPTY = "return_empty"
    STANDARD_OUTPUT = "standard_output"
    COUNT = "count"
    SUM = "sum"
    CONVERSION = "conversion"

@dataclass(frozen=True, slots=True)
class AuthorStatementFact:
    id: str
    owner_symbol_id: str
    text: str
    source_anchor: SourceAnchor
    truncated: bool
    confidence: Confidence  # 必须 Probable

@dataclass(frozen=True, slots=True)
class OperationFact:
    id: str
    owner_symbol_id: str
    kind: HumanOperationKind
    source_anchor: SourceAnchor
    callsite_id: str | None
    target_symbol_id: str | None
    confidence: Confidence

@dataclass(frozen=True, slots=True)
class HumanUnderstandingFacts:
    author_statements: tuple[AuthorStatementFact, ...] = ()
    operations: tuple[OperationFact, ...] = ()
    omitted_fact_count: int = 0
```

`ProjectAnalysis.human_understanding` 使用 `field(default_factory=HumanUnderstandingFacts)`。
构建接口固定为 `build_human_understanding_facts(files, resolution, flow)`。
H1 全局最多 512 条基础事实，每条一个锚点；后续任务聚合/关系预算仍须单独实现，不能把本阶段的基础操作事实冒充完整任务摘要。
事实 ID 使用版本、类型、owner、完整锚点及操作种类/目标的规范 SHA-256；锚点变化可能改变派生 ID，故不得用于替代稳定源码 symbol ID，也不得把它作为 Annotations 的新持久身份。
无作者说明时不生成作者事实；显式 return 仅证明语法，外部操作使用真实解析置信度，不升级成运行时证明。

## Task H1.1：摘要事实

- [x] 先写失败测试：临时目录三个函数分别有“读取分数”“统计数量与平均值”“展示结果”的作者说明；分析结果必须包含对应 owner、说明来源、锚点。作者说明只能标 Probable，不成为 Exact 业务判定。
- [x] 运行 `.desktop-venv/Scripts/python.exe -B -m unittest tests.test_human_understanding -v`，记录缺少新事实的预期失败。
- [x] 按上述已审查值对象实施 `build_human_understanding_facts(files, resolution, flow)`；追加 `ProjectAnalysis.human_understanding`，不改变已有位置参数。
- [x] 说明仅保留首个有效句/行，最多 240 UTF-8 字节；全局最多 512 条基础事实、每条一个来源，截断记录 omitted_fact_count 并增加 UNDERSTANDING_FACTS_TRUNCATED；没有说明/确证操作时保留未知，不根据名称生成业务目的。
- [x] 外部操作只根据解析后的身份归属；用户自定义 print 不归为 stdout。嵌套函数/类/延迟表达式不能把内部效果错归父级。任何记录中的计数均是源码位置数。
- [x] 事实 ID 来自 owner/事实种类等稳定身份，不随机、不单凭行号。H1 不生成流程箭头，不把调用顺序当执行顺序。
- [x] 运行本模块及直接分析消费者测试、检查差异；报告一行后直接执行 H1.2。

## Task H1.2：缓存等价

- [x] 先写失败测试：`analyze_project → encode_analysis → JSON → decode_analysis` 后，新事实严格相等；恢复后的 ParsedFile.tree 全为 None。
- [x] 缓存不含完整 docstring、源码正文或 AST；测试用首句后的独立私密标记验证未保存全文。
- [x] 白名单登记新事实类型及枚举；保持严格字段/类型/值校验。CacheStore 交叉验证 owner、callsite、target 与锚点，坏来源和超预算值不得被缓存恢复为可信事实。
- [x] 升级 ANALYSIS_VERSION 为 5；Parser/Semantic 版本、视图状态、注释不变。
- [x] 运行 `tests.test_human_understanding tests.test_persistence_codec tests.test_persistence_store tests.test_persistence_controller`；已有 API/来源测试只按直接影响增加，不重复无关验证。

## H1 阶段末

- [x] 静态读取 nanoGPT 生成新事实统计；不导入/运行其程序，不发送 API，不声称已完成可读图验收。
- [x] 一次独立复审，聚焦作用域归属、来源链、缓存和计数语义；修复后不重复独立复审，除非引入新核心不变量风险。
- [x] `.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests`，106 个历史 skipped 不是通过；以本次实际数目记录。
- [x] `.desktop-venv/Scripts/python.exe -m ruff check src tests`。
- [x] `.desktop-venv/Scripts/python.exe -m mypy`；随后 `-m mypy --platform linux`。
- [x] 前端源码和产物完全未变则复用已通过的 653 passed / 2 todo 证据，不声称本次重跑或新增原生验收。
- [x] 查看 `git diff`，更新进度文件最多 5 行及可恢复断点：当前任务、最后命令/结果、剩余内容、下一任务。

## 结束后的明确下一步

H2 的输入是已缓存的新事实。目标是 Overview 默认先解释职责和输入/结果，再呈现符号目录；未解释的职责保留未知。点击仍走现有校验后的源码导航，原始 Calls 只作为实现详情。
H3 必须单独证明条件、循环与调用 occurrence 来源；nanoGPT 的周期评估、梯度累积、更新后清梯度和生成结果逐项验收，不能以函数命名套模板。
H4 保持既有 API、费用与源码披露边界，仅把已接受的当前证据重组到功能层；不新增自动请求。
