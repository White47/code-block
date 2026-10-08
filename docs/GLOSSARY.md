# Glossary

| 术语 | 定义 |
|---|---|
| Code Graph | 源码事实层的节点与关系图 |
| Semantic Graph | 对代码结构进行规则语义解释后的组件图 |
| Visible Graph | 面向当前视图和交互的局部渲染结果 |
| SourceAnchor | 文件、行列范围组成的源码定位 |
| Stable ID | 不依赖随机值、尽量跨分析稳定的实体 ID |
| Exact | 有直接结构证据的推断 |
| Probable | 有证据但存在歧义的推断 |
| Unresolved | 无法可靠解析或命名的结果 |
| Definition | 类 / 函数 / 模块等声明实体 |
| Instance | 创建出的对象或模块实例 |
| Invocation | 一次调用 / 使用发生 |
| Data Flow | 数据从一个节点传向另一个节点 |
| Gradient Flow | 梯度反向传播关系 |
| EntryPoint | Train / Evaluate / Inference / Export 等执行入口 |
| Shared | 多处引用同一实例或权重 |
| Progressive Disclosure | 先整体、再组件、再内部、最后源码 |
| Analysis Health | 解析与推断完成度的可见统计 |
| GenericEntryCandidate | 具有识别类别、confidence、依据与源码锚点的普通入口结构候选，不证明实际运行 |
| FunctionFlow | 所选函数 / 方法词法作用域的有界静态控制与调用位置图，区别于训练 Execution |
| LexicalScope | lambda / comprehension 等非具名、非定义的结构作用域 owner |
| Callable candidate | 某调用位置有限的可能函数 / 方法目标，与名称定义归属分开 |
| May def-use | 某次读取可能依赖的定义发生点集合，不等于实际值流或值相等 |
| generic_data | 按需用于普通变量关系的 Data 事实标记，不自动进入 ML 关键数据切片 |
