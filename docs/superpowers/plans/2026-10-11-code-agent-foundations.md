# Code Understanding Agent Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Process amendment (2026-10-15):** process is governed by the repository `AGENTS.md`. Do not load `subagent-driven-development` or `executing-plans`; do not run a per-task spec/quality review pair. Technical requirements below are unchanged.

**Goal:** 交付无网络、无SDK、可独立验证的Agent请求身份、预算及证据注册基础，作为正式设计实施的第一批。

**Architecture:** 新增独立 `codeviz.assistant` 包；不让模型直接调用现有DesktopBridge或修改分析图。合同采用不可变值对象，预算及证据表封装有界状态；后续安全快照适配器负责提供真正解析来源，当前基础不能证明AST与磁盘一致。

**Tech Stack:** Python 3.11+标准库、现有SourceAnchor/Confidence、unittest、Ruff、mypy；保留React19/pywebview。SDK和聊天组件兼容门禁在网络接入/前端任务前完成，不影响本批纯领域合同。

---

## 0. 执行边界

- 用户2026-10-11确认正式设计及测试分层并授权执行。继续当前工作区和codex分支，保留已有测试审计文档与`.codex/`。
- 架构复审先于生产代码；test_writer先写失败测试，core_implementer实现，reviewer独立检查规格后再检查质量。
- 第一批不修改parser/resolver、缓存版本、桥接、UI或依赖，不发真实模型请求，不宣称聊天可用，不自动推送或提交未审查改动。
- 本批就是独立基础子项目，不将全部应用改动堆成一个大任务。完成后连续进入下一个子项目计划，阶段间不再重复询问已批准产品方向。

## 1. 全产品实施路线与来源

正式规格：`docs/superpowers/specs/2026-10-11-code-understanding-agent-design.md`。

| 阶段 | 交付与文件边界 | 验证及未包含内容 |
| --- | --- | --- |
| A 基础合同（本计划） | `src/codeviz/assistant/{__init__,contracts,budget,evidence}.py`，`tests/test_assistant_foundations.py` | request隔离、不可变记录、全部预算、伪造证据；不接API/UI |
| B 来源一致性与安全工具 | `analysis/python.py`解析字节哈希、`persistence/{codec,fingerprint}.py`版本、`assistant/{snapshot,source,tools}.py`、独立tests | 真实哈希/编码/句柄/列转换/新增删除/缓存无AST；不提前开放模型读文件 |
| C Provider/授权与后台任务 | `assistant/{provider,consent,jobs,service}.py`、`ui/{web_bridge,desktop}.py`、`frontend/src/{types,api/bridge}.ts`、`pyproject.toml` | SDK可选、HTTPS目标、key不泄漏、fake provider、幂等/取消/过期、短锁；无执行/写工具 |
| D 侧栏与可信导航 | `frontend/src/app/components/{AssistantPanel,AssistantSettings}.tsx`、独立assistant状态/runtime、`App.tsx`、`i18n.ts`、styles和manifest | assistant-ui兼容门禁、真实引用、开关停止/草稿保护；不替换主图或引入Next.js |
| E 集成/发布/用户指南 | 全功能fixtures、native verifier、CI build后资源检查、README/安全/PRD/UX/限制/用户指南 | 全量、真实最终PC包、wheel/EXE/离线；真实收费API仅用户主动配置和授权 |

各阶段开始前形成独立完整TDD任务计划，既有设计范围不变；缺陷修正不扩大授权。

## 2. 公共 API 与合同

本批导出 `RequestScope`, `JobStatus`, `BudgetLimits`, `BudgetReservation`, `BudgetLedger`, `BudgetUsage`, `AgentError`, `EvidenceRecord`, `EvidenceRegistry`。`AgentError`继承ValueError，公开安全固定code与固定message，不回显不可信输入。

RequestScope的project_epoch、request_id为匹配`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`的ASCII opaque str；analysis_generation为非负int且不超过2**63-1，拒绝bool/float/隐式转换。JobStatus只包含Queued/Running/Completed/Cancelled/Failed/Stale；不实现状态机或完整授权、载荷幂等。

