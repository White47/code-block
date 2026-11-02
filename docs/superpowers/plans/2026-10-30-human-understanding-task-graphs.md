# H3 可读任务图实施计划

执行方式：AGENTS.md 自主队列。前置条件是 H2 修复与阶段门禁完成；不得跳过当前 H2 断点。本计划记录 architect GO，尚未实施。

**Goal:** 在 Overview 展示纵向任务流程和功能架构，让 nanoGPT 与普通 Python 项目首先解释功能、输入和结果，低层调用按需查看。
**Architecture:** 增加窄范围 AST-time 任务/产物语义事实并缓存；独立摘要投影消费 CFG、模型、训练和这些事实。复用现有 React Flow。摘要关系不写回 CodeGraph，不新增 ViewKind，不改变高级 Calls/FunctionFlow/Architecture 或源码桥接合同。
**Tech Stack:** 现有 Python AST、规则层、dataclass/白名单 JSON、React Flow、布局及编辑器；不加依赖。

依据：[总设计](../specs/2026-10-30-human-readable-understanding-design.md)、[nanoGPT 验收](../specs/2026-10-30-nanogpt-readability-acceptance.md)。H3 架构评审已完成；仅完成该评审不代表核心实现或可读性验收通过。

## H3A：窄语义事实与缓存

Core implementer 所有文件：新增 `src/codeviz/analysis/human_tasks.py`，修改 `analysis/project.py`、`persistence/codec.py`、`fingerprint.py`、`store.py`，以及对应事实/缓存/版本测试。

### 已核对的参数共享补充（architect GO）

真实 nanoGPT 的现有 ModelAnalysis 有5个模型定义、0个 shared_with_id，但 `model.py:138` 明确赋值 `self.transformer.wte.weight = self.lm_head.weight`。因此模块共享不能代替参数共享。
允许新增聚焦的 `analysis/model_parameter_sharing.py`，并在 `analysis/model.py` 组合；Parser/Resolver/CodeGraph 不变。

```python
@dataclass(frozen=True, slots=True)
class ParameterSharingFact:
    id: str
    model_definition_id: str
    owner_symbol_id: str
    assigned_instance_id: str
    assigned_attribute: str
    source_instance_id: str
    source_attribute: str
    source_anchor: SourceAnchor
    confidence: Confidence
```

`ModelAnalysis` 尾部追加默认 `parameter_sharings: tuple[ParameterSharingFact, ...] = ()`，H3统一升级缓存至6。
仅单目标 self 静态属性链赋值，去掉参数名后，两端必须唯一映射到同一模型中不同的已注册 ModuleInstance；允许参数表最小覆盖 Embedding.weight、Linear.weight/bias、已识别 LayerNorm.weight/bias。
必须排除明确禁用的参数（如 bias=False、elementwise_affine=False）；不能把 None 属性赋值解释为共享参数。
首版可保守限制为构造函数内无条件、无重复/覆盖写入的明确赋值；未证明的条件/动态/局部中转/cross-owner 情况报有锚点的 MODEL_PARAMETER_SHARING_UNRESOLVED，不猜 Shared。
置信度不得高于两端实例；ID由版本、模型/owner/实例/属性和完整锚点规范哈希。每个属性为允许的单个标识符，至多64 UTF-8字节，不保存赋值源码。
参数共享事实单独最多512条，超限显式报 MODEL_PARAMETER_SHARING_TRUNCATED；与 HumanTaskAnalysis 的512条预算分开校验。
codec/store验证模型、最内层owner、实例归属/不同端点、参数允许性、文件/owner范围、赋值来源与派生ID；坏引用或伪造锚点拒绝缓存，不得静默显示 Shared。
测试区分参数赋值与 `self.right = self.left` 模块别名，覆盖无赋值、禁用参数、动态属性、伪造缓存及冷暖一致。

