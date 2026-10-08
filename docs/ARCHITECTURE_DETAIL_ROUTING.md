# 架构详情与画布路由修复

用户于 2026-10-07 确认方案 B；2026-10-08 恢复执行。目标项目为 `E:\tmp\nanoGPT-master`，只静态读取，不执行、不修改。

## 已确认原因

GPT 的 ModuleDict 成员已被解析，默认第一层只暴露 transformer 和 lm_head 两个直属组件。根节点详情也固定返回第一层，因此复制主图。现有布局采用固定间距，连线只考虑两端点，不认识其他节点或详情岛的障碍。

## 验收合同

1. 新打开模型时披露主要容器成员，控件显示实际层数；用户主动收起后不得自动弹回。普通无容器模型保留原默认。
2. 详情以所选组件为局部上下文，按相对深度展开；根 GPT 详情带来新增内部内容。没有内部内容时明确说明，不创建复制图。详情不得带入无关组件和根模型数据。
3. 所有身份和源码锚点仍可验证；容器模板、共享实例、定义投影不混淆。config.n_layer 未确定时不推断为实际 12 层。
4. 节点按真实测量尺寸纵向分层，同级并列。连线避让第三方节点、区分平行通道；详情岛自动避开已有图。手动位置和保存的岛位置不被默默清除。
5. 不把包含关系变成执行顺序，不为了视觉效果删除真实关系。复杂非平面图或用户制造的重叠不保证绝对零交叉，记录残余问题和明确限制。
6. 新测试须观察原实现失败，然后修复；全量检查、独立复审及真实 nanoGPT PC 验收通过后才交付新的独立安装包。

## 执行状态

### 接口与边界锁定

- Overview 模型 choice 增加可选 `initialStructureDepth: 1 | 2`，仅依据该根的直属、局部、已确认容器成员判断；无字段时沿用 1，不递归猜测。旧 bridge 参数次序不变。
- 初始提示只在稳定的项目／模型 context 初始化时消费；显式层级操作之后，迟到提示、选择变化及重渲染均不得覆盖用户深度。
- 详情相对所选 occurrence 展开 3 层（根为相对 0），总预算 18 节点／40 边，均包含局部根；仍保留最大全局层级 5、路径长度 6、循环和截断校验。局部数据只能来自所选定义及其子结构，不能把外层 GPT 数据混入 Block。
- 根详情须出现初始深度 2 未显示的真实结构 occurrence；预算不够时明确截断，不靠标签或布局变化冒充新内容。非法身份与叶子没有内部结构分别报错。

实施计划：[architecture-detail-routing](superpowers/plans/2026-10-07-architecture-detail-routing.md)。主代理复核基线：后端 653 项 OK（2 项跳过），前端 144 项通过。新增后端详情套件首次为 13 项／9 个失败断言，无导入错误；前端画布套件 11 项，7 FAIL／4 PASS，确实复现节点尺寸、岛遮挡、平行路径及第三方节点穿线问题。初始深度前端套件观察到 9 项／3 FAIL，均为应显示 2、实际为 1。

恢复执行后的首次后端集成检查为 100 项／11 个失败；后续已逐项修复真实缺陷或严格迁移 occurrence-local 旧合同断言。源码锚点仍由真实语义组件或代码符号验证；合法局部 occurrence 不再被强制替换为目标类根；内部异常不得伪装成失效身份并删除缓存状态。

主代理最新后端全量为 **675 项 OK（2 skipped）**；Windows／Linux mypy 各 59 文件通过，全仓 Ruff 通过。Task2 后端独立 spec 与 code-quality 复审通过，包含 relative/global 深度反馈、数据边预算截断、跨根授权、异常时状态保留及旧布局清理。最终前端全量为 **184 PASS／16 文件**；lint、TypeScript 与生产构建通过。Canvas 的 pending drag 4 项观察到 RED 后通过，固定岛增长避障首跑时已修复，不伪称 RED。后端复审新增 4 项状态保护与 3 项 metadata 防回归也未观察到 RED，如实记录为 guard tests。

