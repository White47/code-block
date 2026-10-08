# Roadmap

## 通用 Python 扩展（本轮范围已验收）

按 `GENERAL_PYTHON_PLAN.md` 执行：通用入口与独立 FunctionFlow → 隐式词法作用域 → 有界对象 / 模块关系 → 控制结构与普通数据依赖 → 固定真实仓库交叉验证。现有 ML 能力保留并回归；不整体替换分析器，不执行被分析项目。支持矩阵记录未完成项，阶段完成须有独立复审与新验证证据。

## Phase 0 — Workspace Audit + Docs

完成规范文档、实施计划和 ADR 基线。用户已确认从空目录新建项目，Phase 1 最小技术栈见 ADR-005；指定连接器的 `workspace_info` 尚未成功，恢复后仍需补做身份核验。

## Phase 1–4 — 事实层

建立 Core Types / SourceAnchor / Stable IDs / Graph IR；实现文件发现、AST、Symbol Index、Resolver、Imports、Definitions / References、Call / Control / Critical Data Flow。

## Phase 5–6 — 语义层

实现 Generic PyTorch 模型识别、自定义模块结构、Training Rules 和 Semantic Component Graph。

## Phase 7–9 — 可视化与导航

已实现 View Builder、Architecture / Execution / Symbol / Data Flow Visible Graph、VSCode 风格 UI、Tabs / Split / Inspector、Search / Reveal in Graph / Source Navigation。本轮交互式源码画布交付进一步实现 Architecture / Execution 的稳定 A+B 主流程 + 最多三个细节岛、对受支持结构 / 控制边的确定性纵向 rank、每视图 zoom / canvas pan、Fit / 100%、岛内 Inspector / Source 上下文和独立 View State 恢复；Symbol / Data Flow 仍是可随选择重新查询 / 居中的有界局部上下文。未引入 draw.io runtime。

## Phase 10–11 — 持久化与硬化

Phase 10 已合入主分支，实现本地 Cache、Outdated、Recent Projects 和布局持久化；View State 现还独立保存每视图 viewport 与有效细节岛，损坏状态不影响有效 Analysis Cache。Health / Problems 沿用 Phase 8 的事实呈现，详见 `PHASE10_REPORT.md`。Phase 11 已合入主分支，加入固定 fixture、三条黄金路径自动验收和小 / 中样本性能基线，见 `PHASE11_REPORT.md`。交互式画布的自动化门槛见 `INTERACTIVE_CANVAS_REPORT.md`；正式发布前仍需在有 Tk 显示的桌面完成该报告列出的人工视觉试用、第一次使用者理解度观察，并核验本轮新提交的 GitHub CI。

后续真实仓库试用已覆盖 nanoGPT、PyTorch Examples、YOLOv5 的四档静态范围，结果与保守语义修复见 `REAL_REPOSITORY_VALIDATION.md`。下一项发布前工作是 Problems 降噪、空流程入口说明与首次使用者的人工理解度观察；自动化路径通过不能代替这些观察。

后置：`.drawio` 导出、file watcher / incremental parse、runtime trace、真实 tensor propagation、完整 autograd、distributed 深层语义、复杂配置和 notebook 深度分析。自由绘图、任意节点编辑、无限嵌套细节岛和完整图编辑器不属于当前交付。
