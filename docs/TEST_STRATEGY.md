# 测试策略

## 分层

- Model：SourceAnchor、Stable ID、Confidence、序列化。
- Parser / Phase 2 Index：Python fixture、编码、大小限制、不执行源码、坏文件隔离、忽略目录、词法 scope、稳定 ID、raw reference 与导入别名重绑定。
- Resolver：imports 的跨文件目标、shadowing、definition / reference 关联、unresolved / possible targets。
- Graph：边类型、call / control / data、traceability、Visible Graph 查询。
- Semantic：PyTorch 层、ModuleList、Shared、训练规则、证据。
- UI：Overview、选择、下钻、Inspector、Split、搜索、源码定位。
- Persistence：fingerprint、Outdated、View State、Annotations。

## 必备 fixture

小型纯 Python 项目、PyTorch 自定义模块、Transformer-like 模块、训练 / 验证 / 推理多入口、同名变量、if / loop / recursion、动态调用、坏文件、忽略目录和共享模块实例。

## 验证原则

每个阶段运行真实仓库已有的 lint、typecheck、test、build；空仓库阶段不伪造通过结果。Phase 2 后的质量门禁已配置 Ruff（`src tests`）和 mypy 严格模式（`src/codeviz`），具体命令见 `README.md` 与 `QUALITY_GATES.md`。失败分为原有失败、环境失败、新增失败。禁止为了测试自动执行被分析项目。

## 验收

以 `MVP_ACCEPTANCE.md` 三条黄金路径为集成门禁。来自用户源码的图节点必须能定位到 SourceAnchor；Project、聚合和 ExternalSymbol 等节点至少须显示来源依据或明确的不可跳转原因，不得出现失效的 Open Source 动作。
