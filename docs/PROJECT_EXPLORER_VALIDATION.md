# 项目资源管理器验收记录

基础验收日期：2026-10-09；复核继续于 2026-10-11。基线：`df0db6b`，原有前端 259 项、Python 744 项（跳过 3 项）。本任务在用户选择的当前工作区实施；未更改用户 `.codex/` 配置、未提交或推送 Git。

## 基础功能的真实 PC 验收

`scripts/explorer_smoke_host.py` 创建独立临时项目，默认不 Analyze；`scripts/verify_explorer_native.py` 连接该进程的 WebView2。验证的不是网页演示或伪造 bridge。

| 入口 | 证据目录 | 结果 |
| --- | --- | --- |
| 源码 PC 入口 | `build/explorer-source-final` | 9 项通过 |
| 隔离 wheel 安装入口（`-I`，工作目录非源码目录，校验导入路径） | `build/explorer-wheel-native` | 9 项通过 |
| PyInstaller `CodeViz.exe` | `build/explorer-frozen-native` | 9 项通过 |

每组验证：真实目录懒加载与折叠；未分析项目打开并保存中文 Markdown；独占创建空文件和目录；dirty 改名取消；先保存后改名及标签身份；目录改名；删除确认取消；实际系统回收站 receipt 与原始 bytes；保护目录写禁用及后端路径穿越拒绝。已分析项目变更标记 Outdated，不自行重新分析。

回收站测试只删除自建临时项目中的唯一 UUID 测试文件，核对 `System.Recycle.DeletedFrom` 与内容，可通过系统回收站恢复。没有修改或删除用户项目文件，包括 `E:\tmp\nanoGPT-master`。测试窗口均正常关闭，不使用强制进程终止。

原有能力回归：

- `build/explorer-editor-regression`：真实 PC 编辑器 10 项通过，包括语法颜色、共享草稿/撤销、Ctrl+S、CRLF、外部冲突、dirty 标签关闭、原生退出保护、本地 Worker/CSP。
- `build/explorer-general-regression/report.json`：图到源码、源码标签关闭、搜索及侧边打开、语言持久化、缓存重开 5 项通过；无跳过、无页面或控制台错误。

以上初次安装版构建用于基础链路验收。审查后只读读取能力的最终构建，以及拖拽扩展后的完整验收将在完成后追加；不将旧构建当作最终版本证明。

## 审查期间补充的回归

- 同一路径 A→A 重开：在项目打开完成、会话安装后重新建立 Explorer epoch，不在打开尚未完成时提前 bootstrap。
- 文件保存后立即创建：最终操作队列内重新读取父目录 revision，避免使用保存前版本。
- 安全只读文本可查看/侧边打开；保护路径与磁盘只读仍不可写；NUL、二进制、链接和硬链接不能打开。
- Windows 路径 `lstat` 与 CRT `fstat` 的 ctime 语义差异不作为假冲突；仍核验文件身份、内容哈希、mtime、权限、父链。外部保留 mtime 的内容改动仍被拒绝。

## 边界

2026-10-11 独立复跑 Python 完整回归：813 项，OK（跳过 4 项），耗时 166.559 秒；现有 Tk ThemeChanged teardown 警告仍出现，不是测试失败。资源管理器后端 50 项，OK（跳过 1 项）；Ruff 与 Linux mypy（63 个源码文件）通过。

基础质量复核补充问题已经完成 RED→GREEN 修复：根刷新失效子目录/迟到响应；祖先含保护子目录时新建能力独立判断；重命名目标已有文档、pending open、标签或侧边身份时拒绝（含 Windows 大小写/分隔符等价路径）；不确定原生结果刷新树与会话而不重试写，保留草稿。最终基础 spec 复核批准，独立 quality 结论待追加。

2026-10-11 基础最终复验：

- 前端 301/301，通过 23 个测试文件；Explorer 专项 42/42。本机高负载下默认并发首轮有新大小写用例的预期 RED 及 5 项既有 UI 等待超时；修复大小写后用 `--maxWorkers=1` 完整复跑通过，未更改旧断言或等待时限。
- 无 Playwright 环境：Python 813 项，OK（跳过 7 项）；Ruff 和 Windows/Linux mypy 均通过。
- 前端 build、wheel、PyInstaller 成功。Monaco 体积与非 Windows android 可选模块收集警告不构建失败。
- 最新基础源码、隔离安装 wheel、EXE 各 11 项原生通过，证据分别在 `build/explorer-base-verified-source-native`、`build/explorer-base-verified-wheel-native`、`build/explorer-base-verified-frozen-native`。新增外部变化后刷新子目录，以及受保护文本实际 Monaco 查看/禁输入/磁盘不变断言。

