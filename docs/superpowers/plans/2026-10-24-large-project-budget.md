# 大项目AI解释额度实施计划

执行遵循AGENTS自主模式；用户明确授权增大额度。保留未提交修改，不提交/推送，不发真实API，不读取密钥。
使用systematic-debugging核实预算链、writing-plans记录实施；流程不使用executing-plans/subagent-driven-development。

## 目标与决策

截图12554/20000仅为已准入累计，下一轮历史/检索或其他预算维度仍可能阻断；不从截图猜测确切失败维度。
旧软件阶段证明同批引用与限额内检索已修，本阶段明确扩大解释任务契约，不伪称旧20000合同仍适用。
解释新默认：累计输入估算100000、输出预留8192、普通工具检索8轮、模型最多10请求、工具16调用、180秒。
普通单轮最多512；最终/修复最多2000，保留最终答复空间，沿用SDK既有单请求2000上限。
architectSinger已批准带修正：普通阶段同时预留final+repair共4000；单请求仍<=2000。
8×512+2000+2000=8096不超过8192；退出检索及时生成最终答复，不在实际准入拒绝后自动重发。
来源约束仍8文件/12000源码字符/单片段4000/显式逐文件授权/完整来源闭包；源码不执行、不修改、不自动全量上传。
连接检查保持1024输入估算及原时限/低输出/次数。不新增依赖或自定义额度UI。
新默认由一个解释预算配置传到runtime/service/ledger/wire policy/planner/draft/deadline；通用基础默认可保留旧值以兼容。
Python公共投影与前端仅为explanation新增100000（兼容旧20000）；connection仍严格1024，observed_overrun依据实际声明额度判定。
输入估算不是实际tokens或账单；增大额度不是取消上限，也不触发任何真实请求或已失败任务自动重发。
基础BudgetLimits/WireAccounting默认旧值；新增冻结ExplanationBudgetConfig/EXPLANATION_BUDGET唯一生产配置，
runtime显式绑定同配置至draft/deadline/service/ledger/planner/wire policy，连接1024/128/15s原样。

离线RED：自有真实SDK假HTTP前三轮无命中搜索后再浏览索引，旧实现第四请求即final且无证据失败；
wire3475/3822/4169/4391=15857、输出预留2000，外联0；前端100000新解释用量解码也失败。

## Task L1：设计与公共前端解码

Files：frontend/src/app/assistant/types.ts、poll.ts、estimated-usage.test.ts；本计划/指南。
主代理负责。先新增100000解释用量的失败测试，再调整封闭解码与类型；保持旧值兼容、连接与超限警告校验。
验证：该测试文件及poll直接消费者、tsc、改动文件eslint。无需为常量镜像造一组重复测试。
L1已完成：新增用量测试RED（Invalid assistant poll），扩大解码后GREEN；estimated-usage/AssistantUsage
实际2文件11项通过（不存在独立poll.test.ts未计入），tsc与三个改动文件eslint通过。
L1补充用量反馈：AssistantUsage对明确的100000生产解释档显示输出已预留/8192；
旧20000和连接档保持按历史值显示，避免只看到已用值而误以为额度未变。补一条显示行为红绿测试。

## Task L2：后端预算一致性

Files：budget.py（可仅新增独立配置模块）、retrieval_budget.py、_wire_accounting.py、service.py、
_explanation_context.py、_usage_values.py、poll_public.py；受影响现有测试和少量新增行为测试。
必须先取得architect裁决；涉及跨模块准入/来源链由core_implementer实施，主代理不并行改其文件。
先复现旧额度在多轮有效检索下提前结束/失败，再证明新解释能够超过20000累计、有真实依据完成。
保留旧小额度边界/连接测试及硬拒绝，新增证明新100000/8192边界依然生效且准入拒绝无额外dispatch。
现有断言如锁旧生产2000/20k，按显式新规格改为新解释配置并保持等价超限覆盖；通用/连接旧上限测试不放宽。
定向仅预算/planner/service loop/response/integration/public poll及直接消费者，不跑每轮全量。

