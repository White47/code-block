# 真实 PyTorch 仓库交叉验证（2026-09-19）

本轮把公开仓库浅克隆到产品仓库之外，仅用 CodeViz 读取 `.py` 源码进行静态分析；未安装目标项目依赖、导入目标模块、运行训练脚本、下载权重或把第三方源码复制进产品仓库。以下数字来自本机 Windows / Python 3.12 的单次或少量复测，不是跨机器性能承诺。

| 仓库与固定提交 | 分析范围 | 已解析 Python 文件 | 模型 / 入口 | Analysis Issues | 分析约耗时 |
| --- | --- | ---: | ---: | ---: | ---: |
| [karpathy/nanoGPT](https://github.com/karpathy/nanoGPT/tree/3adf61e154c3fe3fca428ad6bc3818b27a3b8291) `3adf61e`（MIT） | 全仓库 | 12 / 12 | 5 / 1 | 289 | 0.1–0.2 秒 |
| [pytorch/examples](https://github.com/pytorch/examples/tree/acc295dc7b90714f1bf47f06004fc19a7fe235c4) `acc295d`（BSD-3-Clause） | `mnist/` | 1 / 1 | 1 / 2 | 70 | 约 0.05 秒 |
| 同一 `pytorch/examples` 提交 | 全仓库 | 93 / 93 | 68 / 39 | 3314 | 约 2.2–2.6 秒 |
| [ultralytics/yolov5](https://github.com/ultralytics/yolov5/tree/402e17ddf820996f51a191cbb798376e1144f069) `402e17d`（AGPL-3.0） | 全仓库 | 50 / 50 | 42 / 18 | 5373 | 约 3.0–3.4 秒 |

三种项目类型分别覆盖 Transformer 语言模型、PyTorch 多任务示例（含 MNIST CNN）和视觉检测 / 分类项目；四档范围用于交叉比较复杂度，不把文件数量等同于代码复杂度。每个模型和入口均请求单独局部图，共 176 张；每个范围对 `loss`、`forward`、`反向传播` 的前 10 条结果尝试 Search → Reveal，共 82 次；对这些视图与搜索涉及的 656 个去重源码位置，使用应用的只读源码查看路径检查行号，均未报错。默认单图没有超过 100 节点。nanoGPT 还用真实 Tk 桌面控件走通 GPT → `transformer` → `h` → Block、训练 BatchLoop → Backward → Source、搜索 `loss` → Data Flow → Split Source。

## 此轮发现并修复

- nanoGPT 使用 `nn.ModuleDict(dict(wte=..., h=...))`。先前只识别字典字面量，现只在 `dict` 未被局部 / 模块绑定遮蔽时读取具名参数；动态展开仍保留不确定性。
- `ModuleList([Block(...) for _ in range(config.n_layer)])` 的次数不可静态确定。现保留一个带源码的 Probable Block 模板，`repeat_count=None` 且仍发 `MODEL_CONTAINER_DYNAMIC`；不伪称为 `×N` 或已执行 N 次。
- nanoGPT 的主循环直接位于 `train.py` 顶层。现仅对名称暗示训练 / 评估等用途且确有静态训练事件的模块建立入口；顶层 `while` 同时含已追踪模型调用与梯度清理时，给 Probable BatchLoop，不把仅定义未调用的嵌套函数当作循环行为。
- `GradScaler.scale(loss).backward()` 与 `GradScaler.step(optimizer)` 现仅在 scaler 构造器和 loss / optimizer 接收者角色可追踪时映射为 Probable Backward / Parameter Update。缩放器可能跳过实际更新，绝不标 Exact。
- nanoGPT 的 GPT 类与顶层训练入口分别跨 213 / 336 行，原 Inspector 会把整段源码和大量内部节点 ID 放进侧栏。现源码预览与关系各最多 12 行 / 条，关系显示可见节点名称，剩余数量显式提示；Open Source 仍打开完整文件。

回归测试在 `tests/test_model.py`、`tests/test_training.py` 和 `tests/test_ui_tkapp.py`；测试先因这些行为缺失或误报而失败，修复后通过。外部仓库不作为 CI 必需输入，因此质量工作流仍可离线运行。

## 仍需处理

- 较大仓库的逐引用 Analysis Issues 数量很高，主要是动态属性、动态调用和多目标解析，而非文件解析失败。当前 Problems 平铺每条 issue，Overview 直接显示总数；下一轮宜按种类 / 文件汇总并提供筛选，同时保留每条源码事实，不应删除或悄悄降级诊断。
- YOLOv5 的部分 `Export` / `Test` 命名入口没有可识别训练事件，仍会显示空流程。需将“入口已发现、流程未解析”明确呈现，而不能让用户误认为不存在代码。
- 这些是技术路径与局部性能验证，不替代首次使用者的理解度观察；复杂 DDP、FSDP、动态构图、真实 tensor shape、运行时 autograd 仍不在 MVP 保证内。

复现时使用表中固定提交，把克隆置于产品仓库外，调用 `analyze_project(Path(...))` / `build_visible_graph(...)`，或在桌面应用中选择目录并显式 Analyze。不要运行被分析仓库的训练命令。当前本机克隆保留在 `C:\Users\16670\Documents\Codex\*-validation-20260919`，便于继续核验；这些目录不是产品仓库的一部分。
