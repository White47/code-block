# H4 面向人的 AI 理解增强实施计划

> 执行过程服从仓库 `AGENTS.md`：不加载 `subagent-driven-development` 或 `executing-plans`，按失败优先测试实施；本计划编写不触发复审。H3 尚未关闭时不得抢改其在途文件。

**目标：** 把当前有效、已验证的项目级 AI 结果作为 H3 功能架构与任务流的可选说明层，增强同一张图的节点说明和既有关系说明；静态节点、关系、布局和源码保持唯一事实基底。

**架构：** 仅在前端 assistant/controller 层把 `effectiveProjectUnderstanding(...)` 投影为有界 annotation。投影只用现有 `ProjectUnderstanding` revision、七段 `AssistantAnswer`、已验证 `relationFacts`，以及 H3 节点已有的真实 ID；不能精确对齐的 claim 留在项目级折叠区。所有引用继续通过 `WorkspaceNavigation.resolve(...)` 和后端 evidence gate，前端不制造 anchor 或直接打开 AI 声称的源码。

**技术栈：** 现有 React、TypeScript、Vitest、Testing Library；无新依赖、无新 native bridge、无真实 API 请求。

---

## GO 与不可变边界

- GO：采用纯前端 `HumanUnderstandingEnhancement` 投影；不修改 Python analysis/controller、Graph IR、H3 DTO、缓存、源码文档或 provider 协议。
- `effectiveProjectUnderstanding` 是唯一入口。项目、analysis generation、配置、授权、会话 owner/revision 或 stale 校验任一失效时，增强层必须为空。
- AI 永不新增、删除、重排或改名 H3 节点和关系；开关增强层前后，传给 `SummaryCanvas` 的 `steps`、`relations`、summary layout context/token 都相同。稀疏 repair 最多减少 AI annotation，不能覆盖静态图。
- 不按 claim 顺序、文字相似、符号名、文件名或 operation 数量猜节点。未精确匹配的 claim 只作为项目级 AI 推断/未知项展示。
- 不再把七段回答默认渲染成独立 AI 架构。完整回答保留在 H3 图下方的折叠详情；节点命中的说明显示在现有节点详情中。
- 不自动发送请求、不提高预算、不调用真实 API、不执行或修改被分析项目。

## 精确投影合同

新增前端内部类型，不进入 bridge/public DTO：

```ts
type AiAnnotation = {
  itemId: string;
  section: AssistantAnswer['sections'][number]['kind'];
  text: string;
  basis: 'ai_inference' | 'unknown';
  evidenceIds: string[];
};

type HumanUnderstandingEnhancement = {
  artifactId: string;
  revision: number;
  incomplete: boolean;
  coverage: ProjectUnderstanding['coverage'];
  coverageGaps: string[];
  byContext: Record<string, {
    nodeAnnotations: Record<string, AiAnnotation[]>;
    relationAnnotations: Record<string, AiAnnotation[]>;
    omittedCount: number;
  }>;
  projectAnnotations: AiAnnotation[];
  omittedProjectCount: number;
};
```

投影规则固定如下：

1. 对每个 architecture/task-flow context 独立建立 alias 表。一个静态节点的 aliases 仅为 `SummaryStep.id`、`ownerSymbolIds`、`codeNodeIds`；同一 context 内 alias 指向多个节点即为歧义，不可匹配。
2. AI `relationFact` 只有在其 `evidenceId` 存在于当前 answer evidence、confidence 为 `Exact` 或 `Probable`、source/target 各自唯一命中静态节点，且 H3 已存在同方向关系时，才能注释该既有关系。
3. category 兼容表：`control` 仅支持 `control_next/branch_true/branch_false/loop_back`；`data` 仅支持 `data_artifact/shared`；`structure` 仅支持 `contains/shared`；`call` 仅支持 `static_call/model_use`。不兼容或反向关系不附着。
4. claim 仅当 `relationEvidenceId` 或 `evidenceIds` 引用了上述已接受 relationFact 时，才附着到该关系及其端点节点；其余 claim 进入 `projectAnnotations`。`unknown` 可显示但不能成为关系支持。
5. annotation 去重键为 `itemId`；保留原 claim 文本，不改写成静态事实。每项显示“AI 推断”或“AI 未知”、artifact revision 和 evidence 数量。
6. evidence 按钮保留 `itemId/evidenceId/action`，只调用现有 `navigateUnderstanding` → `WorkspaceNavigation.resolve`。不得从 label、relation endpoint 或 claim 文本合成 `SourceAnchor`、graph root 或 owner ID。

