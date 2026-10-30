# 测试资产审计与运行归类

日期：2026-10-11。范围：Agent 开工前的目标与测试策略审计。未删除、移动或改写任何测试断言，未修改 CI。

## 1. 再次明确目标

产品继续是 Python-first 的 PC 程序理解工具：从真实代码恢复结构、执行与数据关系，通过搜索、图、检查器和源码形成理解闭环，支持普通 Python 与 PyTorch 项目。

本轮新增的是只读代码理解 Agent：用户选节点、选源码或自由提问后，得到功能用途、入口、条件、步骤、异常/返回、调用与数据关系的详细说明；关键结论引用可验证源码和真实图节点。右侧助手可开启、关闭和停止，不损坏编辑草稿、标签页和现有画布。

非目标：自动编辑/执行用户代码、完整自主编程 Agent、重建 IDE、无限分析全仓库、让 AI 猜测覆盖静态事实。成功标准不是聊天长度、节点数量或测试数量，而是解释有用、来源准确、推断诚实、权限受控且原功能可继续使用。具体合同见 `superpowers/specs/2026-10-11-code-understanding-agent-design.md`；书面设计确认门禁仍保留。

## 2. 实测基线

本机 `.desktop-venv`、当前工作区 2026-10-11 顺序运行。Python 使用标准 unittest discovery 加仅诊断用途的逐模块计时结果类；前端使用现有 Vitest、单 worker、JSON reporter，不变更断言或超时。数字是一轮实测，不是跨机器性能承诺。

| 自动测试 | 发现/展开的用例 | 结果 | 本轮耗时 |
| --- | ---: | --- | ---: |
| Python，56 个测试文件 | 867 | 无失败，54 skipped | 31.819 秒 |
| 前端，24 个测试文件 | 415 | 415 passed，无 failed/skipped | 57.40 秒，运行起点到最后 suite 结束 |
| 合计 | 1,282 | 1,228 执行通过，54 环境跳过 | 顺序约 89 秒，不含构建和原生验收 |

Python skip：Tk 无显示 47、符号链接不可创建 6、Linux 大小写路径专项 1。此结果不能宣称覆盖了 Windows 原生 GUI 或所有平台；不同许可、显示和操作系统下跳过数量不同。unittest 的 subTest 未单独展开计数，Vitest 的 each 会展开，数字不代表断言总数。

前端报告位于忽略的 `build/test-audit-frontend.json`；文件实际套件时间合计 44.02 秒，剩余包含加载/环境等开销，与运行起止统计不能混用。此报告不提交 Git。

耗时集中：

- `test_project_moves.py`：48 项 / 11.159 秒；`test_project_explorer.py`：53 项 / 6.763 秒，合计约占 Python 本轮 56%。
- `ExplorerMove.test.tsx`：84 项 / 13.99 秒；`ExplorerNavigation.test.tsx`：59 项 / 6.97 秒，合计 20.96 秒。
- `SourceEditingNavigation.test.tsx`：37 项 / 4.03 秒；`StructureExploration.test.tsx`：24 项 / 4.03 秒。

结论：暂无证据显示 1,282 项本身是工程瓶颈；更应控制重复执行频率、按影响范围选择测试，以及区分快速回归、构建和真实桌面验收。文件系统风险矩阵耗时高，但不因此成为可删除重复项。

## 3. 按验证责任归类

以下为文档分类，不移动文件、不改 discovery，不把分类当作降低 CI 覆盖的依据。每个测试文件只计入一个主类；风险交叉可要求多个类同时运行。

### Python：867 项

