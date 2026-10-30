# 估算输入预算合同

日期：2026-10-21。状态：计量切片已实现并完成适用阶段门禁；跳过、待办与前端并发限制见 CODE_AGENT_PROGRESS.md，不表示 DeepSeek 或 E 发布已验收。

## 1. 授权及范围

用户明确回复“授权估算预算合同”。该授权允许将指定可信提供方配置的输入预算
由发送前保证上界改为明确标注的估算控制：已发出的单次请求，实际输入量可能
超过其估算或整个任务阈值；一旦观测到累计超额，不再发送该任务的后续请求。
这不是费用硬上限，也不能撤回服务商已处理或计费的请求。

设计轮只形成合同及实施计划；后续按同日六任务计划实施计量切片，不修改既有测试、SDK 绑定或冻结包。
未授权真实 API、付费调用、用户源码上传、密钥、提交或推送。

只对输入计量语义作此变更。原有源码授权、最终发送字节捕获、完整来源归因、
置信度及证据校验、只读静态理解、来源新鲜度、取消/释放协议和禁止自动重试不放宽。
不能将估算值填入 `PreparedRequest.input_token_upper_bound`，也不能为了启用估算
把 `attribution_complete=False` 改成未经证实的 True。

## 2. 现有边界及兼容要求

- `assistant/provider.py` 的 `PreparedRequest` 表示冻结请求及来源位置，当前
  `input_token_upper_bound` 和 `attribution_complete` 分别承担预算/归因证明。
- `assistant/service.py::_wire_reservation` 拒绝缺失上界或归因不完整；
  `_SendPolicy.report_usage` 当前按单次上界检查。新估算路径必须与旧路径区分。
- 解释任务累计输入阈值保持 20,000；连接检查累计输入阈值保持 1,024。
  它们在估算模式中是累计估算准入阈值，不是实际计费 token 的事前保证。
- 解释任务的 8 文件、12,000 源码字符、8 工具、6 模型请求、2,000 输出分配、
  90 秒，以及连接检查的无源码、2 请求、128 输出分配、15 秒限制保持原合同。
  256 KiB 最终请求体、流/事件/答案大小限制同样保持。
- 默认严格模式及旧未知-framing 拒绝测试不改；仅在已准入的可信配置下使用
  新模式。设置保存、调用者参数或模型回答不能自行指定预算模式。

真实 DeepSeek 协议兼容、完整归因证明及生产工厂接线是后续准入工作。预算合同
获准不等于 `json_schema`、输出参数、工具模式、SSE 或来源映射已兼容。

## 3. 用户可见行为

首次发送前有常驻提示：输入预算可能按估算控制，实际用量可能超过阈值；以服务商
返回用量作为报告，不保证精确账单。此提示不触发网络或额外授权动作。

估算路径显示“输入预算预估（非账单）”，严格路径保留原预留含义；输出预留和
其他硬计数不被改称估算。服务商实际报告、估计报告与未知分别显示；0 必须显示 0，
未知显示“—”，不能用 0 冒充未知。缺失用量意味着该任务不能再发请求，不能自动
重试或自动修复回答来补齐统计。

若最终响应已完整返回、通过七段格式及证据校验，单纯输入超额或用量未知不丢弃
已验证答案，而是保留答案并显示计量警告。若工具阶段后还需要模型请求，则因
超额/未知停止，不捏造最终答案。协议、输出硬限、授权、证据或取消检查失败仍
按原失败规则处理；计量警告不覆盖取消确认或资源释放状态。

手动新请求是新的任务及预算，可能再次计费；系统不自动转成新请求继续旧任务。
来源选择、会话、分析、配置及允许文件仍按各自修订失效，不因统计状态而复活。

## 4. 前端协议原则

当前 `AssistantUsage` 只有 `reserved` 和 `observed`，`poll.ts::usage` 对两者及
其内部字段使用封闭校验。保留该旧结构表示严格路径；估算结构应增加明确封闭的
计量信息，与旧结构组成联合类型。不能通过允许任意额外字段或字符串放松解析。

