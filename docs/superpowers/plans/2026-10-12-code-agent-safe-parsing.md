# B1 Safe Parsing Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Process amendment (2026-10-15):** process is governed by the repository `AGENTS.md`. Do not load `subagent-driven-development` or `executing-plans`; do not run a per-task spec/quality review pair. Technical requirements below are unchanged.

**Goal:** 为实际解析的原始字节绑定 SHA256，并提供只读、限量、句柄核验的共享读取原语。

**Architecture:** `source_handles.py` 提供平台安全读取结果，parser 与 fingerprint 都消费该原语，analysis 不依赖 UI/assistant。只有安全读取成功才产生字节及哈希。本批不签发 AI 引用，也不把旧缓存当成可信解析来源。

**Tech Stack:** Python 3.11+ 标准库、Linux descriptor-relative open、Windows CreateFileW、unittest/Ruff/mypy；不新增依赖、不执行项目代码、不调用网络。

---

## 1. 范围与审批

2026-10-12 architect 已只读复审并放行下列合同。用户要求按阶段继续，沿用已选择的当前工作区及 `codex/general-python-analysis`，保留所有阶段A文档/代码和 `.codex/`。基线 parser 6项、assistant 22项定向通过。

本批是B阶段第一子任务；B2缓存版本、B3不可变快照、B4授权片段/freshness及B5纯查询工具在本批之后按序实施。无API/侧栏/编辑器修改，不自动commit/push。

## 2. 文件职责

| 文件 | 变更 |
| --- | --- |
| `src/codeviz/source_handles.py` | 保留 opened_file_path；增加 SourceRead、SourceReadError、read_source_bytes 及 Linux/Windows读取私有实现 |
| `src/codeviz/analysis/python.py` | ParsedFile末尾追加 content_hash；读取一次并检测编码，AST和哈希使用同一来源 |
| `src/codeviz/persistence/fingerprint.py` | _file_digest委托同一原语，维持50 MB限额和现有版本；B2再升级版本 |
| `tests/test_source_handles.py` | 新增读取、竞态、平台及资源清理测试 |
| `tests/test_parser.py` | 扩展真实字节哈希、编码/错误保留、物理行计数及旧构造兼容 |
| `tests/test_persistence_store.py` | 必要时补共享读取失败不产生成功fingerprint的直接消费者回归 |
| `tests/test_persistence_controller.py` | 缓存来源验证/分析fingerprint读取失败的现有界面错误捕获回归 |

## 3. 已锁定 API

`SourceRead` 是 frozen/slots 记录，字段为 `raw_bytes: bytes`、`content_hash: str`。读取原语返回完整成功记录，错误不暴露部分字节/哈希。

`SourceReadError(ValueError)` 提供稳定 `code` 与固定消息，保留controller捕获fingerprint失败的兼容性；不将底层路径/源码/系统异常字符串作为公开消息。底层异常用 `from None` 抑制公开异常链，不得进入 parser 的公开 AnalysisIssue。

`read_source_bytes(project_root: Path, relative_path: str, *, max_bytes: int) -> SourceRead`：root由可信调用方规范化；限额严格正int且拒绝bool。相对路径必须已规范化，拒绝空/点组件、绝对/drive/UNC/device路径、遍历、NUL和反斜杠；Windows额外拒绝任何组件中的冒号、保留设备名（含扩展名）、尾随点/空格。不导入UI或assistant。

错误码：INVALID_SOURCE_PATH、UNSUPPORTED_PLATFORM、SYMLINK_UNSUPPORTED、NOT_REGULAR_FILE、FILE_TOO_LARGE、SOURCE_CHANGED、SOURCE_VERIFICATION_FAILED、READ_ERROR。非法限额是ValueError。parser复制读取错误码/固定消息到AnalysisIssue；fingerprint直接传播错误而非返回部分成功。

## 4. 读取算法与平台边界

