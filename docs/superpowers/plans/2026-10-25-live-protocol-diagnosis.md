# 真实应用协议失败诊断

用户明确要求真实API测试当前失败；此授权覆盖通过当前应用正常入口复测，替代本任务范围内默认不付费API的限制。
不读取、复制、迁移或持久化密钥，不抓取原始请求/响应/用户源码，不在命令行传key，不自动购买/升级额度。
仅使用用户当前已配置应用及当前有效授权项目；按钮禁用/授权失效时不绕过，不能取得有效授权则只做独立离线诊断。
截图既有失败：5请求，输入估算54693/100000，输出预留2560/8192，实际报告15404/748，协议错误无具体阶段。
这些计数不能证明是哪种异常；先通过只含固定错误码/阶段/HTTP数字/usage的闭合诊断定位。

## 队列

1. 主线程只读核对当前CodeViz进程/版本及上述固定UI状态，不读密码框或原始模型文字。
2. debugger并行核查真实多轮工具参数、最终wire来源归属和错误阶段丢失；无根因不随机改约束。
3. 在当前UI正常授权有效、无待发送草稿、无活动任务时，执行一次“AI分析项目/更新AI架构”，
   最多100000输入估算/8192输出/10请求/180秒，保留应用8文件/12000源码字符等上限。
   默认最多两次完整真实分析（第二次仅用于修复后验收），不因失败自动循环重发。连接测试不替代业务验收。
4. 若根因需要新诊断，添加不含文本/路径/参数值的固定阶段与错误枚举，保留真实异常类型的隐私过滤。
   语义核/来源证明修复按core/architect必要路由；普通UI/接线由主线程；不放宽来源/授权来掩盖错误。
5. failure-first针对真实问题补最小离线回归；定向模块与直接消费者；仅阶段边界全量/Ruff/双平台mypy及唯一复审。
6. 新目录build/live-protocol-20261025/交付，保留旧窗口/内存key与用户未保存内容，不强制关闭或迁移。
   阶段末更新进度最多5行，真实阻塞留当前任务/最后命令/剩余/下一步。绝不将离线模拟称为真实服务商验收。

## 已取得的真实证据

旧App PID48992来自large-project-budget包。UIA只读匹配原图六个数字均存在、grant有效、无活动任务/草稿。
最初offscreen Invoke接口阻塞，未确认网络发出且计数未变，已停止辅助进程，不计真实成功；后续滚动到可见后正常Invoke成功。
真实项目分析：新计数13779/100000、输出1536/8192、3请求，actual汇总未知/已知输入1761，AI_BUDGET_EXCEEDED，无架构。
第二次受控提问（只检索1项并读3行）：9099/100000、输出1536/8192、3请求，actual未知/已知输入1682，同预算错误。
只保存上述闭合数字/错误，未保存用户源码、原模型内容或密钥。旧进程继续保留，不再重复真实请求。

## 架构裁决及明确契约修正

architectLovelace批准仅修DeepSeek profile，不迁移SDK/全局provider，不新增tokenizer。
sdk_provider.BoundedStream.charge原把UTF8字节（含tool ID/name/args/JSON封装）与token预留512比较，单位不成立。
指定SDK环境的独立用例已RED：200个中文字符600字节，服务商声明200 output tokens，预留512，仍被旧代码AI_BUDGET拒绝。
Builder环境的首次用例因固定测试环境要求skipped=1，明确非通过；有效RED来自assistant-sdk-probe-20261012，skipped=0。

- DeepSeek取消bytes<=max_output_tokens比较，generic strict保留旧证明路径；保留raw SSE256KiB、id/name512B、args/payload8KiB、文本/答案64KiB及服务响应256KiB资源界限。
- 请求max_tokens仍512/2000，累计8192硬预留/不退款；只有完整terminal与真实usage验证completion_tokens<=预留后才发布可执行tool/validated answer。
- DeepSeek未知/缺失usage、异常EOF、实际超预留不得发布actionable completion且停止后续请求；绝不能用bytes伪造actual/允许未知继续。
- 字节上限仅证明内存/解析资源有界；token约束由请求上限和terminal实际报告校验，不是账单保证。明确覆盖旧DeepSeek“unknown仍可发布最终answer”的测试前提，generic不变。
- 保留DeepSeek最后带finish_reason的单choice同帧usage；不改成空choices usage-only尾帧。
协议来源：https://api-docs.deepseek.com/api/create-chat-completion/（max_tokens及stream_options，10/25已核）。

