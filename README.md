# 代码模块可视化

Python-first 的代码模块可视化与程序理解项目。目标是从源码构建可追溯的代码图、语义组件图和局部可见图，首版重点面向 Python / PyTorch。

当前处于 Phase 1：已建立源码锚点、稳定 ID、Code Graph IR 与基础语义 / 可视对象。解析器、框架规则和 UI 将按 [实施计划](docs/IMPLEMENTATION_PLAN.md) 分阶段加入。当前不会分析或执行外部项目。

## 本地验证

需要 Python 3.11 或更新版本。项目核心目前只使用 Python 标准库。

PowerShell：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
py -B -m unittest discover -s tests -v
```

产品与技术边界见 `docs/PRD.md`、`docs/ARCHITECTURE.md` 和 `docs/DATA_MODEL.md`。
