# 性能与规模目标

## MVP 目标

- 首版优先保证中小型 Python 项目可靠。
- Full Graph 可比 Visible Graph 大；Phase 7 的单张图默认最多 100 个节点、300 条边，可按请求调低上限。
- 默认高层聚合，选中节点展示 1-hop；展开或搜索才增加局部范围。
- 一次性 Analyze；不在首版承诺 watcher / incremental parse。

## 保护措施

忽略依赖、权重、数据集和构建目录；第三方库作为 External Node。Phase 7 View Builder 已限制输出节点 / 边，超限时提供可追溯聚合及隐藏数量；取消 / Partial 状态尚未接入视图查询。禁止 UI 直接渲染 Full Graph。

## Phase 11 可复现基线

在 2026-09-18 的本地 Windows / Python 3.12 环境测量，未开启内存追踪；耗时单位为毫秒。命令：

```powershell
.\.venv\Scripts\python.exe -B tests/benchmark_mvp.py
.\.venv\Scripts\python.exe -B tests/benchmark_scale.py 50
.\.venv\Scripts\python.exe -B tests/benchmark_scale.py 200
```

| 样本 | Python 文件 | Symbol / Code Node | 可见节点 | Analyze | 指纹 | 缓存保存 / 加载 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 固定 PyTorch MVP fixture，含 1 个坏文件 | 4（3 解析） | 66 / 196 | Architecture 4、Execution 7 | 约 11 | 约 9 | 约 15 / 46 |
| 临时生成的简单 Python，2 次采样 | 50 | 350 / 551 | Symbol 4 | 77–85 | 46–48 | 约 28 / 90–93 |
| 同类简单 Python，1 次采样 | 200 | 1400 / 2201 | Symbol 4 | 约 436 | 约 170 | 约 118 / 348 |

固定 fixture 的缓存 JSON 约 544 KB；50 / 200 文件生成样例约 1.31 / 5.23 MB。固定 fixture 的图构建和搜索均低于本机 1 ms；50 文件符号图约 0.25 ms，200 文件约 0.83 ms。分析规模、机器、杀毒软件和磁盘会显著影响数字，单次结果不是发布 SLA，也不能代表复杂真实 PyTorch 项目的性能。`benchmark_mvp.py --trace-memory` 可查看内存峰值，但 Python `tracemalloc` 明显放大耗时，不应和普通计时混用；首次领域缓存加载的追踪峰值在本机约 63 MB，尚需更大真实项目的压力验证。

当前缓存是全量 JSON 重建；极小项目的缓存加载可能慢于重新分析，这不影响“无变化时不重跑分析”的行为，但说明后续大项目优化应关注反序列化和内容指纹。对类型提示的重复解析已做按类型缓存，并加入不依赖机器速度的回归测试。性能基线可作为今后回归对比，仍需在同一机器和同一 fixture 上重复测量。

真实项目补充样本见 `REAL_REPOSITORY_VALIDATION.md`：1 / 12 / 50 / 93 个 `.py` 的四档范围，包含 CNN、Transformer、多任务示例和视觉检测。实测分析约 0.05 秒至 3.4 秒，视图仍受 100 节点上限约束；复杂度与动态引用数量差异很大，不应按文件数线性外推。该轮未测真实项目的缓存加载峰值或交互式大列表帧率。

## 通用 Python 增量测量（2026-10-04）

本机 Python 3.12，沿用 `benchmark_scale.py` 临时生成源码，未执行生成的函数。单次测量，不作为 SLA；与旧表不是受控同时间性能对照。

| 文件 | Symbol / Code Node | 可见节点 | Analyze | 缓存大小 | 保存 / 加载 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 50 | 350 / 851 | 4 | 122.920 ms | 1,980,374 bytes | 76.360 / 126.292 ms |
| 200 | 1400 / 3401 | 4 | 654.129 ms | 7,922,674 bytes | 157.780 / 484.409 ms |

普通 Data 发生点使 Full Graph / JSON 快照增大，不能因 Visible Graph 小而忽视缓存内存成本。来源集合上限 32、调用候选 16、继承深度 16 与 CFG / continuation 节点预算约束的是各分析阶段，不替代项目总文件 / 总字节限额。真实 Click / Requests / HTTPX 固定范围与 Partial 类型见 `GENERAL_PYTHON_VALIDATION.md`，诊断数量不能当作解析失败数或覆盖率。

通用 def-use 另外限制实际访问工作量：单作用域 1024、单文件累计 8192 次。达到预算后保留已有事实并明确标记 unknown / truncated / Partial，包括被省略的作用域；下一文件重新获得独立预算。这是终止保护，不是完整路径分析保证。深层循环、继承与重导出 DAG 的独立验收测试不使用机器耗时阈值。

继承候选与重导出解析各有累计 4096 次工作预算，配合区分 visiting / completed 的安全 memo，不能只凭候选数或深度限制来声称成本有界。实际文件规模、动态特性和预算截断仍应通过 Analysis Issues 判断；本轮未增加项目总文件 / 总字节硬上限。

候选语句 / 表达式重复访问另有每文件 4096 次累计准入预算，与继承 / 重导出查询预算分开。耗尽后停止重复求值，只做一次线性语法扫描，为全文件调用保留已知候选并标记 unknown / truncated。预算约束的是候选求值，不限制 AST 读取、该线性扫描或整个项目总成本。
