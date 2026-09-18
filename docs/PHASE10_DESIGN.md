# Phase 10 本地缓存与状态设计

日期：2026-09-18。基于已批准的 `CACHE_SPEC.md`、`ERROR_MODEL.md` 和当前 Python/Tkinter 仓库；不改变显式一次性 Analyze、源码只读或 Phase 9 图查询契约。

## 存储决定

采用标准库 JSON、固定 schema 和原子替换；不使用 pickle 或任意类反序列化。应用数据目录按平台取 Windows `LOCALAPPDATA`、macOS Application Support、Linux XDG data home，路径不落在被分析项目内。项目目录的规范化绝对路径只用于散列缓存文件名与缓存身份校验。每项目分开保存 `analysis.json`、`view-state.json`、`annotations.json`；`recent-projects.json` 独立保存。任何缓存错误不能删除或覆盖标注。

Analysis Cache 保存源码指纹、分析配置与版本、文件解析成功状态、Symbol Index、Code Graph IR、模型 / 训练事实、Semantic Graph、issues / health 所需计数；**不保存 AST、源码全文或可执行对象**。反序列化只接受显式白名单的数据类型、字段和大小上限；损坏或不兼容时提示重新 Analyze，不执行缓存内容。`ParsedFile` 的 AST 在内存分析后可用，缓存加载时仅保留 `parsed_success` 元数据，不制造假 AST。当前 UI / View Builder 查询不依赖重新加载 AST。

指纹采用已发现 `.py` 文件的路径、内容 SHA-256 及分析配置指纹。只读扫描排除已有 ignore / symlink 规则，并在读取时核对真实路径仍位于项目内。文件新增、删除、字节变化、Ignore Paths、分析器 / 规则版本或 Python parser 版本变化均标 Outdated；当前旧图可继续查看，但所有相关视图和源码跳转显示陈旧提示。显式 Re-analyze 成功后才替换旧缓存；分析期间变化需保守判为 Outdated。失败时保留旧缓存与标注，并显示失败原因。

View State 独立存已展开节点、手动位置、当前视图/根/选中节点、打开标签、缩放/平移与过滤器（当前 UI 尚无的交互留空）；重建分析缓存不清掉用户位置。应用启动打开目录后若缓存匹配，直接显示 Overview；若不匹配显示 Outdated 与原因并允许手动重分析。Recent Projects 仅保存存在的本地目录路径，数量有上限；不会自动分析最近项目。Annotations 首版建立独立、稳定的读写边界，不做语义编辑器。

## 安全与恢复

写入采用临时文件加 `os.replace`，失败不得留下半份新缓存；读取限制文件大小、JSON 深度/类型、路径身份。加载的 Code Graph / Semantic Graph 仍经领域构造器验证。缓存目录、项目路径或文件指纹无法安全核验时，不把旧结果宣称 Ready；标记 Outdated 或要求 Analyze。缓存不保存完整源码，不写入源码仓库，不上传，也不导入用户项目。

## UI 与验收

保留 Overview 的 Analyze Project 操作，并根据状态显示 Load / Outdated / Re-analyze；Recent Projects 在 Explorer 可选。旧图、Inspector 和 Source 标签必须醒目显示 Outdated，源码预览说明当前文件可能不对应旧锚点。Health / Problems 已在 Phase 8 实现，Phase 10 只补缓存加载后的正确呈现和缓存问题说明。验证覆盖无变化加载、单文件内容修改/新增/删除、配置/版本变化、损坏缓存、标注与视图状态独立、原子写、源码不进缓存、项目目录无污染以及旧结果可用但明显过期。
