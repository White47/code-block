# 交互式源码画布交付报告

## 交付范围与用户可见行为

- Architecture / Execution 的主图保持为稳定的 A 区纵向流程；单击只选择节点、突出邻居并更新 Inspector。普通且可展开节点的双击、Enter 或 `+` 在空白场景位置打开 B 区细节岛，主图不会被替换，根节点和展开历史不变。聚合节点改为打开隐藏成员侧栏供选择 / 重新定位；不可展开节点不响应展开动作，仍可通过 Inspector 打开源码。Symbol / Data Flow 是有界局部上下文，单击可聚焦主节点时可以重新查询并重新居中，不承诺保持原主图几何。
- Contains、Control Flow、Branch True / False 使用确定性的纵向 rank：同层分支左右排列，汇合节点靠近前驱重心，回环不重复节点。其他关系不参与 rank；Symbol / Data Flow 可能使用确定性的同 rank / Stable ID 回退位置。关系以不同颜色、线型 / 标记和箭头表达，路由优先避开节点。
- 每个视图最多保留三个细节岛。岛与来源节点以虚线相连，可移动、关闭；达到上限时给出本地化提示，不自动关闭旧岛。岛内只显示受限局部图，不能继续嵌套新岛。
- 主图和岛内节点共享统一 Inspector。岛标题的 Open Source 打开来源节点；选择岛内节点后打开该节点源码。关闭当前岛后选择回到主图来源节点。
- `Ctrl+滚轮` 以指针为锚缩放，普通滚轮和 Page Up / Page Down 纵向滚动；拖动空白或中键拖动执行 canvas pan。Fit 适配当前场景，100% 恢复 1:1 缩放并尽量保持中心，滚动条覆盖全部已绘制内容。
- 每个视图分别持久化由空白 / 中键拖动产生的 canvas pan、zoom、Fit / 100% 结果和细节岛位置。Tk 原生滚动条、普通滚轮与 Page Up / Page Down 的滚动偏移只在当前会话有效，不写入 View State。重新打开项目时恢复有效状态；来源 Stable ID 已失效的岛会被清理。View State 与 Analysis Cache、Annotations 分离，畸形 View State 只回退视图状态，不能销毁有效分析。
- Outdated 缓存仍可浏览，Overview、图和源码区域保留警告与原因。单个局部图失败时跳过 / 清理对应岛并提示，主图、其他有效状态和源码访问继续可用。
- 交互借鉴 draw.io 的画布与分层图体验；仓库没有捆绑 draw.io runtime、浏览器 / WebView 或远程布局服务。`.drawio` 导出没有实现，仍是后续独立范围。

## 架构与改动模块

- `src/codeviz/ui/canvas_scene.py`：新增 UI 无关的单图 Canvas Scene 投影、场景 / 屏幕坐标变换、25%–250% viewport、细节岛放置、关系避障路由与绘制样式；每次 `graph_scene()` 只转换一个 `VisibleGraph`。
- `src/codeviz/views/layout.py`：为受支持的结构 / 控制边提供可复现的纵向 rank，并为其他关系保留确定性的同 rank 回退；手动位置优先。
- `src/codeviz/views/state.py`、`src/codeviz/views/__init__.py`：新增经校验的每视图 viewport 与细节岛状态，保留旧 View State 的兼容默认值。
- `src/codeviz/ui/controller.py`：新增每岛最多 18 节点 / 40 边的独立局部查询、三岛上限，以及 viewport / 岛的保存、移动、关闭和布局重置命令。
- `src/codeviz/ui/graph_canvas.py`：接收主图与细节图 / 状态对，分别建立单图场景后组合岛和来源连线，并实现上下文命中、选择 / 拖动 / 平移隔离、缩放、滚动、Fit、100%、关闭和源码动作。
- `src/codeviz/ui/tkapp.py`、`src/codeviz/ui/i18n.py`：接通标签、Inspector、源码导航、Outdated 状态、恢复 / 陈旧岛清理、工具栏和中英文提示。
- `tests/test_ui_graph.py`、`tests/test_views.py`、`tests/test_persistence_store.py`、`tests/test_persistence_controller.py`、`tests/test_ui_controller.py`、`tests/test_ui_tkapp.py`、`tests/test_mvp_acceptance.py`：覆盖场景数学、布局、输入隔离、A+B 路径、状态边界、失败隔离和三条黄金路径恢复。
- 本轮 Task 9 对齐 `docs/UX_SPEC.md`、`docs/ARCHITECTURE.md`、`docs/CACHE_SPEC.md`、`docs/KNOWN_LIMITATIONS.md`、`docs/ROADMAP.md`；没有扩展 PRD / MVP 范围。

## 验证元数据

- 日期：2026-09-27。
- 平台：Windows，PowerShell；Windows edition / build 未记录。
- Python：CPython 3.12.9，显式解释器 `<user-home>\AppData\Local\Programs\Python\Python312\python.exe`。
- 已验证实现提交：`04d0f00a068a5a9334f48979e5bc9e56830e162a`（`Harden interactive canvas restore and acceptance paths`）；本次文档修订基于 `d3fe83980b1516f9086163a9e87c19f359c80856`。二者之间只有 Task 9 文档提交，没有产品 / 测试代码变化。
- 本地门槛状态如下；GitHub Actions 状态未查询，因此不声称远程 CI 通过或失败。

## 自动化验证

本机 PowerShell 没有可用的 `python` 命令别名，`py -3` 也没有注册解释器。因此以下要求的模块命令使用同一台机器上的显式解释器 `<user-home>\AppData\Local\Programs\Python\Python312\python.exe`（Python 3.12.9）运行；参数与工作目录保持不变。

