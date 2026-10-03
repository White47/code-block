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

## 7. React 原生桌面迁移验收

Task 5 构建与 CI 门槛：前端 lint / typecheck / test / build，Python core lint / mypy / unit，wheel 内含 `codeviz/ui/assets/index.html` 与构建资源；Windows `scripts/build-desktop.ps1` 输出 `dist/CodeViz/CodeViz.exe` 与 `_internal/frontend/dist/index.html`。`-CheckOnly` 在缺少前端依赖或产物时必须失败，不得跳过检查。这些检查不启动 GUI。

Task 6 人工验收须在已安装原生 WebView2 Runtime 的 Windows 上，从 `.\.desktop-venv\Scripts\python.exe -m codeviz.ui.desktop` 和 `.\dist\CodeViz\CodeViz.exe` 分别启动真实原生窗口，断网验证 A / B / C 三条黄金路径、标签 / 分屏 / Inspector / Source、窗口关闭及静态服务停止。确认资源完全离线，无 CDN；随机 loopback 静态服务仅发布 frontend 根，不能读取项目源码或缓存，bridge 不通过 HTTP 提供。单独打开浏览器、生成 exe 或通过 Tk 测试不能算作 React 原生桌面验收通过。

另在隔离环境安装构建的 wheel 与 `[desktop]` 可选依赖、离开仓库目录启动 `codeviz`，验证包内 assets 路径可用。旧 `codeviz-tk` 保留，直到新桌面完整验收；本节仅定义门槛，不声明人工验收已完成。

2026-10-03 技术执行结果：源码壳、最终 Windows 冻结包和仓库外安装的最终 wheel 已通过真实 WebView2 自动操作，包括 A / B / C、方向查询、标签关闭、分屏、语言及窗口关闭生命周期。Python 376 项（49 跳过）、前端 41 项及本地检查 / 构建通过，独立复审批准。详细证据和边界见 `DESKTOP_MIGRATION_VALIDATION.md`。未物理断网、未验证初次用户的理解程度、未推送触发远端 CI；这三项不能从技术测试推定已完成。