```python
class StaticTaskKind(StrEnum):
    EVALUATION_HELPER = "evaluation_helper"
    LEARNING_RATE_UPDATE = "learning_rate_update"
    GRADIENT_ACCUMULATION = "gradient_accumulation"
    INPUT_ENCODING = "input_encoding"
    TOKEN_SAMPLING = "token_sampling"
    OUTPUT_DECODING = "output_decoding"
    RESIDUAL_MERGE = "residual_merge"
    LAST_TOKEN_SELECTION = "last_token_selection"

class ArtifactAccessKind(StrEnum):
    READ = "read"
    WRITE = "write"

class HumanEntryKind(StrEnum):
    DATA_PREPARATION = "data_preparation"
    GENERATION = "generation"

@dataclass(frozen=True, slots=True)
class StaticTaskFact:
    id: str
    owner_symbol_id: str
    kind: StaticTaskKind
    source_anchor: SourceAnchor
    control_node_id: str
    callsite_ids: tuple[str, ...]
    target_symbol_ids: tuple[str, ...]
    confidence: Confidence

@dataclass(frozen=True, slots=True)
class ArtifactAccessFact:
    id: str
    owner_symbol_id: str
    kind: ArtifactAccessKind
    artifact_key: str
    source_anchor: SourceAnchor
    control_node_id: str
    callsite_id: str | None
    confidence: Confidence

@dataclass(frozen=True, slots=True)
class HumanTaskEntryFact:
    id: str
    root_symbol_id: str
    kind: HumanEntryKind
    source_anchor: SourceAnchor
    evidence_fact_ids: tuple[str, ...]
    confidence: Confidence

@dataclass(frozen=True, slots=True)
class HumanTaskAnalysis:
    entries: tuple[HumanTaskEntryFact, ...] = ()
    tasks: tuple[StaticTaskFact, ...] = ()
    artifacts: tuple[ArtifactAccessFact, ...] = ()
    omitted_fact_count: int = 0
```

- [ ] 失败测试先行，固定 API/词法 binding/控制结构正反例；不得只匹配项目名、路径或 encode/decode 等函数名。
- [ ] 总计至多512条新事实，每组 callsite/target/evidence 引用至多8个；artifact_key 是安全规范化标识，最多160 UTF-8字节。动态路径仅 Probable，冲突 key 不连边；不能只凭同名文件把不同数据集相连。
- [ ] Evaluation helper 需要 eval/no_grad 和模型/损失证据；梯度累积需要同一 binding 参与循环界限与损失缩放/反向传播。
- [ ] Encode/decode 需要生成前后局部数据流，decode 结果还须流向已证明 stdout；sampling 需要循环内明确采样 API 与后续序列使用。
- [ ] 残差和末位置预测需要 AST 运算/切片结构与模型 invocation/data-path 证据。框架规则在分析/语义层组合，不写入 Python Parser。
- [ ] `ProjectAnalysis` 末尾添加默认空 `human_tasks`；ANALYSIS_VERSION 5→6，Parser/Semantic 版本只在实际合同改变时另行记录，不顺手修改。
- [ ] 严格 codec/store 校验 owner、control、callsite、target、anchor、派生 ID 与缺口；冷暖一致，无源码/完整docstring/AST副本，注释与视图状态独立。

## H3B：独立摘要投影

Core implementer 文件：新增 `src/codeviz/views/human_summary.py`，修改 `views/human_understanding.py`，新增 `tests/test_human_summary.py` 及直接消费者。

```python
class SummaryRelationKind(StrEnum):
    CONTROL_NEXT = "control_next"
    BRANCH_TRUE = "branch_true"
    BRANCH_FALSE = "branch_false"
    LOOP_BACK = "loop_back"
    CONTAINS = "contains"
    DATA_ARTIFACT = "data_artifact"
    STATIC_CALL = "static_call"
    MODEL_USE = "model_use"
    SHARED = "shared"

@dataclass(frozen=True, slots=True)
class TaskStep:
    id: str
    kind: Literal["task", "decision", "loop", "result"]
    title: str
    basis: str
    confidence: Confidence
    owner_symbol_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    code_node_ids: tuple[str, ...]
    source_anchors: tuple[SourceAnchor, ...]
    source_location_count: int

@dataclass(frozen=True, slots=True)
class SummaryRelation:
    source: str
    target: str
    kind: SummaryRelationKind
    confidence: Confidence
    evidence_ids: tuple[str, ...]
    code_node_ids: tuple[str, ...]
    source_anchors: tuple[SourceAnchor, ...]
    sharing_kind: Literal["module_instance", "parameter"] | None = None
    sharing_attributes: tuple[str, str] | None = None

@dataclass(frozen=True, slots=True)
class TaskFlow:
    id: str
    entry_symbol_id: str
    title: str
    steps: tuple[TaskStep, ...]
    relations: tuple[SummaryRelation, ...]
    incomplete: bool
    omitted_count: int

@dataclass(frozen=True, slots=True)
class FunctionalArchitecture:
    areas: tuple[TaskStep, ...]
    relations: tuple[SummaryRelation, ...]
    incomplete: bool
    omitted_count: int
```

