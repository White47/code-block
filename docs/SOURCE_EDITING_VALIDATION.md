# Monaco 源码编辑验收记录（2026-10-08）

## 范围与结论

在用户指定的当前工作区实施离线 Monaco 0.57.0，保留 React Flow / WebView2 PC 壳及静态分析核心。架构合同由 architect 先审后实现，生产代码又经独立 reviewer 复核；复审发现的原生销毁失败锁定、CR-only 换行、项目 epoch、原生 CSP 派发与 EditContext 快捷键问题均已修正。

所有源码保存测试仅使用自有临时 fixture，没有修改 `E:\tmp\nanoGPT-master` 或执行被分析源码。未提交、推送或合并 Git；既有 `.codex/` 保留且未修改。

## 改动文件范围

- 后端：新增 `src/codeviz/ui/source_editing.py`；修改 `controller.py`、`web_bridge.py`、`web_contract.py`、`desktop.py`。增加严格编码读取、项目 epoch／文件 identity／原始 bytes revision、冲突保护、权限保留的原子保存、Outdated 与原生关闭握手。
- 前端：固定 package／lock 的 Monaco；新增 `sourceDocuments.ts`、`sourceModels.ts`、`monaco.ts`、`SourceEditor.tsx`、`vite-env.d.ts`；集成 App、state、bridge、types、SourceViewer、EditorTabs、i18n、styles。不同锚点／标签／split 共享草稿和撤销历史，所有丢稿入口走同一 guard。
- 测试：新增保存／桌面边界 Python 测试及 source draft／bridge／navigation／keyboard／component 前端回归；窄改现有桌面 fake 与 NativeApi fixture。
- 工具及文档：新增临时原生／wheel 宿主及编辑验收脚本，将两个旧原生 verifier 迁移到 `native_source_assertions.py`，以精确文件／范围、同行可见行号／装饰／完整片段及有界渲染验证 Monaco；其辅助回归 14 项通过且可不安装 Playwright 导入。更新 README、PRD、UX、缓存、安全、已知限制与实施计划。保持原有整体界面，frontend-design 技能仅用于源码区、未保存标记及确认对话框。

## 最终真实 PC 产物验证

以下三条入口均在最终快捷键补丁重建后通过同一套 **10 项**检查；不是浏览器模拟 bridge，也不是把 mock 编辑器当原生证据。

| 入口 | 证据目录 |
| --- | --- |
| 当前源码 + WebView2 | `build/source-editing-release-source/` |
| 已安装 wheel，隔离导入且从仓库外运行 | `build/source-editing-release-wheel/` |
| 实际 `CodeViz.exe`，隔离应用缓存 | `build/source-editing-release-frozen/` |

各目录的 `results.json`、`monaco-colors.png`、`conflict.png` 是本地产物，不入 Git。检查包括真实 Python keyword／comment 颜色区别，split 共享草稿／undo，EditContext Enter 不展开图，Ctrl+W 关闭 side，真实 undo／redo 不写盘，Ctrl+S 保存并保留 CRLF，分析 Outdated，脏标签取消，外部冲突保留磁盘和草稿，OS close 取消，最终放弃后正常退出。

Worker 空闲时可能不启动；验收另在真实页面同源 CSP 下创建包内 Worker，并按已安装 Monaco 的真实 `$initialize` 协议取得回应，不使用伪 Worker。所有资源保持本地，无 CDN／blob／unsafe-eval 放宽。字体、Python token 规则与 Worker 均在 Vite／wheel／冻结包内。

先前失败已区分：旧 textarea 选择器不适用于 Monaco EditContext DIV；DOM innerText 与 textContent 的换行差异导致撤销断言误报；单次 undo 不承诺撤销整个输入分组；并行等待时两个临时宿主触发 watchdog。修正脚本后最终三入口顺序重跑通过，没有跳过这些检查。

## 自动化与审查

最终前端全量 **259/259 PASS**；后端含迁移回归的全量 **744 项 OK，3 项环境跳过**；无 Playwright 的 CI 风格解释器 **744 项 OK，6 项环境／可选依赖跳过**。Ruff、前端 lint/typecheck/build、mypy Windows／Linux（60 source files）、wheel 与 PyInstaller 构建通过。普通 Python 与模型训练旧原生回归各 5 条链路通过，模型训练证据为 `build/source-editing-mvp-native/report.json`，没有页面错误。

旧原生回归曾遇到 WebView2 CDP 截图间歇超时，诊断时 DOM、字体及交互状态正常。两个 verifier 共用的截图辅助函数仅对 Playwright TimeoutError 重试一次，每次截图限时 10 秒、每次尝试前请求窗口置前；其他异常和第二次超时仍报失败，没有跳过交互或源码断言。窗口置前自身不受这 10 秒参数约束，不能宣称整个辅助函数严格限时 20 秒。修正后完整模型训练回归通过。

最终 wheel／冻结包另附 `third-party-notices/monaco-editor/LICENSE.txt` 与 `ThirdPartyNotices.txt`；正文保留上游声明，换行／末尾 LF 的规范化不宣称与上游逐字节相同。此法律资源补充不改变运行时 JS；Vite、wheel 和冻结包的 4 个 JS 文件已逐一核对 SHA256 一致，声明正文也一致。补充后的实际冻结包重新通过全部 10 项原生检查。最终 wheel SHA256 为 `0c39b318cd41cac77c6545adf7a0b295341872432a89fdc3e82c8904e9ea606a`。

TDD 证据：后端最初 40 项预期 RED，无 import/setup error；关闭失败／CR-only 的 2 项 RED 及前端最后键盘的 6 RED 后均修正为 GREEN。独立审查没有剩余生产阻断项；测试通过不代替该审查。构建仍有 Monaco 大 chunk 提示，PyInstaller 有非 Windows 后端／pycparser 可选项提示，已有 Tk/libpng 警告不视为新失败。

## 使用与限制

关闭旧窗口，运行 `dist/monaco-source-editing/CodeViz/CodeViz.exe`，保留整个 CodeViz 目录；系统仍需 WebView2 Runtime。打开项目／源码，编辑后点击“保存”或 Ctrl+S；关闭或切换时按保存／放弃／取消处理。保存后手动重新分析，不能把旧图当作新源码事实。

CR-only／混合换行、不可安全往返的编码、链接与只读目标保持只读。没有 Python LSP、终端、调试器、自动保存、新建任意文件、强制覆盖或强制终止后草稿恢复。仍有文档明确描述的非协作外部并发修改窗口，不宣称系统级绝对 CAS。Tk 历史入口维持只读，不替代 React PC 编辑验收。
