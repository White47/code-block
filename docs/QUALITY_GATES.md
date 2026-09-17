# 本地质量门禁

## 目的与范围

从 Phase 3 起，开发者与 Codex 应在每阶段结束运行同一组检查。Ruff 检查 `src/` 与 `tests/` 中的 Python 代码；mypy 以 Python 3.11 为目标、用严格模式检查 `src/codeviz/`。测试继续由标准库 `unittest` 运行。被分析的用户项目不在检查范围内，也不会因为这些检查而执行。

开发工具属于 `pyproject.toml` 的 `dev` 可选依赖；产品运行时 `dependencies = []` 保持不变。`.venv/`、`.mypy_cache/`、`.ruff_cache/` 均被 Git 忽略。当前没有 CI 工作流，因此这些是本地门禁，而非声称 GitHub 已自动阻止不合格提交。

## PowerShell 命令

在仓库根目录初次执行：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

每次阶段验证：

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

打包检查沿用 `py -m pip wheel . --no-deps --no-build-isolation --no-index`；如需保存产物，应指定临时输出目录，避免将构建产物纳入提交。

## 配置选择与基线

- Ruff 启用基础语法 / 未使用名称、导入排序、常见 bugbear 规则（`E4`、`E7`、`E9`、`F`、`I`、`B`）；不把格式化检查混入 lint，避免一次性改写整个代码库。
- mypy 使用 `strict = true` 和 `python_version = "3.11"`，只检查产品源码；测试由 Ruff 与 `unittest` 覆盖。
- 首次开启时，Ruff 找出 7 处原有导入排序问题，mypy 找出 6 处原有类型问题。已分别做机械排序和局部类型修正；没有通过关闭规则或全局忽略来掩盖它们。
- 本次实际验证环境：Python 3.12、Ruff 0.16.8、mypy 2.3.1。Python 3.11 语法目标已配置，但本次未在 3.11 解释器下运行。

以后若调整规则或依赖版本，应先记录检查基线和新增失败，再更新配置与本文，不能以放宽规则代替修复。