- [ ] `HumanUnderstanding` 仅追加默认字段 `architecture: FunctionalArchitecture | None = None`、`flows: tuple[TaskFlow, ...] = ()`、`default_flow_id: str | None = None`，保留 H2 兼容。
- [ ] FunctionalArchitecture 末尾默认 id=project-architecture；HumanUnderstanding 末尾增加默认 omitted_flow_count=0，入口流程最多32个，超额明确计数，不把所有流程一次渲染。主要训练/生成/数据入口及模型流程优先，排序确定且不按调用数量制造重要性。
- [ ] TaskStep 的 owner_symbol_ids 都必须是当前索引中的真实 scope；首个为可用的首选实现目标（已解析的本地被调函数/模型定义），不确定时回到真实调用者 scope，不把 external/unresolved/摘要 ID 当实现目标。code_node_ids 仅放实际 CodeGraph 节点；ModelInstance/Facts 引用留在 evidence_ids，不能伪称 IR 节点。
- [ ] 每流程至多16步骤/32关系，架构至多12区域/24关系；每节点/边必须有持久化来源证据。控制边只来自 CFG/control predecessor/branch/loop，CALLS 只识别目标及 STATIC_CALL 架构关系。
- [ ] 同一目标的不同调用 occurrence 不合并为执行次数；`A→B→A` 保留三个位置。循环/递归不能无限展开，所有折叠计数是源码位置数。
- [ ] 统计样例默认恰为读取、统计、展示三个任务；sum/len/print 留在实现详情。hello-world 保留输出结果；自定义 print 按普通目标任务处理。
- [ ] nanoGPT 训练显示迭代而非 epoch；评估/条件检查点在当前 forward 之前，累积循环、scaler更新、随后清梯度遵守真实 predecessor。保持 scaler 的 Probable，不按通用模板重排。
- [ ] 模型 N 由配置决定，不制造固定次数；shared_with_id 生成 SHARED；Flash/manual attention 为分支。生成 stdout 保留为最终结果。
- [ ] 参数事实的 SHARED 使用 sharing_kind=parameter，并携带两端参数名 sharing_attributes；模块别名只用 module_instance 且不携带参数名。UI分别显示“共享参数 weight”和“共享模块实例”，不能混称权重共享。共享端点优先进入预算，端点被省略时不产生悬空边。
- [ ] 数据准备、训练、生成是独立入口，产物依赖用 DATA_ARTIFACT，不串成一次运行。多入口提供选择，不武断认定唯一主入口。

## H3C：纵向画布与真实项目

主代理文件：`ui/presenter.py`、`ui/web_contract.py`、`frontend/src/types.ts`；新增 `app/components/FunctionalArchitecture.tsx`、`TaskFlow.tsx`；修改 `Overview.tsx`、`App.tsx`、i18n/样式与直接测试。

### 已冻结的布局与渲染补充（architect GO）

复用 GraphEditor/canvasLayout/canvasRouting，新增纯前端 summaryGraphAdapter。只渲染有据摘要关系，不查询或修改 native Graph IR。GraphEditor 仅增加默认不影响旧行为的 title、panOnScroll、edgePresentation 可选呈现参数；摘要图初次按宽度显示、顶部开始，旧图保留原相机行为。适应按钮仍可查看全图。
摘要选择只更新本地详情；源码动作传真实 SourceAnchor，实现动作传真实 owner symbol ID。摘要 ID 不得进入旧 source/detail/graph/move API。
概览的首张图优先显示服务端 `default_flow_id` 对应任务流程；功能架构可切换查看。没有流程时才直接显示功能架构，避免普通小程序默认落入符号分工图。
摘要图同层至多3列，超额分行且后续层顺延，保留所有真实关系；避免宽层导致初始缩放过小，长图以纵向滚动浏览。
按画布实际宽度收敛列数：小于600像素用1列，600–999像素用2列，1000像素起至多3列；布局改变重新适应该画布，已保存的手动位置仍优先。

