# 缓存规范

## 三类数据

1. Analysis Cache：Project Metadata、Symbol Index、Code Graph IR、Semantic Graph、Entry Points、Detected Models、Problems、analysis / parser / semantic rule version、source fingerprints、分析配置指纹。
2. View State：node positions、expanded / collapsed、active view、open tabs、selected node、filters，以及每个图视图各自的 viewport（zoom / pan）与最多三个细节岛的 view kind、来源 Stable ID 和场景位置。
3. Annotations：用户标注、未来的语义纠正；必须独立于分析缓存。

## 规则

- 首次 Analyze 后保存；再次打开比较源码 fingerprint、Ignore Paths 与其他分析配置、解析器和语义规则版本。
- 输入与版本均无变化时直接加载；任一项变化时标记 Outdated，并提供 Re-analyze；允许继续看旧结果，但要显示失效原因。无法安全读取的旧缓存应拒绝加载并提示重新分析。
- 不保存完整源码副本；缓存放应用数据目录，不污染用户仓库。
- Stable ID 不因行号单独改变；缓存版本变化要可识别并安全失效。
- 缓存损坏时保留源码访问能力，删除 / 重建分析缓存不应丢失 Annotations。
- View State 与 Analysis Cache 分文件保存、独立加载。viewport zoom 必须在 25%–250% 内，pan / 岛坐标必须为有限数值；视图名、记录字段、ID 长度、重复 ID 和每视图三岛上限都要校验。旧版 View State 缺少新字段时使用默认 viewport 和空岛列表。
- View State 整体损坏、超限或格式错误时记录 `View state:` 问题并回退到默认视图状态，不能删除或阻止加载有效 Analysis Cache / Annotations。重新构建画布时，来源节点已不存在或无法生成局部图的陈旧岛会被单独清理，其他岛、主图和分析事实保持可用。
- 关闭或移动岛、Fit / 100% / 缩放 / 平移只更新 View State，不重写 Analysis Cache。重置项目布局清除主节点手动位置并把岛位置设为自动放置，同时保留 viewport、分析、标签和展开状态。

Phase 10 依据真实标准库 + Tkinter 技术栈选择 JSON，详见 `adr/007-json-local-cache.md`。源码指纹采用发现的 `.py` 文件内容 SHA-256、Ignore Paths 与分析 / parser / semantic rule 版本；文件不可安全核验时不将旧缓存标为 Ready。View State、Annotations 和 Recent Projects 各自独立于 Analysis Cache。
