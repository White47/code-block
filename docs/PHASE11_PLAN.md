# Phase 11 — MVP 硬化与三条黄金路径验收

日期：2026-09-18。以 `MVP_ACCEPTANCE.md` 为验收口径；不新增运行时依赖，不修改目标项目源码或执行它。

| Task | Goal / deliverables / likely files | Dependencies | Tests / acceptance | Out of scope |
| --- | --- | --- | --- | --- |
| 1 固定样例 | 建立含两个模型、共享模型的训练 / 评估入口、ModuleList / Sequential、同名变量、动态调用、坏文件和不可执行陷阱的项目；`tests/fixtures/mvp_project/**` | Phase 1–10 | 文件级静态分析保留其他结果，坏文件只产生 issue；不得 import fixture | 大型真实仓库镜像 |
| 2 架构链路 A | Overview → Select Model → Architecture → Drill Down → Source；`tests/test_mvp_acceptance.py`、必要局部实现 | Task 1、Phase 7–8 | 两模型可辨、子组件与折叠证据、源锚点正确、合成节点不提供失效跳转 | 自动论文图片布局 |
| 3 训练链路 B | Overview → Training Flow → Epoch / Batch → Forward → Loss → Backward → Optimizer → Source；同文件 | Task 1、Phase 6–8 | 多入口独立而共享模型定义；循环、关系线、真实源码位置 | 运行时 autograd |
| 4 搜索链路 C | Search → Reveal → Local Context → Inspector → Source；同文件 | Task 1、Phase 9 | 同名 loss 按作用域区分，中文语义关键词可定位，局部图限额，源码可读 | 全文语义搜索 |
| 5 性能与发布收尾 | 可重复基线脚本、质量报告和 Known Limitations 更新；`tests/benchmark_mvp.py`、`docs/PERFORMANCE.md`、`docs/PHASE11_REPORT.md` | Tasks 1–4 | 测量文件 / 符号 / 图数量、Analyze / View / Search / Cache 耗时与内存；Ruff、Linux / Windows mypy、unittest、wheel；远端 CI 单独核实 | 增量分析、分布式、大型仓库性能承诺 |

每项以固定 fixture 编写验收测试并先验证失败，再做最小修复。若现有代码已满足则保留实现，仅补验收证据。阶段末对照真实仓库更新文档和已知限制，不伪造未测得的性能数值。
