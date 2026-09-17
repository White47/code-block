# 错误与健康模型

## 项目状态

`Analyzing`：分析进行中；`Ready`：结果完整可用；`Partial`：部分文件或规则失败但仍可用；`Outdated`：源码、分析配置或解析器 / 规则版本变化，旧结果可读但已非当前分析；`Failed`：无法建立可用分析结果。

## 问题对象

Problem 至少包含 id、severity、code、message、filePath、sourceAnchor?、phase、recoverability、evidence。示例：unsupported syntax、dynamic call unresolved、possible attention component、import unresolved、cache version mismatch。

## 传播规则

文件错误隔离到文件和相关节点；解析错误不清空其他文件。Unresolved 是合法分析结果，不等于系统失败。UI 必须同时展示结果和问题入口，并说明影响范围。

## 用户可见信息

Overview 显示 files parsed、symbols resolved、Exact / Probable / Unresolved。Inspector 显示规则依据。Outdated 结果可继续查看，但所有视图带明显状态标识。
