# 代码模块可视化

Python-first 的代码模块可视化与程序理解项目。目标是从源码构建可追溯的代码图、语义组件图和局部可见图，首版重点面向 Python / PyTorch。

当前已完成 Phase 2：除源码锚点、稳定 ID、Code Graph IR 与基础语义 / 可视对象外，已有 Python 文件扫描、静态 AST 解析和词法符号索引。可通过 `codeviz.analysis.project.analyze_project(Path(...))` 对本地项目做一次性分析；分析只读，不导入或执行被分析代码。跨文件引用解析、PyTorch 语义、图形界面仍按 [实施计划](docs/IMPLEMENTATION_PLAN.md) 分阶段加入。

## 本地验证

需要 Python 3.11 或更新版本。项目运行时目前只使用 Python 标准库。分析目标仅支持 `.py`；默认跳过依赖、构建、缓存、权重和数据目录，可传入项目相对的 `ignore_paths`。

PowerShell：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
py -B -m unittest discover -s tests -v
```

产品与技术边界见 `docs/PRD.md`、`docs/ARCHITECTURE.md` 和 `docs/DATA_MODEL.md`。
