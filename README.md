# 代码模块可视化

Python-first 的代码模块可视化与程序理解项目。目标是从源码构建可追溯的代码图、语义组件图和局部可见图，首版重点面向 Python / PyTorch。

2026-10-08 源码编辑增量：React PC 源码标签与侧边源码已接入离线 Monaco，支持 Python 彩色语法、行号、查找替换、撤销、折叠和显式保存。修改后标签显示 `●`，点击“保存”或按 `Ctrl+S`；关闭最后一个源码视图、切换项目、重新分析或退出时提供保存／放弃／取消。外部文件变化拒绝覆盖并保留草稿；保存后旧图标为过期，需手动重新分析。混合换行、CR-only、链接、只读或不可安全往返的编码目标不可编辑。源码不会自动运行或上传，未保存草稿不在强制退出后恢复。下方只读描述属于早期／Tk 基线，React 编辑合同以 [SOURCE_EDITING.md](docs/SOURCE_EDITING.md) 为准。

普通 Python 项目也可使用：打开目录 → 分析 → 项目概览的“函数 / 方法”目录 → 选择函数 → **函数流程** → 单击节点查看检查器 → 打开源码或侧边源码。无需模型、PyTorch 或自动入口。搜索普通变量可进入局部数据关系；main guard 等入口只代表静态候选，不等于真实运行轨迹。支持范围、限制与本轮验收见 [Python 支持矩阵](docs/PYTHON_SUPPORT_MATRIX.md) 和 [通用 Python 验证记录](docs/GENERAL_PYTHON_VALIDATION.md)。

当前已有本地桌面阅读、搜索与 Phase 10 本地缓存：首次显式点击 Analyze Project 后，重新打开未变化的项目会直接加载分析结果；源码、Ignore Paths 或版本变化时旧图标为 Outdated，并可 Re-analyze。Recent Projects、图布局、标签与选中节点保存在应用数据目录，Annotations 独立保存。Overview 中可选择模型或入口，浏览 Architecture / Training Flow 局部图；Search / Symbols 可查定义与语义节点，再 Reveal 到局部图、Inspector、只读源码或 Open to Side。Analysis Issues 有独立标签页。自动布局按各视图关系方向从上到下分层，同层分支并列；Symbol / Data Flow 上游允许位于查询焦点上方，环稳定处理，不代表真实执行顺序，详见 `docs/UX_SPEC.md`。关系范围可用“更少层 / 更多层”在 1–5 层间调整；新图自动定位，正常用户视角和手动节点位置保留，详见 [画布恢复](docs/CANVAS_RECOVERY.md)。长图可继续平移、缩放和适应，不保证一屏全部容纳；除概览外的图、源码和问题标签都可通过右侧 `×` 或 `Ctrl+W` 关闭。底层 `analyze_project(Path(...))` 返回 `flow.graph`、`model`、`training` 和 `semantic.graph`；`build_visible_graph(...)` 只生成有预算的 Architecture / Execution / FunctionFlow / Symbol / Data Flow 局部图。Q/K/V → 矩阵得分 → softmax 只给出 Attention-like 候选；梯度关系是 Probable 静态模式，不是真实 autograd 图。源码只读，不导入或执行被分析代码。

桌面主入口采用 React Flow + pywebview 原生窗口。Windows 需要预先安装系统原生 **Microsoft Edge WebView2 Runtime**，打包目录不内置该系统运行时。生产前端 JS、CSS 等资源全部随应用分发，可离线使用，无 CDN。静态服务只绑定随机 loopback 端口、只提供 frontend 资源根；项目源码和应用 API 不通过 HTTP 暴露，应用操作经 pywebview bridge 完成。浏览器打开 Vite 页面仅用于前端开发，不能替代原生窗口。

本轮源码编辑 PC 包：关闭旧应用，运行 `dist/monaco-source-editing/CodeViz/CodeViz.exe`，保留整个 CodeViz 目录，不要只复制 exe。该包沿用架构详情与普通 Python 能力；编辑增量尚未提交或推送。此前 `dist/architecture-detail-routing/CodeViz/CodeViz.exe` 等只读体验包作为历史产物保留。复杂图仍可能连线交叉，详见 [架构详情验收](docs/ARCHITECTURE_DETAIL_ROUTING.md)。

