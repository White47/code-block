# Phase 11 技术验收报告 — 固定样例、黄金路径与性能基线

日期：2026-09-19。阶段分支：`phase11-mvp-hardening`。验收口径：`MVP_ACCEPTANCE.md`、`PHASE11_PLAN.md`。

## 实现与证据

| 范围 | 改动 | 可观察结果 |
| --- | --- | --- |
| 固定项目 | `tests/fixtures/mvp_project/**` | 自定义 Block / Encoder / Classifier 与 Baseline，ModuleList / Sequential，训练和评估共用 Classifier，同名 loss、动态 callback、坏语法、默认忽略目录和禁止执行陷阱 |
| 黄金路径 A/B/C | `tests/test_mvp_acceptance.py`、`tests/test_ui_tkapp.py` | 领域事实与真实 Tk 按钮 / 标签共同覆盖 Overview → 模型架构下钻 → 源码、训练循环 → Backward / Optimizer → 源码、搜索同名变量 → 局部图 → Inspector → Split Source |
| 自定义组件下钻 | `src/codeviz/semantic/components.py`、`src/codeviz/ui/tkapp.py` | 将实例的可解析目标定义作为导航指针；Classifier → Encoder 可进入 Encoder 内部图，Back 返回 Classifier；不伪造包含关系或运行时目标 |
| 性能与缓存硬化 | `tests/benchmark_{mvp,scale}.py`、`src/codeviz/persistence/codec.py`、`tests/test_persistence_codec.py` | 可重复输出文件 / 符号 / 图 / 缓存大小与耗时；类型提示按领域类型缓存，避免每个节点重复求值；回归测试不使用脆弱的绝对时间阈值 |
| 文档与测试配置 | `docs/{MVP_ACCEPTANCE,PERFORMANCE,TEST_STRATEGY,KNOWN_LIMITATIONS,ROADMAP,PHASE11_PLAN}.md`、`README.md`、`pyproject.toml` | 实测数值与人工边界分开；Ruff 只排除故意无效的测试 fixture，仍检查所有产品源码和测试程序 |

固定 fixture 技术验收覆盖的底层事实：4 个 `.py` 被发现、3 个成功解析、66 个符号、196 个 Code Graph 节点、30 个语义组件；默认 Architecture / Execution 局部视图分别仅 4 / 7 节点。损坏文件未阻断模型、训练和搜索，`trap.py` 的顶层异常未被执行。架构链路实际从 Classifier 进入 Encoder，显示其 `layers ×2`，再由源码锚点打开 `class Encoder` 所在行。精确跳转检验了文件和源代码行，不仅是文件内容包含字符串。

## 本地验证与性能

阶段结束的 Ruff、Windows / Linux mypy、全部 unittest、wheel 构建和 diff 检查均通过：204 项测试，2 项既有跳过，无新增失败。性能基线与复现命令见 `PERFORMANCE.md`：本机 50 文件简单项目的 Analyze 两次采样 77–85 ms，200 文件一次采样约 436 ms；这些数值不是跨机器承诺。启用 `tracemalloc` 会放大耗时，普通计时与内存诊断分开记录。

现有 Windows 环境中的 2 个符号链接权限测试仍为既有跳过。Linux CI 上 Tk 实例测试若无显示器会跳过，不能视为远端视觉验收通过。GitHub Actions 运行状态须以推送后的实际 run 为准；本地 `--platform linux` 只是提前发现类型定义差异。

## 未宣称完成的部分

自动化测试证明了路径可操作且可溯源，尚未证明第一次看该项目的非开发者真正理解图。发布前仍需人工可用性观察、至少一个更大的真实 PyTorch 仓库试用和远端 CI 核验。性能基线只覆盖固定 PyTorch 小项目与简单合成 Python 文件，不覆盖大型分布式训练项目、真实 tensor shape 或运行时图。上述工作需要后续单独确认，不以测试通过替代。
