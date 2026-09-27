# 交互式源码画布交付报告

## 交付范围与用户可见行为

- Architecture / Execution 的主图保持为稳定的 A 区纵向流程；单击只选择节点、突出邻居并更新 Inspector。普通且可展开节点的双击、Enter 或 `+` 在空白场景位置打开 B 区细节岛，主图不会被替换，根节点和展开历史不变。聚合节点改为打开隐藏成员侧栏供选择 / 重新定位；不可展开节点不响应展开动作，仍可通过 Inspector 打开源码。Symbol / Data Flow 是有界局部上下文，单击可聚焦主节点时可以重新查询并重新居中，不承诺保持原主图几何。
- Contains、Control Flow、Branch True / False 使用确定性的纵向 rank：同层分支左右排列，汇合节点靠近前驱重心，回环不重复节点。其他关系不参与 rank；Symbol / Data Flow 可能使用确定性的同 rank / Stable ID 回退位置。关系以不同颜色、线型 / 标记和箭头表达，路由优先避开节点。
- 每个视图最多保留三个细节岛。岛与来源节点以虚线相连，可移动、关闭；达到上限时给出本地化提示，不自动关闭旧岛。岛内只显示受限局部图，不能继续嵌套新岛。
- 主图和岛内节点共享统一 Inspector。岛标题的 Open Source 打开来源节点；选择岛内节点后打开该节点源码。关闭当前岛后选择回到主图来源节点。
- `Ctrl+滚轮` 以指针为锚缩放，普通滚轮和 Page Up / Page Down 纵向滚动；拖动空白或中键拖动执行 canvas pan。Fit 适配当前场景，100% 恢复 1:1 缩放并尽量保持中心，滚动条覆盖全部已绘制内容。
- 主节点拖动采用 5 屏幕像素阈值；阈值以下释放只完成选择，不移动或持久化节点 / 岛，跨过阈值时应用从按下点累计的完整位移。空白平移保持即时响应。
- Canvas 可获得键盘焦点；方向键按主图场景顺序、再按岛位置与岛内场景顺序确定性遍历节点，虚线外框提供非纯颜色焦点提示。Enter 展开可展开主节点，O 打开当前聚焦节点源码，Escape 关闭活动岛；Tab / Shift-Tab 未被 Canvas 捕获。
- 每个视图分别持久化主节点手动位置、由空白 / 中键拖动产生的 canvas pan、zoom、Fit / 100% 结果和细节岛位置。同一 Stable ID 在 Symbol 与 Data Flow 的主节点位置互不覆盖；旧版扁平 positions 缓存可容忍读取，细节岛坐标仍使用独立字段。Tk 原生滚动条、普通滚轮与 Page Up / Page Down 的滚动偏移只在当前会话有效，不写入 View State。重新打开项目时恢复有效状态；来源 Stable ID 已失效的岛会被清理。View State 与 Analysis Cache、Annotations 分离，畸形 View State 只回退视图状态，不能销毁有效分析。
- Outdated 缓存仍可浏览，Overview、图和源码区域保留警告与原因。单个局部图失败时跳过 / 清理对应岛并提示，主图、其他有效状态和源码访问继续可用。
- 分层布局内部异常时返回按 Stable ID 排序的确定性纵向位置，保留全部可见节点和源码链路；同时产生结构化 `LAYOUT_FALLBACK` ViewIssue。Controller 在项目会话内最多保留 20 条并去重，状态栏和 Problems / Analysis Issues 可见；该诊断不写入 `ProjectAnalysis` 或 Analysis Cache。
- 交互借鉴 draw.io 的画布与分层图体验；仓库没有捆绑 draw.io runtime、浏览器 / WebView 或远程布局服务。`.drawio` 导出没有实现，仍是后续独立范围。

## 架构与改动模块

