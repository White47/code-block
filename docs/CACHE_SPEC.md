# 缓存规范

2026-10-23 生产组装：DeepSeek 准入、连接能力、密钥和解释仍只在进程内存中；不改变下列缓存合同。
私有离线验收的宿主阶段、计数和产物校验收据存放于任务 build 目录，不写入分析缓存，
不记录凭据、用户问题、源码或 HTTP 请求/响应正文；这些收据不是可恢复 AI 会话。

2026-10-20 AI 状态：密钥、聊天、临时流文本、批准片段及证据租约仅在内存中，不写入分析缓存、偏好或 View State。关闭侧栏不清空安全显示，退出后不恢复会话或密钥；清空、新建、撤销及项目/配置/分析失效使用独立修订和取消协议。原有分析、布局、Annotations 缓存合同不变。缓存隔离与基础 wheel 已定向检查，不代表 E 原生交付通过，见 [助手指南](CODE_AGENT_GUIDE.md)。

2026-10-12 Agent B1/B2增量：ParsedFile保存实际用于AST解析的原始字节SHA256，parser和fingerprint复用限量/句柄核验读取；codec保留hash但仍不保存AST/原始字节/源码副本。当前AnalysisVersion=4、SemanticVersion=3、JSON schema=1。版本3及更早在领域解码前要求重新Analyze，独立布局/注释不丢失。见 [实施进度](CODE_AGENT_PROGRESS.md)。

Monaco 编辑扩展的草稿仅存在当前进程内，不写入 Analysis Cache／View State／Annotations。显式保存成功后，已有分析置为 Outdated、保留旧图与独立注释，重新分析仍需用户触发。编辑版本绑定原始源码 bytes 与项目／文件身份，不复用分析缓存版本作为保存授权。合同见 [源码编辑](SOURCE_EDITING.md)。

## 三类数据

1. Analysis Cache：Project Metadata、Symbol Index、Code Graph IR、Semantic Graph、Entry Points、Detected Models、Problems、analysis / parser / semantic rule version、source fingerprints、分析配置指纹。
2. View State：node positions、expanded / collapsed、active view、open tabs、selected node、filters，以及每个图视图各自的 viewport（zoom / pan）与最多三个细节岛的 view kind、来源 Stable ID 和场景位置。
3. Annotations：用户标注、未来的语义纠正；必须独立于分析缓存。

## 规则

结构探索的 `structure_depth` 是临时查询状态，重新打开默认 1 层，不改变 Analysis Cache 版本或新增 ViewState 深度字段。投影身份校验区分合法路径与当前可见范围：重开后不恢复不可见选择，但仍合法的手动位置和细节岛记录保留，来源重现后可继续使用；源码引用失效才清理。不得只根据 ID 前缀认可投影或为省事删除全部投影位置。新增 `ProjectStructure` 视图沿用已有按视图持久化格式。

同一会话内，仍打开的当前图经过 Source / Settings / Overview 标签再返回时保留结构深度，可以继续收起；这不是深度持久化。切换图或根、关闭图后重开、重新分析和重新打开项目均从深度 1 开始，不能把源码标签切换等同于项目重开。

通用Python增量原来使用3/3；解析来源合同已将 `ANALYSIS_VERSION` 升至 `4`，`SEMANTIC_VERSION` 保持 `3`，Shared JSON schema保持 `1`。版本不匹配在领域对象解码前拒绝Analysis Cache，要求重新Analyze，不把合法旧数据称为损坏；独立View State/Annotations保留。缓存继续包含GenericEntryCandidate、非定义LexicalScope、可能调用候选、通用Data事实与FunctionFlow所需图，追加ParsedFile来源hash，不保存AST或源码。中间版本2及旧版本3都需重新分析。

来源校验在save的编码/写入前，以及load解码后返回前执行：完整分析文件集合与fingerprint集合相同，规范项目相对 `.py` 路径且无重复；非null解析hash必须匹配对应fingerprint，成功解析不得缺hash。失败文件可保留匹配hash或None，仍在完整清单但None不能提供可信源码证据，不用后续fingerprint补填解析hash。写入拒绝不会覆盖旧cache/用户状态，controller保留in-memory结果并标Partial。