从仓库根目录准备并启动（Python 3.11+、Node.js 22，首次安装与构建需获取依赖）：

```powershell
py -m venv .desktop-venv
.\.desktop-venv\Scripts\python.exe -m pip install -e ".[desktop,packaging,dev]"
npm --prefix frontend ci
npm --prefix frontend run build
.\.desktop-venv\Scripts\python.exe -m codeviz.ui.desktop
```

安装后的主命令是 `.\.desktop-venv\Scripts\codeviz.exe`；旧 Tk 入口保留为 `.\.desktop-venv\Scripts\codeviz-tk.exe`，需要 Python 提供 Tk 8.6 与图形桌面。以下原有阅读流程作为既有能力基线，React 桌面逐项验收见 `docs/MVP_ACCEPTANCE.md`。

## 桌面打包与前端开发

```powershell
.\scripts\build-desktop.ps1 -CheckOnly
.\scripts\build-desktop.ps1
.\dist\CodeViz\CodeViz.exe
```

CheckOnly 检查 Python、Node/npm、固定版本打包依赖、前端清单与 lockfile、已安装前端依赖和 `frontend/dist/index.html`；不安装、不构建、不启动 GUI。完整构建执行 `npm ci`、前端 build 和 PyInstaller onedir；分发必须包含整个 `dist/CodeViz` 目录。`-Python 'C:\path\python.exe'` 可指定解释器，默认依次探测可用的 `.desktop-venv`、`.venv` 和系统 Python 3.11+。Windows CI 只验证打包产物，原生窗口 / WebView2 必须另做人工验收。

### 从 GitHub Actions 下载 Windows 包

1. 登录 GitHub，打开仓库的 **Actions → Quality checks**，选择对应当前提交 SHA 的运行，确认 **Windows desktop packaging (no GUI)** 作业成功。
2. 在该次运行的 **Artifacts** 中下载 **CodeViz-Windows-x64**；产物保留 14 天，上传时若找不到打包文件会报错。
3. 将 ZIP **完整解压到一个目录**，保留 `CodeViz.exe`、`_internal` 及全部子目录的相对位置；不要只取出 exe，也不要直接在 ZIP 内运行。
4. 在 Windows x64 系统预先安装 **Microsoft Edge WebView2 Runtime**，然后运行解压目录中的 `CodeViz.exe`。该包包含 Python 和本地前端资源，使用时无需另装 Python 或 Node.js。

这些下载是对应提交的 Actions 构建产物，不是 GitHub Release；新增上传配置不代表当前提交已在远端通过检查或完成原生 GUI 验收，须以对应运行结果及实际验证为准。Python 矩阵作业还会分别提供 `CodeViz-wheel-Python-3.11` / `CodeViz-wheel-Python-3.12`，同样保留 14 天；wheel 不是可直接运行的 Windows 桌面包。

wheel 构建须先生成 `frontend/dist`；构建命令把资源复制至 wheel 内 `codeviz/ui/assets`，不写入 Python 源码目录。冻结包资源位于 `dist/CodeViz/_internal/frontend/dist`。

```powershell
.\.desktop-venv\Scripts\python.exe -m pip wheel . --no-deps -w dist
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run dev
```

分析核心安装无需 `[desktop]`，基础依赖为空，不强制安装 Linux GTK / Qt GUI 系统库。desktop 固定 pywebview 6.2.1，packaging 固定 PyInstaller 6.22.3 与 setuptools 84.0.0；这是直接工具版本锁定，不是传递依赖的完整哈希锁。当前原生入口显式使用 Windows Edge Chromium，Linux / macOS 桌面后端尚未实现；分析核心仍可独立跨平台使用。

默认使用简体中文，可在左侧 **设置 → 界面语言** 中切换“简体中文”或“English”，选择保存在应用数据目录，不会修改被分析项目。点击 **打开项目** 使用原生文件夹选择器；首次点击 **分析项目 / 重新分析** 才解析 `.py` 文件，之后若有效缓存存在则自动加载，不执行用户代码。发现变更时仍可查看带 Outdated 警告的旧图，并点 **重新分析** 更新。

