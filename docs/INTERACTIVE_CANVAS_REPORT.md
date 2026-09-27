# 交互式源码画布交付报告

## 交付范围与用户可见行为

- Architecture、Execution、Symbol 和 Data Flow 的主图保持为稳定的 A 区纵向流程；单击只选择节点、突出邻居并更新 Inspector。普通且可展开节点的双击、Enter 或 `+` 在空白场景位置打开 B 区细节岛，主图不会被替换，根节点和展开历史不变。聚合节点改为打开隐藏成员侧栏供选择 / 重新定位；不可展开节点不响应展开动作，仍可通过 Inspector 打开源码。
- 主图使用确定性的分层纵向布局：同层分支左右排列，汇合节点靠近前驱重心，回环不重复节点。关系以不同颜色、线型 / 标记和箭头表达，路由优先避开节点。
- 每个视图最多保留三个细节岛。岛与来源节点以虚线相连，可移动、关闭；达到上限时给出本地化提示，不自动关闭旧岛。岛内只显示受限局部图，不能继续嵌套新岛。
- 主图和岛内节点共享统一 Inspector。岛标题的 Open Source 打开来源节点；选择岛内节点后打开该节点源码。关闭当前岛后选择回到主图来源节点。
- `Ctrl+滚轮` 以指针为锚缩放，普通滚轮和 Page Up / Page Down 纵向滚动；拖动空白或中键拖动平移。Fit 适配当前场景，100% 恢复 1:1 缩放并尽量保持中心，滚动条覆盖全部已绘制内容。
- 每个视图分别持久化 viewport（zoom / pan）和细节岛位置。重新打开项目时恢复有效状态；来源 Stable ID 已失效的岛会被清理。View State 与 Analysis Cache、Annotations 分离，畸形 View State 只回退视图状态，不能销毁有效分析。
- Outdated 缓存仍可浏览，Overview、图和源码区域保留警告与原因。单个局部图失败时跳过 / 清理对应岛并提示，主图、其他有效状态和源码访问继续可用。
- 交互借鉴 draw.io 的画布与分层图体验；仓库没有捆绑 draw.io runtime、浏览器 / WebView 或远程布局服务。`.drawio` 导出没有实现，仍是后续独立范围。

## 架构与改动模块

- `src/codeviz/ui/canvas_scene.py`：新增 UI 无关的单图 Canvas Scene 投影、场景 / 屏幕坐标变换、25%–250% viewport、细节岛放置、关系避障路由与绘制样式；每次 `graph_scene()` 只转换一个 `VisibleGraph`。
- `src/codeviz/views/layout.py`：把单列提示替换为可复现的分层纵向布局，保留手动位置优先级。
- `src/codeviz/views/state.py`、`src/codeviz/views/__init__.py`：新增经校验的每视图 viewport 与细节岛状态，保留旧 View State 的兼容默认值。
- `src/codeviz/ui/controller.py`：新增每岛最多 18 节点 / 40 边的独立局部查询、三岛上限，以及 viewport / 岛的保存、移动、关闭和布局重置命令。
- `src/codeviz/ui/graph_canvas.py`：接收主图与细节图 / 状态对，分别建立单图场景后组合岛和来源连线，并实现上下文命中、选择 / 拖动 / 平移隔离、缩放、滚动、Fit、100%、关闭和源码动作。
- `src/codeviz/ui/tkapp.py`、`src/codeviz/ui/i18n.py`：接通标签、Inspector、源码导航、Outdated 状态、恢复 / 陈旧岛清理、工具栏和中英文提示。
- `tests/test_ui_graph.py`、`tests/test_views.py`、`tests/test_persistence_store.py`、`tests/test_persistence_controller.py`、`tests/test_ui_controller.py`、`tests/test_ui_tkapp.py`、`tests/test_mvp_acceptance.py`：覆盖场景数学、布局、输入隔离、A+B 路径、状态边界、失败隔离和三条黄金路径恢复。
- 本轮 Task 9 对齐 `docs/UX_SPEC.md`、`docs/ARCHITECTURE.md`、`docs/CACHE_SPEC.md`、`docs/KNOWN_LIMITATIONS.md`、`docs/ROADMAP.md`；没有扩展 PRD / MVP 范围。

