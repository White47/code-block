# ADR-007 — 本地 JSON 缓存与独立用户状态

状态：Accepted for Phase 10。日期：2026-09-18。

## 背景

当前 Python 标准库 + Tkinter 应用在内存中持有 `ProjectAnalysis`，其中 `ParsedFile.tree` 是 AST。MVP 要求分析缓存、视图状态与标注分开保存、不复制源码；既有项目没有数据库依赖。缓存文件由本地磁盘提供，按不可信输入处理。

## 决定

用显式 schema 的 JSON 保存领域结果和源码指纹，AST 与源码全文永不入库；每项目 Analysis、View State、Annotations 分文件，Recent Projects 另存。写入用同目录临时文件和原子替换，读取用大小 / schema / 类型边界。只允许白名单领域类型重建，不使用 pickle。应用数据目录按操作系统惯例选择，不写入被分析仓库。

## 权衡

优点：零运行时依赖、易审计、损坏可隔离、不会执行反序列化对象，和当前 `CodeGraph.to_dict` / `ViewState.to_dict` 相容。代价：JSON 体积和全量重写成本较高，严格类型转换代码比 pickle 多；首版以中小项目和显式 Analyze 为目标。若性能基线显示瓶颈，后续可在保持外层契约和独立状态文件的前提下迁移 SQLite，不在 Phase 10 预先增加复杂度。
