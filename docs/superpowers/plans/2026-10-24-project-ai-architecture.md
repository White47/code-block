# 项目授权与 AI 架构闭环实施队列

> 使用 writing-plans 建立规格；执行方式由 AGENTS.md 覆盖，主代理自主逐项执行。
> 设计：../specs/2026-10-24-project-ai-architecture-design.md。首轮新架构裁决由 architect 执行；T1独立前端行为先实施。

技术栈：现有 React19/TypeScript/assistant-ui/ReactFlow12、Python本地桥接、现有严格回答与证据合同，无新增依赖。

## Task 1：发送可用性与首会话

Files: frontend/src/app/assistant/{store.ts,runtime.tsx,readiness.ts}、components/AssistantPanel.tsx、i18n.ts；
tests: components/AssistantReadiness.test.tsx、assistant/store-readiness.test.ts。

- [x] RED：已授权无会话的首次问题最终调用一次newConversation和一次start；无授权显示阻塞提示（原实现两项失败）。
- [x] 实现：显式send中创建首会话，创建完成再次核对scope和选择，不在mount自动发送；共享UI释放判断与固定提示。
- [x] GREEN：新测试和store/runtime/AssistantPanel直接消费者6文件33项通过；frontend typecheck/改动文件lint通过。
- [x] 验收：旧会话路径不变，补切项目晚返回零start回归；缺条件不静默灰按钮。Task1不是完整项目问答/发布验收。

## Task 2：项目授权候选投影

Files: src/codeviz/ui/web_bridge.py、assistant/bridge_requests.py及当前runtime公开投影模块；frontend assistant/client.ts和types.ts。
测试：新候选接口测试及现有bridge/client直接消费者。

- [x] Linnaeus确认最小合同；新增只读assistant_get_consent_candidates，从发布快照筛选，返回epoch/generation/files，不读源码。
- [x] RED/GREEN覆盖敏感/未解析/hashless/ignore/旧代次、闭合输入和无授权副作用；37项Python直接消费者通过，客户端23项通过。
- [x] 保留现有grantConsent及逐文件复验；Carson核对fixture中denied.py仅是旧consent未允许的普通源码，候选预期修为app.py+denied.py，不改变保护政策。

## Task 3：项目一次授权入口

Files: components/AssistantConsent.tsx、AssistantPanel.tsx、assistant/client.ts、i18n.ts；必要时App.tsx。

- [x] RED/GREEN：默认填全候选、明确checkbox后一次授权、清单可删减且不触发start；授权UI及直接消费者9项通过。
- [x] 绑定变化/晚返回守卫及卸载取消；旧桥接仍支持手动精确清单。类型检查、改动文件Lint通过。

## Task 4：独立 AI 架构视图

Files: 新assistant/architecture.ts、components/AssistantArchitecture.tsx及测试；复用已验证回答DTO、ReactFlow组件与样式。

- [x] RED/GREEN：七段回答形成自上而下解释图，节点引用仅取已发布evidenceIds，不写静态Graph。
- [x] 最多100节点并显示省略数量，全部解释连线为AI推断；relationFacts保留confidence独立展示，端点归属不明不强连。
- [x] 原生App内独立ReactFlow画布支持选择/缩放/适应及证据卡；38项前端定向消费者通过。

## Task 5：项目分析与节点追问集成/交付

Files: components/AssistantPanel.tsx、App.tsx（若采用中央Tab）、i18n.ts、styles.css、集成fixture测试。

- [x] 增加显式AI项目分析与更新动作，固定问题使用原七段schema/预算/工具，原生容器与UI技术栈不变。
- [x] 节点追问预填且使用同会话question；同步ref确保刚点击分析也不继承静态node context。项目架构选择最后一条明确项目分析回答，普通局部回答不替换总览。

唯一阶段审查 Darwin 的4项均已修复：closePanel递增取消版本、普通回答不得回退为架构、
通用上下文仅下一次发送生效且新打开意图恢复原上下文、七段解释全部可绘制并补解释分组连接。
新增关闭晚返回/普通回答/同节点重开回归，四处RED后39项GREEN。修复只改前端，不做第二独立复审；
保留后端1616项(1480通过/136跳过)、Ruff、双平台mypy102通过证据，只重跑变化的前端门禁。
- [x] 唯一Darwin审查4项已修；Python1616项/1480通过136跳过、Ruff和双平台mypy102通过。交互检查补修源码跳转被画布遮挡（新增RED/GREEN）；最终前端603通过2todo、lint/typecheck/build通过，未重复未变后端门禁。最终包为frozen-final和wheel-final，不使用候选包。
- [x] wheel和frozen源码/资源核对、生产SDK假HTTP5次及冻结TLS+假连接2次通过，外联0且释放；实际浏览器固定样例9节点可见、纵向、选择/引用/追问/关闭通过。原生CDP等待失败(已强制清理)，不计通过；真实项目问答/Linux原生未测。见交付记录，源上传和付费业务请求未执行。