实现口径：`unknown` claim 仅保留为项目级说明，不充当节点/关系绑定依据；context 的32条上限按唯一 itemId 计数。内部投影记录每个 context 的 `omittedCount` 和项目级 `omittedProjectCount`，不进入任何 native/public DTO。关系索引用现有关系的种类、端点、共享属性和来源引用构成稳定键，不以数组下标冒充源码身份。

## 最小文件范围

- 新增 `frontend/src/app/assistant/human-understanding-overlay.ts`：纯投影、精确 ID/关系匹配、上限与去重。
- 新增 `frontend/src/app/assistant/human-understanding-overlay.test.ts`：投影合同单测。
- 修改 `frontend/src/app/components/UnderstandingGraphs.tsx`、`SummaryCanvas.tsx`、`Overview.tsx`：把增强层注入当前 H3 context，并在现有选择详情/折叠区展示。
- 修改 `frontend/src/app/App.tsx`：只向 Overview/H3 传当前有效模型、导航及追问回调；停止渲染独立 `ProjectUnderstandingPanel`/Overview child summary。
- 修改 `frontend/src/app/components/SummaryCanvas.test.tsx`、`frontend/src/app/ProjectUnderstandingIntegration.test.tsx`：组件与整合验收。
- 按需修改 `frontend/src/app/i18n.ts`、`frontend/src/styles.css`：AI 来源、revision、覆盖与折叠详情标签；不新增样式系统。

## Task 1：实现可信、无猜测的 AI→H3 投影

**文件：**

- Create: `frontend/src/app/assistant/human-understanding-overlay.ts`
- Create: `frontend/src/app/assistant/human-understanding-overlay.test.ts`

- [x] 先写 `projects only exact current relation evidence onto existing summary contexts`：构造两个静态节点、一个既有 control relation、一个当前 `EffectiveUnderstanding`；断言精确 endpoint + control category 的 claim 只注释既有关系和两个端点，且保留 `itemId/evidenceIds/basis`。
- [x] 写同一测试中的负例：名称相同但 ID 不同、歧义 alias、反向关系、category 不兼容、`Unresolved`、缺失 evidence、无 relation evidence 的 claim 均不得附着节点/关系，只进入项目级 annotation。
- [x] 写 `sparse repair cannot replace static summary`：空或单 claim artifact 产生零或少量 annotation，不返回任何替代 `steps/relations` 字段。
- [x] 实现纯函数 `projectHumanUnderstandingEnhancement(understanding, model)`；按 context 最多保留 32 个 annotation、项目级最多 64 个，超出只计入内部 omitted count，不扩张 UI。
- [x] 运行：`cd frontend; npm test -- --run src/app/assistant/human-understanding-overlay.test.ts`。预期该文件全部通过，且测试不安装依赖、不访问网络。

## Task 2：在同一 H3 图和节点详情中呈现增强

**文件：**

- Modify: `frontend/src/app/components/UnderstandingGraphs.tsx`
- Modify: `frontend/src/app/components/SummaryCanvas.tsx`
- Modify: `frontend/src/app/components/Overview.tsx`
- Modify: `frontend/src/app/components/SummaryCanvas.test.tsx`
- Modify: `frontend/src/app/i18n.ts`
- Modify: `frontend/src/styles.css`

- [x] 扩展组件内部 props，传入可选 `model/enhancement/navigate/onAsk/disabled`；不修改 `SummaryStep`、`SummaryRelation` 或 native bridge 类型。
- [x] 保持 `SummaryCanvas` session identity 只由 project/context/静态 steps/relations 决定，AI artifact/revision 变化不得 remount canvas、丢失选择或触发 summary-layout 写入。
- [x] 在现有选中节点详情中显示该节点的有界 annotation；每项明确标记 AI 推断/未知与 `v{revision}`。有可信 evidence 时按钮调用 `navigate(itemId, evidenceId, action)`；无 evidence 时不显示源码/图按钮。
- [x] 在同一图下加入默认折叠的“AI 完整说明与覆盖”区域，显示 project annotations、coverage 数量、`incomplete/coverageGaps`；不创建第二张图，不把 claim 顺序画成边。
- [x] 扩展 `SummaryCanvas.test.tsx`：开关/更新 annotation 前后 GraphEditor node/edge 输入相同；选中节点显示 AI 标记；点击引用只调用给定导航回调且参数保持原 ID；name-only claim 不出现在节点详情；稀疏 repair 后静态节点仍存在。
- [x] 运行：`cd frontend; npm test -- --run src/app/components/SummaryCanvas.test.tsx`。预期全部通过。

## Task 3：接入当前 artifact 生命周期并移除重复 AI 架构呈现

**文件：**

- Modify: `frontend/src/app/App.tsx`
- Modify: `frontend/src/app/ProjectUnderstandingIntegration.test.tsx`

