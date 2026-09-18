# 代码模块可视化

Python-first 的代码模块可视化与程序理解项目。目标是从源码构建可追溯的代码图、语义组件图和局部可见图，首版重点面向 Python / PyTorch。

当前已完成 Phase 7 的核心：`codeviz.analysis.project.analyze_project(Path(...))` 返回 `flow.graph`、`model`、`training` 和 `semantic.graph`；`codeviz.views.build_visible_graph(...)` 从语义图生成有预算的 Architecture / Execution 局部图。多个模型和 Train / Evaluate / Inference / Export 入口独立展示，节点、边、聚合成员均可回溯到静态事实。仅在 Q/K/V → 矩阵得分 → softmax 的结构证据同时存在时生成 Attention-like 候选；梯度关系只是 Probable 的静态模式，并非真实 autograd 图。源码只读，不导入或执行被分析代码。图形界面与磁盘缓存仍按 [实施计划](docs/IMPLEMENTATION_PLAN.md) 分阶段加入。

最小查询示例：

```python
from pathlib import Path

from codeviz.analysis.project import analyze_project
from codeviz.core import ViewKind
from codeviz.views import ViewRequest, build_visible_graph

analysis = analyze_project(Path("your-python-project"))
architecture = build_visible_graph(analysis, ViewRequest(ViewKind.ARCHITECTURE))
training = build_visible_graph(analysis, ViewRequest(ViewKind.EXECUTION))
```

默认最多 100 个可见节点、300 条边；展开和手动位置可通过 `ViewState` 传入。当前只提供 Python API，尚无图形界面。

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
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Ruff 检查源码与测试；mypy 严格检查产品源码。开发工具只在 `dev` 可选依赖中，不会成为运行时依赖。配置与阶段基线见 `docs/QUALITY_GATES.md`。产品与技术边界见 `docs/PRD.md`、`docs/ARCHITECTURE.md` 和 `docs/DATA_MODEL.md`。

推送到 `main` 或提交拉取请求时，GitHub Actions 会在 Python 3.11 / 3.12 下自动运行同一组检查及 wheel 构建；工作流见 `.github/workflows/quality.yml`。合并保护规则需另行在 GitHub 设置，自动检查本身不会禁止合并。