1. lstat root及每个目录组件：必须是无symlink/reparse的目录且身份可用。leaf必须是无symlink/reparse的普通文件，stat大小先限量。保存目录身份及leaf `(dev, ino, size, mtime_ns, ctime_ns)`。
2. Linux：用root目录fd，逐组件 `os.open(name, O_DIRECTORY|O_NOFOLLOW, dir_fd=parent_fd)`；leaf用 `O_RDONLY|O_NOFOLLOW|O_NONBLOCK`。先fstat确认普通文件，再包装为binary stream。每个fd在所有成功/失败出口关闭。
3. Windows：CreateFileW使用 GENERIC_READ、OPEN_EXISTING、FILE_FLAG_OPEN_REPARSE_POINT，仅FILE_SHARE_READ，不开放WRITE/DELETE共享。检查GetFileType==FILE_TYPE_DISK并核查句柄/leaf属性；正确将handle移交CRT fd，转移失败时CloseHandle，成功后由fd关闭，不双重关闭。
4. 在读取前比较handle与leaf身份/状态，验证 `opened_file_path` 等于预期canonical path，不仅是处于root之下；再次核验目录身份。Linux跨观察比较完整ctime；Windows跨API核验dev/ino/size/mtime及双方可用的birthtime，不比较含义不同的ctime数值。pathname自身各次ctime和handle自身各次ctime均必须稳定。
5. 限量分块读取，每次请求不超过剩余额度+1；实际总量超限拒绝。读至EOF且实际长度必须等于open时大小。
6. 保持handle打开，重复fstat、actual path、leaf lstat和目录/root身份检查；身份/size/mtime一致，且各同一API的ctime均未变化，才计算 `sha256(raw).hexdigest()` 返回。

opened_file_path仅明确支持win32和linux；Linux缺/proc句柄核验、平台不支持或身份不可用时拒绝。只读允许硬链接，不移植编辑服务的权限/文档状态。

限制：stat观察不能证明对恶意同尺寸改写/元数据恢复或目录ABA的原子快照；O_NONBLOCK也不是整个文件系统I/O期限。只承诺来自已核验句柄的有界字节及未发现变更，不宣称敌对文件系统sandbox。Windows目录前后检查不等价Linux目录fd约束。

Windows补充（2026-10-12 architect已复审批准）：本机Python3.12.14原生lstat的ctime与CreationTime一致，fstat可与ChangeTime一致，两者birthtime一致；这是具体运行环境的实测，不是所有Windows/Python版本的保证。不同API数值比较不能提供有意义的变更检测，因而按上述同API稳定性修订。**没有可比较的打开前ChangeTime基线**，不能声称识别每个打开前同尺寸改写并恢复mtime的情况，birthtime不会补上这个缺口；哈希仍精确绑定用于AST的实际字节。需回归可接受的跨API差异、handle/path同API变化拒绝，不能只修改文档省略测试。

