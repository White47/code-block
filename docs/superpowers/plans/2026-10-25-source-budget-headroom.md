# Source headroom 最小架构裁决

## 裁决

缺陷属 `service` 的预路由遗漏，不属 ledger 上限或 final-wire 计量错误。保留
`EXPLANATION_BUDGET` 的 `100000/8192/8 ordinary + final 2000 + repair 2000`，也保留
generic 默认值；不得增大预算、去重重复源码 occurrence，或用 actual usage 代替预留。

最小实现只改以下 owner：

- `src/codeviz/assistant/retrieval_budget.py`：增加私有 `SourceHeadroomPlanner`。从应用生成的
  `ProviderDraft` 中校验并读取 `sourceTags`/`metadataTags`；源码量严格为每个 source tag 的
  `end-start` 之和，文件为两类 tag 的规范化路径并集。`fits(usage, *drafts)` 对每个 draft
  重复累计源码字符、对文件取并集，只返回布尔值，不预留、不退款、不产生公共 DTO。
- `src/codeviz/assistant/service.py`：以当前 `BudgetLimits` 构造 planner；在普通轮发送前和
  `fit_projections` 内加入 source/file headroom 条件。`budget.py`、`_wire_accounting.py`、
  provider/API/UI 均不改。

## 算法

1. 每次普通发送前生成既有 `current` 与非变异 `final_preview`。仅就 source/file 资源，
   repair footprint 等于 `final_preview`：`repair()` 只追加无源码 user 指令，不改变 tags。
2. 只有 `usage + current + final_preview + final_preview(repair)` 同时满足 12k source 和
   8-file 才允许下一次 ordinary prepare。否则追加现有
   `Further tool retrieval was skipped to preserve the final-answer budget.`，停止检索并发送
   final；答案按既有 `coverage_gaps => incomplete` 规则真实标记不完整。
3. ordinary 已返回工具调用后，`fit_projections` 对“候选工具结果后的 final”同时检查既有
   input 条件及 `usage + final + repair` 的 source/file 条件。完整投影不合适时沿用现有
   bounded projection：搜索/关系裁剪；源码 excerpt 只能整段保留或整段换成无源码 omission，
   禁止截断后冒充原 SourceAnchor/receipt。omission 继续产生具体 coverage gap。
4. 全量投影若能保留 final 但不能保留 repair，优先规则仍是“已证明源码不被静默改写”：
   不删既有 transcript proof；实现可只对本轮尚未 forward/bind 的结果采用完整 omission。
   在源码型结果的 floor 路径下，必须验证旧 proof 保留且新 receipt 未进入 `provided`。
5. final/repair 仍走原 `send -> prepare -> admit`。逻辑预览不是授权；prepared wire 有任何
   变换、遗漏归因或计数差异，仍由 `inspect_wire`/ledger 原子拒绝。已硬拒的 draft 不重试，
   repair 最多一次；预算耗尽保持 Failed/不完整证据语义，不伪造成功答案。

## 精确性与安全边界

对仓库内关闭的 SDK adapter，final/repair source preview 可精确到“真实源码字符 occurrence”：
SDK 将每个 service tag 无去重映射到内容相同的 wire message slot，DeepSeek 仅移动 message
index，不改变 slice；wire 计量再按该 slice 的字符长度累计。repair 不新增 source tag，故其
source footprint 与 final 相同。该结论不外推到 input token/HTTP byte preview；二者继续使用
现有估算与 final-wire admission。

不能、也不应同时 prepare 两个 future wire 来取得“双 prepared-wire 精确值”：provider/policy
只允许一个 active preparation。安全最小 fallback 是先用上述 tag-exact footprint 路由并保证
final；final 与可选 repair 再各自顺序执行一次真实 no-network prepare/admit。若任一 prepared
wire 与预测不一致或被 ledger 拒绝，该 wire 零 dispatch、后续零 send、无重试；final 前拒绝则
Failed，repair 前拒绝则保留既有 source evidence/失败语义，绝不合成 Completed。正常关闭 adapter
因映射恒等而无需走此 fallback。

计算只解析内部 JSON/tag 并投影已经由只读工具返回的数据，不导入或执行用户项目代码，
也不读取额外源码。4,000-character excerpt、secret block、freshness、每-wire 重复计数、
不退款、SourceAnchor、receipt/binding 证明全部保持原 owner 和顺序。

不新增 closed reason 或公共契约。现有 `AI_BUDGET_EXCEEDED`、既有 coverage gap，以及 UI 已
显示的 `usage.reserved.sourceCharacters/fileCount/toolCalls` 足以支持本修复；正常 source-cap
路径应在 ordinary prepare 前转为 early-final，而不是再制造一种公开错误枚举。