新增独立 summary layout bridge，主代理所有文件可新增 `ui/summary_layout.py`，修改 controller/web_bridge/web_contract/types 及直接测试：

```text
get_summary_layout(context)
move_summary_node(context, layout_token, node_id, position)
save_summary_viewport(context, layout_token, viewport)
context = {kind: architecture | task_flow, id: 当前DTO中真实context ID}
返回 = {context, layoutToken, positions: 当前摘要节点ID→Point, viewport: {zoom,pan}}
```

FunctionalArchitecture 增加稳定的 id（首版固定为 project-architecture，仍隔离于项目缓存）；TaskFlow 使用已有 id。三个方法都从当前分析重建摘要并精确校验 context/node，不接受前端提供合法节点集合。
token 是当前项目作用域、分析源指纹及 context/节点几何标签/关系（含共享种类与属性）的规范哈希；只是过期写保护，不作为持久化键。写 token 不匹配则拒绝且状态不变。
坐标必须有限，摘要位置/pan 绝对值至多1,000,000，zoom沿用0.25–2.5；context/node长度至多512字符。
持久位置放在既有 positions_by_view[Overview] 的服务端生成键 `summary:v1:{context_digest}:{node_digest}`，不同context的同名节点不冲突。读取只映射当前存在节点；写时只清理失效 summary:v1 键，不动旧native键；总计至多1024，优先保留当前context。
viewport复用 viewport_by_view[Overview]，明确是最后一个活动摘要图的共享相机；节点位置仍逐图保存，不承诺逐context相机。不得滥用filters、伪节点或随端口变化的localStorage。旧ViewState格式/version不变。
布局响应追加可选 `viewportSaved` 布尔值，明确区分默认相机与用户保存的相机；无保存值时按宽度从顶部显示，避免把默认 zoom/pan 当成已恢复布局。
覆盖context隔离、合法读写、foreign/stale拒绝无状态变更、重分析后清理、旧状态往返及源码/实现回调；这是H3阶段内验证，仍只在H3A/B/C齐备后跑一次全量门禁。

- [ ] 失败测试后增加可选 JSON 字段，保持 H2/source bridge 兼容。卡片、图节点的源码动作走真实 anchor/owner，不把摘要 ID 冒充符号 ID。
- [ ] 复用 React Flow 做从上到下的任务图/架构图；同层可并列，条件/循环/产物/共享关系有明确区别。多入口选择与图例清楚，详细调用仍可单独查看。
- [ ] 小样例、hello-world、自定义print、条件/循环/递归、交错调用、nanoGPT风格训练/模型/生成固定夹具做定向验证；只读 `E:\tmp\nanoGPT-master` 做真实静态验收，不执行项目或调用API。
- [ ] 浏览器检查布局、选择/下钻/源码；自动测试不替代真实可读性与最后原生桌面验证。

## 单一 H3 阶段末门禁

H3A/B/C 内只跑受影响模块与直接消费者；全部完成后才做一次独立复审及全量后端/Ruff/双平台mypy、全量前端test/lint/typecheck/build。必要修复后复用未变证据，不例行二次独立复审。进度最多5行，断点写当前任务/最后命令及结果/剩余/下一任务。

后续 H4：当前有效 AI 结果增强同一图与说明，维持授权/预算/版本和来源门禁；不能用单条 repair 覆盖完整静态图，不自动发送付费请求。最后再打包并做原生交付验收。

## H3 单次复审补充：数据产物与准备入口

架构结论：第三项 GO，按以下最小修复交给 `core_implementer`；这不是第二次整 diff 复审。修复仅覆盖持久化 artifact facts、准备入口和其 H3 投影，不启动 H4。

### 已确认事实