首次追加 `graphRoots/selectedId` 严格比较后，wheel 与冻结 exe 暴露局部选择误写主图持久化选择；失败报告保留在 `wheel-final`、`frozen-final`，未放宽断言。App 现将岛内选择保持为瞬态，仅在局部源码直接返回原图时保留当前主图选择；显式模型导航的新选择优先，不恢复旧快照。双主节点回归观察到 1 RED／7 PASS 后修复为 8 PASS；最终独立架构与代码复审均 APPROVE。

## 最终交付与真实 PC 证据（2026-10-08）

运行 `E:\Codex\代码块\dist\architecture-detail-routing\CodeViz\CodeViz.exe`，保留整个 CodeViz 目录。旧应用先关闭；本轮不替换用户缓存或既有发布目录。使用本机 WebView2，不需要另外安装 Python／Node。

最终 attach-only UI 报告均在 `build/architecture-detail-native/`：

| 入口／报告目录 | 结果 |
| --- | --- |
| 源码宿主 `source-navigation-final` | nanoGPT 9 项通过 |
| 已安装 wheel `wheel-navigation-final` | nanoGPT 9 项通过 |
| 冻结 exe `frozen-navigation-final` | nanoGPT 9 项通过 |
| 源码宿主 `structure-final` | 模型＋普通 Python 结构探索 9 项通过 |
| wheel `general-final` | 普通 Python 流程、搜索、源码、分屏、语言、缓存 5 项通过 |
| 源码宿主 `mvp-final-retry` | 模型详情、层数、训练、搜索／源码、设置 5 项通过 |

三种 nanoGPT 入口均实际点击 Recent／Analyze／模型、2→3→2→1、局部 transformer／h／Block、源码行高亮、鼠标拖动、返回与同根重新选择。最后关闭非活动源码标签，确认标签消失且主选择、graphRoots、positions、positionsByView、detailIslands 不变。迟到响应还由集成单测覆盖；600ms 原生观测窗口不被宣称为无限时间保证。

冻结版 16 个几何阶段：节点重叠 0、详情岛重叠 0、第三方节点内部穿线 0。真实 SVG 按 ≤0.75px 采样，不是解析几何证明；交叉线段对仍为 1–14、共享线段对 3–44，不是独立交点计数，也不宣称所有线之间零交叉。截图见 `frozen-navigation-final/initial-depth-2.png` 及各详情阶段。

旧 MVP 脚本第一次因当前活动侧栏为搜索、无路径输入框而失败；仅改用既有真实 Overview／Recent UI 入口后重跑通过，未绕过 UI 写 bridge，也未放宽功能断言。两处验收脚本修正另经独立 reviewer APPROVE。

wheel SHA256：`148933A96863BCA792BFB9BE18DE645A75EE0DC8B92D58A5C65F4093C5595A4B`。exe SHA256：`8378FA0BABDDC35DBF7FB4B717C5C34178E1A82DB6BE5F2784B17154F04E0E51`（exe 哈希不是整个目录清单）。冻结版使用隔离应用目录，nanoGPT 预置 annotation 保留。目标 model.py／train.py 哈希与基线一致，未执行或修改目标代码。

本轮文件范围（不把既有脏工作区全部归为本次）：后端 `views/architecture_structure.py`、`structure_identity.py`、`ui/controller.py`、`presenter.py`、`web_contract.py`、兼容 `tkapp.py`；前端 `App.tsx`、`types.ts`、`GraphEditor.tsx`、新增 `canvasLayout.ts`／`canvasRouting.ts`；测试 `test_architecture_details.py` 及相关结构／controller／contract／Tk 回归、`ArchitectureCanvas.test.tsx`、`ArchitectureInitialDepth.test.tsx`、`DetailSelection.test.tsx`；验证脚本 `architecture_detail_smoke_host.py`、`seed_architecture_native.py`、`verify_architecture_detail_native.py` 及两处旧 native verifier；本记录、执行计划、UX、限制、README／IMPLEMENTATION_PLAN 入口。无新增依赖或分析事实格式迁移。

现有未提交修改均保留。不提交／推送 Git，不新增依赖，不清理用户缓存。此前 structure-exploration 安装包保留，不作为本轮修复的验收证明。
