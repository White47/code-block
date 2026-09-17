# ADR-004：Static Analysis 默认不执行用户代码

- Status：Accepted
- Date：2026-09-17

## Context

项目分析可能包含副作用、网络访问、未知依赖和不可信输入；MVP 的价值是可解释的源码结构，不是运行时观测。

## Decision

默认只执行文件读取和静态解析，不执行用户模块、训练循环或 import side effects。Runtime Trace、真实 tensor shape、真实 autograd graph 属于后续用户主动触发且需单独安全设计的能力。

## Consequences

所有推断必须标记 Exact / Probable / Unresolved；shape / dtype / device 只能使用静态证据；文档、测试和 UI 都要明确 Static Inference != Runtime Observation。
