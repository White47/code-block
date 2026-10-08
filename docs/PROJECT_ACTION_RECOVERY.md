# 项目打开与重新分析恢复（2026-10-05）

## 用户反馈与范围

用户报告第一次打开项目可用，后续不能打开，重新分析无结果。本轮只修桌面项目操作生命周期，不改 Parser / Resolver、不降低后端校验、不删除用户缓存、布局或标注。保留此前通用 Python 的未提交改动。

## 已复现的根因

1. 原生文件夹选择使用普通 RPC 的 15 秒超时。真实 Windows 选择框等待 17 秒后，前端报 `BRIDGE_TIMEOUT: choose_project timed out`，但原生对话框仍开着、Python 调用和共享锁仍在等待。后续选择成功时，后台已切换项目，界面还停留旧项目或空项目。上一轮“最近项目”切换验证绕过了原生选择框，不能证明这个路径正常。
2. 项目没有可用分析缓存，却保留旧 `selectedId` / `graphRoots` 时，界面先保存旧布局，后端正确返回 `NOT_READY`。旧实现把辅助布局保存当成打开 / 分析的前置门禁，错误直接中断真正操作。真实窗口 trace：点击重新分析 / 再次打开，只有 `save_layout` 的旧 ID 请求及 `NOT_READY` 返回，没有 `analyze` / `choose_project` 调用。

第二项不是锁泄漏：绕过界面前置门禁后，后台能分析并清理陈旧 ID。默认用户缓存权限正常；不能把之前沙箱账户创建的测试缓存权限错误套用于这次 GUI 故障。

## 最小修复合同

- 原生 chooser 等待用户选择、取消或明确失败，不使用自动 RPC 截止；普通数据读取仍保持 15 秒。项目写请求不能因前端超时释放队列（见下节补审决策）。
- 操作前布局保存保持队列顺序，但失败作为可见辅助警告，不阻断用户显式打开 / 分析。
- 不把保存失败当保存成功、不删除旧 ViewState / Annotations、不重试旧布局到新项目。
- 操作本身失败仍显示主要错误；成功后的状态和布局以后台返回为准，旧 ID 不合并回新分析。
- 原生取消不切换项目；操作互斥、generation 和晚到轮询保护仍保持。

## 验证门禁

先观察失败测试：桥接长等待选择 / 取消五项 RED；真实 App 的陈旧布局恢复四项 RED。必须重跑前端全量、lint / typecheck / build，后端适用检查及测试，并重建独立 Windows 包。

原生验收必须实际打开 Windows 文件夹选择框（不只点击 Recent）：超过 15 秒后选择与取消、连续切换至少两个项目、分析后重新分析、缺分析缓存但有旧布局、版本不兼容缓存及独立标注保留。不得仅以单元测试通过宣称用户问题已解决。最终包路径、检查结果和独立复审结论在完成后追加。

## 独立复审补充（尚未交付）

第一版修复已在独立冻结包通过真实选择框七项、普通 Python 五项、ML 五项，但 reviewer 仍未批准：普通布局 RPC 超时只结束前端等待，不取消后台写入；捕获超时后切换项目可能使晚到旧布局写入新项目。另 `Outdated` 可表示缓存版本不兼容、没有 analysis，不能一概当作可查询图。

架构补审允许一个小范围替代：布局保存等待真实返回，保持操作队列和项目互斥，不在未完成时切换；这避免跨所有写接口增加新身份协议。真实返回的布局保存错误可以警告后继续，但等待超时不等于后台失败。Session 应提供后台派生 `hasAnalysis`；false 时不查询旧图、也不保存旧分析布局，持久化布局和标注保留到后续有效分析清理。新增回归测试与产物重建后才能交付，第一版产物证据不能代替最终版本证据。

进一步受控时序检查确认 `save_viewport` / `set_analysis_settings` 也有同类风险，不能只保护 `save_layout`。最终最小安全范围扩展为整个前端项目写队列：`choose_project`、`open_project`、`analyze`、布局 / 节点 / viewport / 展开 / 细节岛写入、分析设置。均等待后台真实完成后才放行后续切换，不把 15 秒或原分析 300 秒前端截止误当取消；普通只读数据请求仍 15 秒、等待 bridge 建立仍 5 秒。