`observed.basis=actual` 只在整个累计已发送集合都收到实际报告时成立。
一部分已知、另一部分未知时，保留已知部分的独立下限统计，但整个累计仍为未知；
后来的实际报告不能把此前未知覆盖成完整实际值。不能从会话状态恢复未保存账本，
也不把估算/密钥/批准片段写入分析缓存。

前端需要同步修改 usage 类型、严格 parser、使用该类型的显示及新增回归；
不删除或改写旧严格 fixture。源码/图引用内容、搜索、导航及 bridge 请求字段不变。

## 5. 验收口径

采用新测试验证：同一冻结请求的计量绑定、严格/估算分支隔离、累计超额、
未知不能被后续已知洗白、0 与未知区别、已发送失败/取消仍可能计费、
最终已验证答案保留、后续发送/repair 被阻止及 closed DTO。

仅运行受影响模块及直接消费者；实现阶段边界才运行适用完整门禁，沿用未改动
快照的既有结果并写清范围。设计文档不运行应用全量，也不计为测试、原生桌面、
Linux 或真实 API 验收通过。既有 E 复审不再重做；新合同实现后的阶段审查只看新差异。

## 6. 估算公式与状态机

本版本只采用 `wire-bytes-v1`：`E = len(prepared.body)`，即冻结最终请求体的
UTF-8 字节数作为输入 token 的启发式预估。它包含最终序列化的消息、工具描述、
输出 schema、历史与重复内容；不新增 tokenizer 依赖，不添加声称已验证的
隐藏 framing 常数。它不是实际 token 数、数学上界或账单证明。

设 `R` 为此前已准入请求的累计预估（不退款），`A` 为已 dispatch 请求的累计
实际报告输入，`L` 为任务输入阈值。首个请求前 `R=A=0` 且实际累计完整。
后续准入要求实际累计仍完整，并且：

```text
max(R, A) + E <= L
```

准入成功后 `R := R + E`，同时预留原合同的请求数、输出分配及源码等计数。
准入失败不占用新请求，不调用 transport；既有预留不退回。
`A=19000,R=1000,E=2000,L=20000` 应拒绝；反向
`A=1000,R=19000,E=2000` 也应拒绝。

当实际报告到达，必须先记录实际数值再计算是否阻止后续发送。单次 actual
大于其预估、但任务累计尚未超限，不再误判成“输入上界证明被违反”的协议错误。
若 `A > L`，标记 `observed_overrun`；即使无后续请求，也保留此事实供 UI 展示。

actual 0 为合法已知值。provider estimated、unknown、缺失 usage，或可能发送后
失败/取消且没有 actual，都使该任务的实际累计不完整；状态不可逆，之后收到
另一条 actual 也不能清除未知。可保留合法实际报告之和作为已知下限，但不能
把它标为完整累计。该状态阻止后续发送，不退款，也不转而调用模型查账。

状态机与取消/释放状态机相互独立：标记预算阻止不表示任务已完成、取消已确认
或资源已释放。首次手动开始的新任务可使用新账本；不会自动新建任务延续旧请求。

## 7. 冻结记录与可信准入

`provider.py` 新增 frozen/slots 的 `EstimatedInputAccounting`，字段为：

```python
profile_id: str
method: Literal["wire-bytes-v1"]
estimated_input_tokens: int
body_sha256: str
```

`PreparedRequest` 最后增加
`estimated_input_accounting: EstimatedInputAccounting | None = None`，不改变旧构造位置。
严格模式要求新增字段为 None 且旧上界为合法整数；估算模式要求旧上界为 None
且新记录有效，禁止两者同时存在或同时缺失而降级放行。

准入检查使用精确类型，bool 不能冒充 int。profile ID 为 1–128 可打印且可严格
UTF-8 编码的字符；method 精确匹配；估算值为正整数且等于最终 body 字节数；
hash 为 64 位小写十六进制，且等于 `sha256(prepared.body).hexdigest()`。
body 必须经过原有严格 JSON 及重复 key/尺寸/范围检查。body 中 model、endpoint、
profile ID 与服务端私有 `TrustedEstimatedProfile` 精确匹配，记录本身不能批准自己。
两种模式仍须 `attribution_complete is True`，并通过原有完整来源/重复片段归因验证。

