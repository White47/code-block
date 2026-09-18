# 缓存规范

## 三类数据

1. Analysis Cache：Project Metadata、Symbol Index、Code Graph IR、Semantic Graph、Entry Points、Detected Models、Problems、analysis / parser / semantic rule version、source fingerprints、分析配置指纹。
2. View State：node positions、expanded / collapsed、active view、open tabs、selected node、zoom / pan、filters。
3. Annotations：用户标注、未来的语义纠正；必须独立于分析缓存。

## 规则

- 首次 Analyze 后保存；再次打开比较源码 fingerprint、Ignore Paths 与其他分析配置、解析器和语义规则版本。
- 输入与版本均无变化时直接加载；任一项变化时标记 Outdated，并提供 Re-analyze；允许继续看旧结果，但要显示失效原因。无法安全读取的旧缓存应拒绝加载并提示重新分析。
- 不保存完整源码副本；缓存放应用数据目录，不污染用户仓库。
- Stable ID 不因行号单独改变；缓存版本变化要可识别并安全失效。
- 缓存损坏时保留源码访问能力，删除 / 重建分析缓存不应丢失 Annotations。

Phase 10 依据真实标准库 + Tkinter 技术栈选择 JSON，详见 `adr/007-json-local-cache.md`。源码指纹采用发现的 `.py` 文件内容 SHA-256、Ignore Paths 与分析 / parser / semantic rule 版本；文件不可安全核验时不将旧缓存标为 Ready。View State、Annotations 和 Recent Projects 各自独立于 Analysis Cache。
