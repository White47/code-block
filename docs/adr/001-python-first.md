# ADR-001：Python-first

- Status：Accepted
- Date：2026-09-17

## Context

目标用户和首版价值集中在 Python / PyTorch 项目的模型与训练流程理解；同时需为其他语言保留扩展空间。

## Decision

首版以 Python Parser、通用 Python 语义、PyTorch / Transformer / Training 规则为主；语言扩展通过 Language Adapter 接入，不把其他语言的假设散落到核心图模型。

## Consequences

Python fixture 和 PyTorch 语义是 MVP 门禁；Code Graph IR 的基础字段保持语言中立。新增语言必须提供 parser、resolver、SourceAnchor 与规则适配，而不是修改 UI 以兼容特殊节点。
