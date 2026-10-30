# 项目级 AI 理解与主工作区更新

## 产品目标

用户打开 Python 项目后，软件先完成不使用 AI 的全项目静态分析，产生目录／模块、
入口、导入／调用关系及模型结构初稿。显式运行项目 AI 分析后，模型在授权与预算内
结合跨文件证据细化解释；通过校验的结果更新原来的项目概述、项目结构、项目架构
主视图。右侧助手用于围绕该项目结果或其中模块追问，不再默认弹出独立 AI 架构。

目标路径：静态项目初稿 → 项目级取证 → AI 项目理解 → 更新原主区 → 选模块追问／源码。
架构裁决：build/project-understanding-20261028/architecture.md（含 owner 与 MVP 补充）。

## 首版范围与默认

- 当前静态 Python 项目，包括现有导入、调用、入口和 PyTorch 结构；第三方库仍为
  外部符号。不能把“已建立全项目索引”称作“AI 已逐文件理解所有源码”。
- 项目任务有显式 context/purpose；普通文件／符号提问继续存在，不靠中文提示全文
  或回答文字猜任务类型。大项目按模块／入口／依赖分层取证，显示实际覆盖缺口。
- 成功后自动应用 AI 增强层；三个原主入口默认显示同一生效版本，可切回静态结果。
  真文件树、源码、CodeGraph、静态 SemanticGraph 及原 confidence 不被 AI 改写。
- MVP 保留模型七段输出。summary/entry 的已验证 claims 可投影为 AI 逻辑组，
  关联标签为“相关源码／证据”，不声称引用集合证明真实成员或 Exact contains。
  没有依据的条目保持推断／未知、无可信导航；不是已验证代码实体。
- 依赖只来自实际 RelationFact／静态边。组间映射标为推断、保留原端点和证据；
  映射歧义不生成全组合连线。不能用 root-to-claim 或步骤顺序冒充项目依赖。
- 不跨重启持久化 AI 结果；每个当前项目一个生效理解版本，服从现有内存及撤销策略。
  不预建任意层级生成 schema、语义编辑器或新存储系统。

## 项目级取证和可信覆盖

静态全图留在本地。AI 仅使用用户授权且通过既有 B4 安全／新鲜度检查的元信息与
源码；目录、名字、关系端点和历史内容也不免检。内部种子取证同样计工具次数，
每个真实 wire 的内容／重复发送／final／repair 均计原预算。不新增自动付费任务。

项目任务不再用整段自然语言项目指令做 search_symbols 查询。首先提供有界、确定性、
跨文件覆盖的索引／入口／依赖上下文，再读取代表性代码，最后合成项目说明。选择策略
不能长期只返回同一文件的前二十个定义；普通搜索语义不因项目规划而被静默改变。
模型工具和七段校验尽量沿用，所有出站字段经过原 provenance／计量路径。

覆盖由服务记录，而非模型自报：本地已索引文件、授权文件、真实发送元信息所涉及的
文件、真实发送源码片段所涉及的文件、截断／预算／未验证缺口分别显示。安全检查读取
全文件不等于模型读过该文件；源码片段不等于完整阅读；本地统计不得作为免检 prompt。

## 生效结果与生命周期

ProjectUnderstanding 是服务拥有的版本化引用，绑定 artifactId/revision、原会话／任务、
项目 epoch、分析 generation、config／consent revision 及 scope。复用原 validated
answer 与 retained context，避免额外复制源码、证据权威或无限历史。元信息额外开销
也受原容量限制。新版本只在原校验、provider close、freshness 和提交身份检查通过后
原子替换；失败保留旧有效版本及静态初稿，普通问答不覆盖全项目版本。

提交用完整 publication CAS；生效寿命不绑每次只读视图 context_map 更新的 revision。
项目切换、分析／源码已发现变化、忽略规则变化、授权／config 失效、owner 会话清除或
eviction 使该版本不可用，主区回到静态层。不得重贴过期 proof 或保留永久导航 lease。

## 主区、追问和导航

Overview 展示项目摘要、功能区域、入口／依赖说明与覆盖。2026-10-29 层级恢复修订：
ProjectStructure 与 Architecture 始终展示各自现有 GraphEditor、真实静态边、层级及展开
控件；AI 七段解释在同一主编辑区以可折叠面板补充，不能用同一组摘要卡片替代底图。
普通项目、模型与组件沿用各自静态入口；没有可信映射时不把 AI claim 变成静态节点或边。
AI 说明、RelationFact 支持文本与静态图权威分离，不写入 context_map 或 save_layout。
本轮实施以 2026-10-29-project-understanding-hierarchy.md 为准。

主区引用不依赖右侧助手可见或当前聊天选择。导航请求增加严格可选
understanding={artifactId,revision,itemId}，服务核对当前 artifact、owner 和该 item
确实拥有的原证据，再执行原 C6 proof／freshness／权限和资源流程。前端以 origin
view/tab 与 artifact 版本持有独立 consumer，拒绝切页、关页或失效后的晚到导航。
不伪装 visible=true，不从模型 label/ID 直接打开源文件或构造静态图。

细节追问增加 typed understanding-item context（artifactId/revision/itemId）。服务解析
其有界说明和相关范围；旧 AI 文本作为不可信上下文，旧 evidenceId 不能充当新任务的
免费证据。新事实重新取证／绑定／计量。只有显式更新项目理解才替换项目版本。

## 验收

1. 三个合成 Python 文件存在导入／调用／入口链；项目 AI 确实获得跨文件上下文和
   至少两文件代码片段，能追踪关系出处，不是以按钮名称证明整项目分析。
2. 通过结果更新原主 Overview／ProjectStructure／Architecture；不默认打开 AI dialog。
   关闭增强可恢复完全相同静态结果；无 AI、失败、Partial 和无 nn.Module 均可用。
3. 主区条目可追问及打开源码；右侧关闭／切聊天不破坏主区有效引用。撤权、清 owner、
   换项目／代际、替换版本、关 origin tab 后的旧请求不能更新当前主区或抢焦点。
4. 覆盖、预算、权限、chosenTarget、并发 CAS、取消／lease／容量、普通旧路径回归
   均有必要反例。测试按受影响模块执行，阶段门禁一次；真实 API 与原生验收单列。