- `src/codeviz/ui/canvas_scene.py`：新增 UI 无关的单图 Canvas Scene 投影、场景 / 屏幕坐标变换、25%–250% viewport、细节岛放置、关系避障路由与绘制样式；每次 `graph_scene()` 只转换一个 `VisibleGraph`。
- `src/codeviz/views/layout.py`、`src/codeviz/core/model.py`：为受支持的结构 / 控制边提供可复现的纵向 rank，并为其他关系保留确定性的同 rank 回退；手动位置优先。分层布局异常只在布局边界内转换为纵向回退和结构化 ViewIssue，没有诊断接收方时异常继续抛出。
- `src/codeviz/views/state.py`、`src/codeviz/views/__init__.py`：新增经校验的每视图 viewport、每视图主节点位置与细节岛状态，保留旧 View State 的兼容默认值。
- `src/codeviz/ui/controller.py`、`src/codeviz/ui/presenter.py`：新增每岛最多 18 节点 / 40 边的独立局部查询、三岛上限、viewport / 岛命令，以及会话级有界布局诊断到 Problems 的展示桥接。
- `src/codeviz/ui/graph_canvas.py`：接收主图与细节图 / 状态对，分别建立单图场景后组合岛和来源连线，并实现上下文命中、5 像素拖动阈值、键盘节点焦点 / 动作、选择 / 拖动 / 平移隔离、缩放、滚动、Fit、100%、关闭和源码动作。
- `src/codeviz/ui/tkapp.py`、`src/codeviz/ui/i18n.py`：接通标签、Inspector、源码导航、Outdated 状态、恢复 / 陈旧岛清理、工具栏和中英文提示。
- `tests/test_ui_graph.py`、`tests/test_views.py`、`tests/test_persistence_store.py`、`tests/test_persistence_controller.py`、`tests/test_ui_controller.py`、`tests/test_ui_tkapp.py`、`tests/test_mvp_acceptance.py`：覆盖场景数学、布局、输入隔离、A+B 路径、状态边界、失败隔离和三条黄金路径恢复。
- 最终复审同时对齐 `docs/UX_SPEC.md`、`docs/ARCHITECTURE.md` 与 `docs/DATA_MODEL.md`；没有扩展 PRD / MVP 范围。

## 验证元数据

- 日期：2026-09-27。
- 平台：Microsoft Windows `10.0.26200.7171`，PowerShell；当前会话可用 Tk 图形显示，live-Tk / Canvas 测试已实际运行。
- Python：CPython 3.12.9，显式解释器 `<workspace>\.venv\Scripts\python.exe`；所有 Python gate 显式设置 `PYTHONPATH=<user-home>\Documents\Codex\code-block-final-review\src`，确保导入本地 C: clone，未修改原 E: worktree。
- 最终复审基线为 `df157906e8623152cc435e3d76098c1ff63068a6`（`Ground canvas release docs in verified behavior`）；交互修复提交为 `41bce220117249d4325a30e201d8f4dceaba9ea9`（`Address interactive canvas review findings`）；布局诊断提交为 `38b98ad`（`Surface layout fallback diagnostics`）；最终代码 / 测试提交为 `54e610c7f789160612c1d66fa0b7806870ce8ac8`（`Fix live Tk restore assertions`）。最终验证范围为 `df157906..54e610c`；本报告在其后的独立文档提交中记录复验结果。
- 本地门槛状态如下；GitHub Actions 状态未查询，因此不声称远程 CI 通过或失败。

## 自动化验证

本机 PowerShell 没有可用的 `python` 命令别名，`py -3` 也没有注册解释器。因此以下要求的模块命令使用上述显式解释器运行；命令参数与仓库根工作目录保持不变。

- `python -m ruff check src tests`：通过，`All checks passed!`。
- `python -m mypy`：通过，`Success: no issues found in 47 source files`。
- 聚焦测试 `python -B -m unittest -v tests.test_views tests.test_persistence_store tests.test_persistence_controller tests.test_ui_controller tests.test_ui_graph tests.test_ui_tkapp`：通过，运行 157 个测试，0 failures / 0 errors，1 skipped。
- `python -B -m unittest discover -s tests -v`：通过，运行 302 个测试，0 failures / 0 errors，2 skipped。
- `python -m pip wheel . --no-deps -w dist`：本地隔离构建通过，`Successfully built code-module-visualizer`，产物 `code_module_visualizer-0.1.0-py3-none-any.whl` 为 111829 bytes。构建使用临时 `PIP_CACHE_DIR` 避免全局 pip cache 权限问题，并取得声明的 `setuptools>=61` 构建依赖。`dist/` 由 `.gitignore` 排除；wheel 只作为 gate 产物，不纳入任何提交，并在最终验证后删除。
- `git diff --check`：通过，无输出。

2 个跳过均为 Windows 符号链接能力用例：当前环境不能创建测试链接。此前无图形显示时会跳过的 47 个 live-Tk / Canvas 用例在本次最终复验中全部实际运行并通过，包括拖动阈值烟测。无显示器测试同时覆盖每视图位置隔离、键盘遍历以及强制布局失败的几何 / 去重 / Problems 可见性。

为使下述人工步骤基于真实 fixture，本轮还通过公开 `AppController` / `view()` / `detail_view()` 路径检查 `tests/fixtures/mvp_project/`：All Models Architecture 中 `Encoder`（`model.Encoder`）可见，`metadata["has_children"] == True` 且不是聚合节点；其 detail graph 包含带源码锚点的 `model.Encoder.layers`。同一检查确认 `model.Classifier.encoder` 不可展开，因此手工步骤不使用它。

以上数字应以本文件所在提交前最后一次完整验证为准；若后续修改代码、测试或文档，需要重新运行全部门槛并更新结果。

## 人工视觉试用