`TrustedEstimatedProfile` 是 frozen/slots 私有记录：`profile_id, endpoint, model,
method="wire-bytes-v1"`。默认允许集合为空；只有可信应用组装层可传入已准入的
记录，bridge 请求、设置保存、模型返回和 provider 的字符串声明均无此权限。
本预算切片不注册实际 DeepSeek profile，不接线生产工厂。

service/connection 冻结字段检查必须包含整个新记录；不能在一次 prepare/dispatch
间改 method、hash、estimate 或 profile。旧 generic SDK 仍返回 None/False，继续拒绝；
未来专用 SDK profile 必须独立更新其内部 handoff fingerprint 和隐私证明。
冻结比较必须复制该记录的四个已验证原始字段，而不是保存记录对象引用；防止
对同一对象的底层变更使前后两个引用同时改变，从而绕过比较。

## 8. 模块职责与接口

采用追加记录而非替换全部 `PreparedRequest` 的方案：旧构造器及严格路径保持兼容，
新增路径有显式 XOR 验证。将原记录替换为另一种请求对象会波及全部 provider，
本版本不选。把估算塞进原上界字段会说谎，明确禁止。

- `provider.py`：只增加估算冻结记录与默认字段。
- 新 `_usage_values.py`：放置原 `ObservedUsage` 与新 `InputAccountingSnapshot`
  两个轻量 frozen/slots 记录；无 SDK、service、poll、I/O 依赖。
  `poll_public.py` 重导出同一 `ObservedUsage` 类，旧 import/type identity 不变。
- 新 `_wire_accounting.py`：最终 wire 检查、预留与私有逐任务状态机。
  仅依赖 provider/budget/source-policy/轻量记录，不反向导入 service/poll。
- `service.py`：保留 `_wire_reservation` 与 `_SendPolicy` 作为薄兼容层，委派新模块；
  不能直接别名后让旧错误类型或旧调用顺序失效。
- `connection.py`：共用计量逻辑，保留 source-free、阶段 profile、输出/时间限制。
- `jobs.py` / `poll_public.py`：保存不可变统计快照、封闭投影，不承担预算判断。

`InputAccountingSnapshot` 字段：`method, limit_tokens, known_actual_input_tokens,
blocked_reason`；reason 为 None 或 `estimated_limit / observed_overrun / usage_unknown`。
`WireReservation` 字段：`reservation: BudgetReservation, files: tuple[str,...],
mode: Literal["strict","estimated"], profile_id: str|None, method: str|None`。

新私有模块提供以下固定接口：

```text
inspect_wire(prepared, *, trusted_profiles=()) -> WireReservation
wire_reservation(prepared, *, trusted_profiles=()) -> (BudgetReservation, files)
WireAccountingPolicy(ledger, *, input_limit=20000, trusted_profiles=())
  ensure_can_prepare() -> None
  admit(prepared, phase: ordinary|final|repair) -> (BudgetReservation, files)
  dispatch(preparation_id) -> None
  observe(preparation_id, usage: ObservedUsage) -> None
  finalize(preparation_id) -> None
  report_usage(reservation, usage_dict) -> None  # strict-only legacy façade
  observed -> ObservedUsage
  snapshot -> InputAccountingSnapshot|None
```

首次 admission 后 mode/profile/method 在该任务内固定。每个 preparation 只准入、
dispatch、observe 各一次；一个 active send 结束后才允许新的 prepare。
`finalize` 在 `finally` 中执行并可安全重复清理：已 dispatch 而无合法 actual 的
请求会让估算账本 sticky unknown；未 dispatch 的预留不冒充实际请求或费用。

旧 `_SendPolicy.report_usage(reservation, dict)` 仍允许 admit 后直接调用，不要求
dispatch，保留原严格单次 input/output 上界检查。该兼容方法拒绝 estimated
admission；新的发送链路使用 dispatch/observe/finalize，不复用旧 façade 偷换语义。
模块内只抛固定 code 的 `WireAccountingError`；service 薄层转为原 `ServiceError`。
connection 对协议错误仍使用原 ProviderError，对预算阻止转为
`AgentError("budget_exceeded")`；禁止为避免循环依赖而反向导入 service。

