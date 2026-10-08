# 代码模块可视化 MVP — PRD

## 0. 基线与审计结论

2026-09-17 初次审计 `E:\Codex\代码块` 时目录为空且不是 Git 仓库。用户随后确认从空目录新建项目；Phase 1 采用 Python 标准库建立领域核心，见 ADR-005。本文锁定产品边界；桌面壳、渲染库、状态库和缓存格式仍待相应阶段依据实际代码最小决策。

## 1. 产品目标

构建 Python-first 的代码模块可视化 / 程序理解应用，首版重点支持 Python + PyTorch。应用把源码还原成适合人理解的项目架构图、模型组件图、执行流程图和源码关系图，并保证每个抽象都能追溯到文件、符号和代码行。

用户已批准通用 Python 扩展：普通脚本、工具函数、库、面向对象和异步代码同样属于核心用户场景。PyTorch 是通用事实层之上的框架增强，不是普通项目可浏览的前置条件。实施进度与支持边界分别见 `GENERAL_PYTHON_PLAN.md`、`PYTHON_SUPPORT_MATRIX.md`。

目标路径：`Source Code → Parse → Resolve → Code Graph → Semantic Component Graph → Visible Graph → Search / Drill Down / Inspector → Source`。

## 2. 用户与核心任务

- 第一次看到项目的人：从 Overview 找到模型、主要区域和入口。
- Python / PyTorch 开发者：理解自定义模块、forward 顺序、训练循环和关键数据。
- 维护者：搜索函数、变量或语义关键词，定位图中上下文并跳回源码。

## 3. MVP 范围

- 显式一次性 Analyze；默认静态分析，不执行用户代码。
- 从本地文件夹打开项目并提供 Recent Projects；首版不要求 Git URL、ZIP 或远程仓库导入。
- Python 文件发现、AST、符号索引、基础 import / call / control / data 关系。
- Generic Python、Generic PyTorch、Transformer、Training 规则插件。
- 四级视图：Project / Model Overview、Major Components、Internal Flow、Source。
- Architecture、Execution / Training Flow、Calls / Data Flow 的 Visible Graph。
- Search、Reveal in Graph、Inspector、Source navigation、Open to Side / Split。
- Analysis Health、Problems、Exact / Probable / Unresolved、缓存与 Outdated 状态。
- 支持多个 EntryPoint 和多个模型；每个入口有独立 Execution Flow，模型可被多个入口复用。

## 4. 非目标

首版不执行项目代码，不承诺真实 runtime tensor shape / dtype / device，不实现完整 autograd graph、DDP/FSDP/DeepSpeed、复杂 hooks、动态 graph surgery、通用无限层级系统或源码重构。2026-10-08 用户批准在现有 PC 应用加入 Monaco 源码编辑 MVP，替代原“无代码编辑器”边界：仅显式安全保存、未保存保护、保存后分析过期，不包含终端、调试器或语言服务器；具体合同见 [SOURCE_EDITING](SOURCE_EDITING.md)。

## 5. 成功标准

MVP 不以 AST 节点数量为成功标准，而以第一次看到项目的人能否完成三条黄金路径为标准：

1. Open Project → Analyze → Overview → Model → Architecture → Drill Down → Source。
2. Overview → Training Flow → Epoch / Batch → Forward → Loss → Backward → Optimizer → Source。
3. Search symbol / variable / semantic keyword → Results → Reveal in Graph → Local Context → Inspector → Source。
4. 普通项目 → Overview → 模块 / 具名函数或方法 → FunctionFlow → Inspector → Source；无训练入口、无模型的库也必须成立。静态函数控制结构不是实际执行轨迹。

## 6. 产品原则

- 默认给人看，不是给编译器看。
- Progressive Disclosure：先整体，再组件，再内部流程，再源码。
- 抽象必须 Source Traceable；不知道时保留真实名称，不伪造确定性。
- Code Graph、Semantic Graph、Visible Graph 分离。
- 模型架构、执行流程、Data Flow、Gradient Flow 分离。
- Full Graph 永不直接全部渲染；Visible Graph 默认几十到约 100 个节点。
