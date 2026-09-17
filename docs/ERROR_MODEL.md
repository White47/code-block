# 错误与健康模型

## 项目状态

`Analyzing`：分析进行中；`Ready`：结果完整可用；`Partial`：部分文件或规则失败但仍可用；`Outdated`：源码、分析配置或解析器 / 规则版本变化，旧结果可读但已非当前分析；`Failed`：无法建立可用分析结果。

## 问题对象

Problem 至少包含 id、severity、code、message、filePath、sourceAnchor?、phase、recoverability、evidence。示例：unsupported syntax、dynamic call unresolved、possible attention component、import unresolved、cache version mismatch。

## 传播规则

文件错误隔离到文件和相关节点；解析错误不清空其他文件。Unresolved 是合法分析结果，不等于系统失败。UI 必须同时展示结果和问题入口，并说明影响范围。

Phase 4 的动态可调用值产生 `DYNAMIC_CALL_UNRESOLVED`，多态接收者产生 `DYNAMIC_DISPATCH_UNRESOLVED` 并保留静态候选；控制、关键数据或调用分析在单文件失败时分别产生 `CONTROL_FLOW_ERROR`、`DATA_FLOW_ERROR`、`FLOW_ERROR`，保留其他文件的图。此时部分节点 / 边可能已生成，消费者应展示 Partial，而不是把局部结果宣称为完整执行路径。

Phase 5 的 `MODEL_CONTAINER_DYNAMIC` 表示成员或重复次数无法静态确定；`MODEL_CONTAINER_MEMBER_UNRESOLVED` 表示容器内某成员没有可信的模块构造证据。这些是局部未知，不应清空已识别模型，也不应自动推成 `×1` 或 Shared。

模型规则对某个文件的导入识别、某个构造或调用发生异常时记录 `MODEL_ERROR` 并继续处理其他可用文件 / 事实。它表示分析器局部失败，不是用户代码执行失败；消费者应把模型结果显示为 Partial。

Phase 6 的训练规则导入匹配或某个入口分析失败时产生 `TRAINING_ERROR`；Attention-like 组件候选规则失败时产生 `SEMANTIC_RULE_ERROR`，保留其他模型和入口。Probable 训练事件、checkpoint 候选或梯度关系是正常静态不确定性，不自动算系统错误；UI 应展示其证据与置信度。

## 用户可见信息

Overview 显示 files parsed、symbols resolved、Exact / Probable / Unresolved。Inspector 显示规则依据。Outdated 结果可继续查看，但所有视图带明显状态标识。
