# 代码模块可视化

Python-first 的代码模块可视化与程序理解项目。目标是从源码构建可追溯的代码图、语义组件图和局部可见图，首版重点面向 Python / PyTorch。

当前已有本地桌面阅读、搜索与 Phase 10 本地缓存：首次显式 Analyze 后，重新打开未变化的项目会直接加载分析结果；源码、Ignore Paths 或版本变化时旧图标为 Outdated，并可 Re-analyze。Recent Projects、图布局、标签与选中节点保存在应用数据目录，Annotations 独立保存。Overview 中可选择模型或入口，浏览 Architecture / Training Flow 局部图；Search / Symbols 可查定义与语义节点，再 Reveal 到局部图、Inspector、只读源码或 Open to Side。Analysis Issues 有独立标签页。底层 `analyze_project(Path(...))` 返回 `flow.graph`、`model`、`training` 和 `semantic.graph`；`build_visible_graph(...)` 只生成有预算的 Architecture / Execution / Symbol / Data Flow 局部图。Q/K/V → 矩阵得分 → softmax 只给出 Attention-like 候选；梯度关系是 Probable 静态模式，不是真实 autograd 图。源码只读，不导入或执行被分析代码。

安装后启动桌面应用（需要带 Tk 8.6 的 Python 和图形桌面）：

```powershell
.\.venv\Scripts\codeviz.exe
```

点击 **Open Folder** 选择目录；首次点击 **Analyze Project** 才解析 `.py` 文件，之后若有效缓存存在则自动加载，不执行用户代码。发现变更时仍可查看带 Outdated 警告的旧图，并点 **Re-analyze** 更新。Search 输入名称或“反向传播”等语义词后按 Find，选择结果按 Reveal 进入局部图；Symbols 用于浏览全部定义。图上可用 Upstream / Downstream / + hop 扩展局部上下文。Ignore Paths 当前可通过 `AppController(ignore_paths=(...))` 配置，桌面 Settings 表单尚未提供。

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

需要 Python 3.11 或更新版本。项目运行时目前只使用 Python 标准库。分析目标仅支持 `.py`；默认跳过依赖、构建、缓存、权重和数据目录，可传入项目相对的 `ignore_paths`。

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

推送到 `main` 或提交拉取请求时，GitHub Actions 会在 Python 3.11 / 3.12 下自动运行同一组检查及 wheel 构建；工作流见 `.github/workflows/quality.yml`。合并保护规则需另行在 GitHub 设置，自动检查本身不会禁止合并。
