# Protocol rejection diagnostic：最终最小架构裁决

## 裁决

**批准**在现有 `ProtocolDiagnostic` 上增加两个可选、封闭字段：

- `checkSite`：仅用于 `boundary == "dispatch"` 的 `AI_PROTOCOL_INCOMPATIBLE`。
- `finishReason`：仅允许在 `checkSite == "terminal_finish"` 时出现。

这两个字段是现有诊断对象的兼容扩展，不是新错误码或成功状态。拒绝内部-only
`failureSite`：它无法通过普通 App 抵达用户，会继续迫使 raw capture、日志或重包。

首版 `checkSite` 闭集固定为：

`response_headers | sse_syntax | chunk_shape | usage_shape | delta_shape |
terminal_finish | stream_eof | sdk_decode | context_exit | cleanup`

`finishReason` 闭集固定为：

`length | content_filter | insufficient_system_resource | aborted`

合法组合只有：

1. 旧形态 `{boundary, httpStatus?}`；
2. dispatch 协议失败 `{boundary:"dispatch", checkSite}`；
3. 已知非成功终止 `{boundary:"dispatch", checkSite:"terminal_finish", finishReason}`。

未知 finish 值仍 fail closed，但只公开 `checkSite:"terminal_finish"`，不得复制、散列或
字符串化原值。JSON 截断归 `sse_syntax` 或 `stream_eof`，不得猜测成 `length`。

## 不可违约束

- 不新增/改变成功条件、顶层错误码、cap、token/source/file/tool 预算、usage/Completed 规则、
  C5/fingerprint/key/授权、请求次数或重试策略。
- `length/content_filter/insufficient_system_resource/aborted` 仍全部是协议失败；不得把官方
  不存在的 usage-only chunk 当作成功路径，也不得伪造 terminal usage。
- 不记录或传播 payload、数值计数、key、header 值、URL、path、tool args、messages、source、
  provider text、raw frame、异常字符串/类型名、traceback；不新增日志文件、analytics 或网络
  telemetry。真正 dispatch 拒绝后无自动/付费/盲重试。
- validator 必须 exact-key、own-property、closed-enum、组合约束 fail closed；未知字段或未知
  枚举使整个 diagnostic 无效，不能宽松丢弃后继续显示。

## 文件所有权与传播

- `src/codeviz/assistant/issues.py`：拥有两个闭集、`ProtocolDiagnostic` 组合校验及唯一 public
  projection；既有 `boundary/httpStatus` 行为保持兼容。
- `src/codeviz/assistant/sdk_provider.py`：十类拒绝点只附固定常量；wrapper 捕获
  `SDKAdapterError` 时必须保留其已有 diagnostic，只有未分类异常才映射固定 `sdk_decode`，绝不
  带异常对象或文本。非成功 finish 先按闭集映射，再沿原失败路径退出。
- `src/codeviz/assistant/service.py`：只透传已验证 diagnostic；不得重新分类、解析异常或补 payload。
- `frontend/src/app/assistant/types.ts`、`diagnostic.ts`：同步 closed union 与严格复制；UI/i18n 只按
  固定枚举显示本地化标签。无新 endpoint、bridge method、顶层 DTO、依赖或持久化。

下游影响仅为现有 protocol diagnostic 的可选字段：旧生产者/旧对象继续有效；新前端可显示
精确检查阶段；其他错误类型、预算 UI、provenance 与会话内容均不变。

## Failure-first 定向测试

1. backend diagnostic：旧 shape 继续通过；十个 `checkSite` round-trip；非-dispatch site、
   非-terminal 的 `finishReason`、未知枚举、extra/inherited/raw/message/counter 字段全部拒绝。
2. SDK stream：四个官方非成功 finish 分别得到同名 `finishReason`，仍无 answer/Completed/actual
   usage、仅一次 dispatch、无重试；未知 finish 只得 `terminal_finish` 且 sentinel 不泄漏。