不新增运行时依赖，不执行项目源码。不提供 LSP、文件监听、Git 状态徽标、自动 import 修复、外部文件拖入或永久删除 fallback。Windows 是原生 PC 验收平台；Linux mypy 与无浏览器测试不等同于 Linux 原生桌面验收。外部非协作进程的 TOCTOU 只能缩小，不能宣称消除。

拖拽待基础质量门完成后实施；本记录目前不是最终完工声明。

## 路径别名与创建故障复核（2026-10-11）

后续 quality 报告提出的源路径大小写别名漏 guard/remap/baseline 失效，以及新建已落盘后失败未标 Outdated，已补真实文件层 RED 并实现平台统一匹配与失败分类。Python 全量独立复跑 819 项 OK（跳过 5 项）；前端全量单 worker 323/323，Explorer 64/64；这不是最终质量放行。

原生新增恢复标签 `SRC/HELPER.PY` 的验收又复现一个自动测试缺口：保存后磁盘 basename 成为 `HELPER.PY`，旧树条目仍用 `helper.py` 精确 fresh lookup，导致保存成功但重命名报 ENTRY_CONFLICT。已确认磁盘与真实错误提示；architect 批准只修 fresh lookup/prepare 的路径与 revision 配对，不扩大源码原子保存实现。修复与原生复验通过前不得宣称该链路完成，也不得开始拖拽。

该缺口现已完成 RED→GREEN：精确路径优先，平台允许时跨分页确认唯一 alias；prepare 使用同一真实条目的 path/revision，歧义及读失败拒绝写。前端最终基础 331/331、Explorer 72/72；Python 819 OK（跳过 5 项），无 Playwright 819 OK（跳过 8 项），lint/typecheck/Ruff/mypy/build/wheel/EXE 均通过。

最终基础普通链路源码/wheel/EXE 各 11 项，证据在 `build/explorer-base-current-{source,wheel,frozen}-native`；恢复大小写标签链路三入口各 3 项，证据在 `build/explorer-alias-fixed-source-native`、`build/explorer-alias-wheel-native`、`build/explorer-alias-frozen-native`。失败的原生场景没有跳过，使用全新临时项目完成复验。architect 最终基础 spec 和独立 reviewer 最终基础 quality 均 Approved。

2026-10-11：基础门禁通过，开始树内移动扩展。此批准不覆盖尚未实现的拖拽，扩展需新的 RED、规格/质量复核与原生打包验收。

## 树内移动实施基线（2026-10-11）

中断后恢复时主代理独立运行新增移动测试：后端 `test_project_moves.py` 42 项、47 个失败断言（含 subtest），原因是 `operation: move` 尚被现有接口以 INVALID_REQUEST 拒绝；没有导入或 setup 错误。前端 `ExplorerMove.test.tsx` 首批 18 项全部预期失败：缺少“移动到…”菜单与真实树内拖拽行为。测试作者继续补充交互与安全边界，以上数量不是最终验收数量。

原生验收工具 `scripts/verify_explorer_move_native.py` 的 Ruff 检查通过；尚未把工具本身通过检查视为应用通过验收。工具将在自建临时项目中实际拖动文件与目录，验证 dirty Cancel、Save 后移动、菜单移回根目录、标签／侧边路径迁移、字节保持与禁止拖入后代；分别验证源码、隔离 wheel 与 EXE。用户项目不会用于写操作。

移动仍处于实施中：基础构建、基础质量批准与既有测试不能作为移动扩展的最终证据。最终测试数量、独立 review、原生结果与新构建路径将在真实通过后追加。

## 移动自动回归与原生排查进度（2026-10-11）

后端与前端 spec 独立复核已批准。审查期间新增目标 directory＋canCreate 独立判断（目标含保护兄弟目录仍可接收）和菜单移动失败保留原目标输入，分别经过新增 RED→GREEN；drag/drop 失败不自动弹菜单，不自动重试写。另增加真实移动完成后才抛原生异常的永久回归，确认两侧 baseline 失效、bytes 保留、Outdated 与 token 消耗。

主代理最新完整复跑：前端 409/409、24 个文件；Python 867 OK（跳过 7 项）；无浏览器 CI 867 OK（跳过 10 项）。Ruff、Windows/Linux mypy（63 文件）、前端 lint/typecheck/build、wheel 与 PyInstaller 成功。已有 Tk teardown、Monaco 大包与可选 android 模块收集警告保持，不是新增测试失败。

源码 PC 移动 9 项已完整通过，证据在 `build/explorer-move-source-native`。工具早期缺少调用视图所需上下文、使用错误标签关闭选择器及未等待侧边异步关闭，均先读取实际错误／DOM 再修正；失败夹具正常关闭，使用新夹具重测，不跳过断言。