L2已冻结：7生产文件/5测试文件；147项定向通过25环境跳过、最终poll补测2/2通过，12文件Ruff与7文件mypy通过。
实际SDK假HTTP已GREEN：7请求，累计输入估算40489/100000、输出预留5072，有效源码evidence，外联0/释放。

## Task L3：阶段验收与打包

一次阶段独立复审，修复后最终Python全量、Ruff、Windows/Linux mypy；前端全量单worker串行、lint/tsc/build。
实际固定SDK＋假HTTP、自有合成项目：多轮累计超过20k后生成七段答复、证据有效、显示100000且无外联/资源释放。
新目录build/large-project-budget-20261024/离线wheel/冻结包，核对源码/前端资源/TLS策略，跑最终包探针。
真实用户项目问答/完整原生交互/Linux原生未验须如实保留；阶段边界进度文件追加最多5行可恢复断点。
唯一阶段复审Cicero无阻塞，已给出配置/deadline贯穿（service2221/2493/2964/3795）、
独立connection1024（connection618）、单请求<=2000、public/前端按声明limit判overrun和输出显示的具体证据。

## 验收边界

10/25门禁记录：首次前端静默reporter全量超过120秒无可见进展，按规则中断，不计通过/跳过。
未改代码再次定向19项verbose运行21.8秒全绿（此前同集合3.34秒）；最终全量改逐项报告观察真实进展，
仍单worker/串行、原断言/timeout/覆盖不变，保留首次中断日志。

100000额度贯穿后端与UI，没有一层仍20k硬限阻断新解释；连接1024不变；检索、最终和修复按同一配置计费预留。
授权/来源/取消/释放/未知用量/拒绝不重试与旧测试覆盖保留。完成后提供可直接启动的新桌面目录。

## 阶段门禁收尾修正

最终frontend完整606通过2todo，首次Python全量1638项：1500通过、2失败、136跳过。
失败1：security集成预算攻击fixture固定20001，已不超过用户授权的新100000；必须改为
EXPLANATION_BUDGET.max_input_tokens+1，原拒绝/零dispatch/不发布答案与私密错误断言全部保留。
失败2：legacy jobs测试的slow_ledger(*,clock)被新BudgetLedger(None,clock)接线误调用；
预算None分支恢复旧keyword-only调用，有生产配置分支继续传真实limits，不改任何timeout或测试断言。
由debugger确认具体调用证据后主代理做最小接线/fixture修正；不改语义核或来源不变量，不追加独立复审。
修正快照只重跑受影响测试及Python/Ruff/双平台mypy门禁；未变前端完整证据复用，重建包后重新核对源码。
debuggerZeno已以具体签名/阈值证据确认两项原因；主代理修正后原jobs34项、security7项全部通过，未改断言/超时。
修正快照实际SDK多轮仍7请求/累计40489/输出5072/source有效/100000限额/外联0/释放；Ruff和双平台mypy103通过。

## 完成记录（2026-10-25）

- [x] L1：100000解码/历史兼容/声明阈值警告与8192输出上限显示，新增2项失败先行行为测试。
- [x] L2：统一冻结配置贯穿runtime/draft/deadline/ledger/planner/policy；旧通用默认、连接和来源约束保留。
- [x] L3：唯一阶段复审无阻塞；最终Python1638项=1502通过136跳过，前端606通过2todo，Ruff/双平台mypy103/前端lint-type-build通过。
- [x] 修正后wheel/桌面包与103源码/98冻结模块/20前端/4helper/入口/TLS策略匹配，最终冻结/生产wheel探针外联0且释放。

产物build/large-project-budget-20261024/frozen-final/CodeViz/CodeViz.exe；详见该目录上两级REPORT.md。
软件切片结束，真实项目问答、完整原生交互与Linux原生未验；不宣称全部E已完成，不自动请求真实API。