3. 每个其余 site 至少一个失败 fixture，断言 wrapper/service/public poll 保留同一 diagnostic；
   含秘密的异常对象断言 issue、`repr`、poll、UI 均不含秘密、异常文本或 traceback。
4. frontend：严格 validator/copy/types 与十个固定本地化标签；未知/额外字段使 poll fail closed。
5. 复用现有 generic/DeepSeek、usage/token/source、C5/key/authorization 测试；不跑 live API，
   synthetic 只证明分类，不宣称证明当前 live 根因。

## 符号证据

- `src/codeviz/assistant/issues.py:50-69`：现有 `ProtocolDiagnostic` 与 public serializer；
  `:72-108`：diagnostic 仅附着 protocol issue。
- `src/codeviz/assistant/sdk_provider.py:64-90`：`SDKAdapterError/_reject/_check` 是固定诊断入口；
  `:328-335`：wrapper 当前把缺失诊断折叠为 boundary，实施时须保留已有 diagnostic。
- `src/codeviz/assistant/sdk_provider.py:1170-1180`：frame/choice/usage 形态检查折叠点；
  `:1255-1258`：非 `stop/tool_calls` finish 的统一拒绝点，可安全映射四个闭集值而不改变接受条件。
- `src/codeviz/assistant/sdk_provider.py:1272-1320`：chunk、SSE、DONE/EOF 拒绝点，可映射 site 而无需
  保存 raw 数据。
- `src/codeviz/assistant/service.py:178-214`：已有 boundary attachment 与 SDK error 透传路径。
- `frontend/src/app/assistant/diagnostic.ts:3-17`、`types.ts:5`：现有 strict validator/copy 与窄类型
  是前端唯一 owner；既有 extra/private-field 测试证明应扩闭集而非放松校验。

本裁决只授权上述诊断合同与定向测试；不授权任何 transport、预算或成功语义变更。

## 执行队列

D1 主代理：failure-first，增加闭合诊断字段、SDK拒绝分类与严格前端显示；仅直接消费者定向测试，不依赖真实API。httpStatus与checkSite两种附加形态互斥，保持上述合法组合清晰；终止原因未知时只留terminal_finish，不复制原值。
D2 阶段收尾：一次新diff独立复审；全量Python/前端、Ruff、Windows+Linux mypy及前端lint/type/build各一次，复用未变证据；build/protocol-site-20261026独立wheel/frozen和字节/假HTTP探针。已通过的SSE阶段结果不冒充新快照门禁。
D3 真实验收：当前48784的单次live已失败且不重发；新包全部独立检查完成后，仅在用户配置其个人内存key和同一项目授权后单次验收。诊断改进不等于业务修复或线上通过；不更改成功标准、cap、token/source预算，不自动重试。

既有SDK测试只断言dispatch boundary的四处检查，随本合同扩展为更强的固定checkSite断言（sdk_decode/context_exit/sse_syntax/cleanup），不删除原错误码、源码/消息不泄漏、单次发送及资源释放断言。先前广匹配SDK发现命令在未安装项目的probe环境提前导入packaging测试失败，并有两处原5秒计时超时；保留失败记录，后续显式PYTHONPATH指向源码且仅运行实际受影响SDK与直接消费者，不更改原超时。

唯一复审指出generic已知非成功finish原因被遗漏；现已共用闭合映射，所有失败条件保持不变，并增加generic实际SDK拒绝回归。不运行第二次复审。Banach对计时失败的闭合采样证明：4.5秒仅780帧，线程停在asyncio debug创建Handle时的linecache.checkcache/traceback OS文件状态读取；未进入新诊断构造。仅many-frame单个测试使用生产loop debug=False，try/finally恢复，其余小流测试仍debug=True；原5秒和全部raw/frame/usage/answer/失败释放断言均保留。这是有证据的fixture代表性修正，不能把此前计时失败记为通过。