版本不兼容的缓存可能只保留布局、没有可用分析；此时需重新分析，不能查询旧图。文件夹选择等待选择或取消；项目写入和分析等待后台真实完成后才允许切换，避免晚到写入影响其他项目。布局保存的真实失败会提示，不应阻断显式打开或重新分析。异常停滞尚无后台取消协议，可正常关闭后重启应用；勿将单纯前端超时当作后台取消。修复与验证记录见 [项目操作恢复](docs/PROJECT_ACTION_RECOVERY.md)。

从概览点击模型进入架构、点击训练入口进入训练执行流程；普通入口或具名函数进入独立函数流程。单击节点显示检查器，双击或 Enter 在同一画布展开细节岛；源码可打开为独立标签或在侧边并排查看。标签的 `×` 或 `Ctrl+W` 只关闭当前标签，不退出整个应用。搜索名称或“反向传播”等语义词会自动返回结果，点击 **在图中定位** 或 **源码** 完成跳转。设置同时提供缓存状态和每行一项的项目相对 **忽略路径**；修改忽略路径后旧分析会标为过期，需重新分析。

本次结构探索修复采用分视图操作：**架构／项目结构**的“更多层／更少层”展开或收起真实组件层级，**符号／数据关系**的关系范围控制上游、下游及最多五跳；函数流程不再提供不起作用的层数按钮。普通 Python 项目可从项目结构查看模块、类、函数，不要求先识别出 PyTorch 模型。定义展开不是运行实例观测，节点仍链接真实源码。合同、开发状态及最终验证记录见 [结构探索修复](docs/STRUCTURE_EXPLORATION.md)；文档说明不替代该记录中的交付门禁。

最小查询示例：

```python
from pathlib import Path

from codeviz.analysis.project import analyze_project
from codeviz.core import ViewKind
from codeviz.search import search_project
from codeviz.views import ViewRequest, build_visible_graph

analysis = analyze_project(Path("your-python-project"))
architecture = build_visible_graph(analysis, ViewRequest(ViewKind.ARCHITECTURE))
training = build_visible_graph(analysis, ViewRequest(ViewKind.EXECUTION))
matches = search_project(analysis, "反向传播").results
```

默认最多 100 个可见节点、300 条边；展开和手动位置可通过 `ViewState` 传入。桌面 UI 的位置和标签保存在应用数据目录，不写入被分析项目；不保存 AST 或完整源码副本。

## 本地验证

需要 Python 3.11 或更新版本。分析核心运行时只使用 Python 标准库，原生桌面另需 `[desktop]` 可选依赖。分析目标仅支持 `.py`；默认跳过依赖、构建、缓存、权重和数据目录，可传入项目相对的 `ignore_paths`。

在仓库根目录首次设置开发环境（PowerShell）：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

之后运行全部质量检查：

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m mypy --platform linux
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Ruff 检查源码与测试；mypy 严格检查产品源码。开发工具只在 `dev` 可选依赖中，不会成为运行时依赖。配置与阶段基线见 `docs/QUALITY_GATES.md`。产品与技术边界见 `docs/PRD.md`、`docs/ARCHITECTURE.md` 和 `docs/DATA_MODEL.md`。

推送到 `main` 或提交拉取请求时，GitHub Actions 在 Python 3.11 / 3.12 下运行 core lint / mypy / unittest 和含前端资源的 wheel 构建，另执行前端 lint / typecheck / test / build 与 Windows 无 GUI 打包检查；Linux core job 不安装桌面 GUI 依赖。工作流见 `.github/workflows/quality.yml`。合并保护规则需另行在 GitHub 设置，自动检查本身不会禁止合并。

固定的 MVP 样例与三条黄金路径自动验收在 `tests/fixtures/mvp_project/`、`tests/test_mvp_acceptance.py` 和 Tk 界面测试中；`docs/MVP_ACCEPTANCE.md` 区分自动验证与仍需人工观察的理解度验收。在已激活的虚拟环境中运行 `python -B tests/benchmark_mvp.py`，或运行 `python -B tests/benchmark_scale.py 50` / `200` 对比简单多文件项目。基线解释见 `docs/PERFORMANCE.md`，不构成所有项目的性能保证。

三份公开 PyTorch 仓库的只读交叉验证、固定提交和已知问题见 `docs/REAL_REPOSITORY_VALIDATION.md`；第三方源码不在本仓库，也不参与 CI。
