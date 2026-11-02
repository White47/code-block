# H2 概览职责说明与源码导航

执行方式：AGENTS.md 自主队列，H1 已完成；本阶段不提交或推送。复用整体设计与 H2 architect GO 合同，不加载逐任务派发流程技能。

**Goal:** 打开 Overview 先看到有来源的职责说明，再按需查看实现和分析指标。
**Architecture:** 纯投影读取 H1 持久化事实，Overview 增加可选字段；前端复用当前源码和函数流程回调。H2 不生成项目目的、图边或执行顺序，nanoGPT 图形可读性仍在 H3 验收。
**Tech Stack:** 现有 Python dataclass/JSON bridge/React/中英 i18n；不加依赖。

## 已冻结的接口

新增 `src/codeviz/views/human_understanding.py`：

```python
@dataclass(frozen=True, slots=True)
class OperationFacet:
    kind: HumanOperationKind
    confidence: Confidence
    source_location_count: int
    fact_ids: tuple[str, ...]
    source_anchors: tuple[SourceAnchor, ...]

@dataclass(frozen=True, slots=True)
class ResponsibilityCard:
    owner_symbol_id: str
    code_name: str
    owner_kind: NodeKind
    responsibility: str | None
    basis: Literal["source_author_statement", "unknown"]
    confidence: Confidence
    author_fact_id: str | None
    author_truncated: bool
    operations: tuple[OperationFacet, ...]
    source_anchor: SourceAnchor

@dataclass(frozen=True, slots=True)
class HumanUnderstanding:
    status: Literal["source_backed", "partial", "unknown"]
    responsibilities: tuple[ResponsibilityCard, ...]
    omitted_responsibility_count: int
    omitted_fact_count: int
    discarded_fact_count: int
```

`build_human_understanding(analysis: ProjectAnalysis) -> HumanUnderstanding` 只消费已持久化 facts、SymbolIndex、generic_entries 和 training entry。
不访问 AST、磁盘源码或新分析。卡片至多12个，facet 的 fact_ids/anchors 各最多8个；计数是所有可确认源码位置数，不是运行次数。
相同操作聚合时保留最弱置信度，不因折叠提高置信度。

只为至少有有效事实的 Module/Class/Function/Method 建卡。作者说明原样保留、Probable，标“源码作者说明（未验证）”；无作者说明或同 owner 多条冲突说明时 responsibility=None / basis=unknown，不用名称、参数数、操作数量推导业务目的。
来源身份与锚点必须仍属于当前索引；失效/外来/不适用于此摘要的事实丢弃并计数。
排序为有效入口、入口词法父模块/类、其他带作者说明 owner、仅操作 owner；同级按路径/源码位置/id。不按调用数排名，不生成顺序箭头。

### H2 一次复审后的来源校验补充（architect GO）

作者说明与 return 的 owner 必须是当前索引中包含完整事实锚点的最窄词法 owner；候选包含 LexicalScope，避免把 lambda/comprehension 内部事实错归外层。调用操作使用 SymbolIndex.references 的真实 scope_symbol_id/位置/目标作归属依据，保留 default、decorator、annotation 和 comprehension 外层迭代器在创建作用域求值的既有语义。
入口允许只读现有 FlowAnalysis.graph 作来源核验，禁止新增图事实或据调用数/顺序排名：

- MainName：模块直属 main 函数，候选锚点等于当前 owner 锚点。
- MainModule：__main__.py 模块，候选锚点等于当前模块锚点。
- MainGuard：唯一匹配锚点、模块 CONTAINS 的 ControlBlock/If，条件是规范化的 `__name__ == '__main__'` 或反向形式。模块 root 以此为证；函数 root 还需来自 Guard BRANCH_TRUE 区域的 CallSite → 该函数 CALLS，不能取 false/unrelated 区域。
- Training EntryPoint：ID 存在、kind 合法、entry 锚点等于当前 owner 锚点。
- 无来源、未知检测类型、外来或陈旧引用均跳过，不抛界面错误。