权衡：异常停滞的后台写入会继续占住队列，不能安全地自动跳过它。若进程不再响应，需正常退出 / 重启应用；首版没有后台取消协议。未来如需可取消分析和故障写请求恢复，应先引入项目 generation 身份与后台确认 / 取消机制，不能恢复单纯 `Promise.race` 放行项目写操作。没有删除原缓存或标注以绕过这个风险。

## 最终验证与交付（2026-10-05）

补审问题已关闭：前端项目写方法分类等待真实返回，独立 reviewer 对当前生产快照批准，无剩余阻断。主代理先独立捕获补充 RED：前端 37 FAIL / 23 PASS，backend 三项缺 `hasAnalysis` 失败；修复后 fresh 全量前端 **93/93**、后端 **614 项、零失败、49 跳过**。前端 lint / typecheck / production build、后端 Ruff / mypy（56 文件）、脚本 Ruff 和桌面构建前置检查通过。原 `.venv` 指向不存在的 Python，不能启动；验证改用现有 `.desktop-venv`，不是产品故障。未新增依赖。

从最终重建产物验收，而非上一版产物：

- `build/project-recovery-final-picker/report.json`：**9 项 passed**。真实 Windows 文件夹选择框两次各等待 17 秒后选择 / 取消，busy 未提前释放；缺缓存 / 版本不兼容缓存保留旧布局时，`hasAnalysis=false` 且无错误或旧 FunctionFlow 图；连续重新分析、切换第二项目、缓存再次打开均通过。分析后 `hasAnalysis=true`。
- `build/project-recovery-final-general-native/report.json`：冻结 PC 包普通 Python **5 项 passed**，涵盖概览函数 / FunctionFlow / Inspector / 真实源码行、标签关闭、名称搜索 / 图定位 / 分屏、中英语言、重新打开缓存。无 skipped / errors / pageErrors / consoleErrors。
- `build/project-recovery-final-ml-native/`：同一最终冻结包原 ML **5 项通过**：架构 / 细节岛、查询方向、训练步骤 / 源码、语义搜索 / 标签 / 分屏、语言设置；stdout pageErrors=[]，保留截图。
- `build/project-recovery-final-wheel-native/report.json`：源码目录外的独立安装最终 wheel 普通 Python **5 项 passed**，无错误；包和 frontend assets 均来自 `build/wheel-venv/Lib/site-packages`。
- 新隔离 cache 中两项目独立标注 `retained-test-note` 在重复分析 / 切换后完全保留。没有删除、修改用户实际缓存；没有导入 / 执行被分析 fixture。

最终产物：`dist/project-recovery/CodeViz/CodeViz.exe`（连同整个 CodeViz 目录使用，仍需系统 WebView2）。请关闭旧应用并从此新路径启动；原 `dist/general-python/CodeViz` 未覆盖。该独立路径第一轮候选曾重建替换，下面 SHA 只对应上述最终验证版本：

- exe SHA256：`9b5b839c457440223f17f5dec35fa0d98ea2e02bc1d5c2be24d72b818a63fee7`。
- wheel SHA256：`110307c1061736a9b8c6e74ce0564b99c6089b170cb4f559940968c99d3b36e5`。

本轮文件归组：生产 `frontend/src/api/bridge.ts`、`frontend/src/app/App.tsx` / `i18n.ts`、`frontend/src/types.ts`、`src/codeviz/ui/web_bridge.py` / `web_contract.py`；测试 bridge / App recovery / central fixtures、session recovery / 精确 DTO；新增原生 picker 验收脚本；README / DATA_MODEL / ERROR_MODEL / 本记录同步。此前 general Python 未提交修改及用户 AGENTS / model routing 文件保留，未自动提交或上传。

批准和验收只针对已复现的项目操作生命周期缺陷及声明的静态分析能力；不保证任意用户项目都可完全解析，未新增后台取消、运行时观察或大项目性能承诺。用户实际项目若仍失败，需保留窗口具体错误 / 项目目录规模继续定位，不应清空缓存掩盖问题。

最终独立交付复核：reviewer 核对当前生产快照、产物 SHA、9 / 5 / 5 报告和标注保留，确认本轮可交付，无剩余阻断或证据缺口；ML stdout 和测试窗口 / 端口关闭由主代理确认。全部本轮专用窗口已正常关闭，未关闭用户旧应用。