- `human_tasks._artifact_kind` 当前只按末级调用名识别 `open/memmap/read_text/write_text`；`build_human_task_analysis` 又把每个拥有任意 artifact fact 的 owner 都升成 `DATA_PREPARATION`。因此只有 READ 的 `train.get_batch` 被误标为数据准备入口。
- nanoGPT `train.py:get_batch` 通过解析为 `numpy.memmap` 的调用，以 `mode='r'` 读取由 `data_dir = os.path.join('data', dataset)` 决定的 `train.bin/val.bin`。
- `data/shakespeare/prepare.py` 与 `data/shakespeare_char/prepare.py` 先由 `numpy.array` 构造数组，再调用 receiver 的 `tofile(...)` 写出 `train.bin/val.bin`；当前未识别该写端。`data/openwebtext/prepare.py` 使用 `numpy.memmap(..., mode='w+')`，但路径先保存在局部 `filename`，现有 key 提取不能据此证明跨 owner 的规范路径。
- nanoGPT 同时有默认 `dataset='openwebtext'` 和可覆盖为 `shakespeare`、`shakespeare_char` 的配置。静态分析没有实际启动参数，不能把默认值或相同 basename 当作某次训练选择的证明。
- 动态 artifact key 含 owner identity，跨 owner 必然不同；`human_summary._build_architecture` 明确排除 `dynamic:` key。当前 nanoGPT 应为 0 条 `DATA_ARTIFACT`，不能为了验收改成猜测连线。

### 修复合同

1. `DATA_PREPARATION` 入口只由同一 owner 的一个或多个 `ArtifactAccessKind.WRITE` facts 建立；`evidence_fact_ids` 和入口 confidence 只取这些写 facts。READ-only owner 永不成为准备入口。读事实仍保留给输入说明和未来有证据的产物关系。
2. artifact API 分类不得继续只看末级名称。先由 callsite 的唯一 `CALLS` target 取得现有 resolver 产生的规范 `qualified_name`；只接受明确 allowlist（至少 `builtins.open`、`numpy.memmap`）及其解析置信度。项目自定义 `open/memmap/tofile`、歧义或 unresolved target 不产生 artifact fact。
3. `numpy.ndarray.tofile` 不按裸 `.tofile` 名称识别。只接受同一词法 owner 内的简单 receiver binding，且其唯一可达/无冲突定义来自规范解析为 `numpy.array` 的构造调用；任何重绑定、分支歧义、未知 producer 或自定义同名方法均放弃。该事实最多为 `Probable`，因为外部实现和返回类型未被检查；常量路径不能把 API 身份升级为 `Exact`。
4. 继续使用现有 `ArtifactAccessFact`、`HumanTaskEntryFact`、`SummaryRelationKind.DATA_ARTIFACT`；不新增字段、关系种类、Graph IR 边或 public DTO。fact confidence 取“callee/receiver 身份证明”和“path key 证明”的较弱者。
5. `DATA_ARTIFACT` 仍只允许一个 writer 与 reader 共享完全相同、非动态的规范 persisted key。动态 key 保持 owner 隔离并继续禁止投影；basename、默认 dataset、源码行邻近或目录名均不能用于连边。
6. 当同一分析中同时存在动态 WRITE 与动态 READ、但不能形成规范 key 对应时，架构摘要必须保持 0 条关系并计入有界 incomplete/omitted gap；该 gap 只表示“可能的产物对应无法证明”，不表示两端匹配。
7. `human_tasks` 已进入 analysis cache，提取语义变化要求 `ANALYSIS_VERSION` 从 6 升到 7，使旧的 READ-only 准备入口不能从 warm cache 继续出现。Parser/Semantic 版本、ViewState 和其他缓存合同不变。

### 验收边界

- 失败优先测试覆盖：READ-only `numpy.memmap` owner 无准备入口；由 `numpy.array` 唯一 producer 支持的 `tofile` 产生 WRITE fact 和准备入口；自定义/歧义/重绑定 lookalike 不产生事实。
- 常量规范 key 的单 writer→reader 仍可产生有来源的 `DATA_ARTIFACT`；多 writer、动态 key 或只有 basename 相同均不产生关系。
- nanoGPT 静态验收必须看到：`train.get_batch` 不再是 `DATA_PREPARATION`；有据的 Shakespeare `tofile` owner 是准备入口；`DATA_ARTIFACT == 0` 且摘要明确 partial/incomplete。不得声称训练实际选择了 Shakespeare 或 openwebtext。
- 冷分析与 v7 cache 恢复后的 facts、entries、0 条 nanoGPT artifact relation 和 gap 状态一致；不执行 nanoGPT，不调用 API。
- 这项修复不删除或放松现有源码安全/可读性验收。H3 最小可交付阶段的静态能力门禁是：常量规范 key 夹具能以真实 writer/reader facts 生成有来源关系；动态 key 不连边并明确标为 partial/incomplete。满足该能力门禁后可以关闭 H3 静态能力阶段并进入 H4，不要求静态分析证明由外部执行参数决定的实际 dataset。
- nanoGPT 的具体数据准备→训练路径仍必须记录为 remaining/partial：不得把它记作已证明或 passed，也不得声称已完整理解全项目；但该已知静态边界不是永久阻塞 H4 的硬门禁，更不能用 basename、默认配置或猜测关系消除缺口。