证据记录字段：scope、anchor、content_hash、confidence、symbol_id/code_edge_id/semantic_id（可选）、truncated。SourceAnchor沿用真实路径规范，但新边界必须严格核验行列整数与bool并复制锚点，SHA256严格64位小写hex；confidence必须为Confidence实例，truncated为bool，可选code IDs为非空str最多512字符。记录不能包含源码全文、任意metadata、URL或可执行命令。验证结构不能取代实际行数、字节边界、节点存在性与provenance验证。

证据注册只供受信Python调用，不供模型工具；`register(record)->str`由后端secrets生成opaque ID，碰撞最多重试8次再拒绝；`lookup(evidence_id, *, scope, expected_file_hash)->EvidenceRecord`校验全部归属与哈希，expected_file_hash必须由后续后端fresh检查提供，不采信模型。ID不能由调用者指定，lookup不可将跨request证据当可点击结果，`clear()`使已签发证据失效并永久关闭此表，迟到register也拒绝，新请求创建新表。有界entries与确定UTF8 JSON序列化字节预算（含ID及全部记录字段），更新失败不得影响原表；会话/对话总预算由后续service在接入多个表与历史前统筹，这个表仅管理自身预算。默认1000项/2MiB，构造可用更小正整数限额；只读entry_count/byte_count用于运行时容量管理。

预算默认8不同文件/12000累计源码字符/8工具/6模型请求/20000输入tokens/2000输出tokens/90秒。BudgetLimits字段为max_files/max_source_characters/max_tool_calls/max_model_requests/max_input_tokens/max_output_tokens/timeout_seconds。`reserve(BudgetReservation(tool_calls=0, model_requests=0, input_tokens=0, output_tokens=0, source_fragments=()))->BudgetUsage`在一个锁内先检查所有候选计数，再整体更新。source_fragments严格为tuple of tuple(path,text)，每次外传含重复/历史都计字符；file count按SourceAnchor规范化项目相对身份去重。BudgetUsage字段为files（sorted tuple）、source_characters、tool_calls、model_requests、input_tokens、output_tokens，表示保守已预留量，不是实际消费或费用。

所有计数strict非负int且reject bool；字段不允许零或非法limits、NaN/inf时限；clock采用注入monotonic，cancel幂等，到期拒绝；预算失败/cancel/timeout不得部分扣数。读取usage不可修改内部状态。不退款，所有重试均重新预留；输出预算是每次明确分配的最大输出量，上层根据剩余额度分配，不能每轮默认再次请求完整2000。

这些API只做执行边界，不提供后台线程、模型输出渲染、真实源码fresh检查或snapshot真实性证明。

## Task 1：身份与预算（core_implementer，test_writer先行）

**Files:** Create `src/codeviz/assistant/__init__.py`, `contracts.py`, `budget.py`; Test `tests/test_assistant_foundations.py`。

- [x] 写身份/enum/limits与预算真实行为测试，使用小限额和可控时钟，不用sleep。代表断言：

```python
scope = RequestScope('epoch-a', 1, 'request-a')
ledger = BudgetLedger(BudgetLimits(max_input_tokens=5))
ledger.reserve(BudgetReservation(input_tokens=4))
before = ledger.usage
with self.assertRaises(AgentError):
    ledger.reserve(BudgetReservation(input_tokens=2, model_requests=1))
self.assertEqual(ledger.usage, before)
self.assertEqual(scope.request_id, 'request-a')
```

- [x] 运行定向 unittest（主代理独立使用同范围 `-q`），确认缺少功能导致失败：18 failures、0 errors、0 skips，全部明确报告缺少 `codeviz.assistant`。
- [x] 用frozen/slots dataclass与StrEnum实现值对象，预算更新用Lock+不可变usage；非法请求先检查候选值再赋值。外部clock回调在状态锁外执行，返回后在同一状态锁内重核取消/期限及候选预算，再提交；不能只改用RLock而漏掉回调触发的取消。公开默认限额只在一处定义，不把框架依赖带进core。
- [x] 验证每个预算维度的边界/超限、累计重复片段、非空相对路径、非法types、失败原子性、exact timeout、取消和并发最后一个额度争用。
- [x] 定向测试及Ruff/mypy通过后进行规格与独立质量审查；修正后复验。未审查改动不提交。

