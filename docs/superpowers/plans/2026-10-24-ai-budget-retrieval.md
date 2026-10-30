# 项目AI分析预算失败修复

执行：AGENTS.md自治，保留现有修改，不提交/推送、不购买额度、不使用真实API或用户项目源码。

## 已有证据

截图为estimated_limit；3475是已准入预留量，不包括被拒绝的下一轮请求。预算合同保持累计20000输入估算、
2000输出、8文件及完整来源证明，不通过加阈值、漏计历史或伪造已知用量掩盖失败。
build/ai-budget-20261024/reproduce.py使用真实快照/工具投影和合法20条长名称符号，首轮3475/512、
实际1026/157，下一轮最终wire估算32917，被拒且累计仍3475；外联/真实请求0。
该样例复现同型错误，不声称读到了用户真实工具调用或实际下一轮大小。

## Task B1：准确的阻塞说明（独立前端任务）

- [x] failure-first：保留3475/20000显示，明确下一轮被拒且上方仅此前预留；新增测试原文案RED。
- [x] 仅修改i18n固定中英文案，保留实际/估算/未知用量区分，不展示任何原始请求或响应。
- [x] AssistantBudgetReason/Usage/Panel共11项GREEN，前端typecheck/改动文件lint通过；未跑未变全量。

## Task B2：预算感知检索与最终答复

独立architect Jason已实际读源码并明确“批准带约束，无设计阻塞”（首次自述角色失败已澄清，不作为批准证据）。
SourceTraceability相关实现交core_implementer；主代理只修改B1文案、文档和交付脚本。

- [x] 输出必须先留512 final，ordinary=min(512,2000-used-512)；不足时提前final，保留原draft512语义。
- [x] ToolLoopTranscript增加不变异的final预览，逻辑UTF8体积只用于保守路由，不伪称SDK真实wire上界。
- [x] project_tool_result可接受私有投影预算：保留确定性事实子集，逐个保留完整provenance闭包；省略项不转发ID/锚点/targets，只显示省略数量与覆盖缺口。
- [x] 服务在prepare/admit之前按剩余预算安排检索、紧凑投影和提前final；保持同代次、授权、来源验证和累计账本。
- [x] provider.py/sdk_provider.py/_explanation_context.py不改，不新增abandon，不在admit拒绝后压缩重试或重新发网络请求。
- [x] 红绿覆盖3475案例/512最终预留、投影闭包/遗漏不可引用、提前final、真实wire最终拒绝零网络；仅受影响模块测试。

B2：12项新增测试与45项直接消费者回归通过，三后端文件Ruff/mypy通过。
真实SDK假HTTP大索引样例Completed，3请求wire1829/6224/7427，累计15480，
输出预留1536，sourceEvidence=true，externalAttempts=0，资源释放；不是服务商在线验收。

目标为在原限额内控制检索与工具返回体积、为最终七段答复保留空间；不在准入拒绝锁定后自动重发。
失败仍保留真实预算原因；无法完整覆盖时明确范围/缺口，不宣称完整解析或静态事实。
使用合成大索引与实际SDK假HTTP复现/验收，不上传用户项目。

## Task B3：阶段交付

唯一阶段复审发现两项覆盖说明问题：原始local_relations.truncated必须让最终答案标incomplete；
搜索API本身的limit分页不可误称预算裁剪。收尾只修私有投影覆盖说明与service缺口传播，
保留provenance/targets/receipts、Parser/Resolver/Graph IR及预算合同不变，不再独立复审。
新增失败先行用例证明自然分页与预算裁剪区分、关系原始截断传播，再运行适用门禁。
自然搜索分页同样保留真实的覆盖缺口并传播incomplete，但不冒称预算裁剪；
omittedCount只计算本轮投影省略的已返回事实，原工具截断未知数量不伪造数值。

前端门禁运行证据：默认并行下7项UI等待/超时失败，第二次双worker无进展120秒已中断。
debugger对原6文件54项单worker验证全绿，并验证新增预算文案测试；没有改断言/timeout/配置。
最终改用目标Node22、单worker且关闭文件并行完成同一全量，保留前两次失败/中断记录。

适用模块测试后唯一阶段复审，最终快照全量Python/Ruff/双平台mypy及前端suite/lint/typecheck/build，
未变证据复用；离线构建新目录并核对源码/资产/真实TLS与假HTTP。
原生/真实业务验收如受阻如实报告。阶段边界或真实技术中断仅追加最多5行进度与可恢复断点。

### B3 全量发现的来源绑定回归（同阶段收尾）

首轮Python全量1631项：1494通过、136跳过、1失败。既有saved-snapshot集成路径
同批先返回relation再读取源码；预算重构把choose_target提前到投影注册之前，可能丢失
同批已转发关系的来源绑定，最终回答校验后触发修复且预算不足。由debugger确认、core修复。
只从已选中转发的投影按原调用顺序累积targets后再选取源码绑定，省略关系绝不能获信任。
这是新暴露的Source Traceability风险；允许且必须对该最小绑定修复单独独立复审一次，
仅审该新diff，不重审此前覆盖说明/预算不变diff。随后在修复快照跑适用阶段门禁，重建产物。
debuggerMeitner已确认：call-source提前选择得到None，response判invalid_relation后进入repair，
AI_BUDGET_EXCEEDED为二次症状。保留原三请求、源码及CALLS引用/两种导航验收，不改旧集成测试。

### B3 实际SDK首轮3475的输入留量验收

将自有fixture问题加1646字节，使首轮实际wire精确3475；原4096 metadata软cap下，
3请求3475/7915/8299虽未超总额，但后续完整源码被省略，final缺依据，验收失败。
仅离线monkeypatch软cap3072的假设实验完成：3475/6284/7487=17246、源码246字符、
真实evidence、3请求、外联0/资源释放，标hypothesisOnly，不当作生产已通过。
决策：内部metadata投影软cap改3072，为后续源码检索留量；仍完整保留选中事实的来源闭包，
省略原因/数量与incomplete保留，不缩减源片段、不提升20k/2000合同，不在admit后重发。
这只调已复审投影的保留规模，不改变Source Traceability不变量，不追加独立复审。
要求实际生产SDK long-question probe无monkeypatch通过且带证据；不允许empty unknown冒充验收。
debuggerGodel已从capped/fits与实际对照确认原因并认可最小软cap修，无需新调度架构。

## 阶段交付结果

- [x] 两项覆盖说明修复、同批来源绑定修复及源码信任回归；新绑定diff唯一独立复审无阻塞。
- [x] 首轮3475的生产实际SDK假HTTP无monkeypatch验收：3请求累计17246，源码246字符/evidence有效，外联0/释放。
- [x] 最终Python1632项：1496通过136跳过；Ruff、Windows/Linux mypy103全绿。
- [x] 未变前端最终完整证据：604通过2todo；lint/typecheck/build通过，保留初次失败与中断记录。
- [x] 重建wheel/桌面包，源码/资源/运行时TLS匹配及冻结探针、生产wheel离线验收通过；详见任务REPORT.md。

软件边界完成；真实用户项目AI问答、完整原生交互与Linux原生仍未验收，不宣称全部E完成。
最新目录build/ai-budget-20261024/frozen-final/CodeViz；旧窗口不自动更新，密钥仅在进程内存。
