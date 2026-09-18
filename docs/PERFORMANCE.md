# 性能与规模目标

## MVP 目标

- 首版优先保证中小型 Python 项目可靠。
- Full Graph 可比 Visible Graph 大；Phase 7 的单张图默认最多 100 个节点、300 条边，可按请求调低上限。
- 默认高层聚合，选中节点展示 1-hop；展开或搜索才增加局部范围。
- 一次性 Analyze；不在首版承诺 watcher / incremental parse。

## 保护措施

忽略依赖、权重、数据集和构建目录；第三方库作为 External Node。Phase 7 View Builder 已限制输出节点 / 边，超限时提供可追溯聚合及隐藏数量；取消 / Partial 状态尚未接入视图查询。禁止 UI 直接渲染 Full Graph。

## 基线

Phase 2 出现可分析的样本项目后建立文件数、解析耗时、符号数、Visible Graph 构建耗时和内存基线；当前只有领域核心，没有解析性能基线，不能伪造数字。