| 主类 | 数量 | 文件（省略 `tests/test_` 与 `.py`） |
| --- | ---: | --- |
| 分析与身份核心 | 217 | discovery, parser, symbols, identity, graph, resolution, flow, control_exit_semantics, lexical_scope_edges, scope_declaration_conflicts, generic_def_use, general_python_bindings, general_python_objects, general_python_data, general_python_control, value_candidates, analysis_work_budgets |
| 模型/训练/语义 | 60 | model, training, training_rules, semantic_components, semantic_graph |
| 图视图/布局/搜索/导航 | 162 | views, structure_exploration, architecture_details, function_flow_review_regressions, vertical_layout_recovery, search, navigation, ui_graph, relations |
| UI/桌面/bridge/项目会话 | 166 | ui_controller, ui_web_bridge, ui_web_contract, ui_presenter, ui_desktop, task1_contracts, task1_desktop, desktop_acceptance, project_recovery_session, ui_preferences, ui_tkapp |
| 源码编辑/文本/文件管理 | 175 | source_editing, source_editing_desktop, text_documents, project_explorer, project_moves |
| 缓存版本/持久化/打包合同 | 27 | persistence_store, persistence_controller, persistence_codec, analysis_version, packaging_layout |
| 集成验收/验证器自身合同 | 60 | general_python_acceptance, mvp_acceptance, general_python_verifier, native_source_assertions |

### 前端：415 项

| 主类 | 数量 | 文件（省略 `frontend/src/` 与 `.test.ts[x]`） |
| --- | ---: | --- |
| bridge 协议 | 62 | api/bridge, api/sourceEditingBridge |
| 文件树与操作集成 | 156 | app/ExplorerMove, app/ExplorerNavigation, app/components/ProjectExplorer |
| 编辑与源码生命周期 | 66 | app/SourceEditingNavigation, app/SourceEditingKeyboard, app/SourceDrafts, app/components/SourceEditing, app/components/SourceViewer |
| 结构/节点/画布/相机 | 96 | app/StructureExploration, app/DetailSelection, app/ArchitectureInitialDepth, app/CanvasRecovery, app/components/GraphEditor, app/components/ArchitectureCanvas, app/components/StructureExplorationCamera, app/components/GraphEditorCameraQueue |
| 应用状态/恢复/设置/Overview | 35 | app/App, app/ProjectRecovery, app/Task1Workspace, app/state, app/components/SettingsPanel, app/components/Overview |

`native_source_assertions` 和 `general_python_verifier` 包含无 GUI 的 helper/依赖隔离测试，不能因文件名含 native 或 verifier 就移出自动测试。Tk 是仍保留的 fallback，不在未批准退出支持前删除。

## 4. 精简运行频率，不精简安全效果

| 时机 | 必须运行 | 不必每轮重复 |
| --- | --- | --- |
| 阶段内小步迭代 | 当前修改测试 + 直接上下游消费者 + 相应 lint/typecheck；新 Agent 的权限、版本、证据、预算、取消用例 | 所有无关文件管理用例、全量打包、全部真实仓库扫描 |
| 跨模块修改/阶段结束 | Python/前端完整自动测试、适用 lint/typecheck/build；存在源码或 bridge 风险则补相关 native 专项 | 同一状态已被全量包含的定向测试无需再额外跑一遍 |
| PR 与 main push | 保留现有 Python 3.11/3.12 和前端完整发现、lint/typecheck/build、wheel 与 Windows 打包 | 不因本地已跑而跳 CI，不换成 changed-files-only |
| 发布/桌面交付 | 最终 PC 包真实 UI验收 + 受影响源码/wheel/EXE验证 + Windows安全边界；记录未支持环境的 skipped | 纯文案或契约文档修改不重复打整个包，不拿旧包结果证明新包 |

同一提交、同一未提交改动、同一依赖/环境与相同测试集合的成功结果才可复用；任何变化或未决失败都重新判断。重复次数减少不意味着检测能力已实测等价；发布/CI门禁保留。

影响选择规则：