该补充只强化派生投影的来源验证，不修改 Graph IR、Parser、Resolver、源码句柄或缓存合同；修复后按门禁收尾，不例行再派独立 reviewer。

置信度测试夹具修正：原聚合测试只将一个 OperationFact 升级为 Exact，却保留 Probable 的来源 reference，已不符合新增的一致性校验。混合置信度聚合夹具须同步构造一致的 reference/CALLS 证据，继续断言10个位置、8个引用及最弱 Probable；另以独立负例确认单独升级事实会被拒绝。该修正不降低产品校验或聚合覆盖。

`OverviewData` 尾部新增 `understanding: HumanUnderstanding | None = None`，`build_overview` 填充投影。
`overview_payload` 非空时追加 camelCase `understanding` 字段；schema version 和旧字段保留。结构对应上述 dataclass，枚举转换成字符串，anchors 走现有转换。
前端 `Overview.understanding?` 兼容旧响应；App 传现有 `openSource`。源码按钮只传当前 owner anchor，函数流程按钮只传 `ownerSymbolId`，不得传 fact/card ID。

## Task H2.1：核心纯投影

Files: 新增 `views/human_understanding.py`、`tests/test_human_understanding_view.py`，修改 `views/__init__.py`。由 core_implementer 实施。

- [x] 先写失败测试，确认无投影；实现上述值对象和纯函数。
- [x] 验证冷暖 DTO 相等、作者身份/unknown、入口及父级排名、操作位置数、空旧分析、外来引用拒绝、冲突、12卡预算和省略数。
- [x] 仅跑该模块及直接 views 消费者，检查差异；原始图不变。

## Task H2.2：桥接与界面

Files: `ui/presenter.py`、`ui/web_contract.py`、`tests/test_ui_presenter.py`、`tests/test_ui_web_contract.py`；`frontend/src/types.ts`、`app/App.tsx`、`app/components/Overview.tsx`、新增 `app/components/ResponsibilityOverview.tsx`、`app/i18n.ts`、现有样式文件和对应测试。主代理实施。

- [x] 先写失败测试：新职责卡显示原作者说明和未验证标识；源码/函数流程动作分别传真实 anchor/owner；旧字段缺失时兼容。
- [x] 实现可选 DTO 序列化；后端测试验证 JSON 安全、既有字段和冷暖一致，不将事实ID冒充源码符号ID。
- [x] Overview 首先呈现职责区；符号目录及分析指标移入默认折叠的“实现与分析详情”。模型和入口导航仍可使用。
- [x] 原有函数导航测试先展开该详情再验证相同回调；这是已明确的默认折叠行为变更，不删覆盖或放宽导航断言。
- [x] 原作者内容不自动翻译；所有新界面标签提供中文/英文。静态操作使用“源码位置数”标签，详情按需展开；范围缺口明确但不把 unknown 伪装成失败。
- [x] 仅跑 presenter/web_contract/直接 bridge 消费者及 Overview/App 受影响前端测试，Ruff/typecheck 按修改范围运行。

## 阶段末

- [x] 用小型统计项目和 nanoGPT 做本地投影与缓存检查；不运行目标源码、模型 API 或读取密钥。
- [x] 一次独立复审，修复后用最终源码完成门禁，不做例行第二次复审。
- [x] 全量 unittest、Ruff、mypy、mypy --platform linux；原生 Windows 句柄检查使用已验证的原生环境，保留失败记录。
- [x] 前端全量 test、lint、typecheck、build，各一次；2个历史 todo 如实记录，本阶段前端变化不可复用旧全量证据。
- [x] 检查 git diff，更新进度最多5行和断点，然后继续 H3；H2 的通过不等于主流程图/nanoGPT可读性/AI质量或原生桌面验收。

下一阶段 H3：职责任务图及控制证据、nanoGPT 模型/训练/生成路径；H4：只对当前有效 AI 结果作有证据投影。打包与原生整体验收在可读图形成后执行。