本环境没有可用 Tk 图形显示，因此没有启动桌面窗口，也没有执行或声称完成人工视觉试用。以下场景供具备 Tk/Tcl 与真实显示器的 Windows / Linux 桌面复现。先在仓库根目录按 `README.md` 启动：`.\.venv\Scripts\codeviz.exe`，在 Settings 中切换 English 以使用下述标签，并以 `tests/fixtures/mvp_project/` 为项目目录。

### 场景 1：基线 A+B 与源码

原验收要求的精确基线序列保留如下。这里的 `Open Project` / `Analyze` 是需求原文；实际 UI 标签分别是 `Open Folder` / `Analyze Project`。该序列会在 reopen 前关闭岛，因此只验证基本交互，不验证岛恢复：

```text
Open Project → Analyze → Architecture → single-click node → double-click/+ →
move detail island → select island node → Open Source → close island → reopen project
```

1. 点击 `Open Folder`，选择 `tests/fixtures/mvp_project/`，再点击 `Analyze Project`。
2. 在 Project Overview 点击 `Open Architecture`（或点击左侧 Architecture activity），确认 breadcrumb 为 `Architecture › All Models`。
3. 在主图找到显示名 `Encoder`、代码名 `model.Encoder` 且带 `+` 的节点。单击它，确认 Inspector 更新且 All Models 主图不变。
4. 双击 `Encoder` 或点击其 `+`，确认细节岛出现；拖动岛，在岛内选择代码名 `model.Encoder.layers` 的源码节点，再点 `Open Source`，确认打开 `model.py` 中 `Encoder.__init__` 的 `self.layers = nn.ModuleList(...)` 行。
5. 关闭岛，再次点击 `Open Folder` 选择同一 fixture（或从 Recent Projects 打开）；确认分析仍可用。此场景不应声称恢复已关闭的岛。

### 场景 2：viewport 与岛恢复

1. 通过 `Open Architecture` 返回 `Architecture › All Models`，再次对带 `+` 的 `Encoder`（`model.Encoder`）打开细节岛并移动它；保持该岛打开。
2. 依次执行空白拖动或中键拖动、`Ctrl+滚轮`、Fit、100%，最后再设置一个明显非默认 zoom / canvas pan；记下缩放百分比和岛位置。
3. 用普通滚轮或 Page Down 产生额外原生滚动偏移；不要关闭岛，直接重新打开同一项目（或退出后按 README 命令重启并从 Recent Projects 打开）。
4. 确认有效 `Encoder` 岛、岛位置、最终 zoom 和 canvas pan 恢复；普通滚轮 / Page Down 的临时 Tk 滚动偏移不要求恢复。

### 场景 3：Outdated 与岛保留

1. 不修改仓库 fixture；在 PowerShell 创建临时副本：`$trial = Join-Path $env:TEMP ("codeviz-mvp-" + [guid]::NewGuid()); Copy-Item tests/fixtures/mvp_project $trial -Recurse`。
2. 点击 `Open Folder` 选择 `$trial`，点击 `Analyze Project`，再点 `Open Architecture` 进入 `Architecture › All Models`；对带 `+` 的 `Encoder`（`model.Encoder`）打开细节岛，并保持岛打开。
3. 在应用外执行 `Add-Content (Join-Path $trial 'run.py') '# outdated trial'`，然后再次点击 `Open Folder` 选择 `$trial`。
4. 确认状态为 Outdated、Overview / 图 / Source 显示警告，旧主图仍可浏览且有效 `Encoder` 岛恢复。试用结束后关闭应用，再删除该唯一临时目录 `$trial`。

## 剩余限制

- 自动路由是有界启发式；候选通道都受阻时会退回直接连线，复杂大图仍可能产生交叉。
- 细节岛是单层、受限局部视图，不支持岛内继续展开、自由绘图、任意节点编辑或协作。
- `.drawio` 导出、关系通用过滤器、增量索引 / file watcher 和运行时追踪尚未实现。
- 本轮自动化不能替代真实显示器上的高 DPI、长中文名称、多岛避让、长训练流、三岛 / 第四岛拒绝、聚合成员侧栏和第一次使用者理解度观察。
- Windows 文件符号链接与 Tk 显示相关路径仍需在具备相应系统能力的环境补验。

## 规范一致性自审

- `PRD.md`：仍遵守静态分析、不执行用户代码、Visible Graph 有界、抽象可追溯到 SourceAnchor；画布只是现有分析结果的 UI 投影。
- `MVP_ACCEPTANCE.md`：Architecture、Training Flow、Search 三条黄金路径仍由自动化覆盖；本报告明确区分技术验收与未执行的人工视觉 / 理解度观察。
- `SECURITY_AND_PRIVACY.md`：没有上传源码、执行目标项目、保存完整源码或放宽源码路径校验；View State 仅保存视图坐标和 Stable ID，并继续位于应用数据目录。
