# 代码模块可视化

Python-first 的代码模块可视化与程序理解项目。目标是从源码构建可追溯的代码图、语义组件图和局部可见图，首版重点面向 Python / PyTorch。

当前已完成 Phase 6：`codeviz.analysis.project.analyze_project(Path(...))` 返回 `flow.graph`、`model`、`training` 和 `semantic.graph`。`training` 按 Train / Evaluate / Inference / Export 入口分别保存静态事件，`semantic.graph` 组合模型定义、子模块实例与训练事件，保留源码锚点、证据及 Exact / Probable / Unresolved。仅在 Q/K/V → 矩阵得分 → softmax 的结构证据同时存在时生成 Attention-like 候选；梯度关系只是 Probable 的静态模式，并非真实 autograd 图。规则位于独立的 `semantic/` 层，源码只读，不导入或执行被分析代码。局部 Visible Graph 与图形界面仍按 [实施计划](docs/IMPLEMENTATION_PLAN.md) 分阶段加入。

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