### 路由与风险

该修复属于 Symbol Resolution 消费、持久化语义事实和 Source Traceability 边界，必须由 `core_implementer` 完成。最主要风险是把局部变量的 `.tofile` 误当 `numpy.ndarray`；因此 receiver provenance 不能退化为名称启发。次要风险是旧缓存保留误分类；必须通过 v7 失效而不是在投影层掩盖。

## H3 discovery 前提修正：`data/` 与 `datasets/` 中的 Python 源码

架构结论：GO，纳入同一 H3 core 修复，不开启新 review。目录名 `data` 或 `datasets` 不能证明其内容不是项目源码；nanoGPT 根项目下的真实 prepare 脚本不能以子项目 probe 代替整项目覆盖。

### 最小合同

1. 仅从 `analysis.discovery._IGNORED_DIRECTORIES` 移除名称 `data`、`datasets`；普通 discovery 与 strict discovery 共享该集合，必须一致生效。
2. discovery 仍只返回常规、非符号链接的 `.py` 文件。`.bin`、`.npy`、`.npz`、`.pt`、checkpoint、权重和其他二进制数据不会因进入目录而被解析或保存到 analysis cache。
3. 其余默认剪枝保持不变，包括 `.git`、`.venv/venv/env`、`node_modules`、`__pycache__`、`build/dist`、`.tox`、`.mypy_cache/.pytest_cache/.ruff_cache`、`site-packages`、`weights/checkpoints/runs/wandb/work`；不借本修复扩大依赖或生成物扫描。
4. 项目级 Analysis Settings 的显式 `ignorePaths` 继续使用现有规范化字面前缀合同。用户可明确填写 `data`、`datasets` 或更窄子目录；默认行为变化不覆盖用户选择。
5. symlink/junction 防护、strict discovery 的 entries/directories/files 上限、路径规范化、排序和失败即无 partial inventory 的安全合同均不变。
6. H3 artifact 语义不变：纳入 prepare `.py` 只提供真实 WRITE/准备入口证据；训练端动态 dataset 路径仍不与写端连 `DATA_ARTIFACT`，继续标 partial/incomplete，不能从目录结构推断实际配置。

### CacheVersion 决定

`ANALYSIS_VERSION` 保持本 H3 已批准的 7，不再升到 8。理由：v7 尚未发布；且 `fingerprint_project` 与分析共同调用 `discover_python_files`，旧实现遗漏 `data/**/*.py` 的 cache fingerprint 文件集合会与新发现集合不同并自动失效。项目若没有新增纳入的 `.py`，则分析输入和结果不因本修正改变。Parser/Semantic/ViewState 版本不变。

### 验收与下游影响

- discovery 与 strict discovery 的失败优先测试均须证明 `data/prepare.py`、`datasets/custom.py` 被发现，而同目录下二进制文件不在 inventory；显式忽略 `data` 后仍完整排除该前缀。
- 既有默认忽略目录、符号链接拒绝、strict budgets 和 fingerprint source-change 测试保持，不得删除或弱化。
- nanoGPT 根项目 probe 必须从实际 discovered files 中看到 `data/shakespeare/prepare.py`、`data/shakespeare_char/prepare.py`、`data/openwebtext/prepare.py`，并基于同一次根项目分析产生有据的 prepare WRITE/entry；不得用单独打开子目录的结果冒充。
- nanoGPT 根项目仍预期 0 条猜测性动态 dataset `DATA_ARTIFACT`；该 remaining/partial 不阻塞 H4，但不能记录为完整数据路径 passed 或全项目完整理解。
- source inventory、搜索、结构视图和 AI 授权候选会新增这些 `.py`；现有来源校验、疑似秘密阻断、显式授权和预算继续适用，不因 discovery 纳入而自动上传源码。

