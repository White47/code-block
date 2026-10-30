# 设置一致性与桌面阻塞实施计划

> 自主执行，过程以 AGENTS.md 为准；不提交、推送，不调用真实 API。技术内容见同日 spec。

**Goal:** 修复配置草稿/连接目标错配，收敛已复现桌面阻塞并交付可验证试用包。
**Architecture:** 设置局部草稿与已应用配置分离，连接只接受已保存且已启用状态；桌面由 debugger 有证据地定位，不改变语义核。
**Tech Stack:** 既有 React/TypeScript/Vitest、Python/pywebview/WebView2 和现有离线打包环境。

## Task H1：设置草稿与应用状态一致

Files: frontend/src/app/components/AssistantSettings.tsx、AssistantSettings.test.tsx（必要时新增聚焦测试文件）、frontend/src/app/i18n.ts。

- [ ] 先写未保存字段阻止测试/隐藏旧结果、保存失败保持阻塞、规范化保存恢复测试、非法草稿仍可清钥的失败测试。核心断言：`expect(button.disabled).toBe(true)`、`expect(testConnection).not.toHaveBeenCalled()`。
- [ ] 运行固定 Node22 的 `node_modules/vitest/vitest.mjs run src/app/components/AssistantSettings.test.tsx`，确认行为缺失导致失败。
- [ ] 实现 `dirty = saveUnconfirmed || baseUrl !== settings.baseUrl || model !== settings.model || enabled !== settings.enabled || key !== ''`；连接按钮同时要求 `!dirty && settings.enabled`，回调同样防护。结果仅在 `!dirty` 且 revision 匹配时显示。
- [ ] save 成功同步 baseUrl/model/enabled 并清除未确认标志；失败保留阻塞，密钥输入仍清空；clear 使用 saved 配置。
- [ ] 运行设置与其直接消费者的测试、检查增量，无额外阶段内全量。

## Task H2：原生阻塞定位与有证据的修复

Ownership: debugger 负责 scripts/assistant_smoke_host.py、scripts/verify_assistant_native.py、src/codeviz/ui/desktop.py 中实际涉及部分及直接回归测试。

- [ ] 读取最新自有回执和必要启动路径，提出可区分的单一假设；最多三次不同假设的 owned fake-only 原生检查，不反复重试不变快照。
- [ ] 可实现的问题先失败测试，再最小修复和定向测试；若需多模块架构变化，先 architect；不通过改变验收、关闭安全限制或更换浏览器冒充修复。
- [ ] 若外部环境阻塞，提供具体证据、已排除项和唯一人必需动作；不执行系统修复/安装。

## Task H3：针对性复测及收尾

- [ ] 复用/扩展既有 probe_faults.py 的假 HTTP、重复连接、取消、换配置场景；nanoGPT只静态分析，应用缓存独立，不执行源码。
- [ ] 唯一阶段末复审本次增量，修复具体问题，不重复复审不变 diff。
- [ ] 前端全量测试、ESLint、TypeScript、构建；Python 代码改变时跑全量、Ruff、mypy 默认/Linux，否则复用 2026-10-23 最终通过证据（1601项/1465通过136跳过、Ruff、双平台102文件）。
- [ ] 独立目录 build/assistant-hardening-20261024 离线生成新 wheel/exe，核对当前源码和前端，不覆盖 dist 或旧试用包。真实原生和 API 验收不能由构建成功替代。
- [ ] 边界 docs/CODE_AGENT_PROGRESS.md 追加最多五行，写清实际结果、残余阻塞和恢复位置。
