# Roadmap

## Phase 0 — Workspace Audit + Docs

完成规范文档、实施计划和 ADR 基线。用户已确认从空目录新建项目，Phase 1 最小技术栈见 ADR-005；指定连接器的 `workspace_info` 尚未成功，恢复后仍需补做身份核验。

## Phase 1–4 — 事实层

建立 Core Types / SourceAnchor / Stable IDs / Graph IR；实现文件发现、AST、Symbol Index、Resolver、Imports、Definitions / References、Call / Control / Critical Data Flow。

## Phase 5–6 — 语义层

实现 Generic PyTorch 模型识别、自定义模块结构、Training Rules 和 Semantic Component Graph。

## Phase 7–9 — 可视化与导航

实现 View Builder、Architecture / Execution Visible Graph、VSCode 风格 UI、Tabs / Split / Inspector、Search / Reveal in Graph / Source Navigation。

## Phase 10–11 — 持久化与硬化

实现 Cache、Health、Problems、布局持久化、fixture、验收测试和 MVP hardening。

后置：file watcher / incremental parse、runtime trace、真实 tensor propagation、完整 autograd、distributed 深层语义、复杂配置和 notebook 深度分析。