工具自然链路另有确定缺陷：search给symbol ref而relations拒symbol，模型直接复制会整job protocol失败。
模型schema增加symbol，仅dispatch按definition/同ID/kind/name/qualifiedName/scope/filePath/唯一source anchor全部相同证明转code；
无精确映射返回现有unavailable结果，不猜ID或降置信度；基础RelationsRequest/index strict接口不改。
协议opaque诊断复用现有boundary：tool参数/模型DTO=result_validation；投影/注册/transcript=pre_send；transport/cleanup=dispatch。
不输出args、路径、源码、ID、异常原文或key，不把普通调用错误隐藏成服务商不兼容。

## 实施分工与验收

P1 core_implementer：sdk_provider、tools、_explanation_context、service及必要最小helper/测试；不改Parser/Resolver/IR或来源授权，后端独占。
failure-first覆盖合法中文/长工具JSON、实际超token/未知/EOF零actionable、generic旧界限；symbol自然链路的强同一性与无映射；诊断只闭合字段。
仅跑SDK/工具/service直接消费者，指定SDK环境不可将skip记通过；不每任务全量或独立复审。
P2主线程：修UI旧失败issue跳过新成功/running任务仍显示的选择问题，一条行为红绿，不掩盖最新真实错误。
P3阶段一次独立复审、最终门禁/重建/字节与离线探针。真实修复后验收如需新进程key，只能用户在App填入，不能迁移旧内存key；
到这个必要人工步骤之前先完成全部可独立的实现、测试和具体新包，不把离线修复当真实服务商通过。

## 唯一阶段复审收尾

Euclid发现public iterator/aclose cleanup重建错误丢失dispatch；repair pre_send被吞为ResponseError；UI未隔离consentRevision。逐项补回归并修正投影/错误传播，不改语义核不追加二次复审。
cleanup回归初写假定public diagnostic含version/空httpStatus，经issues.ProtocolDiagnostic.to_public既有契约核对，实际只含boundary、存在HTTP值时才附httpStatus；修正新断言严格匹配该闭合字段集合，未放宽生产契约或移除敏感文本/资源归属断言。
最终全量发现两个legacy证据失败回归，Banach按实际触发原因修正：repair构造在legacy send捕获范围外，保留pre_send；repair发送有闭合diagnostic时原样传播，无diagnostic的legacy协议失败仍映射既有AI_EVIDENCE_INVALID。原service_response断言不改，新增一条有dispatch诊断的repair发送用例；错误传播/投影收尾不触及语义核，不追加独立复审。

## 修复后一次真实业务验收

用户已在最终修复版准备项目、密钥与文件授权并回复“已就绪”，继续执行一次正常应用项目分析。旧版两次诊断不再重复；本轮只一次主动业务动作，不循环重发。预先核对最终exe哈希/进程、应用有效grant、空草稿及无活动任务；任何条件不满足即不发送。
独立收据目录build/live-protocol-20261025/postfix-live；在正常按钮Invoke前用CreateNew记录单次marker，调用不确定不得重发。只保存固定状态/错误阶段/HTTP数字/usage/控件存在及导航成功的闭合事实，不保存密钥、源码、原始模型文字或参数。
UIA测试驱动存在WebView可访问树间歇缺失，debugger仅修本地测试助手、不得触发模型请求。未改生产源码不重跑已通过软件门禁。真实验收需核对可信结果、源码引用和AI架构；仅按钮调用、连接成功或临时流文本不算业务通过。

## 新复现后的计量可见性

真实结果仍预算失败，现有输入/输出计数不足以唯一归因。主线程仅在现有Usage组件显示后端已提供的reserved.sourceCharacters/fileCount/toolCalls，不新增API字段、不把预留称为实际披露、不猜未声明的资源上限；中英沿用现有布局。此普通UI工作独立于待架构裁决的源码留量算法，先一条行为回归再实现；全量仅新修复阶段边界执行。
