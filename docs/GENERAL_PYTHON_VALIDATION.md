# 通用 Python 验证记录

## 修改前基线

分析基线 9c09fd9，Python 3.12.14。三份官方仓库固定提交下载在已忽略的 work/general-python-corpus；仅静态扫描指定源目录，未安装 / 导入 / 执行目标代码，扫描前后源码摘要一致。数字不等于正确率，必须另行核对语义与源码锚点。

| 仓库 | 固定提交 | 许可 | 扫描范围 | 解析成功 / 文件 | 符号 | issues | 分析耗时 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Click | 06b2a678741131fd577ce170e23e5ca0aeba0309 | BSD-3-Clause | src（只含 click） | 17/17 | 3845 | 2214 | 8.092s |
| Requests | 611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60 | Apache-2.0 | src（只含 requests） | 19/19 | 2054 | 1059 | 3.765s |
| HTTPX | b5addb64f0161ff6bfe94c124ef76f6a1fba5254 | BSD-3-Clause | 仓库根（只含 httpx） | 23/23 | 3021 | 1467 | 5.731s |

来源样本：[Click command](https://github.com/pallets/click/blob/06b2a678741131fd577ce170e23e5ca0aeba0309/src/click/decorators.py#L168)、[Requests get](https://github.com/psf/requests/blob/611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60/src/requests/api.py#L74)、[HTTPX get](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_api.py#L174)、[HTTPX AsyncClient.get](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_client.py#L1751)。

Click / Requests 概览入口为空、默认 Execution 0 节点。HTTPX 的 validate_json / validate_auth / validate_path 被名称规则误识别 Evaluate，Execution 3 节点；这不是正确识别训练入口的证据。

主要 issues（Click / Requests / HTTPX）：AMBIGUOUS_SYMBOL 811/408/222，DYNAMIC_ATTRIBUTE_UNRESOLVED 1098/547/1025，UNRESOLVED_SYMBOL 76/20/90，DYNAMIC_CALL_UNRESOLVED 71/23/17，DYNAMIC_DISPATCH_UNRESOLVED 158/61/97；HTTPX 另有 16 个 STAR_IMPORT_UNRESOLVED。后续不应通过删除诊断或将未知升为 Exact 来美化数字。

## 本轮验证基线

- 原后端 unittest：376 项，49 跳过，通过。
- 原前端 Vitest：6 文件 / 41 项，通过。
- Ruff、mypy（默认与 Linux 平台）、前端 lint / typecheck：通过。
- 前端生产构建、桌面 CheckOnly：通过；不等于真实窗口验收。
- wheel：隔离构建因网络受限不能下载构建依赖；使用已核对版本的本地依赖、--no-build-isolation 成功构建。这个环境限制不作为源码失败。
- 新增首批回归红基线：16 项 / 17 个断言失败 / 0 错误，均对应缺失能力；实现后的新证据另行记录。

## 完成门槛

实施记录必须包含新回归转绿、全量检查、两阶段独立复审、固定仓库前后对照、真实桌面普通函数路径和原 ML 路径。以下中间记录保留历史状态；当前整合证据以文末最新检查点为准。

## 2026-10-04 中断后复核

代理曾因额度限制中断；用户再次要求继续后重新派发原任务，不切换弱模型、不覆盖已落地文件。

- 首批普通流程 7 项、绑定 9 项已转绿。Task2 默认值 walrus / nonlocal 错误归属及隐式作用域泄漏 ML 事件已修复并通过独立需求复审；质量审查仍未完成。
- Task1 独立需求复审仍发现：无关 torch 导入 / 仅 optimizer 名称可错误满足 ML 入口证据；GeneratorExp 缺少 CFG 限制诊断。必须修复后重新审查。
- 当前通用专项测试：52 项，23 个失败断言。对象关系、普通数据与 finally / match 控制路径仍为未完成能力，不能报告全量测试通过。
- relations.py / value_candidates.py 已有中断中的实现，但尚未验收；Ruff 检出 2 处导入格式，mypy 检出 value_candidates.py 的 2 处类型错误，已交原任务继续修复。
- 真实 WebView2 窗口已启动并成功渲染普通项目切换；测试脚本 session 读取 / 等待失效导致后续检查未完成。没有以截图存在冒充 UI 路径通过。
- 语料验证脚本默认目录漏掉下载压缩包的固定提交目录前缀，三份语料尚未复验；已交验证作者修正路径与回归测试。

上述均为实施检查点，不是最终交付。保留全部源码、测试与用户原有未跟踪文件，未提交 / 推送。

## 整合复核（2026-10-04）

- 固定 `build/remote-9c09fd9` 的 HEAD 再核验为完整基线 SHA；以相同包上下文重扫 17 / 19 / 23 个源码文件，符号与 issues 数量和上表一致。单次计时为 8.520 / 3.890 / 6.193 秒，不作为性能改善证据。
- 新前端 8 文件 / 43 测试、lint / typecheck / build 通过；后端 Ruff 与 Windows / Linux mypy 56 文件通过。桌面 CheckOnly 通过，但不代表最终冻结包验收。
- Task1 需求审查通过；Task2 需求与质量复审通过，包括无效 global / nonlocal 和 lambda-walrus 训练隔离。Task1 质量审查发现预算聚合节点不可选择，正在修复。
- Task3 对抗审查发现重绑定、短路、推导式 walrus、捕获与继承遮蔽等八个候选传播缺口；Task4-A 发现视图旧来源、解构删除、绑定诊断与来源预算缺口；Task4-B 发现预算耗尽后续函数缺 Partial 提示。已交原作者分别修复，不能以各专项通过替代规格达标。
- 全量阶段性运行 543 测试 / 49 跳过，剩一处同源码两次语义图相等断言失败；单独重复 12 次通过，正在检查并发文件变化或共享状态，不删除稳定性断言。
- 普通 WebView2 源码路径验证报告 `build/general-python-native/report.json` 有五个通过检查，errors / pageErrors / consoleErrors 均为空。原 ML 五项原生黄金路径也已通过选择器修正后的验证；最终版本仍需重新验证。

所有结论限定到对应检查时的源码版本，最终全量与打包证据另行补充。

## 后续检查点（非最终收尾）

Task1 与 Task2 的两阶段独立审查已通过，Task2 的 Controller Partial 集成也已核验。旧 fixture / native selector 调整保持原验收断言，独立审查批准；harness 的输出硬链接风险通过原子替换修复，fsync / replace 失败与写后源变更的安全复审通过。早先单次语义图相等失败未在 380 次定向 / 顺序 / hash-seed 复核中重现，未盲修或删除稳定性断言；后续全量 582 项、49 跳过已通过。这里与 543 / 565 项记录是不同实施快照。

Task3 和 Task4-A 需求审查已通过，但质量审查随后发现 DAG / 嵌套循环总工作量预算，以及推导式候选、finally 数据上下文的缺口。已进入原作者修复和独立复审，当前不宣称最终完成。Task4-B 需求与质量审查均通过。

已重建 Windows 包（仅提权重跑同一配置解决环境目录访问问题，未改权限 / 依赖）；冻结包普通 Python 五项与原 ML 五项原生交互通过，页面错误为零。最新 wheel 在既有隔离环境安装、源码目录外启动，五项普通原生交互通过。新临时 venv 缺 `[desktop]` 造成的 helper 启动失败已定位，不是产品代码缺陷。测试窗口正常关闭，静态服务端口停止；仅操作测试所属进程。

上述产物证据对应预算修复前的快照。待质量问题关闭后必须再次重建并验证，不将这些报告冒充最终版本。

## 最新代码与产物检查点（2026-10-05）

Task1、Task2、Task3、Task4A / 4B 的需求与质量复审均批准。Task3 最后复审独立运行 107 项测试，验证 DAG 工作预算 / memo、推导式迭代重绑定和权威 nonlocal owner；Task4A 最后复审验证实际访问预算、finally unknown 与缓存往返。主代理独立运行新增五项预算验收通过；这些独立验收测试加入时修复已经存在，不能虚称它们观察到了生产代码 RED→GREEN。

- 后端：604 项、零失败、49 跳过；跳过保留原有环境条件，不将跳过视为实际运行通过。
- Ruff（src / tests / scripts）、mypy Windows / Linux：通过，类型检查各 56 文件。
- 前端：8 文件 / 43 项测试，lint / typecheck / production build 通过；桌面 CheckOnly 通过。
- wheel 与 PyInstaller Windows onedir 重建成功；本机 Python 3.12.14。没有执行 Python 3.11 运行时或远程 GitHub CI，不将 mypy 平台目标视为运行时测试。

固定语料最终报告：`build/general-python-corpus-final/report.json`，四个范围均 verified，源码内容和文件身份指纹前后一致、无分析器内部错误；抽样函数图受 40 节点 / 80 边预算约束，缓存恢复无 AST 且 facts / views / overview 相等。报告中的 `feature_complete=false` 是刻意保留的语料验证边界，不是完整 Python 语义通过声明。

| 范围 | 解析 / 文件 | 符号 | 可浏览函数 / 方法 | 单次 Analyze |
| --- | ---: | ---: | ---: | ---: |
| Click 固定 src / click | 17/17 | 3859 | 586 | 16.927s |
| Requests 固定 src / requests | 19/19 | 2045 | 268 | 8.238s |
| HTTPX 固定 httpx | 23/23 | 3130 | 446 | 12.694s |
| 原 ML 夹具（含故意坏文件） | 3/4 | 67 | 11 | 0.184s |

计时发生在其他验证 / 构建并行期间，不能与基线或之前快照比较并声称性能改善。Click 有 32 个文件工作预算 Partial；它们明确表示普通数据分析范围省略，不是 AST 解析失败。finally、跨函数调用、捕获绑定和异步限制仍见 Problems，未删除诊断来美化数量。

人工源码抽样限定：Click 的动态 callback 未硬连唯一目标，重载函数保留多候选；Requests 的 Session 构造有候选，但 context manager 返回接收者仍可能 Unresolved；HTTPX Client.send 的项目方法与继承辅助方法有 Probable 候选，response.read / close 不强制确定。普通模块不再因 validate / export 命名伪造训练入口。公开定义抽样使用 Command.invoke、pass_context、Session.request、api.request、Client.send；唯一索引、真实 def 行与图锚点由报告校验。

最新产物原生交互：

- `build/general-python-packaged-native-final-user/report.json`：冻结包普通五项全部 passed，skipped / errors / pageErrors / consoleErrors 为空，cacheIssue=null。
- `build/general-python-packaged-ml-native-final/`：同一冻结包原 ML 五项通过，stdout 验收 JSON 的 pageErrors=[]；保存实际截图 / DOM，不虚构此脚本不存在的 report.json。
- `build/general-python-wheel-native-final/report.json`：最新 wheel 在既有隔离 venv 仅安装本项目包，从源码目录外启动；确认 import 与生产资源来自 site-packages，普通五项 passed。

首轮新冻结包测试 `build/general-python-packaged-native-final/report.json` 为 failed，保留记录：沙箱账户创建缓存（Owner=CodexSandboxOffline），实际 GUI 用户无法写入。三个交互已通过但缓存设置失败，关闭窗口后 TargetClosedError 退出。未改 ACL / 源码；用 GUI 相同用户创建新的独立测试缓存后上述五项重跑成功。所有本次测试窗口正常关闭，CDP 和静态资源服务端口均已停止。

产物 SHA256：

- `dist/general-python/CodeViz/CodeViz.exe`：`c575f0878a865cfe2ac2b2577ef27558bd14528f22663e80a7dfe1293ebf3d33`。
- `dist/general-python/code_module_visualizer-0.1.0-py3-none-any.whl`：`4f80c7938fe2144a8194a681da909d2c7f7f868ae990d36996bccb9bd603c3a1`。

分发必须包含整个 `dist/general-python/CodeViz` 目录，不能只复制 exe；Windows 仍需要系统 WebView2 Runtime。本轮未提交 / 推送、未新增依赖，保留用户 AGENTS.md、.codex/ 与 MODEL_ROUTING.md。整体独立审查仍待单独记载，不以分阶段批准冒充最终批准。

### 整体审查新发现（待修复）

整体审查未批准：ValueCandidates 的每循环迭代上限不能限制嵌套循环累计遍历。独立复现 8 / 10 / 12 层循环分别产生 1026 / 4098 / 16386 次表达式访问，没有触发单层循环预算。已交原核心作者补累计工作预算、独立测试作者捕获失败基线、架构师确认未知 / 截断传播；不会以 604 测试或已通过产物交互掩盖此缺口。上述 SHA 和界面证据仍有效对应这个快照，修复后必须重建重验。

## 累计候选访问修复后的发布候选证据（2026-10-05）

上述整体重要项已修复并经独立 quality 批准：候选 stmt / expr 每文件 4096 次累计准入，实际重复访问计费；原对抗深度 10 / 12 / 15 / 18 均受限。耗尽后的单次线性扫描保留已知调用位置候选，为全文件调用标记 unknown / truncated，不回退 Exact；下一文件预算独立、诊断有锚点、缓存无 AST 仍保留事实。独立测试文件在修改前实际失败四个记录，修改后七项全部通过，没有放宽访问上限或删除断言。

主代理 fresh 全量 611 项、零失败、49 跳过；前端 fresh 8 文件 / 43 项通过，后端 Ruff、mypy Windows / Linux 56 文件通过。前端生产构建 / lint / typecheck 在未改动的同一前端快照通过。wheel 和 Windows onedir 从修复后的源码重新构建，不复用上节旧产物作为本次证据。

最终静态报告 `build/general-python-corpus-release/report.json`：Click 17/17、Requests 19/19、HTTPX 23/23、ML 夹具 3/4，均 verified；符号和目录规模与上节一致，源码身份 / 内容指纹前后相等，缓存 facts / views / overview 相等。候选访问预算 Partial：Click 1 个、HTTPX 2 个；其动态调用 Unknown 增加是明确截断，不是解析失败，也不是正确率改善。单次 Analyze 为 1.894 / 0.917 / 1.446 / 0.020 秒；不同系统负载，不作跨快照性能承诺。

从修复后产物执行：

- `build/general-python-release-native/report.json`：冻结 PC 包普通五项 passed，无 skipped / errors / pageErrors / consoleErrors，cacheIssue=null。
- `build/general-python-release-wheel-native/report.json`：源码目录外、隔离安装最终 wheel 的普通五项 passed，无错误；包与 assets 来自 site-packages。
- `build/general-python-release-ml-native/`：同一最终冻结包原 ML 五项通过，stdout pageErrors=[]，保留 DOM / 截图。普通与 ML 都通过真实 WebView2 界面动作，不是浏览器 demo。

最终产物 SHA256（覆盖同名构建产物，上节 SHA 只属于历史快照）：

- `dist/general-python/CodeViz/CodeViz.exe`：`92591aea1fda0d821733b6bfb0c931d74128bce01845ea7ad64832c374f4aa4e`。
- `dist/general-python/code_module_visualizer-0.1.0-py3-none-any.whl`：`35769929fcd550227f8a8f58c3bdb9cd81a65c8937b157eef61ffb817712b6f5`。

普通函数主路径为：打开目录 → 显式分析 → 概览函数 / 方法 → FunctionFlow → Inspector → Source；也支持普通名称搜索、局部数据关系、标签 / 分屏、语言与缓存恢复。跨函数值传播、动态派发、完整 MRO、隐式异常 / 异步运行时序及真实运行轨迹仍不承诺；finally 数据来源明确近似。整体验收结论在最终独立审查确认后记录，不能仅因上述测试通过就自行宣布完成。

### 最终独立结论

2026-10-05，独立 reviewer 给出整体批准、无剩余阻断；核对当前源码与已审 release 一致，最终 exe / wheel SHA 匹配，上述 fresh 门禁和产物记录齐全。候选嵌套循环重要项已关闭，支持矩阵与计划已同步。批准仅限明确声明的静态分析支持范围，不代替用户理解度、物理断网、Python 3.11 运行时或远端 CI 验证。

改动归组：analysis / core 增补通用入口、词法作用域、关系与候选、普通 def-use、控制退出；views / navigation / ui / persistence 支持独立 FunctionFlow、目录入口、源码与缓存；frontend 增补概览目录 / 视图类型 / 标签与中文文案；tests / fixtures / scripts 增补边界、预算、语料与原生验收；docs 更新计划、架构、支持范围和证据。未新增依赖。当前保留 `codex/general-python-analysis` 未提交改动和原用户文件，不自动合并或上传；测试窗口与服务均已关闭。下一阶段为用户实际项目试用与后置能力收集，本计划 Tasks 1–5 已完成。