load一致性校验不重新扫描磁盘，内部一致但已过时的图仍可加载并由controller标Outdated；可点击Agent引用还需后续授权/fresh核验。相同before/after指纹不替代实际AST来源，即使源码短暂变化后恢复，parsed hash不匹配也拒绝保存。这些检查不证明恶意修改cache的真实性，也不证明图对象深不可变。

- 首次 Analyze 后保存；再次打开比较源码 fingerprint、Ignore Paths 与其他分析配置、解析器和语义规则版本。
- 输入与版本均无变化时直接加载；源码或配置变化且缓存版本兼容时标记 Outdated，允许读取旧图并显示失效原因。版本不兼容时在解码前拒绝旧 Analysis Cache，要求 Re-analyze，仅保留独立 View State / Annotations。无法安全读取的旧缓存也应拒绝加载并提示重新分析。
- 不保存完整源码副本；缓存放应用数据目录，不污染用户仓库。
- Stable ID 不因行号单独改变；缓存版本变化要可识别并安全失效。
- 缓存损坏时保留源码访问能力，删除 / 重建分析缓存不应丢失 Annotations。
- View State 与 Analysis Cache 分文件保存、独立加载。viewport zoom 必须在 25%–250% 内，pan / 岛坐标必须为有限数值；视图名、记录字段、ID 长度、重复 ID 和每视图三岛上限都要校验。旧版 View State 缺少新字段时使用默认 viewport 和空岛列表。

React 相机运行时以项目会话 / view / root 为 context，不新增持久化版本。不同 root 不机械复用同 view 的旧 pan；节点测量完成后定位新图。同 context 的有效可见 viewport 保留，恢复位置若令所有节点屏外则适应，不清空磁盘 state。手动主节点 / 岛坐标不受此恢复影响。详见 [画布恢复](CANVAS_RECOVERY.md)。
- View State 整体损坏、超限或格式错误时记录 `View state:` 问题并回退到默认视图状态，不能删除或阻止加载有效 Analysis Cache / Annotations。重新构建画布时，来源节点已不存在或无法生成局部图的陈旧岛会被单独清理，其他岛、主图和分析事实保持可用。
- 关闭或移动岛、拖动画布空白 / 中键产生的 canvas pan、`Ctrl+滚轮` 缩放、Fit 和 100% 更新 View State，不重写 Analysis Cache。Tk 原生滚动条、普通滚轮和 Page Up / Page Down 只改变当前 Canvas 的临时滚动偏移，不写入 viewport，也不在重启后恢复。重置项目布局清除主节点手动位置并把岛位置设为自动放置，同时保留 viewport、分析、标签和展开状态。

Phase 10 依据真实标准库 + Tkinter 技术栈选择 JSON，详见 `adr/007-json-local-cache.md`。源码指纹采用发现的 `.py` 文件内容 SHA-256、Ignore Paths 与分析 / parser / semantic rule 版本；文件不可安全核验时不将旧缓存标为 Ready。View State、Annotations 和 Recent Projects 各自独立于 Analysis Cache。

## 项目理解的内存边界（2026-10-29）

ProjectUnderstanding 引用、原Answer及私有来源manifest在同一个有界ConversationStore事务中保留，计入原容量，不额外永久pin会话。分析缓存、偏好及View State不保存它们，不保存完整源码副本或密钥。2026-10-29 层级恢复后不再维护独立 AI 画布/位置 Map；增强态底图的静态位置和相机继续按原 View State 保存，不能将 AI 条目 ID 写入静态 save_layout。

成功项目分析原子替换当前版本；失败不覆盖。原会话清空/淘汰、项目/分析/源码/ignore/配置/授权失效后不再生效；仅视图映射发布不删除项目解释，但仍可使一个在途C6定位失效，用户需明确重新点击，不能自动请求模型。重启后恢复静态缓存与原布局；AI理解、会话及内存密钥不作为持久化恢复内容。
