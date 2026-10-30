# 连接检查安全诊断实施计划

> 执行过程以 AGENTS.md 为准；自主执行，不提交或推送。架构输入：Nash。

**Goal:** 将含糊的连接失败定位到安全边界，不暴露服务商文本或秘密。
**Architecture:** 依据同日 spec，在既有错误 DTO 增加可选封闭 diagnostic；后端只采集应用拥有的边界和整数 HTTP 状态，前端严格校验和本地翻译。
**Tech Stack:** 现有 Python dataclass/SDK/async runner + React/TypeScript/Vitest，无新增依赖。

## Task D1：中性错误描述（已完成）

- 文件：frontend/src/app/i18n.ts、assistant/ui.test.ts。
- 先验证旧文案导致测试失败，再将其改为中性的“协议校验未通过”。
- 已验证 ui/AssistantSettings/AssistantConnectionCapabilities 三个文件、五项测试通过。

## Task D2：后端可信诊断

- core_implementer 负责 src/codeviz/assistant/{issues,sdk_provider,connection,service}.py 及对应 tests/test_assistant_* 定向测试。
- 先为 `SafeIssue` 的封闭诊断序列化写失败测试，再实现不可变类型和 optional 序列化。
- 用原有 fake SDK/HTTP fixtures 为发送前失败、HTTP 400/402/404/500、流错误、结果错误建立小型失败用例，再实现可信边界传播。
- HTTP 非成功状态不读取响应体；SDK 专用码、计量及 cleanup 保持不变；任意异常伪造 code/diagnostic 必须被忽略。
- 验证：.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_issues.py -v；runner/jobs 同样逐文件。实际 SDK tests 用 build/assistant-sdk-probe-20261012/Scripts/python.exe，禁止真实联网。

## Task D3：前端严格解析与显示

- 主代理负责 frontend/src/app/assistant/{types,diagnostic,poll,store,ui}.ts、i18n.ts、对应测试和 components/AssistantConnectionDiagnostics.test.tsx。
- 先新增合法三字段响应与越界/额外字段失败测试，再允许旧形状或封闭新形状；不信任后端 message。
- 仅渲染本地阶段翻译及数字 HTTP 状态；无诊断时保持原有中性错误。
- 验证：固定 Node22 运行 vitest 的 assistant ui/connection-client 和 Settings 直接消费者测试。

## Task D4：边界交付

- 唯一独立复审本次诊断增量，修复问题后按 AGENTS 边界清单收尾；不复审此前应用交付 diff。
- 全量 unittest、Ruff、默认和 Linux mypy、前端完整测试/lint/typecheck/build 各一次；未变快照复用已通过证据。
- 在 docs/CODE_AGENT_PROGRESS.md 追加最多五行：本次改动、实际门禁、未验收真实 API、可恢复命令/下一步。
- 交付决定：用户需要在应用内重现，提供独立的诊断试用包，不能让用户误用旧 exe。用现有隔离 SDK/desktop 构建环境和已构建的前端离线生成新 wheel/onedir，输出 build/assistant-diagnostics-20261023；不覆盖 dist 或此前冻结包、不安装下载新依赖。
- 不将 fake-only 或构建成功称为原生窗口/真实连接成功，不用用户凭据；原生环境阻塞仍须明确报告。试用包须与当前源码和前端一致，保存路径、哈希及验证结果。
