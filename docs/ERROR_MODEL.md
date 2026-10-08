# 错误与健康模型

## 项目状态

`Analyzing`：分析进行中；`Ready`：结果完整可用；`Partial`：部分文件或规则失败但仍可用；`Outdated`：源码、配置或版本与当前不一致；仅版本兼容的旧分析可继续读取，版本不兼容时拒绝解码并要求重分析，独立状态 / 标注保留；`Failed`：无法建立可用分析结果。

## 问题对象

Problem 至少包含 id、severity、code、message、filePath、sourceAnchor?、phase、recoverability、evidence。示例：unsupported syntax、dynamic call unresolved、possible attention component、import unresolved、cache version mismatch。

## 传播规则

文件错误隔离到文件和相关节点；解析错误不清空其他文件。Unresolved 是合法分析结果，不等于系统失败。UI 必须同时展示结果和问题入口，并说明影响范围。

Phase 4 的动态可调用值产生 `DYNAMIC_CALL_UNRESOLVED`，多态接收者产生 `DYNAMIC_DISPATCH_UNRESOLVED` 并保留静态候选；控制、关键数据或调用分析在单文件失败时分别产生 `CONTROL_FLOW_ERROR`、`DATA_FLOW_ERROR`、`FLOW_ERROR`，保留其他文件的图。此时部分节点 / 边可能已生成，消费者应展示 Partial，而不是把局部结果宣称为完整执行路径。

Phase 5 的 `MODEL_CONTAINER_DYNAMIC` 表示成员或重复次数无法静态确定；`MODEL_CONTAINER_MEMBER_UNRESOLVED` 表示容器内某成员没有可信的模块构造证据。这些是局部未知，不应清空已识别模型，也不应自动推成 `×1` 或 Shared。

模型规则对某个文件的导入识别、某个构造或调用发生异常时记录 `MODEL_ERROR` 并继续处理其他可用文件 / 事实。它表示分析器局部失败，不是用户代码执行失败；消费者应把模型结果显示为 Partial。

Phase 6 的训练规则导入匹配或某个入口分析失败时产生 `TRAINING_ERROR`；Attention-like 组件候选规则失败时产生 `SEMANTIC_RULE_ERROR`，保留其他模型和入口。Probable 训练事件、checkpoint 候选或梯度关系是正常静态不确定性，不自动算系统错误；UI 应展示其证据与置信度。

## 用户可见信息

通用 Python 增量区分可用的未知与阶段性缺失：

- `CFG_PARTIAL`：异常 / 异步 / generator 时序近似或 CFG / continuation 预算截断；整文件截断必须传播到同文件被省略的函数视图，不影响其他文件。
- `SCOPE_DECLARATION_CONFLICT`：参数与 global / nonlocal 或两声明冲突；词法事实保留但引用不制造 Exact，Controller 标记 Partial。普通动态 Unresolved 不因此一律变 Partial。
- `GENERIC_DATA_BINDING_PARTIAL`：match / except 捕获的具体值来源未建模，绑定定义身份仍可索引，Data 来源保留 unknown。
- `GENERIC_DATA_CALL_PARTIAL`：项目函数 / callable 的返回值被使用，但未进行跨函数值传播；Problems 中保留调用锚点，不能把参数读取伪装成真实返回依赖。
- `GENERIC_DATA_BUDGET_PARTIAL`：到达定义集合超过 32，保留已知候选与 truncated / unknown，不假装已枚举全部来源。
- `GENERIC_DATA_WORK_BUDGET_PARTIAL` / `GENERIC_DATA_FILE_WORK_BUDGET_PARTIAL`：作用域或文件累计访问预算耗尽；标记截断与未分析作用域，不影响其他文件独立分析。
- `GENERIC_DATA_FINALLY_PARTIAL`：finally 的退出路径来源采用保守合并，读取和后继值保留 unknown；不能将多个路径来源解释为精确运行时数据。
- `IMPORT_WORK_BUDGET_PARTIAL` / `VALUE_INHERIT_WORK_BUDGET_PARTIAL`：重导出或继承图累计查询预算耗尽；保留已知候选、unknown / truncated 和当前引用锚点。缓存中的 partial 结果不能变成完整解析。
- `VALUE_COMPREHENSION_PARTIAL`：推导式迭代内可能重绑定 callable，单轮候选不足以确定所有调用；保留 Unknown，不把创建时的首 iterable 调用混为循环内调用。
- `VALUE_FILE_WORK_BUDGET_PARTIAL`：普通 callable / 实例候选的语句和表达式访问耗尽文件累计预算，停止重复求值；全文件调用保留已知候选并标记 unknown / truncated，遗漏目标不能回退到词法 Exact。一次线性语法扫描补全不完整标记，下一文件独立分析。
- `GENERIC_DATA_FLOW_ERROR`：通用 def-use 内部失败，与上述设计限制区分；索引失败的文件不得继续进入此阶段制造重复错误。

Partial 不是不能阅读：有效图、搜索、源码与缓存仍可用。图的节点 / 边预算截断同时正确记录 hidden counts、has_more / budget_truncated，不能仅因节点未超限就漏报边截断。

Overview 显示 files parsed、symbols resolved、Exact / Probable / Unresolved。Inspector 显示规则依据。仅缓存版本兼容的 Outdated 结果可继续查看，并带明显状态标识；版本不兼容时要求重分析。

桌面 Session 的 `hasAnalysis` 由后台实际 analysis 是否存在派生，不从状态名称猜测。`Outdated + hasAnalysis=false` 不查询旧节点、不保存旧分析布局，只提示重新分析；`Outdated + hasAnalysis=true` 仍可只读查看旧结果。显式打开 / 分析前，辅助布局保存的真实失败单独显示警告，不吞掉操作本身的错误。项目写请求等待后台真实完成，不将前端截止当成取消或完成；异常停滞需退出 / 重启，取消协议仍后置。详见 [项目操作恢复](PROJECT_ACTION_RECOVERY.md)。
