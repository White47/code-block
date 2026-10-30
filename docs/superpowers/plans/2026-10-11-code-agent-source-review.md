# Agent 来源一致性：阶段 B 架构审查记录

本记录是下一批的审查输入，不是已完成的实现或完整执行计划。2026-10-11由 architect 只读核查实际仓库；阶段 A 的实现与审查不等于阶段 B 放行。

## 已确定边界

1. **B1 安全读取与解析哈希**：`analysis/python.py` 的 `ParsedFile` 末尾追加可选 `content_hash`，保留位置参数兼容。只读原语放在 `source_handles.py`，不能让 analysis 依赖 assistant 或 UI。一次二进制读取的同一份原始字节用于编码检测、严格解码、AST 和 SHA256；独立 fingerprint 不可替代实际解析来源。
2. **B2 缓存一致性**：`persistence/codec.py` 保存哈希而不保存源码/AST；AnalysisVersion 升至 4，SemanticVersion 保持 3。`store.py` 在解码前拒绝 v3，保留视图及注释。保存和加载时还要核验解析文件清单/非空哈希与 fingerprint 一致，避免源码临时变化后恢复导致错配。
3. **B3 不可变快照**：新增 `assistant/snapshot.py`，复制白名单字段及锚点，不能保留含可变 metadata 的原节点或整个 ProjectAnalysis。可信调用方显式提供项目根及 discovery/ignore 配置；缓存没有 AST 时仍可构造快照。
4. **B4 授权读取与 freshness**：新增 `assistant/source.py`；路径同时属于授权集合与快照文件集合。句柄来源、文件 identity、普通文件、目录链接、实际字节上限、读前后状态均须检查，验证失败关闭访问。完整扫描检测新增/删除/修改，无法验证不能当 fresh。返回片段仍独立核验解析哈希。
5. **B5 纯索引工具**：新增 `assistant/tools.py`，只查询快照并调用 B4，不调用会发布 session 的 DesktopBridge 查询，也不改变图、标签页、磁盘或编辑状态。

## 不能省略的细节

- 安全读取成功而 decode/syntax/depth 失败，仍可保留原始字节哈希；读取失败、超限或竞态失败则为 None。缺哈希文件仍在完整清单，但不能签发可信源码证据。
- `opened_file_path()` 目前支持 Windows 与 Linux `/proc/self/fd`；不支持的平台须明确拒绝，不退化为只检查 resolve。
- AST 列是解码文本的 UTF-8 字节偏移，不是原文件编码字节或字符数。验证字符边界后转换为 UTF-16 单位，Monaco 使用一基列；拒绝 bool、越界和倒置范围。无列时明确整行处理。
- 分行遵循 Python 源文件换行规则，避免 `splitlines()` 将字符串中的 Unicode 分隔符错误视为源码行。
- freshness 仅证明检查时点，不承诺后续磁盘永不变化。查询/点击时继续核验对应来源，模型不能提供“可信哈希”。

## 下一批计划必须包含的验证

| 子任务 | 定向测试与直接消费者 |
| --- | --- |
| B1 | parser：编码 cookie、BOM、CRLF、读取增长/替换、语法失败；既有解析消费者 |
| B2 | codec/store/version：v3 解码前拒绝、错误哈希、来源错配、用户状态保留；controller 缓存回归 |
| B3 | snapshot：原图/metadata 改动不影响快照，无 AST 缓存恢复、完整文件集合 |
| B4 | source：授权、路径、Unicode 列、句柄验证故障、changed/added/removed、hash 不匹配 |
| B5 | tools：确定性检索、未知 ID、外部/无锚点节点、无 UI 与写副作用；search 回归 |

实施顺序 B1→B2，B3 依赖已锁定字段合同，B4 依赖 B1+B3，B5 依赖 B3+B4。每项先补失败测试，核心代码交 core_implementer，独立审查后推进；阶段内定向回归，阶段末完整适用门禁。没有真实 API 调用或用户源码外传。