该修正改变 source discovery/指纹输入与 Source Traceability，仍由 `core_implementer` 实施；不需要新的 domain dataclass、bridge、UI 或依赖。

## H3 收尾：模型实例呈现名称

架构结论：GO，作为 H3 最终 snapshot 的 additive view-contract 修正纳入现有门禁，不开启新 review。当前 `TaskStep.owner_symbol_ids` 是可信实现导航 scope，不一定是人可读的实例名；不得为了显示 `wte/wpe/lm_head` 而替换或伪造 owner identity。

### 最小合同

1. `TaskStep` 末尾追加默认字段 `code_name: str | None = None`。非 `None` 时必须是非空字符串；它仅是呈现标签，不是 symbol、CodeGraph node、native target、证据或 source identity。
2. 模型实例生成的 architecture area 与 model-flow step 使用已有 `ModuleInstance.name` 填充 `code_name`。其他 step 默认 `None`，不从标题、owner 名或层类型生成新名称。
3. `_model_area_title` 对所有 `Embedding` 使用中性标题 `Look up embedding vectors`。不能仅凭层类型断言输入一定是 token；`wte` 与 `wpe` 由 `code_name` 区分，而不是分别猜测 token/position 业务语义。
   标准层用途标签只适用于已证明的框架层；具有本地 `target_definition_id` 的自定义模块保留真实组件名与通用结构，不能因类名含 `Attention` 或尾名恰为 `Embedding` 就套用 PyTorch 用途。后续有结构证据的语义或有效 AI 说明可再补充。
4. `_summary_step_payload` 仅在 `code_name is not None` 时输出可选 `codeName` 字符串；前端 `SummaryStep` 增加 `codeName?: string`。该字段为向后兼容的 additive JSON，不升级 bridge schema。
5. `summaryGraphAdapter` 的节点 `codeName` 优先使用 `step.codeName`，其次才沿用 owner-symbol 名称映射和 source path fallback。`displayName/title`、summary node ID、owner IDs、code-node IDs、evidence 和 anchors 均不改变。
6. `SummaryCanvas` 的源码按钮继续只使用 `sourceAnchors`，实现/双击导航继续只使用 `ownerSymbolIds[0]`。任何 `codeName` 值，包括恰好等于 native ID 的字符串，都严禁传给 `openView`、source bridge、summary-layout move/save 或旧 graph API。
7. `TaskStep` 是从 analysis 构建的 view DTO，不写入 analysis cache，因此不改变 `ANALYSIS_VERSION`。summary layout token 已对 architecture/flow 使用 `asdict`，会自然纳入 `code_name` 的显示几何变化；无需新增 layout schema 或手工 token 字段。

### 测试与验收

- `tests/test_human_summary.py`：构造同一 owner 下两个 embedding 实例 `wte/wpe`，断言 owner IDs 可相同而 `code_name` 分别为 `wte`、`wpe`，标题均为 `Look up embedding vectors`；模型 root/普通 task 的默认值仍为 `None`。
- `tests/test_ui_web_contract.py`：有名称的 step 输出 `codeName`，无名称 step 不输出该 key；owner/evidence/code-node/source 字段保持原值。
- `frontend/src/app/components/summaryGraphAdapter.test.ts`：`step.codeName` 覆盖相同 owner fallback，使 `wte/wpe` 两节点可区分；缺字段时保持旧 fallback。
- `frontend/src/app/components/SummaryCanvas.test.tsx`：选择/双击带 `codeName` 的 step 后，源码仍打开真实 anchor，实现仍调用 `FunctionFlow` + owner ID；断言 `codeName` 从未作为导航参数。
- 本修正可随尚未运行的前端第三批与后端 full gate 一次验证；不单独重跑已通过且未受影响的门禁，也不新增复审。

文件范围限于 `src/codeviz/views/human_summary.py`、`src/codeviz/ui/web_contract.py`、`frontend/src/types.ts`、`frontend/src/app/components/summaryGraphAdapter.ts` 及上述直接测试。无 Graph IR、source handles、analysis cache、bridge method、依赖或生产源码修改范围扩张。