## Task 2：有界请求证据（core_implementer，test_writer先行）

**Files:** Create `src/codeviz/assistant/evidence.py`; Modify只限本包exports；Test同一基础测试文件。

- [x] 先写scope/hash/类型与注册上限测试，包括同项目不同request、不同代次、未知ID、clear后ID失效、非法range/hash、UTF8字节上限。代表断言：

```python
scope = RequestScope('epoch-a', 1, 'request-a')
record = EvidenceRecord(scope, SourceAnchor('app.py', 1), 'a' * 64, Confidence.EXACT)
registry = EvidenceRegistry(scope)
evidence_id = registry.register(record)
self.assertIs(registry.lookup(evidence_id, scope=scope, expected_file_hash='a' * 64), record)
with self.assertRaises(AgentError):
    registry.lookup(evidence_id, scope=RequestScope('epoch-a', 1, 'request-b'), expected_file_hash='a' * 64)
registry.clear()
with self.assertRaises(AgentError):
    registry.lookup(evidence_id, scope=scope, expected_file_hash='a' * 64)
```

- [x] 运行同一定向unittest命令，证据测试RED；实现本地签发ID、scope固定的registry、固定安全错误与失败无副作用的数量/字节检查。追加随机源及真实JSON失败回归也先确认RED再修复。
- [x] 使用真实registry和不可变record测试，不能mock lookup或创建“只为测试”的生产后门；不可将record是否来源真实作为本批测试通过的结论。
- [x] 验证注册/clear并发安全及公开错误不泄漏源码/路径/哈希；规格review通过后独立quality review。

## Task 3：阶段门禁与记录

**Files:** Update本计划与 `docs/TEST_SUITE_AUDIT.md`新增类别；Create `docs/CODE_AGENT_PROGRESS.md`。

- [x] 主代理检查实际git diff，只允许本批包/测试/文档新增，不覆盖之前未提交文档。
- [x] 运行本批完整适用门禁。修复后重跑受影响门禁；完全未改动的前端已通过本阶段全量检查时，保留同一文件状态的有效结果，不无理由重复：

```powershell
.desktop-venv/Scripts/python.exe -m ruff check src tests scripts
.desktop-venv/Scripts/python.exe -m mypy
.desktop-venv/Scripts/python.exe -m mypy --platform linux
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -q
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend test -- --run --maxWorkers=1
npm.cmd --prefix frontend run build
git diff --check
```

- [x] 如环境导致skip，分别记录原因，不拿Skipped当Passed；未变动桌面壳/资源打包时不重复PyInstaller或原生验收。CI配置不改变。
- [x] 记录文件列表、RED→GREEN、最终实测、review结论、后续来源/SDK/后台/UI阶段。Agent新测试只覆盖独立风险等价类，不重复全量旧fixture。
- [x] 阶段全部验收后按既有授权决定本地提交；本批保留工作区修改，不自动commit/push/main更新。未完成应用不能称为用户可试用版本。

## 阶段结果

Task 1/2/3 完成：架构放行→18项初始RED→实现→独立规格审查修复随机源错误→独立质量审查修复时钟回调锁死及序列化错误→22项GREEN→复审通过。主代理最后后端全量889项无失败、54环境跳过、221.561秒；Ruff及Windows/Linux mypy通过。未改前端的阶段全量415项、lint/typecheck/build通过，build保留既有大chunk警告。最后全量耗时偏高但正常完成，原因未确定，不将其当已修复性能问题。

具体文件和验证见 `docs/CODE_AGENT_PROGRESS.md`。阶段B的已核查风险及边界见 [来源一致性架构记录](2026-10-11-code-agent-source-review.md)，下一步先制定B1安全解析来源的完整TDD计划再实施。全规格其他能力分别由B/C/D/E承担，不将它们标记已完成。
