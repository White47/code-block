# DeepSeek SSE 资源预算条件性裁决

> 状态：最终裁决；enriched actual-SDK replay 已满足实施门槛。

## 条件与结论

实施门槛已由 Banach 的 enriched actual pinned-SDK fixture 满足：同一合法七段中文 final、
5704-byte decoded text、终帧 actual 1800 output tokens（allocation 2000），compact 形态为
`35 frames / 16331 raw bytes`，fragmented 形态为 `2106 frames / 656270 raw bytes`。每个合法
frame 使用普通 `chatcmpl-` 加 48 ASCII id、10 位 created、`fp_` 加 48 ASCII fingerprint，
非终帧 `usage:null`；两种形态均由 actual SDK 正常解析，旧代码仅在
`sdk_provider.py:1280-1281` 的共享 aggregate cap 拒绝 fragmented 形态。fixture 零网络、零用户
源码、未改生产代码。该证据证明 adapter 缺陷；live 未采 raw，仍不宣称现场唯一根因。

据此选择“DeepSeek 私有、严格多维有界的 streaming envelope”，不改 final 为 non-stream；
其数值覆盖合法普通 header，不依赖简化 RED fixture。
generic 路径继续保持 raw aggregate `262144 pass / 262145 AI_BUDGET_EXCEEDED`，不得改旧 `_CAP`
含义或修改 generic 测试来放宽它。

non-stream 不是最小修复：当前关闭 profile 要求 `stream=true` 和 `include_usage`，SDK 运行链使用
`request_stream`，C5 又冻结并释放同一已捕获请求。final-only 改 `request()` 会改变 wire profile、
捕获/续接路径、响应 JSON/usage 所有权和取消清理证明，不能作为本故障的局部资源修复。

## 冲突与安全模型

旧 256 KiB 同时承担 raw 网络/解析工作上界；它不是输出 token、正文或常驻内存上界。
DeepSeek 可把不超过 2,000 tokens 的几 KB 合法正文拆成大量小 SSE JSON frame，每帧重复
`id/object/created/model/choices/delta` 等 envelope。因而 raw aggregate 可先耗尽，而正文、
终帧 `completion_tokens`、64 KiB final answer 和服务 output reservation 全部仍在界内。

必须继续分层：

- raw total：网络吞吐和最坏处理字节；每个 chunk 在累计前收费。
- frame count：限制 JSON 解析次数/CPU；所有 data、comment、DONE frame 都计数。
- per-frame：限制未分帧 buffer 与单次 JSON/comment 解析对象；不能用提高 raw total 代替。
- decoded output：现有 final text `<=65536 bytes`、tool argument `<=8192 bytes` 等保留。
- output tokens：唯一终帧 actual `completion_tokens <= draft.max_output_tokens <= 2000`；未知、
  缺失、重复或伪造 usage 都不能 Completed。
- time/retention：沿用原始 monotonic deadline、单事件 backpressure、cancel/aclose/join/清引用。

raw bytes 不应整流保留；正常只保留当前 frame buffer、SDK 已解析且受 64 KiB/tool caps 限制的
parts，以及小型完成事件。提高 DeepSeek raw throughput 不等于允许同量常驻内存。frame count
限制解析放大，per-frame 限制单对象，deadline 限制慢流；三者共同替代单一 raw cap 的 DoS 假设。

## 最小私有要求

Owner 仅为 `src/codeviz/assistant/sdk_provider.py` 的 `_bounded_stream` 及直接测试。增加关闭的
private stream limits，由已冻结 `_SDKWireProfile` 选择，响应或 draft 不得自报/改变：
这是普通 SDK transport bug，由 main inline 实现；不触及 Parser/Resolver/Graph/Source 核心，
不再派 agent，也不新增 API、DTO 或依赖。

- generic：`raw_total=262_144`；其余行为完全沿用现状。
- DeepSeek 最终：`raw_total=1_048_576`、`frame_count=4_096`、
  `frame_bytes=262_144`、`decoded_text_bytes=65_536`。
- raw total 包含 SSE envelope、comments、分隔符、usage 与 DONE；frame count 包含 comments/DONE。
  `frame_bytes` 在 `_remote_json` 前检查，也约束无分隔符的 buffer。一个 262,145-byte comment
  frame 仍失败，保留旧大块攻击防护；许多小合法 data frames 才可使用更高 DeepSeek total。