## 自动化验证

本机 PowerShell 没有可用的 `python` 命令别名，`py -3` 也没有注册解释器。因此以下要求的模块命令使用同一台机器上的显式解释器 `<user-home>\AppData\Local\Programs\Python\Python312\python.exe`（Python 3.12.9）运行；参数与工作目录保持不变。

- `python -m ruff check src tests`：通过，`All checks passed!`。
- `python -m mypy`：通过，`Success: no issues found in 47 source files`。
- `python -B -m unittest discover -s tests -v`：通过，运行 289 个测试，0 failures / 0 errors，48 skipped。
- `python -m pip wheel . --no-deps -w dist`：通过，`Successfully built code-module-visualizer`；生成 `code_module_visualizer-0.1.0-py3-none-any.whl`（109849 bytes，SHA-256 `b9e02ac9ff102463621cc9fb9d8f4ebb5af7c4efd2f064984a915128d2ecd6b5`）。首次受限网络运行因隔离构建无法取得 `setuptools>=61` 而退出 1；授权构建依赖下载后，完全相同的 gate 退出 0。`dist/` 由 `.gitignore` 排除，wheel 未纳入提交。
- `git diff --check`：通过，无输出。

48 个跳过均为环境相关：2 个 Windows 符号链接能力用例因当前环境不能创建测试链接而跳过；其余 46 个需要可用 Tk 图形显示的 live-Tk / Canvas 用例因 `Tk display unavailable` 跳过。纯场景、控制器、持久化、验收和 headless Tk 协调测试已运行，不把跳过的 live-Tk 用例计为通过。

以上数字应以本文件所在提交前最后一次完整验证为准；若后续修改代码、测试或文档，需要重新运行全部门槛并更新结果。

## 人工视觉试用

本环境没有可用 Tk 图形显示，因此没有启动桌面窗口，也没有执行或声称完成人工视觉试用。发布人员应在提供 Tk/Tcl 和真实显示器的 Windows / Linux 桌面按以下精确顺序试用：

```text
Open Project → Analyze → Architecture → single-click node → double-click/+ →
move detail island → select island node → Open Source → close island → reopen project
```

试用时同时确认：单击不改变主图；对普通可展开节点，岛在空白处打开且主图保留；聚合节点打开隐藏成员侧栏，不可展开节点不生成岛；拖动后位置保存；岛内 Inspector 与源码行正确；关闭岛不清除分析；重新打开项目后 viewport 和仍有效的岛恢复；Fit、100%、普通滚轮、`Ctrl+滚轮`、Page Up / Page Down、空白拖动 / 中键平移均按 UX 规范工作；第四个岛被本地化提示拒绝；Outdated 警告不会被岛操作遮蔽。

## 剩余限制

- 自动路由是有界启发式；候选通道都受阻时会退回直接连线，复杂大图仍可能产生交叉。
- 细节岛是单层、受限局部视图，不支持岛内继续展开、自由绘图、任意节点编辑或协作。
- `.drawio` 导出、关系通用过滤器、增量索引 / file watcher 和运行时追踪尚未实现。
- 本轮自动化不能替代真实显示器上的高 DPI、长中文名称、多岛避让、长训练流和第一次使用者理解度观察。
- Windows 文件符号链接与 Tk 显示相关路径仍需在具备相应系统能力的环境补验。

## 规范一致性自审

- `PRD.md`：仍遵守静态分析、不执行用户代码、Visible Graph 有界、抽象可追溯到 SourceAnchor；画布只是现有分析结果的 UI 投影。
- `MVP_ACCEPTANCE.md`：Architecture、Training Flow、Search 三条黄金路径仍由自动化覆盖；本报告明确区分技术验收与未执行的人工视觉 / 理解度观察。
- `SECURITY_AND_PRIVACY.md`：没有上传源码、执行目标项目、保存完整源码或放宽源码路径校验；View State 仅保存视图坐标和 Stable ID，并继续位于应用数据目录。