可信组装入口为 `AssistantService.__init__` 的新 keyword-only 参数
`trusted_input_profiles=()`，保留 provider_factory、before_dispatch、conversations
三个原参数及其默认含义。连接函数增加 keyword-only `trusted_profiles=()`。
这些是服务端组装端口，不是 bridge、设置或模型工具字段；本轮不修改正常桌面组装。
原有最多一次、重新 prepare 且独立计入预算的格式 repair 保留；它不是网络请求
重试，也不能在 unknown、超额、取消或来源失效后执行。不得借此增加自动重发。

## 9. 串接、失败与封闭展示字段

发送顺序为：预算允许 prepare → prepare → admit → 原授权/来源/freshness/frozen
检查 → 标记 dispatch → provider dispatch → 验证并 observe usage → 同步任务快照
→ finally finalize 并再次同步快照。同步只交换内存记录，不在状态锁下进行哈希、
I/O、SDK await 或资源关闭。

`observe` 先累计合法报告，再判断输入超额；输出超限及格式错误仍失败。异常或
取消也在传播前保留计量快照，不能因先抛异常而丢失已经产生的用量。
严格旧单次上界行为不变；估算模式不因单次 actual 大于 estimate 而误报协议错误。
actual 累计只在所有已发送请求均报告 actual 时完整；之后出现新未知请求则不完整，
未知状态不恢复。完整性在内部保存，不从 UI 或统计值猜测。

`_Job`、`_ConnectionJob`、`PollState` 以及 `ConnectionProbeResult` 末尾增加
`input_accounting: InputAccountingSnapshot | None = None`。连接资源保存相同快照，
异常路径仍能读取。估算模式每次 observe/finalize 后更新；严格模式为 None。
source/graph 导航 poll 不新增 usage，不从聊天历史恢复计量控制权。

严格 usage 保持精确两键：`reserved, observed`。估算 usage 精确三键，例：

```json
{
  "reserved": {
    "inputTokens": 1000,
    "outputTokens": 512,
    "modelRequests": 1,
    "toolCalls": 0,
    "sourceCharacters": 0,
    "fileCount": 0
  },
  "observed": {
    "inputTokens": 19000,
    "outputTokens": 10,
    "basis": "actual"
  },
  "inputAccounting": {
    "basis": "estimated",
    "method": "wire-bytes-v1",
    "limitTokens": 20000,
    "upperBound": false,
    "knownActualInputTokens": 19000,
    "blockedReason": null
  }
}
```

`inputAccounting` 内必须恰好六键。basis/method/upperBound 为上述常量；limitTokens
为对应 job 的 20000 或 1024；knownActualInputTokens 为安全非负整数；blockedReason
仅允许 null 或三个 approved 值。未知、非有限数、bool-as-int、额外键或任意错误文本
拒绝。完整 actual 时已知累计与 observed input 必须相同；unknown 时保留独立的
已知 actual 下限，但 observed 的两项仍按原 unknown 合同为 null。
前端解析器必须根据外层 explanation/connection kind 检查对应的固定阈值，不能
仅因 1024 或 20000 在枚举内就允许跨种类交换；navigation 仍不接受 usage。
校验同时保留策略状态含义：usage_unknown 必须对应 unknown 累计；observed_overrun
必须对应实际输入累计超过该任务阈值。严格与估算两种结构均不允许 actual/estimated
计数为 null，或 unknown 计数为 0；服务商输入/输出报告须先通过安全整数检查再记账。

parser 只凭封闭形状判别两种 usage，不凭 observed.basis 或 token 大小猜模式。
严格 producer 不产生 inputAccounting；合法三键及六键元信息即表示 estimated。
前后端随同本地 bundle 协同升级，旧前端对新形状拒绝时不会自动重发。
外层 bridge schemaVersion、本次请求字段、导航合同和错误码集合不变。

## 10. 架构结论及完成边界

Pascal 已完成本次新合同的架构设计咨询，并确认逐任务累计公式、兼容 façade、
独立记录模块及封闭联合协议。主代理补充薄错误映射，避免新模块反向依赖 service。
这是新合同的设计工作，不是旧 E 差异的第二次独立复审，也不是实现验收。

下一步按同日实施计划执行新测试/计量模块/发送接线/前端显示。是否启用真实
DeepSeek profile 仍需其独立协议和完整来源证明准入；Linux 和 API 验收状态不变。