- transport chunk 不是 logical SSE frame。一个 coalesced chunk 可包含许多各自小于 256 KiB 的
  frame，chunk 本身可大于 256 KiB，只受 1 MiB raw total 限制；不得按 chunk 大小套用
  `frame_bytes`。同一 raw stream 任意切块或合并必须得到相同 frame count、解析结果与拒绝位置。
  扫描时先找 delimiter，再逐 logical frame 检查 `position <= frame_bytes`；处理完所有完整 frame
  后，唯一未完成尾 buffer 也必须 `<=frame_bytes`。
- 每一维 exact boundary 可过，`+1` 在向 SDK yield 该越界 frame 前失败并关闭 underlying stream。
  资源越界仍是 `AI_BUDGET_EXCEEDED`；结构/usage/terminal 错误仍是
  `AI_PROTOCOL_INCOMPATIBLE`。任何失败均无 answer/usage completion、无第二次 send。

1 MiB 为有证据边界：656,270-byte 合法流占 62.6%，保留约 392 KiB 给同一关闭 profile 的合法
序列化差异；4,096 frames 覆盖 2,106-frame 复现但仍限制解析放大。相比旧 256 KiB，DeepSeek
profile 最坏 raw 吞吐显式增加 4 倍，这是对旧 P1 的局部技术修订；generic 不变。新增 frame
count、单 logical frame/尾 buffer 上界，加上既有 decoded/tool/deadline/cleanup 边界，避免把
单一 aggregate cap 放宽成无界 DoS。不得再逐次抬常量或只改断言取得绿色。

内存论证只主张“常数有界”，不声称任意瞬间不超过 256 KiB：coalesced network chunk 本身可达
剩余 raw budget，当前 bytearray 也可能短暂复制接近 1 MiB；切出 frame/data/JSON 时还有至多
256 KiB 级临时副本及 Python 对象开销。实现必须在 raw check 通过后才 extend，并在每轮清除已
消费前缀，使跨 await 保留的未完成尾 buffer 不超过 256 KiB。故网络/临时内存上界由 1 MiB raw
与 256 KiB frame 共同给出，SDK 长期 retained parts 仍由 64 KiB text/tool caps 约束。

不改 `_deepseek_admission.py` 的 endpoint/model/profile id，不改 wire fingerprint、auth/key、
source attribution、授权、逐-wire/source budgets、4k excerpt、8 files、output 2000 或 service
完成条件；不新增 API、DTO、依赖、retry 或 provider fallback。

## Failure-first 测试

1. 在 `tests/test_assistant_sdk_stream_deepseek.py` 固化 enriched actual pinned-SDK many-frame
   final：`656270 raw / 2106 frames / 5704 decoded / 1800 actual output`。修复前必须仅因 raw cap
   拒绝；修复后产出同一 final answer 与 actual usage，仍只有一个 underlying transport entry。
2. 保留 `tests/test_assistant_sdk_provider.py` 的 generic aggregate exact/one-over 断言不变。
3. DeepSeek 分别覆盖 raw total、frame count、per-frame、decoded text 的 exact/+1；大 comment
   单帧仍在 262,145 拒绝，多小 frame 才能验证新 aggregate。每个失败断言零完成事件、stream/
   response/client 释放、adapter 无隐式重试。
4. 对同一合法 frame 序列分别按单字节、小 chunk、逐 frame、全部 coalesced chunk 输入，断言
   完成事件与计数相同；coalesced chunk `>262144` 但每个 logical frame 合法时必须成功。另测
   单 logical frame 与无 delimiter 尾 buffer 的 262144/262145 边界，证明没有把 chunk 当 frame。
5. 保留并复用 terminal usage、DONE、空帧、reasoning、tool fragment、deadline/cancel 测试；新增
   many-frame 缺失/重复 usage 负例，证明扩大 envelope 不会伪造 usage 或 Completed。
6. 直接 consumer 仅跑 DeepSeek stream、generic SDK provider、DeepSeek SDK/service error 定向文件；
   本裁决不授权 fulltest、live API 或重新发送已失败 live 请求。

## 符号证据

