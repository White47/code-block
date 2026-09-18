# 测试策略

## 分层

- Model：SourceAnchor、Stable ID、Confidence、序列化。
- Parser / Phase 2 Index：Python fixture、编码、大小限制、不执行源码、坏文件隔离、忽略目录、词法 scope、稳定 ID、raw reference 与导入别名重绑定。
- Resolver / Phase 3：绝对与相对 import、`src` 布局、包重导出、真实跨模块循环与纯别名循环、scope shadowing、`global` / `nonlocal`、重复定义及重复函数体、静态成员、第三方 ExternalSymbol、动态属性、缺失本地成员、星号导入、未使用导入、unresolved / possible targets；不得导入或执行被分析项目。
- Graph / Phase 4：Full Graph 的节点与锚点、同一行独立 CallSite、确定 / 模糊 / 外部 / 动态及继承派发调用、分支步骤到调用位置的归属、nested if / loop / with / try / continue / return、定义时调用、递归 / 互调环及长链、关键数据与 `self` 属性读写、隐藏 tmp 来源传递与覆盖、导入别名不误标、单文件阶段失败隔离；Visible Graph 查询留给 Phase 7。
- Model / Phase 5：torch 导入别名、项目内同名 torch 包与局部遮蔽、自定义 Module 继承、常见层、Sequential 顺序、ModuleList 同构 / 共享成员 / 零次 / 动态次数、ModuleDict 部分动态键、共享别名与链式赋值、条件构造、直接 / 索引 / 循环 forward CallSite 关联、未知工厂候选、稳定 ID、坏文件与规则异常隔离、不执行源码；训练规则留 Phase 6。
- Training / Semantic / Phase 6：显式导入与局部遮蔽、接收者 Exact / Probable、train/eval 与 no_grad 区分、epoch / batch 循环归属、多入口共享模型定义、条件重绑定、checkpoint 候选、Attention-like 正反例、模型 Definition / Instance 分离、图端点 / 子节点校验、来源锚点、Probable 梯度静态关系、坏文件隔离。
- Visible Graph / Phase 7：Architecture / Execution 多根与指定根、默认折叠和展开、选中组件 1-hop、单节点预算保留选中项、节点与边预算、执行主控制边优先、关键数据与真实代码级 DATA_FLOW 投影、可追溯溢出聚合、重复结构与共享实例区分、关系白名单、LOOP_BACK 布局、图例、手动位置覆盖及 JSON 数据往返；不得修改分析事实或执行项目代码。
- UI：Overview、选择、下钻、Inspector、Split、搜索、源码定位。
- Persistence：fingerprint、Outdated、View State、Annotations。

## 必备 fixture

小型纯 Python 项目、PyTorch 自定义模块、Transformer-like 模块、训练 / 验证 / 推理多入口、同名变量、if / loop / recursion、动态调用、坏文件、忽略目录和共享模块实例。

## 验证原则

每个阶段运行真实仓库已有的 lint、typecheck、test、build；空仓库阶段不伪造通过结果。Phase 2 后的质量门禁已配置 Ruff（`src tests`）和 mypy 严格模式（`src/codeviz`），具体命令见 `README.md` 与 `QUALITY_GATES.md`。失败分为原有失败、环境失败、新增失败。禁止为了测试自动执行被分析项目。

## 验收

以 `MVP_ACCEPTANCE.md` 三条黄金路径为集成门禁。来自用户源码的图节点必须能定位到 SourceAnchor；Project、聚合和 ExternalSymbol 等节点至少须显示来源依据或明确的不可跳转原因，不得出现失效的 Open Source 动作。
