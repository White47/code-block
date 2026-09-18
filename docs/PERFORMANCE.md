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