- 只改聊天呈现：新增聊天测试 + bridge +应用状态 +详情引用/源码导航；不需每步跑所有文件操作矩阵。
- 改证据、安全读取、分析版本：对应核心类 +源码读取/编辑消费者 +缓存版本 +通用 Python/ML集成；不能仅跑聊天UI。
- 改 App 共享草稿/关闭/项目切换：编辑、文件管理、恢复及侧栏都可能受影响，须扩大前端选择；核心 bridge/shared lock 同样扩大后端选择。
- 改 parser/resolver/flow/Graph IR/SourceAnchor：完整分析核心与其视图、语义、搜索、缓存消费者；阶段结束全量。
- 改依赖、协议或打包：离线启动、无可选 SDK、CSP、资源合同和最终桌面包专项。
- 无法明确影响范围：跑全量，不靠文件名猜测。

### 阶段内源码/引用基线示例

下面是现有消费者的起点，不是 Agent 功能完成的充分条件。新增 Agent 测试必须同时加入；涉及上面的共享状态时扩大范围。

```powershell
$sourceContractFiles = @(
  'test_identity.py', 'test_search.py', 'test_navigation.py',
  'test_ui_controller.py', 'test_ui_web_contract.py', 'test_ui_web_bridge.py',
  'test_source_editing.py', 'test_text_documents.py',
  'test_analysis_version.py', 'test_persistence_controller.py'
)
foreach ($testFile in $sourceContractFiles) {
  .desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p $testFile -q
  if ($LASTEXITCODE -ne 0) { throw "Test failed: $testFile" }
}
npm.cmd --prefix frontend test -- --run --maxWorkers=1 src/api/bridge.test.ts src/api/sourceEditingBridge.test.ts src/app/state.test.ts src/app/SourceDrafts.test.ts src/app/DetailSelection.test.tsx src/app/SourceEditingNavigation.test.tsx
```

前端完整命令继续为 `npm.cmd --prefix frontend test -- --run --maxWorkers=1`，Python完整命令继续为 `.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -q`。CI的 worker 策略不在本轮改变；本地单 worker 用于稳定基线，不承诺最快配置。

以上示例在本机实测：Python 142 项，无失败、3项符号链接环境跳过，10次启动总墙钟5.118秒；前端133项/6文件全部通过，Vitest报告10.15秒、外部调用总墙钟11.175秒。合计约16秒，是特定源码/引用消费者的反馈时间，不宣称275项与1,282项覆盖等价；Agent新增测试和实际改动影响范围仍须加入。

## 5. 冗余判定与后续优化顺序

1. 先停止同一改动重复全量运行和无关 native/打包，按风险选择阶段内测试。
2. 相同准备逻辑可抽取 fixture/helpers，但每次重新创建可变文件/bridge/cache/DOM状态；不复用可变对象换速度。
3. 相同控制路径、仅输入不同的测试可参数化；不同权限、平台、竞态顺序、取消/失败阶段和断言层次必须保留。
4. 已存在 `.each` 与 `subTest` 的矩阵，不为减少显示数量再次合并成不可定位的大测试。参数化主要减少维护代码，不自动减少实际运行量。
5. 删除前列出旧→保留用例的逐项映射（前置状态、入口、刺激、断言、环境、历史缺陷），必要时通过受控故障验证仍可抓到同一问题；独立 reviewer 确认后才删除。不以“全量通过”或覆盖率数字单独证明等价。

目前没有核准可直接删除的用例。历史 Task1 文件名、版本号断言或复用相同 fixture 不等于测试过时；缓存代次升级时更新当前版本契约，但保留旧缓存拒绝及用户状态保留覆盖。

只读逐项审查得到的具体候选：