字段语义依平台/文件系统而异，Windows自3.12增加birthtime且ctime已弃用作创建时间的接口；文档背景见 [Python 3.12 os.stat_result](https://docs.python.org/3.12/library/os.html#os.stat_result)。上述具体跨API差异来自本机原生观察，不能仅由文档推导。

## Task 1：失败测试与共享读取（test_writer → core_implementer）

- [x] 新增真实临时文件行为测试，用subTest归类输入，不重复整项目fixture。示例：

```python
def test_read_exact_bytes_and_hash(self):
    with TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        raw = b"# coding: latin-1\r\nname = '\xe9'\r\n"
        (root / "app.py").write_bytes(raw)
        self.assertTrue(hasattr(source_handles, "read_source_bytes"))
        result = source_handles.read_source_bytes(root, "app.py", max_bytes=len(raw))
        self.assertEqual(result.raw_bytes, raw)
        self.assertEqual(result.content_hash, sha256(raw).hexdigest())
```

- [x] 写入空/恰限/超限/读取增长及缩短、非法path/limits、leaf/ancestor/root替换、handle错指、零身份/核验失败、普通目录和special leaf、资源关闭覆盖。实际source22项中18通过，3Linux native及1Windows symlink权限跳过，不冒称全平台已验证。FIFO回归用限时子进程，不让suite挂死。
- [x] writer以-v、主代理独立以-q运行同范围；初始18项中14明确断言缺少API导致RED、4平台跳过，无collection错误。
- [x] core_implementer按第3/4节实现共享原语；现有opened_file_path调用者保留接口，不修改UI编辑行为或降级为Path.resolve安全检查。
- [x] 定向复跑，检查所有fd/handle失败清理；Ruff与Windows/Linux mypy通过。

## Task 2：Parser同源哈希与fingerprint消费（同批核心合同）

- [x] 在test_parser.py补测试：UTF8/BOM/cookie/CRLF/CR、Unicode分隔符不增加物理行、成功及decode/syntax/depth/AST编码失败保留实际raw哈希；读取失败/超限无哈希。旧六参数ParsedFile构造仍可用。示例：

```python
def test_hash_preserved_on_syntax_error(self):
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        raw = b"def broken(:\r\n"
        (root / "app.py").write_bytes(raw)
        parsed = parse_python_file(root, "app.py")
        self.assertTrue(hasattr(parsed, "content_hash"))
        self.assertEqual(parsed.content_hash, sha256(raw).hexdigest())
        self.assertIsNone(parsed.tree)
        self.assertEqual(parsed.issues[0].code, "SYNTAX_ERROR")
```

- [x] 独立确认RED再改生产；ParsedFile末尾追加 `content_hash: str | None = None`，_failed添加keyword-only content_hash。保留原有构造位置与was_parsed语义。
- [x] parser规范化root/relative后调用一次read_source_bytes（默认5,000,000）；编码检测使用BytesIO(raw).readline，严格decode后CRLF/CR→LF，与旧tokenize.open行为一致，不重编码产生hash、不再重开文件。
- [x] ast.parse沿用filename与type_comments=True；decode检测/lookup/Unicode错误和AST Unicode错误为DECODE_ERROR，语法为SYNTAX_ERROR，递归为PARSE_DEPTH_EXCEEDED，均保留安全读取结果hash。读取失败hash=None。物理行数按规范化LF分割，排除尾随换行形成的空项、最小1。
- [x] fingerprint `_file_digest` 使用 `read_source_bytes(root, relative_path, max_bytes=50_000_000).content_hash`，失败不能返回任何成功fingerprint。B1验收时AnalysisVersion3/SemanticVersion3；B2实施后版本将独立升级，不将B1当缓存来源已经可用。
- [x] 定向回归parser14、controller8、symbols/flow/resolution/model/training/semantic/persistence直接消费者109项通过。新controller fixture以真实symbol及完整ViewState验证，替换原虚构ID被状态清理的无效准备，不放宽产品行为。

## Task 3：两阶段审查与结果

- [x] 独立规格复审通过（已批准Windows时间合同修订），之后独立quality/security review通过。补pathname-only ctime建议回归并由主代理复跑，审查者关闭Minor；没有Critical/Important遗留。本机实施可推进，不代表跨平台发布/merge门禁完成。
- [x] B1完整Python921项无失败、58环境跳过、32.732秒；Ruff全仓及Windows/Linux mypy通过。最后只追加既有测试subCase，无生产改变，source22项另独立复跑通过；前端未改，B阶段末统一完整门禁。
- [x] 实测/限制/文件清单更新至 `docs/CODE_AGENT_PROGRESS.md`、CACHE_SPEC与KNOWN_LIMITATIONS；无API/源码外传/Git推送。
- [x] [B2计划](2026-10-12-code-agent-cache-provenance.md)已建立并经architect复审通过，接着测试先行，不重复请求产品方向。

当前状态：B1本机实现、适用门禁及两阶段独立复审完成。Linux native与Windows链接权限覆盖为未关闭的平台发布验证项，明确保留；B2/B3/B4/B5未标完成。B1不提供AI可用的授权片段或可信缓存。
