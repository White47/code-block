# React Flow 原生桌面迁移验收

日期：2026-10-03。目标平台：本机 Windows 11 / WebView2，Python 3.12.14；本地 Node 25.7.0，CI 配置 Node 22。迁移保留 Python 分析核心，新增 React 展示层、窄桥接与 pywebview 原生窗口；不是将 Vite 开发页面当作 PC 交付。

## 实现与文件范围

- `frontend/`：React Flow 画布、主图 / 细节岛、标签、搜索、Inspector、完整只读源码 / 侧边源码、语言 / 项目忽略路径设置、Python View State 持久化。
- `src/codeviz/ui/{web_contract,web_bridge,desktop}.py`：JSON DTO、controller 桥接、固定 API 白名单、随机 loopback 静态资源服务、原生窗口及资源 / 导航限制。
- `scripts/`：Windows onedir 与 wheel 资源构建；真实 WebView2 验证与指定窗口正常关闭；构建性能诊断。
- `tests/test_ui_web_*.py`、`test_ui_desktop.py`、`test_desktop_acceptance.py`、`test_packaging_layout.py`：桥接、路径 / 分发安全、三条黄金路径、缓存与过期状态、打包回归。
- `pyproject.toml`、`.github/workflows/quality.yml`、README、架构 / UX / 验收 / 限制 / 计划和 ADR：入口、可选依赖、前端与 Windows 打包门禁和实施口径。

用户原有 `.codex/`、AGENTS.md、MODEL_ROUTING.md 未作为迁移内容覆盖。未自动提交或推送 Git。

## 检查与证据

Python Ruff（含 scripts）、Windows / Linux mypy 已通过；全量 unittest 376 项，49 项跳过，主要为旧 Tk GUI 与当前 Windows 无法创建的符号链接测试。不能把这些跳过说成 Tk 视觉验收通过。前端 lint / typecheck / build 与 41 项测试通过。独立 reviewer 复现并推动修复项目切换 / 同项目重新分析的迟到响应、设置串行化、标签选择、布局 flush、API 参数数量与方向映射；最终复审批准，在本次范围内未发现剩余可复现阻断。

实际生成 `dist/CodeViz/CodeViz.exe` 及完整 `_internal` 目录，并生成含本地前端资源的 wheel。真实原生验证脚本使用 CDP 连接实际 WebView2；不模拟 controller、解析器或 bridge。已分别验证冻结包和独立 wheel 在仓库外启动：架构 / Inspector / 同画布细节、上游 / 下游 / 双向、语义搜索 / Reveal、源码完整文件行号与锚点高亮、Ctrl+W、侧边源码、语言设置写回 Python；页面错误为零。截图位于 `build/native-final-packaged/` 与 `build/native-final-wheel/`，属于本地生成证据，不提交用户项目源码副本。

最终源码壳（独立临时缓存）、冻结包和重新安装的最终 wheel 均额外通过完整训练操作：从 Overview 选择 train_epoch，展开 Epoch / Batch，依次选择 Forward / Loss / Backward / Parameter Update，经 Inspector 打开 optimizer.step() 的真实源码锚点。三种入口均无页面错误；最后竞态修复后重新构建并重新测试，不沿用修复前的产物作为最终证据。源码截图位于 `build/native-final-source/`。

最终 wheel SHA-256：`02fef50f42f498877d6702558105c9f3350ef66066b4337be881ec8d4fe61beb`。前端构建约 0.55 秒；最终 JS 407.83 KB / gzip 132.80 KB，npm ci 审计未报告漏洞。

通过指定 PID 对测试窗口发送原生关闭消息，确认进程退出，两个静态端口拒绝连接。未关闭其他用户程序。测试 fixture 故意包含 broken.py，因此 Partial 与语法问题是预期结果，不是迁移引入的失败。

构建分析确认 Rollup tree-shaking 导致异常 CPU / 内存开销；以关闭 tree-shaking 的受控对比恢复构建。保留压缩、不使用 mock、无 CDN，当前 JS 约 408 KB / gzip 133 KB。未将未确定的具体依赖表达式归因为某个已证明的上游 bug。

## 发布与剩余边界

分发整个 `dist/CodeViz`，不能只复制 exe；用户机器需安装 WebView2 Runtime。Windows 新入口、纯 Python 核心跨平台，Linux / macOS 原生壳尚未实现。Tk 保留为可选 fallback，不移除用户既有阅读能力。

静态资源与应用 API / 项目源码分离，运行资源全部本地；此次没有物理断开机器网络，离线可用的证据来自本地打包资源、CSP / 导航 / 资源限制及对应测试，不声称已完成断网实测。CI 工作流已修改，但本次未推送，不能宣称 GitHub 远端已通过。

Inspector 易读关系名称、内嵌源码预览、完整键盘导航仍存在 UX 差距；Source → Graph 反查、标注编辑器、增量索引、运行时 / autograd 观察均不在此次迁移范围。三条链路自动验收不代替初次使用者是否真正理解项目的人工研究。其他限制见 KNOWN_LIMITATIONS 与 UX_SPEC。
