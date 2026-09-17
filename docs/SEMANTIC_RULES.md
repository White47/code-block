# 语义规则规范

## 1. 规则层

规则插件按 Generic Python、Generic PyTorch、Transformer、Training 分层。Python Parser 不包含框架语义；每个规则输出 `role`、`confidence`、`evidence`、`sourceAnchors` 和可选的 `alternatives`。

## 2. 置信度

- Exact：静态证据能确认调用形式及接收者角色，例如已解析为 PyTorch loss / optimizer 的 `loss.backward()`、`optimizer.step()`。Exact 不表示调用在运行时一定执行或成功。
- Probable：有结构证据但仍有歧义，例如 Q/K/V 与 attention score 的自定义实现。
- Unresolved：无法可靠判断；保留真实代码名和通用结构。

仅看到 `.backward()`、`.step()` 等方法拼写，或接收者未解析时，不得把训练语义标成 Exact；可输出带证据的 Probable 候选或保持 Unresolved。保留用户确认的典型语义映射，但置信度由实际解析证据决定。

系统必须允许用户查看“为什么这样判断”。

## 3. PyTorch 模型规则

识别 `nn.Module`、`forward()`、Sequential、ModuleList、ModuleDict、`self.xxx = SomeModule(...)`，以及 Linear、Embedding、Conv1d/2d/3d、LayerNorm、BatchNorm、Dropout、ReLU/GELU/Sigmoid/Tanh/Softmax、MultiheadAttention、Transformer Encoder/Decoder 及其 Layer。自定义 `nn.Module` 至少恢复子模块、forward 顺序、分支、合并和调用。

ModuleList 中结构相同的实例可在 Visible Graph 折叠为 `×N`；这与 Shared 共享实例必须区分。

Phase 5 已实现 `ModelRule` 协议和 `PyTorchModelRule`。只有显式 `torch` / `torch.nn` 导入可追溯的名称才认作框架；项目内存在同名 `torch` 路径或别名被文件 / 函数局部绑定遮蔽时不继续认定，包括顶层条件、循环、`with` 中的绑定。规则只报告有限内建层、容器和 Module 基类，用户自定义类通过独立 Resolver 已证实的项目类继承链识别。`self` 子模块读取 `__init__` 中直接及常见条件 / 循环 / try 语句的赋值；有条件或重复执行风险时标 Probable，不能当作唯一 forward 目标。`forward` 的直接 `self.child(...)`、已知容器静态索引、动态索引和简单容器循环调用与已有 CallSite 关联；动态选择保留候选。Sequential 成员保留顺序与未知成员槽位；ModuleList 同构且成员来源不共享时记录折叠倍数，`range(常量)` 推导式倍数仍为 Probable，共享实例推导式不计作独立 ×N；ModuleDict 成员保留键名。未知构造器若在 forward 中被调用，则保留 Unresolved 候选，不冒充 PyTorch 层；动态或部分容器发 issue。`shared_with_id` 只记录明确的 `self.b = self.a`、链式赋值或容器中的同一对象别名，不把相同类的两次构造当共享。

## 4. 训练规则

| 证据 | 语义 |
|---|---|
| `model.train()` | Training Mode |
| `model.eval()` | Evaluation Mode |
| `model(x)` / `forward()` | Forward Pass |
| `criterion(pred, target)` | Loss Calculation |
| `optimizer.zero_grad()` | Clear Gradients |
| `loss.backward()` | Backward Pass |
| `optimizer.step()` | Parameter Update |
| `scheduler.step()` | LR Update |
| `torch.no_grad()` | Disable Gradient |

识别 epoch loop、dataloader / batch loop、validation、checkpoint、gradient clipping、autocast、GradScaler 时以简化语义呈现，并标注证据。

## 5. 控制流与数据

首版表达顺序、if True/False、条件摘要、折叠循环、LOOP_BACK、递归和可折叠 Call Cycle。普通临时变量默认隐藏；关键输入、target、prediction、loss、parameter、gradient 作为 DataNode 候选。

## 6. 禁止事项

规则不得执行用户代码、把 dynamic call 当作确定调用、把 Data Flow 当作 Gradient Flow、把重复结构当作共享权重，或在证据不足时生成论文式确定命名。
