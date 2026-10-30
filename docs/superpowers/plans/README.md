# 计划文档索引

> **流程以根目录 `AGENTS.md` 为准。**
> 2026-10-15 起，本目录所有计划头部原有的「REQUIRED SUB-SKILL: subagent-driven-development /
> executing-plans」与「reviewer 逐个任务独立审查」要求**均已作废**。
> 计划文档只保留技术内容（文件归属、契约、不变量、验收标准）。
> 这些计划头部仍保留原文，但紧接着都带有一行 **Process amendment (2026-10-15)** 说明。

先读本索引定位文档，**不要逐个打开计划**。只有需要动手实施时，才打开对应那一份。

当前实际状态以 [CODE_AGENT_PROGRESS.md](../../CODE_AGENT_PROGRESS.md) 为准，本索引只负责定位。

## 活跃（当前正在实施）

| 文档 | 主题 |
| --- | --- |
| [2026-10-12-code-agent-runtime.md](2026-10-12-code-agent-runtime.md) | C 阶段总纲：API / 授权 / 后台任务 |
| [2026-10-13-code-agent-publication.md](2026-10-13-code-agent-publication.md) | C3：控制器发布、CAS、镜像、交付视图映射 |

## 已完成（作为背景参考，不要重读全篇）

| 文档 | 阶段 / 主题 |
| --- | --- |
| [2026-10-11-code-agent-foundations.md](2026-10-11-code-agent-foundations.md) | A：请求身份 / 预算 / 证据注册 |
| [2026-10-12-code-agent-safe-parsing.md](2026-10-12-code-agent-safe-parsing.md) | B1：共享安全读取、AST 同源哈希 |
| [2026-10-12-code-agent-cache-provenance.md](2026-10-12-code-agent-cache-provenance.md) | B2：缓存来源校验与版本拒绝 |
| [2026-10-12-code-agent-analysis-snapshot.md](2026-10-12-code-agent-analysis-snapshot.md) | B3：快照白名单投影 |
| [2026-10-12-code-agent-readonly-tools.md](2026-10-12-code-agent-readonly-tools.md) | B4：严格扫描与完整 file inventory |
| [2026-10-12-code-agent-source-excerpts.md](2026-10-12-code-agent-source-excerpts.md) | B5：源码摘录与来源策略 |
| [2026-10-12-code-agent-dependency-remediation.md](2026-10-12-code-agent-dependency-remediation.md) | 隔离兼容探针与依赖整改 |
| [2026-10-13-code-agent-sdk-adapter.md](2026-10-13-code-agent-sdk-adapter.md) | C2：SDK 适配器（隔离范围内已清关） |
| [2026-10-13-code-agent-response-contract.md](2026-10-13-code-agent-response-contract.md) | C6：回答合同与引用校验 |
| [2026-10-11-code-agent-source-review.md](2026-10-11-code-agent-source-review.md) | 源码审查补充说明 |
| [2026-10-07-architecture-detail-routing.md](2026-10-07-architecture-detail-routing.md) | 架构细节视图路由（已交付） |
| [2026-10-07-structure-exploration.md](2026-10-07-structure-exploration.md) | 结构展开专项（已交付） |
| [2026-10-08-monaco-source-editing.md](2026-10-08-monaco-source-editing.md) | Monaco 离线源码编辑（已交付） |
| [2026-10-09-project-explorer.md](2026-10-09-project-explorer.md) | 项目浏览器与文件移动（已交付） |

## 已归档

更早的已完成计划在 [docs/archive/plans/](../../archive/plans/)。
