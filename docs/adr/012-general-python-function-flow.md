# ADR-012：通用 Python 事实与独立函数流程

状态：用户授权实施；架构审查通过。完成进度以 GENERAL_PYTHON_PLAN.md 和验证记录为准。

## 背景

普通源码已能 AST / 索引，但 Overview / Execution 依赖模型与训练语义。普通 main 无可见入口；HTTPX validate_* 名称被误标评估。训练入口和普通函数可能共享 symbol ID，直接复用 Execution 并猜测模式会混淆标签、详情和布局恢复。

## 决策

- 使用独立 FunctionFlow；Execution 保留训练聚合，框架角色必须有框架证据。
- 通用入口候选持久化结构依据与置信度；人工选择任意具名函数 / 方法不伪装自动入口。
- 隐式 lambda / comprehension 使用非定义 LexicalScope owner，Symbol / Resolver / Flow / Data 对齐作用域。类作用域、首 iterable、默认值和 walrus 遵循 Python 词法规则。
- 调用值候选独立于词法归属。函数别名仍解析到其变量定义，候选 callee 进入 uncertain CALLS；实例与继承不自动证明 runtime 分派。
- 通用 def-use 与 ML critical data 分开投影，避免普通变量淹没模型 / 训练视图。
- 首批通用 def-use 为过程内 may analysis。项目 callee 返回值依赖无法传播时保留 unknown 和调用锚点 Partial；读取实参不等于证明被调函数返回依赖它。跨函数传播与独立预算留后续，架构复核确认这一明确边界满足“允许不知道、不能伪造确定性”。
- 控制图使用有界静态结构；已知异常、并发、生成器简化必须诊断。完整 finally 修复区分正常 / return / raise / break / continue 退出，不按源码顺序伪造路径。
- 缓存分析版本独立升级，解码前检查；View State / Annotation 共享存储 schema 不随分析版本升级。

## 备选与影响

不整体改用第三方调用图 / 类型分析器。Pyan、LibCST、Jedi、Pyright 的作用域、候选与局部图方法作为研究参照，自建夹具验证；不复制许可受限实现，不执行目标源码。

增加类型和视图需要端到端契约、缓存、导航、前端和打包回归。项目支持矩阵必须持续标出限制；静态图不承诺解释所有 Python 动态行为。
