# 通用 Python 程序理解：调研与候选路线

日期：2026-10-03。基线提交：9c09fd9。本文保存当时的调研 / 候选方案，历史“待确认”不是当前执行状态。用户随后已授权实施，正式范围与进度以 `../GENERAL_PYTHON_PLAN.md`、`../GENERAL_PYTHON_VALIDATION.md` 为准；不新增解析依赖，不自动提交 / 推送。

## 结论

推荐在现有系统上建设通用 Python 基座，PyTorch 等框架保留为可选语义增强。不是把普通业务代码硬套进训练模型，也不是直接替换成某个调用图工具。需要分别解决语法读取、名称与调用解析、控制 / 数据关系和 UI 可达性；单纯换 AST parser 不会使普通程序自动出现可读流程。

## 已核实的开源参考

下面的最近推送日期来自查询当时的 GitHub 仓库 API。未归档和近期 push 是维护线索，不保证每次推送均为功能维护，也不是兼容性或正确性承诺。GitHub API 的 updated_at 可能仅来自星标等变化，不用它判断持续开发。

| 项目 | 状态 / 最近 push | 借鉴内容 | 接入判断 |
| --- | --- | --- | --- |
| [Pyan3](https://github.com/Technologicat/pyan) | 未归档；2026-09-21 | 普通 Python 定义 / 使用关系、分组、递归和有界上下游图 | 最接近图工具；GPL-2.0，优先借鉴概念并自建测试，不复制实现到核心 |
| [LibCST](https://github.com/Instagram/LibCST) | 未归档；2026-08-11 | 准确位置、函数 / 类 / 推导式作用域、限定名元数据 | 可做语法 / 作用域校验参照；不是完整调用 / 执行图引擎，native wheel 增加分发验证成本 |
| [Jedi](https://github.com/davidhalter/jedi) | 未归档；2026-10-01 | 定义跳转、引用、导入、类型及候选推断 | 适合独立候选解析器试验，不立即变成默认依赖 |
| [Pyright](https://github.com/microsoft/pyright) | 未归档；2026-10-02 | 分支、赋值、汇合、循环和不可达路径的分析思想 | 参考控制流与类型收窄，暂不将 TS 内部分析器搬进 Python 核心 |
| [Astroid](https://github.com/pylint-dev/astroid) | 未归档；2026-09-30 | 扩展 AST、词法作用域、保守推断与推断上限 | 借鉴建模；LGPL-2.1、活对象 / 扩展导入路径需独立安全与分发评估 |
| [code2flow](https://github.com/scottrogowski/code2flow) | 未归档；2025-07-27 | 基于目标函数的局部图、上下游深度、目录 / 类聚合 | MIT，可参考；其 README 明确承认同名方法、回调、lambda 等局限，不直接替换 Resolver |
| [PyCG](https://github.com/vitsalis/PyCG) | 已归档；2023-11-26 | 高阶函数、赋值传播和迭代收敛的方法 | 研究参考与对比基准，不作为长期默认依赖 |
| [tree-sitter-python](https://github.com/tree-sitter/tree-sitter-python) | 未归档；2026-09-13 | Python grammar；配合 Tree-sitter 的容错语法树思路 | 后续坏文件局部恢复候选；恢复树不能自动升级成确定语义事实 |

阅读了官方 README / 文档及 LibCST scope_provider.py、Jedi settings.py / inference/imports.py、Astroid manager.py、Pyright codeFlowTypes.ts 的相关代码。未运行这些工具的对比基准，不能声称已证明其结果优于当前引擎。第三方代码未经移植，也没有安装进项目。

相关官方依据：

- [LibCST Scope / Position / QualifiedName metadata](https://github.com/Instagram/LibCST/blob/main/docs/source/metadata.rst)：推导式有独立作用域；限定名与位置可作为独立事实提供。
- [Jedi 功能与安全边界](https://jedi.readthedocs.io/en/latest/docs/features.html)：Script 的纯 Python 静态分析与 Interpreter 的活对象执行不同；不能启用不可信扩展执行。
- [Jedi settings](https://github.com/davidhalter/jedi/blob/master/jedi/settings.py) 与 [imports](https://github.com/davidhalter/jedi/blob/master/jedi/inference/imports.py)：存在 auto_import_modules 路径；候选适配器还必须审计导入路径 / 环境、关闭自动导入、禁用 Interpreter，不能只设置一个开关就宣称安全。
- [Pyright 类型收窄](https://github.com/microsoft/pyright/blob/main/docs/type-concepts-advanced.md) 与 [控制流节点](https://github.com/microsoft/pyright/blob/main/packages/pyright-internal/src/analyzer/codeFlowTypes.ts)：分支合并保留可能类型，不把条件后的单一目标当作必然结果。
- [code2flow 限制](https://github.com/scottrogowski/code2flow#known-limitations)：其全局唯一名称回退不能作为本项目 Exact 调用依据。
- [Python ast](https://docs.python.org/3.12/library/ast.html)：语法支持受分析器运行时影响，未来语法不能由 feature_version 开关任意补齐。

## 当前仓库的直接证据

- `analysis/python.py` 已通过标准库 ast.parse 读取普通 Python；不是仅能解析 PyTorch。遇到单文件语法错误会保留 issue，但丢弃该文件的 AST。
- `analysis/project.py` 已有通用扫描、SymbolIndex、Resolver 和 Flow，再构建模型 / 训练 / Semantic；这些通用事实可复用。
- `ui/presenter.py::build_overview` 只从 model.definitions 和 training.entry_points 生成可选模型 / 入口。普通库和 main 脚本即使已索引，也难从概览进入。
- `views/builder.py` 的 Architecture 以 ModelDefinition 为根，Execution 依赖语义 EntryPoint；通用 Symbol / Data Flow 有独立 Code Graph 上下文，但不等于任意函数的执行流程。
- `analysis/control_flow.py` 已有顺序、if、循环等基础结构；try / finally 显式简化。不能把“AST 可读”说成异常、生成器、异步调度及 match 的运行语义均已完整恢复。

只读内存探针（不执行示例源码）直接验证：`def main(): print('hello')` 加 main guard，AST 成功、3 个索引符号、11 个 Code Graph 节点，但 Overview 入口和 Execution 可见节点都是 0。可见性缺口不是 parser 没有读到文件。

独立 explorer 另验证了以下语言 / 事实缺口，核心均未改动：

| 缺口 | 当前证据位置 | 候选修复边界 |
| --- | --- | --- |
| 普通入口遗漏、export_csv 被名字规则当作 Export | `training.py:81`、`presenter.py:75`、`builder.py:79` | 拆通用入口与框架角色；任意函数可作为人为流程根 |
| 推导式变量误落到外围作用域，lambda / match 捕获缺绑定 | `symbols.py:183,291`、`resolution.py:144` | 独立作用域 / 绑定事实；定义归属和运行时绑定状态不能混为一谈 |
| 构造实例的方法、继承 / super 解析不足，IR 未充分产出导入 / 继承关系 | `resolution.py:188,218`、`flow.py:178`、`graph_build.py:16` | 有界候选实例类型、继承 / 导入关系；保留动态分派不确定性 |
| match case 与 finally 提前退出路径不完整 | `control_flow.py:127,147` | 修正 CFG，并为简化 / 未支持路径显式报告，不靠词法顺序代替执行 |
| path / rows / result 等普通变量不进入 Data Context | `data_flow.py:21,188,218`、`code_context.py:52` | 独立通用 def-use 索引；ML 数据角色只影响默认显示 |

例如 `j = Job(); j.run()` 与 `super().run()` 当前仍可能 Unresolved；async 能读出调用，但没有任务 / 挂起恢复语义。这里的目标不是一律把它们升为 Exact。引用解析到未来赋值的定义也不单独证明解析错误：名称归属可以确定，而引用发生时是否已绑定、调用是否可达，需要另外的顺序 / 值传播证据。

## 三种候选方案

1. **推荐：通用基座渐进补强。** 复用当前 AST / Resolver / Graph IR / SourceAnchor / React Flow；先使普通项目可浏览，再补强语言事实；无必要的新运行时依赖。风险是需要认真维护支持矩阵和测试，不能靠扩大遍历范围宣称推断准确。
2. **混合方案：引入可选 Jedi / LibCST 适配器。** 原始结果先转换为候选事实，做作用域、来源、安全和预算校验后再入图。可能减少自研推断工作，但增加版本 / 许可 / 打包 / 环境差异，需要固定样本实测才能选定。
3. **整体切换到第三方分析器。** 不推荐当前采用：没有一个候选同时满足可解释图、源码回溯、独立缓存、现有 ML 语义和原生桌面接口；贸然替换会丢失已有边界。

## 候选分阶段顺序

### G0 — 普通 Python 基线与支持矩阵

区分四个维度：语法可读、定义 / 引用、控制 / 数据事实、界面可达。建立自己编写的普通脚本、CLI、库、面向对象、async、数据处理样例；不复制第三方受限许可的测试源码。记录每类已知缺口和错误 Exact，而不是只统计节点增加。

首批明确以当前 3.12 桌面运行时可解析的语法为边界；3.11 CI 与 3.12 专用语法分别验证，不能误把分析器版本过旧归咎于用户代码损坏。未来语法通过运行时升级或独立 adapter 评估处理。

### G1 — 让普通程序先看得见

概览增加模块 / 文件 / 类 / 函数浏览和入口候选；库即使没有入口，也可选任意具名函数 / 方法进入函数内流程。导入依赖仅展示已存在且有依据的事实，补齐 IMPORTS / INHERITS / EXPORTS 属于后续任务。main guard 与 __main__.py 提供结构依据；仅名为 main 的函数是候选，不宣称其必然执行。人为选择的流程根也不伪装为自动检测入口。

将通用执行事实与训练聚合分开。没有 PyTorch 的项目不得只有空模型区和空训练区；有 PyTorch 的项目仍保留原有模型与训练视图。普通变量默认隐藏；通用参数 / 返回值 / 局部 def-use 上下文是 G3 目标，不在首批承诺完整实现，也不依赖 loss / prediction 名称。

可能影响 `analysis/entries.py`（新）、`core/model.py`、`views/builder.py`、`views/code_context.py`、通用视图新模块、`ui/presenter.py`、`web_contract.py`、`web_bridge.py`、前端 Overview / Search / Inspector 及持久化白名单。最终名称 / DTO 由获批设计统一，不在此调研稿提前锁定。

### G2 — 名称、对象和调用更可靠

优先补充推导式 / lambda / 闭包作用域、global / nonlocal、import alias / 相对导入 / 重导出 / namespace package。再补构造实例、self / cls、继承 / super、静态 / 类方法、property、函数别名与有限回调传播；区分定义、实例和调用。

重绑定与条件汇合保留候选集合和判断依据；动态 getattr、运行时注册、monkey patch 保留 Unresolved。类型注解是提示而不是运行时证明。绝不按同名或全局唯一名称直接连 Exact CALLS。

### G3 — 控制流与通用数据关系

按独立任务补 match、try / except / else / finally、提前退出、loop else、with / async with、await / yield 等结构。无法建模的异常或调度明确标记 Partial / issue，不画出虚假的必经顺序。定义执行、调用执行、生成器迭代和异步调度不能混成一张词法顺序图。

普通代码的参数 / 赋值 / 返回值依赖采用有界静态 def-use，再按需研究跨函数传播；不把语法依赖当运行时值等价，不声称推断真实 tensor shape / 完整并发时序。

### G4 — 真实仓库与依赖选型试验

选择 Click 类 CLI、Requests 类库、Rich 类面向对象库、Flask 类 Web 应用、async 样例及原 ML 夹具交叉验证；具体下载必须固定提交 / 分析范围并记录许可。只扫描，不安装或执行目标仓库，也不运行其 setup.py。对抽样调用 / 来源人工标注，检测假 Exact、错误作用域、假入口及错误源码锚点。

只有当可选解析器在同一自建样本的精度、覆盖、耗时、内存和安全 / Windows 打包上确有收益，才决定是否新增依赖。坏文件恢复和外部 .pyi 仅作为独立候选任务，不自动扫描全部第三方包或环境路径。

## 架构复核后的首批实施边界（待确认）

首批仅锁定两个目标，避免把 G1–G3 一次性扩成引擎重写：

1. **通用入口与函数流程。** 概览可浏览普通模块 / 函数；有入口时展示结构证据，没有入口的库可人工选择任意具名函数 / 方法为流程根。通用 Execution 投影现有函数 CFG，保留训练专用聚合路径。收紧仅凭 export 等名称推断训练角色的规则，使 export_csv 不再无依据地被标作训练 Export。
2. **词法绑定正确性。** 补 lambda 参数、推导式局部绑定和 match 捕获。推导式的第一个 iterable 在外围作用域求值，其余绑定按推导式作用域处理；match case 不创建新的 Python 词法作用域。引用的 Exact 仅说明定义归属，不代表该路径已赋值、调用目标唯一或实际执行。

对象类型传播 / super、导入与继承边构建、match CFG、finally 正确异常路径、通用 def-use 和跨函数传播分开后续实施。首批即须对已知简化的 finally 等控制流提供 issue / Partial 提示，不能因为允许打开普通函数，就将近似流程展示为完整确定结果。

首批验收使用自建普通 main、无入口的函数库、export_csv、lambda、推导式、match 绑定夹具，并回归原有 ML 视图与缓存。检查概览可达、函数流程、正确源码锚点、作用域消歧、已知限制可见；不是只检查 AST 节点数量。

## 不变边界与验收

- 不执行目标项目、不改目标源码、不上传源码；第三方默认 External。
- SourceAnchor / Stable ID / Definition–Instance–Invocation / confidence 继续是统一契约；改模型时显式升级缓存版本，保留独立 View State / Annotation，失效引用安全清理。
- 任何解析 adapter 必须落入同一 IR，不能向 UI 暴露解析器私有对象；Full Graph 不全部渲染。
- 新黄金路径：普通项目 → 概览 → 模块 / 函数 → 局部调用 / 控制流程 → Inspector → 正确源码；没有模型和训练入口也成立。
- 原 ML 三条黄金路径必须回归。每个阶段运行 Ruff、Windows / Linux mypy、unittest、前端 lint / typecheck / test / build，以及受影响的 wheel / Windows 打包与真实窗口验证。
- 支持度按已声明的语言特性和人工标注样本报告；不提前承诺“所有 Python”或未经测量的覆盖百分比。

确认推荐方向后，再写获批设计及可执行任务计划，由 architect 审核架构、core_implementer 改核心、test_writer 补回归、reviewer 独立复审。