- [x] `App` 仅把 `enhanced ? projectUnderstanding : null` 传给 Overview/H3；静态模式只隐藏 annotation，不重新查询、替换或改变 H3 图。移除 Overview 的独立 `ProjectUnderstandingSummary` child 和 native Architecture/ProjectStructure 上的 `ProjectUnderstandingPanel` runtime 渲染。
- [x] 继续复用 `understandingKey = artifactId:revision`、`understandingContextAvailable`、`currentWorkspaceNavigation` 和 `WorkspaceNavigation.resolve`；artifact 更新、切项目、analysis/config/consent/conversation revision 变化或 stale message 时立即撤下增强层并使旧回调失效。
- [x] 更新整合测试：显式分析完成后 AI 内容出现在 Overview 的 H3 节点/折叠说明中；右侧 Assistant 不自动打开；无第二张 AI graph；source 点击仍先发带 artifact/revision/itemId 的 `assistant_resolve_evidence`，只消费后端返回 anchor；静态/增强切换不调用 `get_view`、summary-layout 写入或源码保存。
- [x] 增加失效断言：把 validated message 标 stale 或改变 artifact/revision 后，旧 AI 文本和引用按钮消失，H3 静态图仍完整。
- [x] 运行：`cd frontend; npm test -- --run src/app/ProjectUnderstandingIntegration.test.tsx src/app/components/SummaryCanvas.test.tsx src/app/assistant/human-understanding-overlay.test.ts`。预期全部通过。
- [x] 运行直接消费者检查：`cd frontend; npm test -- --run src/app/assistant/project-understanding.test.ts src/app/assistant/workspace-navigation.test.ts`，再运行 `npm run build`。预期测试及 TypeScript/Vite build 通过。

## H4 验收

- 当前有效 AI artifact 只能作为 H3 同图 annotation；不存在独立默认 AI 架构或 AI 生成的新边。
- AI 关闭、失效或只有单条 repair 时，H3 静态节点、关系、布局、来源和实现导航完全保留。
- 节点/关系附着只来自精确、唯一、category 兼容的已验证 relationFact；无法证明时明确留在项目级，不猜。
- 每条 AI 说明可区分推断/未知，展示 revision 和覆盖；可信引用仍通过现有 evidence/navigation 门禁。
- 测试不发真实 API、不执行用户项目、不修改源码；H4 不宣称 nanoGPT 或人工可读性最终验收通过。

## 路由与风险

**Core 路由：否。** 本切片只改前端投影和呈现，不触碰 Parser、Symbol Resolver、CFG/Graph IR、Stable ID、Source Traceability、Python controller 或缓存。若实施发现必须新增后端 evidence binding、改变 H3 ID/关系，或让 AI 生成新语义边，应停止 H4 而不是暗中扩权，并重新走 architect + `core_implementer`。

主要风险是现有 AI evidence 没有足够精确 endpoint，导致多数 claim 只能显示为项目级说明；这是诚实降级，不允许用名称猜测提高命中率。次要风险是 artifact 更新导致 canvas 重挂载；通过把增强数据排除在 `SummaryCanvas` identity/layout context 之外并以整合测试锁定。

测试口径：整合 fixture 的静态任务须保留来源 fact ID（`buildSummaryGraph` 拒绝无证据节点）；owner turn 的 question/answer 同时失效时撤销 projectUnderstanding reference，与 `client.state` 的恢复合同一致。不能用“stale answer 仍带有效 reference”的非法响应模拟正常失效；原来的引用门禁、跨会话追问、静态图保留和失效断言均保留。

交付补充：原生验证发现 H3 的三个现有 summary-layout bridge 方法未登记到 `desktop._API_METHODS/_API_ARITIES`；补齐明确名称和固定参数数量（1/4/3），让原生窗口实际获得既有布局能力，不改变 DTO、解析或语义核。增加一个原生暴露回归，保留拒绝未知名称/错误参数的守卫；这是交付接线修正，适用 desktop 与直接消费者测试及修正后后端门禁，前端未变则复用 H4 证据。

原生启动补充：pywebview 6.2.1 的 `js/api.js` 先建立 `api:{}` 再由 `_createApi` 安装方法；真实窗口曾在首调时报 `METHOD_UNAVAILABLE:get_session`，随后相同方法可调用。`waitForBridge` 的轮询只接受已有可调用方法的 API，仍接受正式 `pywebviewready`，不延长超时、不绕过 native dispatch。缺失方法测试需先发送正式 ready 事件，再断言同样的 `METHOD_UNAVAILABLE`；新增占位到方法就绪的首调用回归。此改动后须重跑前端门禁，不能沿用修前快照。
