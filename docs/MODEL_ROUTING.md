# 模型路由

本文件只回答「什么任务用什么模型」。
**流程规则（何时复审、跑多少测试、如何停止）由根目录 `AGENTS.md` 决定，不在本文件重复。**

## 默认

- model: `gpt-6.1-sol`
- reasoning_effort: `medium`

## 实际存在的代理与档位

以 `.codex/agents/` 下的文件为准，本表仅作汇总。

| agent | model | reasoning | 何时使用 |
| --- | --- | --- | --- |
| `architect` | gpt-5.6-sol | high | 跨模块 / 跨阶段的设计决策：Graph IR、Semantic Graph、Resolver 与 parser 边界、重大重构 |
| `core_implementer` | gpt-5.6-sol | high | Parser、Resolver、Graph IR、Control/Data Flow、Semantic Rules、Stable ID、来源可追溯 的实现 |
| `debugger` | gpt-5.6-sol | high | 常规修复已经失败过一次的复杂缺陷 |
| `implementer` | gpt-5.6-sol | medium | 普通功能、普通缺陷、UI 改动、用户反馈修改 |
| `reviewer` | gpt-5.6-sol | high | 阶段边界或里程碑复审（含 C3 Task 6）。**不是每个任务都跑** |
| `test_writer` | gpt-5.6-terra | low | 批量重复的测试与夹具编写 |
| `explorer` | gpt-5.6-terra | low | 大范围只读仓库检索（非日常） |
| `final_auditor` | gpt-6-astra | xhigh | 里程碑终审，或二次失败仍未解决。**全项目最贵，禁止例行使用** |

## 不派发子代理的情形

以下工作由主代理直接完成，不走路由表：机械改动（重命名、夹具调整、文档、
日志接线、格式化）、单文件小改动、以及所有 `AGENTS.md` 已判定为常规的工作。

## 已废弃条目

以下名称在旧版本本文件中存在，但 `.codex/agents/` 中从未实际创建，
已于 2026-10-15 移除，避免路由到不存在的代理：

`analyst`、`implementer_light`、`debugger_deep`、`architecture_reviewer`、`docs`。

若需其中之一，应先创建对应的 `.codex/agents/<name>.toml`，再写入本表。
