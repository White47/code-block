# 性能与规模目标

## MVP 目标

- 首版优先保证中小型 Python 项目可靠。
- Full Graph 可比 Visible Graph 大；单张默认可见几十到约 100 个节点。
- 默认高层聚合，选中节点展示 1-hop；展开或搜索才增加局部范围。
- 一次性 Analyze；不在首版承诺 watcher / incremental parse。

## 保护措施

忽略依赖、权重、数据集和构建目录；第三方库作为 External Node。View Builder 必须有节点 / 边上限、超限聚合和取消 / Partial 状态。禁止 UI 直接渲染 Full Graph。

## 基线

Phase 2 出现可分析的样本项目后建立文件数、解析耗时、符号数、Visible Graph 构建耗时和内存基线；当前只有领域核心，没有解析性能基线，不能伪造数字。