- `python -m ruff check src tests`：通过，`All checks passed!`。
- `python -m mypy`：通过，`Success: no issues found in 47 source files`。
- `python -B -m unittest discover -s tests -v`：通过，运行 289 个测试，0 failures / 0 errors，48 skipped。
- `python -m pip wheel . --no-deps -w dist`：本地构建通过，`Successfully built code-module-visualizer`。pip 的隔离构建先通过网络取得声明的 `setuptools>=61` 构建依赖；首次网络受限运行退出 1，授权下载后完全相同的 gate 退出 0。该次生成的 `code_module_visualizer-0.1.0-py3-none-any.whl` 为 109849 bytes，观测 SHA-256 为 `b9e02ac9ff102463621cc9fb9d8f4ebb5af7c4efd2f064984a915128d2ecd6b5`；wheel / ZIP 元数据包含构建时间，不能把该 hash 当作可复现构建保证。`dist/` 由 `.gitignore` 排除，产物未提交并在验证后删除。
- `git diff --check`：通过，无输出。

48 个跳过均为环境相关：2 个 Windows 符号链接能力用例因当前环境不能创建测试链接而跳过；其余 46 个需要可用 Tk 图形显示的 live-Tk / Canvas 用例因 `Tk display unavailable` 跳过。纯场景、控制器、持久化、验收和 headless Tk 协调测试已运行，不把跳过的 live-Tk 用例计为通过。

以上数字应以本文件所在提交前最后一次完整验证为准；若后续修改代码、测试或文档，需要重新运行全部门槛并更新结果。

## 人工视觉试用

本环境没有可用 Tk 图形显示，因此没有启动桌面窗口，也没有执行或声称完成人工视觉试用。以下场景供具备 Tk/Tcl 与真实显示器的 Windows / Linux 桌面复现。先在仓库根目录按 `README.md` 启动：`.\.venv\Scripts\codeviz.exe`，在 Settings 中切换 English 以使用下述标签，并以 `tests/fixtures/mvp_project/` 为项目目录。

### 场景 1：基线 A+B 与源码

原验收要求的精确基线序列保留如下；该序列会在 reopen 前关闭岛，因此只验证基本交互，不验证岛恢复：

```text
Open Project → Analyze → Architecture → single-click node → double-click/+ →
move detail island → select island node → Open Source → close island → reopen project
```

1. Open Project 选择 `tests/fixtures/mvp_project/`，点击 Analyze；在 Overview 选择 `model.Classifier` 打开 Architecture。
2. 单击代码名以 `.encoder` 结尾的普通可展开节点，确认 Inspector 更新且 Classifier 主图不变。
3. 双击该节点或点击 `+`，确认细节岛出现；拖动岛，在岛内选择 `layers` 或其他带 Source 的节点，再点 Open Source，确认打开 `model.py` 对应行。
4. 关闭岛，再次 Open Project / Recent Projects 打开同一 fixture；确认分析仍可用。此场景不应声称恢复已关闭的岛。

### 场景 2：viewport 与岛恢复

1. 在 `model.Classifier` Architecture 再次打开 `.encoder` 细节岛并移动它；保持该岛打开。
2. 依次执行空白拖动或中键拖动、`Ctrl+滚轮`、Fit、100%，最后再设置一个明显非默认 zoom / canvas pan；记下缩放百分比和岛位置。
3. 用普通滚轮或 Page Down 产生额外原生滚动偏移；不要关闭岛，直接重新打开同一项目（或退出后按 README 命令重启并从 Recent Projects 打开）。
4. 确认有效 `.encoder` 岛、岛位置、最终 zoom 和 canvas pan 恢复；普通滚轮 / Page Down 的临时 Tk 滚动偏移不要求恢复。

### 场景 3：三岛上限与节点类型

1. 在 Architecture 中依次对三个不同、带 `+` 的普通节点打开岛，保持三个都打开。
2. 对第四个不同、带 `+` 的普通节点尝试打开岛；确认岛数仍为三个，状态栏显示 `Close a detail view before opening another`（中文模式显示对应译文），且没有旧岛被自动关闭。fixture 的可见节点随所选模型 / 聚合预算而异，因此以四个实际显示 `+` 的普通节点为准。
3. 对聚合节点双击 / Enter / `+`，确认打开隐藏成员侧栏而非第四个岛；对没有 `+` 的终端节点双击 / Enter，确认不展开，并仍可从 Inspector 使用 Open Source。

### 场景 4：Execution 与 Symbol / Data Flow 差异

1. 从 Overview 打开 `train_epoch` Training Flow；打开 `EpochLoop` 细节岛，确认岛内可见 `BatchLoop`，并验证 `BackwardPass` 打开 `loss.backward()`、`ParameterUpdate` 打开 `optimizer.step()`。
2. Search 输入 `loss`，选择 `run.train_epoch.loss` 并 Reveal；在 Data Flow 主图单击另一个可聚焦普通节点，确认局部上下文允许重新查询 / 居中，而不是套用 Architecture / Execution 的“主图不变”保证。

### 场景 5：Outdated 与岛保留

1. 不修改仓库 fixture；在 PowerShell 创建临时副本：`$trial = Join-Path $env:TEMP ("codeviz-mvp-" + [guid]::NewGuid()); Copy-Item tests/fixtures/mvp_project $trial -Recurse`。
2. Open Project 选择 `$trial`，Analyze，打开 `model.Classifier` Architecture 和 `.encoder` 岛，并保持岛打开。
3. 在应用外执行 `Add-Content (Join-Path $trial 'run.py') '# outdated trial'`，然后再次 Open Project 选择 `$trial`。
4. 确认状态为 Outdated、Overview / 图 / Source 显示警告，旧主图仍可浏览且有效 `.encoder` 岛恢复。试用结束后关闭应用，再删除该唯一临时目录 `$trial`。

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