安装版首轮真实手势没有 drop，零 prepare/apply。曾发现树滚动造成目标在视区外，修正后仍有一次失败，已交 debugger 继续检查真实事件、命中位置与树容器裁剪；不能仅根据源码验收推断 wheel／EXE 通过。最终 quality、安装版／EXE 原生验收与基础回归仍待关闭，因此 Task 5 尚未完成。

Task 5 整体 independent quality 现已 Approved，无剩余 P1/P2；reviewer 实际核对 diff／未跟踪文件并独立复跑移动前端 78/78。此批准仅关闭代码质量门，不替代尚在进行的安装版／EXE 原生验收。

后续 debugger 真实事件证明仍有生产 UI 缺口，已撤销最终放行：hover 提示段落条件插入文件树上方，目标顶部从 321.33px 变为 346.67px，指针未动却转而命中 `.venv`，因此没有 drop。此前仅修验收坐标不足以解决。架构批准始终挂载、固定高度的提示槽，保持 role=status、完整 title 和允许／拒绝反馈，不改拖拽授权。正在新增 RED→GREEN，随后重新 spec／quality 和三入口原生验收。新工具记录完整事件／几何证据并检查 hover 至 drop 前目标位置稳定；不会自动重复写入或伪造 drop。

## 最终关闭（2026-10-11）

基础功能与 Task 5 均已达标。固定提示槽回归先确认缺槽／移除槽的 RED；默认 Vitest 会清空 CSS raw 导入，测试作者改为按 import.meta.url 读取真实生产 CSS，保留完整样式断言，无新依赖或全局配置。最后在默认 CSS 配置、单 worker 下完整前端 **415/415，24 个测试文件**；移动专项 84 项，配合树组件专项共 97 项。Python 完整 **867 OK，跳过 7 项**；无浏览器 CI **867 OK，跳过 10 项**。Ruff、Windows/Linux mypy、前端 lint/typecheck/build、wheel 与 EXE 构建均通过。

| 最终入口 | 移动（9 项） | 基础（11 项） | Windows 别名恢复（3 项） |
| --- | --- | --- | --- |
| 源码原生 PC | `build/explorer-move-fixed-source-native` | `build/explorer-move-base-source-native` | `build/explorer-move-alias-source-native` |
| wheel 独立安装，-I 且工作目录非源码 | `build/explorer-move-fixed-wheel-native` | `build/explorer-move-base-wheel-native` | `build/explorer-move-alias-wheel-native` |
| 实际 CodeViz.exe | `build/explorer-move-fixed-frozen-native` | `build/explorer-move-base-frozen-native` | `build/explorer-move-alias-frozen-native` |

以上每格均完整通过，移动三入口均无页面错误。每次合法移动只用一次真实鼠标手势，不合成 drop、不重试写操作；释放前实际目标 Y 位移均 **0px**。事件与命中／几何日志在各移动证据目录的 `drag-diagnostics.json`。移动后先等 tree aria-busy=false 才验证导航关闭，保留原生操作中的锁；没有用放宽导航锁绕过测试时序。

最终旧能力回归：`build/explorer-move-final-editor-regression` 真实编辑器 10 项；`build/explorer-move-final-general-regression/report.json` 阅读／搜索／语言／缓存 5 项，无跳过或页面／控制台错误。所有测试窗口按实际 PID 正常关闭；删除测试仅将自建唯一 UUID 临时文件移到回收站，仍可在系统回收站恢复。用户源码、`E:\tmp\nanoGPT-master` 与用户 `.codex/` 未改动。

固定槽修复后 architect 再次 spec Approved，reviewer 独立 quality Approved；reviewer 检查实际三入口事件日志，确认 0px 位移、合法 drop 命中目录和后代目标无 drop。旧的临时放行／失败记录保留为时间线，不代表最终状态仍未完成。

最终构建：`dist/project-explorer/CodeViz/CodeViz.exe`（保留整个目录）；wheel `dist/project-explorer/code_module_visualizer-0.1.0-py3-none-any.whl`，SHA256 `91eb720006ee7d06781c1c4ac2df4f44f4fb74895089b13577d706b628160ed8`。最新独立安装验收目录 `build/explorer-move-fixed-slot-install`；早期安装目录不是最终包证明。

主要改动：Python 的 project_explorer/project_file_policy/recycle_bin、source_editing/web_bridge/desktop 白名单；React 的 ProjectExplorer/explorerState/App/Sidebar/SourceEditor/Monaco/types/bridge/state/i18n/styles；新增资源管理器／文本／移动测试及隔离原生验收工具；产品、权限、UX、合同、计划与验收文档同步。未新增运行时依赖，未修改 Parser／Resolver／Graph IR／Semantic Graph。不自动修复 import、不支持外部文件拖入或跨项目／跨卷移动，不承诺恶意外部进程下的绝对 CAS。保留现有分支与用户未提交配置，未提交或推送 Git。
