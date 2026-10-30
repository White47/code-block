# DeepSeek explanation final-output admission：最小架构裁决

## 实施队列

F1 core_implementer按本裁决实现预算/冻结job/C5阶段准入/SDK结构与资源档/紧凑提示及直接回归；failure-first，仅定向测试，不发真实API。
F2 主代理一次新diff独立复审，修正发现后运行最终Python全量/Ruff/Windows+Linux mypy；前端未变，复用protocol-site阶段611通过2todo及lint/type/build有效证据。build/final-output-20261026独立wheel/frozen及源码/资源/TLS/假HTTP探针；保留旧17012及内存key，不覆盖旧包。
F3 软件边界通过后才准备新正常窗口和同一项目；仅在用户输入新进程个人key后单次真实验收。旧protocol-site live marker已使用，禁止重发；公开记录terminal_finish/length、实际累计未知，不把新4096上限当业务成功证明。

## 结论

批准新增一个**应用自有、仅 DeepSeek explanation 使用**的冻结预算档：

- ordinary：最多 4 轮，每轮 512；
- final：4096；
- repair：至多一次，2000；
- 总输出仍为 8192；最坏预留 `4*512+4096+2000=8144`，保留 48；
- max requests 10、input 100000、source 12000、files 8、tools 16 均不变。

`EXPLANATION_BUDGET` 的 generic 默认（8/512/2000/2000，合计 8096）原样保留；probe
仍为 128。此次是 ordinary 深度换 final headroom，不增加一次作业的总 token/付费预算。

事实：本次 live 的封闭诊断是 `terminal_finish/length`。这证明 2000-token final 被截断，
但没有 terminal usage，因此不证明 4096 一定足够。官方也只建议为 JSON 输出设置合理
`max_tokens` 以避免截断；4096 是有界兼容修复，不是成功保证：
https://api-docs.deepseek.com/guides/json_mode

## 冻结选择与发送授权

1. `_deepseek_admission.py` 在应用装配时构造唯一 DeepSeek budget；裸/通用 runtime 继续使用
   `EXPLANATION_BUDGET`。不得按用户 URL、模型字符串或响应内容选择预算。
2. `service.py` 接受私有 budget 注入；创建 explanation job 时把完整 frozen config 放入
   `JobRequest`。`jobs.py:JobRequest.digest()` 纳入其 canonical 数值，初始 draft 必须等于
   `final_output_tokens`。worker 后续只读 job 内这份配置，不能再读可替换的 runtime 默认。
3. `retrieval_budget.output_allocation` 对配置化 explanation 改为 stage all-or-none：余量不足
   512/4096/2000 时返回 `None`，不得静默生成 tiny ordinary/final/repair。首个 prepare 前验证
   总和及请求数；失败在 pre-send closed。
4. SDK prepare 只做结构性接受：仅 frozen DeepSeek wire profile、非 probe、`mode=final` 可接受
   `max_tokens<=4096`；generic 和其他 mode 仍 `<=2000`，probe 仍精确 128。prepare 不等于发送授权。
5. 现有 C5 `send_policy.admit(prepared, phase)` 是 purpose authority：它必须用 job 的 frozen
   config 检查 exact allocation——ordinary 512、final 4096、repair 2000——再原子预留 ledger。
   repair 与 final 虽同为 wire `mode=final`，不得从 body 推断 phase。admit 后既有 frozen fields、
   fingerprint、handoff 与 dispatch recheck 保留 body/max allocation；真正拒绝后不重试。

现有 prepare→C5 admit→frozen handoff 链已经携带 `phase`、body 和
`PreparedRequest.max_output_tokens`，因此不新增 `ProviderDraft` capability/grant 或公共契约。
若实现检查发现 phase 在 admit 后丢失，唯一允许的 fallback 是增加私有 frozen
`StageAdmission(phase, allocation, job_digest)`：由 service 在当次 admit 签发，仅该 prepared ID
可用，单次消费，deadline/config/consent/scope freshness 沿现有 job authority；不得由 SDK、响应
或调用者自签。

## 配套 SSE 资源档

4096 final 若沿用 DeepSeek 1MiB/4096-frame 档，会对合法高碎片输出形成已知的新拒绝面。因此
仅对上述可信 profile、非 probe、final phase、exact allocation 4096 启用：

- raw cumulative：2 MiB；logical frames：8192；
- per logical frame 256 KiB、decoded answer 64 KiB、tool args 8 KiB 均不变；
- DeepSeek allocation `<=2000`、repair、generic、probe 保持各自现有 cap。

资源档在 prepare 时由 frozen trusted profile + validated mode/allocation 选定，并随 send binding
冻结；dispatch 前由 C5 exact phase admission确认。不能由 response、reported usage 或 draft 数值
单独升级。TCP chunk 可合并多个 logical frame，也可拆分 frame；应先按 SSE 边界解析再应用
per-frame cap。单个网络 chunk 可接近 2MiB，内存论证不得声称任何瞬间都小于 256KiB。

## 内容与失败语义

- `ToolLoopTranscript.final/repair` 增加固定简洁指导：有效 JSON、避免复述、优先证据/结论/
  coverage gaps；不得删除 SourceAnchor、绑定证明或真实 gap。
