# MVP 验收标准

## 1. 前置条件

测试夹具至少包含：两个可区分的模型（其中一个为自定义 `nn.Module`）、一个 Sequential / ModuleList、训练与评估或推理两个入口、可被多个入口引用的模型、loss / backward / optimizer、if / loop、同名局部变量和一个 unresolved dynamic call。

## 2. 黄金路径 A：架构

Open Project → Analyze → Project Overview → Select Model → Architecture → Drill Down → Internal Flow → Open Source。

验收：多个模型分别可见；可解析的自定义模块实例可以进入其目标定义的内部图并返回原模型；组件可以展开；重复结构可聚合；来自用户源码的可见节点最终打开正确文件与行范围；合成或外部节点显示来源或不可跳转原因；不确定命名有 confidence 与 evidence。

## 3. 黄金路径 B：训练

Overview → Training Flow → Epoch / Batch → Forward → Loss → Backward → Optimizer → Source。

验收：两个入口分别生成流程，共用模型不会被复制成两个定义；顺序、循环回边、关键数据节点和 Backward / Parameter Update 均可读，且关系不会全部显示为同一种箭头。

## 4. 黄金路径 C：搜索

Search Symbol / Variable / Semantic keyword → Results → Reveal in Graph → Local Context → Inspector → Source。

验收：按作用域区分同名符号；结果能在合适视图定位；提供 1-hop 上下文和准确源码锚点。

## 5. 质量门槛

- 不执行用户源码。
- 一个文件失败不影响其他文件分析。
- Overview 显示 parsed / resolved / exact / probable / unresolved 健康度。
- 源码变化后旧结果标记 Outdated，不静默当作最新。
- 默认不污染用户仓库，不保存完整源码副本。
- 单张 Visible Graph 超过阈值时聚合、折叠或提示下钻。

## 6. Phase 11 自动验收证据与人工边界

固定夹具：`tests/fixtures/mvp_project/`。`tests/test_mvp_acceptance.py` 覆盖 A / B / C 的模型、入口、局部图、搜索、Inspector 与真实源码锚点，另检验坏文件隔离、默认忽略目录和“不执行用户模块”；`tests/test_ui_tkapp.py` 的 `test_mvp_fixture_three_paths_are_navigable_in_desktop` 在有 Tk 显示的环境通过真实按钮与标签导航三条路径。缓存与过期状态见 `tests/test_persistence_*.py`。

这些是可重复的技术验收，不等同于第一次接触项目的真实用户已经看懂架构或训练流程。三仓库、四范围的真实项目静态试用及其剩余问题见 `REAL_REPOSITORY_VALIDATION.md`；正式发布前仍需人工可用性观察。无图形显示的 CI 可跳过 Tk 实例测试，不能把跳过说成视觉验收通过。