## Failure-first 测试

- 在 `tests/test_assistant_retrieval_budget.py` 固化现有 RED：复用
  `postfix-live/repro-source-headroom.py` 的 3,900 字符 root 与 `_SchedulingProvider(tool_turns=8)`；
  修复后必须 Completed，ordinary 次数提前收敛、final 真发送、`answer.incomplete=True` 且含
  budget coverage gap，并保留已 forward 的 root source evidence，reserved source 不超过 12k；
  不得把 prepare 计作 dispatch。
- planner 纯测试覆盖 exact-boundary/超 1 字符、Unicode 字符而非 UTF-8 bytes、同一 source
  在三个 draft 中计三次、文件跨 draft 去重、第九文件拒绝、输入 draft/tag 畸形 fail-closed，
  并断言无 ledger 变更。
- 工具投影测试覆盖：完整 excerpt 可保留时 source spans/SourceAnchor/binding 不变；为 final+
  repair headroom 必须 omission 时，整段源码、path、receipt、target 均不 forward，旧 proof
  仍可解析，specific coverage gap 进入最终不完整答案。
- repair 测试令 final 首次 schema 无效：headroom 可用时只发一次 repair；不可用时零 repair
  dispatch、保留失败证据且不发布成功答案。复用现有 no-repeat 断言，不扩大测试矩阵。

## 证据与影响

- `budget.py:83-95` 固定生产预算；`budget.py:191-206` 对候选整体原子检查，源码 occurrence
  在 `195-196` 逐次累加，失败不改 usage。
- `_wire_accounting.py:142-198` 验证每个 source/metadata occurrence；`:201-232` 从最终
  prepared wire 生成唯一 reservation；`:378-463` 才执行真实 admission。
- `service.py:2631-2695` 已有非变异工具结果 final preview/裁剪；`:2758-2776` 是
  prepare→admit 边界；`:2993-3016` 当前只检查 output/input，正是遗漏点；`:3032-3066`
  是 final 与一次 repair。
- `tool_loop.py:457-520` 追加结果、final、`preview_final`、
  `preview_tool_results_final` 和 repair；`sdk_provider.py:499-545` 证明 tag 无去重映射并拒绝
  未归因复制。
- 可复用 RED（本裁决未重跑）：`postfix-live/repro-source-headroom.py` 已给出 service
  `preparedDrafts=4/dispatches=3/all tools/AI_BUDGET_EXCEEDED`；纯 ledger 精确为
  `26871 input / 1536 output / 3 requests / 12000 source`，第四次原子拒绝且 usage 不变。
- 下游仅改变普通检索轮数、projection omission 与 coverage gap；不改变 B4/B5 读取、证据、
  SDK transport、poll DTO、UI、配置默认值或已保存会话格式。core 实现前以本文为边界。

## 授权实施队列

R1 core_implementer：实现上述私有planner及service early-final/projection门禁，先固化3900字符端到端RED；保留真实usage、逐wire源码累计、原有来源证明和旧失败语义。仅受影响模块/直接消费者测试，不新增依赖/不改公共DTO，不提交推送。
R2主线程：AssistantUsage显示既有源码字符/文件/工具尝试预留，中英及单行为红绿已完成，相关16项及tsc/局部ESLint通过；无未声明上限，不称预留为实际披露。
R3阶段收尾：新diff一次独立复审（风险为重复source计数/omission后receipt绑定/最终留量），修后最终Python/frontend全量、Ruff、Windows+Linux mypy，复用未变SDK证据；独立build/source-budget-20261025新包/源码资产核对/离线探针，不覆盖当前存密钥窗口。阶段边界再更新进度文件。
真实修复后验收：本轮已授权一次真实API已完成且失败，不盲重发；新进程的个人密钥仅用户在App输入。未实际取得可信回答/引用/画布前不得称在线业务通过。实际现场唯一原因仍未知，修复针对已静态证明和端到端复现的source headroom缺陷，不把相同计数当作raw参数证明。

## 唯一复审收尾约束

Curie发现固定512-byte floor仍可保留第9文件，需先尝试不含新path/target/receipt的完整零项omission；source planner还需与真实accountant一致拒绝不可解析pointer、越界/重复span及大小写路径冲突。补最小失败回归后修正，仍属原计量预测/omission契约，不改变真实admission、SourceAnchor或接受证据的语义核规则，无第二次独立复审。main已恢复generic keyword-only ledger构造兼容，旧启动测试/timeouts保留。