- ordinary 达 4 轮或 source headroom 提前结束时，沿既有机制真实声明 coverage gap。
- `finish_reason=length` 仍为 `AI_PROTOCOL_INCOMPATIBLE`，保留现有 diagnostic；无 terminal usage、
  不发布 Completed、不进入 repair、不自动重试。repair 仅用于已完整 stop 且本地 JSON/schema
  校验失败的既有路径，至多一次 2000。
- 不改 source/file/excerpt/secret/privacy、actual terminal usage、raw/frame/text 之外的 cap、
  key/auth/fingerprint、错误码、API/DTO/UI 或依赖。

## 文件所有权

- `budget.py`：保留 generic 常量；结构 ceiling 显式允许 final 4096、repair 仍 2000、总和不变。
- `_deepseek_admission.py`：拥有并注入 exact DeepSeek budget，禁止外部配置注册。
- `jobs.py`、`service.py`、`retrieval_budget.py`：job freeze/digest、phase exact C5 admission、
  all-or-none allocation 与 honest early-final。
- `sdk_provider.py`：profile-scoped structural 4K gate及 2MiB/8192 resource profile；generic 不变。
- `tool_loop.py`：仅固定 compact final/repair guidance。

## Failure-first 定向测试

1. budget：generic tuple 仍为 8096；DeepSeek tuple 为 8144；第五 ordinary、partial stage、
   final>4096、repair>2000、总和>8192 均 pre-send 拒绝且 ledger 原子不变。
2. service/C5：四次 512 后 final 4096，可选 repair 2000；phase/max/profile/job digest 任一篡改均
   在 dispatch 前拒绝，transport entries=0。三 ordinary 的 live 形态预留 final 后仍可进入 final。
3. SDK：可信 DeepSeek nonprobe final 4096 可 prepare；generic 4096、DeepSeek tools 4096、probe
   非128、repair phase 4096 均拒绝。prepared/fingerprint/handoff allocation 改写不得发送。
4. SSE：allocation4096、reported usage3500、合法 stop+terminal usage、raw>1MiB 且 frames>4096
   在 2MiB/8192 内成功；分别 exact cap 成功、`+1` fail closed。coalesced 与 fragmented TCP
   输入等价；失败后资源关闭且无第二 dispatch。
5. 回归：DeepSeek `<=2000` 仍 1MiB/4096，generic 仍 256KiB；per-frame 256KiB、decoded 64KiB、
   args 8KiB 不变。`length` 仍失败且零 repair/retry/Completed；compact guidance 不丢 source proof。

## 符号证据

- `budget.py:63-68,83-95`：当前 final/repair 2000 ceiling 与 generic 8096 配置。
- `retrieval_budget.py:113-140`：当前阶段预留及可能 `min(..., remaining)` 的 partial 分配点。
- `service.py:2600-2608,3031-3072,3382,3884-3892`：阶段 allocation、ordinary loop、单一默认
  budget 与初始 final draft。
- `jobs.py:68-115`：frozen `JobRequest` 及当前未包含 budget 的 digest。
- `sdk_provider.py:229,991`：两处全局 2000 gate；`:49-51,1171,1317-1331`：现有 generic
  256KiB、DeepSeek 1MiB/4096 与 raw/frame enforcement；`:1224`：actual output 不得超 allocation。
- `tool_loop.py:467`：final draft owner；preview 路径复用同一 final 构造。

## 安全 fallback

若不能证明 exact phase C5 binding 或 2MiB/8192 边界测试，禁止发布 4K 档：保持现有
2K/generic/SSE 配置，只可加入 compact guidance 与继续公开 `terminal_finish/length`。该 fallback
安全但**不算修复本次 live failure**；不得改成降低 repair、提高总 8192、自动重试或宣称成功。

## F3后续：累计工具历史兼容修复

真实验收4次ordinary已完整结束，但final尚未准入即plain protocol失败；离线实际SDK复现合法9条历史被sdk_provider._draft的累计len(seen)<8拒绝，与DeepSeek应用作业16次工具上限不一致。合成形状吻合，不冒充已采集真实参数或唯一线上触发点。
H1主代理内联：仅可信DeepSeek非probe的累计tool-call历史允许16；每条assistant消息/单次响应最多8保持不变，generic/probe累计8保持不变。ID全局唯一、每个结果配对、参数/正文/source/预算/已冻结字段守卫全部保留，不增加作业工具或付费总额。直接failure-first测试覆盖16/17、单turn9、重复ID、generic9与正常4ordinary→final真实SDK假HTTP链。
本轮还补齐三个原为空的工具说明：search_symbols支持空query浏览已授权索引、按字面匹配不支持通配符；源码读取须用搜索返回的真实相对路径/行号，关系查询须用已获得ID。这里只解释已有合同，不改参数、搜索算法、授权范围或seed行为；index_tools已有空查询回归复用，不为描述文字新增镜像断言。
唯一复审要求避免discoverability再次退化：在既有工具schema链路测试中补充“说明非空、空查询/真实相对路径/已获ID能力提示”的最小合同断言，不镜像整句文案或增加新测试批次；使用旧baseline函数做RED对照，当前实现做GREEN。该补正仅测试，不需第二独立复审。
H2一次新diff复审、最终Python/Ruff/双平台mypy与独立tool-history包/字节/假HTTP探针；前端及未变SDK大流证据复用。H3仅在新进程用户key与项目范围就绪后单次真实验收，旧10196 marker已使用不得重发。该SDK兼容与工具说明修复不改架构或公共DTO，按AGENTS默认由主代理内联执行。