| 资产 | 重叠证据 | 安全处置边界 |
| --- | --- | --- |
| `test_model.py`、`test_training.py`、`test_flow.py` | 三份 `analyze_files` 临时目录/UTF-8文件写入/分析 helper 内容相同，另有测试跨测试模块导入 | 可统一 fixture 工具；每次仍独立创建项目，不删除任何功能断言 |
| `test_project_explorer.py`、`test_project_moves.py` | 临时项目/cache/controller/bridge及envelope校验重复，move额外含目标目录 | 可共享基础helper，不合并不同的token/目标revision状态 |
| `frontend/src/api/bridge.test.ts` | 已有方法×结果矩阵包含choose_project空项目成功；另一个cancel空session用例有部分pending/timer/返回断言重叠 | 后续考虑将payload维度并入矩阵，但先证明保留非空selection与cancel差异，尚不删除 |
| `test_project_explorer.py` create/rename/delete状态保留用例 | 都准备已分析状态并检查图、布局、标注、缓存保留及Outdated；delete另有revision失效/无关文档可保存 | 可共享准备及断言函数，保留每个入口及delete附加合同 |
| `ExplorerNavigation.test.tsx`、`ExplorerMove.test.tsx` | 数据工厂、deferred promise和草稿/model辅助存在重复 | 只抽取小helper，不把不同操作的模拟后端合成可变共享状态 |

未发现完整相同的测试体或可直接清理的 `.snap` 历史快照。重点文件已经参数化：ExplorerMove 45处声明展开84项，ExplorerNavigation 38处展开59项；数量多主要来自不同输入与状态矩阵。相同内容/mtime变化在save、rename、move入口分别检查，不算等价重复。

CI效率另有机会：前端 job和两个Python job各构建前端，可评估同提交产物复用；前端 build已含typecheck，可评估重复类型检查。须保留类型失败门禁、产物身份、本地资源/禁CDN检查及每个wheel校验，本轮不直接修改工作流。

发现一处现有覆盖条件：Python测试先于前端build，`test_packaging_layout.py` 的dist资源检查在干净CI可跳过。后续应在build之后补跑该测试；wheel文件存在检查不等价于资源路径/禁CDN断言。不得将减少skip数量误称降低用例数量。

## 6. 本轮变更与限制

本轮仅更新测试策略文档、记录审计和归类；未删除测试、放宽超时/断言、缩平台矩阵、改CI或开始Agent编码。因没有执行最终原生包或收费API，不能宣称这些门禁通过。

执行分层将用于阶段内反馈；原“每阶段结束完整门禁”保持。Agent交付后提供配置API、开启/关闭侧栏、停止、引用定位、旧结果处理和下一步建议，不要求用户在聊天中透露密钥。

## 7. Agent 实施后的增量分类

2026-10-11阶段A新增独立 `test_assistant_foundations.py`：22项，身份/类型与不可变性、预算各维度/失败原子性/时限/取消/并发、证据归属/容量/清空/随机ID碰撞、随机源异常、时钟回调锁死及序列化安全提示。输入等价类用 `subTest`，无网络、收费API或sleep，不复制既有parser/UI fixture。锁死回归用限时隔离子进程，超时终止并回收，不能让测试永久卡住。

阶段A结束时全量Python57文件/889项（867旧项+22新项），前端24文件/415项，总计1,304项。A最后生产修复后实跑：Python221.561秒、0失败、54环境跳过；前端56.67秒、415通过。最后后端全量耗时偏高但正常完成，原因未确定；此前35.384/32.125秒，不将单次慢运行当已定位缺陷或性能验收。此前1,282项是开工前基线。

2026-10-12 B1新增32项（source22、parser8、controller2），归类为实际读取/句柄/路径/平台、真实AST字节来源、编码与错误保留、界面错误捕获；B2新增17项（codec5、version1、store9、controller2），归类为来源序列化、旧版本失效、集合/hash一致性、手工fingerprint与中心temp-flip。输入风险用subTest，旧测试/断言/平台矩阵未删除。Native Windows行为与无法执行的Linux/链接覆盖分开报告。

当前Python58文件/938项、前端24文件/415项，总计1,353项。主代理B2最后全量实跑：Python37.674秒、0失败、58环境跳过（Tk47、symlink权限7、Linux专用4）；前端57.64秒、415通过。阶段内主要运行41项persistence和直接消费者，不每次小步重复全量/无关打包；B2里程碑保留完整门禁。Provider、后台任务、快照/授权工具与聊天UI仍待对应阶段覆盖，不拿当前数量证明Agent已经交付。
