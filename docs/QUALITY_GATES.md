# 本地质量门禁

2026-10-20 助手验收：Task9 定向 9 项、夹具直接消费者 1 项及 Task10 启动接线/缓存隔离 3 项通过；本次 native 模块最终 18 项、publication 直接消费者 80 项通过。Windows 源码、隔离安装 wheel、当前最终冻结包各通过 7 项真实 WebView2 假提供方交互，保留截图、退出码 0 和资源释放记录；无真实 API 调用。当前 wheel 的无额外依赖隔离导入/资源/声明及零请求探针亦通过。唯一 E 复审发现的验证器失败清理问题已修复，并通过真实窗口故障注入；没有第二次独立复审。修复后最终后端全量 1448 项：1379 passed / 69 skipped、零失败（53.956 秒）；Ruff、Windows/Linux mypy（各 98 个源码文件）通过，见[交付记录](CODE_AGENT_DEPENDENCIES.md)。前端未改，沿用 D 同代码快照的全量 546 passed / 2 todo 与 lint/typecheck/build 结果，不重复执行。原生 Linux、真实 API 继续 skipped，生产 SDK 门禁未开放；不得用类型检查、jsdom 或假模型代替这些验收，Task10/E 尚非全部完成。

## 目的与范围

2026-10-11测试审计补充：阶段内小步开发采用受影响模块与直接消费者的定向回归，减少无关测试、同一状态的重复执行和不必要打包；每阶段结束的完整质量门禁不降低，GitHub全量和平台矩阵不变。目标、完整归类、实测与选择规则见 [测试资产审计](TEST_SUITE_AUDIT.md) 和 [测试策略](TEST_STRATEGY.md)。定向通过不能冒称全量、原生桌面或发布验收通过。

从 Phase 3 起，开发者与 Codex 应在每阶段结束运行同一组检查。Ruff 检查 `src/` 与 `tests/` 中的 Python 代码；mypy 以 Python 3.11 为目标、用严格模式检查 `src/codeviz/`。测试继续由标准库 `unittest` 运行。被分析的用户项目不在检查范围内，也不会因为这些检查而执行。

开发工具属于 `pyproject.toml` 的 `dev` 可选依赖；产品运行时 `dependencies = []` 保持不变。`.venv/`、`.mypy_cache/`、`.ruff_cache/` 均被 Git 忽略。开发工具版本固定为本地验证过的 Ruff 0.16.8 和 mypy 2.3.1；升级时要显式复验。

## GitHub 自动检查

`.github/workflows/quality.yml` 在推送到 `main`、提交拉取请求时运行。GitHub 托管的 Ubuntu 24.04 环境分别使用 Python 3.11 和 3.12，执行 Ruff、mypy、unittest 和 wheel 构建。两组检查彼此独立；任一步失败会使相应任务失败。

工作流仅授予 `contents: read`，不使用仓库密钥或写入令牌；checkout 不保留 Git 凭据，两个官方 Action 固定到已核实的完整提交 SHA。它使用 `pull_request` 事件，不使用权限更高的 `pull_request_target`。自动运行不等于合并保护：若要禁止检查失败时合并，还需单独在 GitHub 仓库规则中把对应状态设为必需检查；本次未更改仓库规则。

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
.\.venv\Scripts\python.exe -m mypy --platform linux
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Windows 本机的 mypy 通过不等于 Ubuntu 通过。`--platform linux` 使用 Linux 平台类型定义，可在推送前发现 Windows-only API 属性错误；GitHub Actions 仍须在真实 Ubuntu 上单独验证。Phase 8/9 的 CI 失败即由该差异引起，已在 `779a864` 修复并推送；不把本地模拟结果冒称为远端成功。

打包检查沿用 `py -m pip wheel . --no-deps --no-build-isolation --no-index`；如需保存产物，应指定临时输出目录，避免将构建产物纳入提交。

## 配置选择与基线

- Ruff 启用基础语法 / 未使用名称、导入排序、常见 bugbear 规则（`E4`、`E7`、`E9`、`F`、`I`、`B`）；不把格式化检查混入 lint，避免一次性改写整个代码库。
- mypy 使用 `strict = true` 和 `python_version = "3.11"`，只检查产品源码；测试由 Ruff 与 `unittest` 覆盖。
- 首次开启时，Ruff 找出 7 处原有导入排序问题，mypy 找出 6 处原有类型问题。已分别做机械排序和局部类型修正；没有通过关闭规则或全局忽略来掩盖它们。
- 本地实际验证环境：Python 3.12、Ruff 0.16.8、mypy 2.3.1。GitHub 工作流新增 Python 3.11 / 3.12 环境；其首轮远端结果须在推送后单独核实，不能用本地通过代替。

以后若调整规则或依赖版本，应先记录检查基线和新增失败，再更新配置与本文，不能以放宽规则代替修复。

## 估算输入计量阶段门禁（2026-10-21）

本切片使用 `.desktop-venv/Scripts/python.exe` 与已有任务本地 Node 22.23.3。
阶段末运行 Python 全量、Ruff、Windows/Linux mypy，以及前端 lint、类型检查、
全量测试与构建；同一未改快照已通过的命令不重复运行，失败复跑遵守 AGENTS.md 限次规则。
定向测试只证明所测模块；skipped、todo、真实 Linux、SDK/API 和原生打包验收分别记账。
封闭 usage 联合只允许旧严格两键或估算三键，后者含六个固定计量字段，并按任务种类
校验 20,000 / 1,024 阈值。未知不作 0，预估不作上界，不为通过门禁放宽旧严格测试。
生产工厂默认 OFF；本阶段通过不等于真实 DeepSeek、Linux 或新 wheel/exe 已验收。