- `sdk_provider.py:44`：共享 `_CAP=262144`；`:126-128` 单 JSON parser 也用该 cap。
- `sdk_provider.py:351-366`：`_SDKWireProfile` 是应用关闭选择，不接受 provider/draft 声明。
- `sdk_provider.py:698-723`：DeepSeek wire 固定 `stream=true`、`include_usage=true`、
  `max_tokens==allocation`，final 固定 `json_object`。
- `sdk_provider.py:1145-1164`：当前 DeepSeek 与 generic 共用 stream，generic 另有 byte-based
  output gate；DeepSeek 不把 bytes 冒充 tokens。
- `sdk_provider.py:1178-1202`：usage 唯一、严格整数，terminal output 不得超过 allocation，
  reasoning/audio usage 被拒。
- `sdk_provider.py:1223-1266`：decoded text 64 KiB、tool 字段/arguments 及 framing 已分别有界。
- `sdk_provider.py:1268-1326`：raw 在 `1280-1281` 单维累计；当前无 frame-count/per-frame 独立
  resource budget，所有失败最终关闭 underlying stream。
- `sdk_provider.py:846-963`：实际 SDK 走 `request_stream`，正常终止后才组装 answer/usage events。
- `tests/test_assistant_sdk_provider.py:1190-1203`：generic raw 262144/262145 现有安全证据。
- `tests/test_assistant_sdk_stream_deepseek.py:220-235`：DeepSeek 保留 generated/raw caps 与大
  comment 拒绝；新测试必须细化资源模型，不能删除或反转该安全意图。
- `build/live-protocol-20261025/postfix-live/repro-sdk-final-framing-budget.py:61-89`：actual-SDK
  fixture 固定预算错误、零 actionable event、资源关闭和零外联断言；本裁决复用其执行收据，
  不重复运行。
- `docs/superpowers/plans/2026-10-13-code-agent-sdk-adapter.md:135-137`：旧 P1 明定 aggregate
  raw 256 KiB、generated output 与 deadline 分层。本文件把已证实的合法 DeepSeek framing 开销
  冲突登记为显式新技术需求，不追溯改写旧结论。

## 实施澄清与队列

compact enriched流由实际SDK成功校验；fragmented流使用完全相同合法字段/正文，旧代码在raw256KiB处拒绝，修复后需实际SDK完整成功，不把旧拒绝称作解析成功。
单帧frame_bytes按logical block本身计（不含LF/CRLF分隔符），raw_total仍包含所有分隔符；先找完整delimiter，再检block length/count，残留无delimiter尾buffer检查256KiB。TCP chunk合并/拆分不能改变判定。frames计数包括data/comment/DONE，原空frame协议拒绝不放宽。
S1 main inline：仅sdk_provider.py的DeepSeek私有raw/frame总量/每帧尾buffer guard及直接SDK回归；generic _CAP/既有frame解析、业务source/C5/fingerprint均不改。failure-first，指定实际SDK环境，不以base skip作通过，不真实重发。
S2 main阶段收尾：新diff一次独立复审（风险为帧边界/TCP coalescing、profile选择、所有失败零actionable与cleanup）；最终Python全量/Ruff/双平台mypy一次，前端未变609通过2todo与lint/type/build证据复用。独立build/sse-budget-20261026 wheel/冻结包/资源TLS核对，不覆盖仍存key的38864。
S3 real：当前已授权recheck已用且失败，不循环重发；新包全部独立检查完成后才交用户在App输入仅内存key/确认scope，之后一次真实验收。现场原始frame未记录，唯一live归因仍unknown，不能声称offline证明等于线上完成。

## 唯一复审收尾

Kierkegaard发现exact-size frame后分隔符内部跨TCP切分时误把前缀当正文。新增LF/CRLF逐字节分隔符RED后，尾检查排除最长“可能是delimiter的严格前缀”（最多3字节）；全部仍计入raw总量，逻辑frame正文仍<=256KiB。下一字节若不能组成delimiter，前缀重新按正文计并拒绝；EOF不完整仍协议失败。物理未完成尾最多256KiB+3，仅新增framing暂存，不扩正文/单帧边界。该修复只保持已批准的切块等价，不新增语义核风险，无第二独立复审，修后最终门禁收尾。
