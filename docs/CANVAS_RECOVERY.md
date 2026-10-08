# 画布定位、关系层数与纵向布局恢复（2026-10-05）

## 用户反馈与真实复现

用户报告“更多层”只能增加，到最大后不能收回；打开框架 / 图时画布像空白，点击适应才出现；要求上到下为主，同层可以并列。此次保留此前未提交修改，不改源码分析事实或删除用户视图缓存。

真实 WebView2 窗口复现：打开 MVP Classifier 后四节点可见，通过真实鼠标平移画布四次，再从概览打开 Baseline。同一 Architecture 的持久 viewport 被新 root 复用，transform 为 translate(2973.72px,1367.65px) scale(0.776327)，新图节点全部屏外；点击适应后可见。截图在 `build/canvas-baseline/new-model-before-fit.png` / `new-model-after-fit.png`。这不是节点未生成。

“更多层”是关系查询 hops，不是 zoom。实际界面从 1/5 增加到 5/5，只有更多按钮，无法减少。固定 80ms fit 同时存在测量就绪风险，但不是本次已证实的空白根因，不能混为一谈。

## 已批准的最小合同

- 关系范围可增加 / 减少，保持 1–5；保留 root / direction，切换 root 时重置 1 层，不以增层代替缩放。
- camera context 包含项目会话 / view / root。换 root 后在节点完成测量、容器非零尺寸时适应。相同 context 的正常用户 viewport 恢复；若全部节点屏外，适应恢复，不用清空缓存。
- 不再以固定延迟假定测量完成。完成适应后才确认已处理，晚到的旧 context 回调不能移动新图；空图不重复适应。
- 相同 context 的选中 / 展开 / 收起不强制重排和适应。新细节岛按测量后的实际边界显现；手动节点位置与细节岛坐标保留。
- 自动布局按视图主语义选择关系：Architecture 结构父上子下；Execution / FunctionFlow 前向控制和分支，同级并列，回边不推高层；DataFlow 使用已投影的信息方向；Symbol 使用明确展示的依赖方向。环稳定有界，布局仅为阅读提示，不宣称真实运行顺序或并发。
- 手动位置最高优先级，不因 camera 适应 / 换 root / 自动排层覆盖或写回分析事实。不新增布局依赖，不迁移持久格式。

## 交付门禁

先观察关系范围 / camera / 布局失败回归，再实现最小修复。全量前后端检查及构建、独立复审、从最终冻结 PC 包实际测试 Classifier 平移→Baseline 原复现、1→5→3→1、节点测量 / 细节岛 / 正常 viewport 恢复、纵向布局及原搜索 / 源码 / 缓存能力。最终独立包路径和验证结果完成后追加；不要复用修前包证据宣称修复。

## 原生验收发现的候选包回归

首轮候选包虽然通过 110 项前端测试，实际 WebView2 中新增的图工作区包裹层没有承接 flex 高度，`.react-flow` 仅高 34.67px。节点与窄条画布部分相交不足以证明可交互：真实双击 encoder 未展开细节岛。因此该候选包不作为交付版，增加实际可用编辑区高度检查，修复包裹层布局后重新构建并重跑全部画布交互。不能以单元测试或点击适应替代此门禁。

独立复审发现另一个时序缺陷：React Flow 12.8.5 的 `fitView` 会排队，事后的 context 检查只能阻止旧结果标记完成，不能阻止库内旧请求移动新图。真实库的受控节点内存复现捕获 A 请求在 B 节点、宽高均为零时移动视口。架构补审批准改为：microtask 重读 store 尺寸 / 测量 / 节点身份，在最后 context 校验后同步计算边界并调用 duration=0 的 `setViewport`；不排队自动 `fitView`，适应上限 1、下限 .25 不变。需补充真实库时序回归并重新冻结验证，不能复用此前候选包的证据。

高度修复后的真实双击仍失败，进一步事件诊断确认：第一次点击确实命中 encoder 的文字；选中更新将 controlled nodes 替换成无 `measured` 的新对象，React Flow 短暂隐藏全部节点等待重测。第二次点击与 dblclick 因此命中 pane，触发缩放而非详情。Inspector 的“展开细节岛”正常，后台没有故障。修复应按稳定节点 ID 保留几何未变化节点的测量结果；几何变化仍重测，不能只关闭双击缩放掩盖事件丢失。原测试 rig 自动制造 measured，未能发现此问题，因此需真实库回归和实际双击验收。

## 最终验证与独立交付（2026-10-05）

上述候选包回归及复审阻断均已关闭。最终生产快照获得独立 reviewer 批准：高度链、camera 即时操作、测量复用 / 失效、层数边界与纵向布局无剩余 blocker。真实库测试包含旧 A 请求不能移动未就绪 B、选择后无需第二次 ResizeObserver 仍保持可见，以及父子详情绝对边界 / zoom 上限。全量前端 **115/115**，后端 **628 项、零失败、49 跳过**；前端 lint / typecheck / production build、后端 Ruff / mypy（56 文件）、脚本 Ruff、diff check、wheel 与冻结 PC 构建通过。使用现有 `.desktop-venv`，未新增依赖。

从最终冻结包和独立 wheel 实测，早期失败报告保留为诊断证据，不作为通过证据：

- `build/canvas-recovery-delivery-native/report.json`：**8 项 passed**。实际画布填满可用编辑区；Classifier 父上子下、同级并列；真实双击 encoder 生成细节岛、全部详情节点自动进入可见区域并可关闭；关系范围 1→5→3→1；按钮及滚轮缩放可逆；实际平移直到旧节点全部屏外后，从 Overview 打开 Baseline 自动可见；关闭 / 重开同 root 图保留正常用户 viewport。全程 **0 次 Fit 点击**，无 errors / pageErrors / consoleErrors。初次 delivery 运行中脚本误将尚未持久化的上一 root 当作新 root，已改为先读取实际新节点，再等待持久化 root 与其一致；完整重跑通过，未降低可见性断言。
- `build/canvas-recovery-delivery-general-native/report.json`：冻结 PC 普通 Python **5 项 passed**，覆盖函数目录、FunctionFlow / Inspector / 实际源码行、标签关闭、名称搜索 / 图定位 / 分屏、中英切换及缓存重开；无 skipped 或错误。
- `build/canvas-recovery-delivery-ml-native/`：同一冻结包原 ML **5 项通过**，覆盖架构 / 细节岛、查询方向、训练步骤 / 源码、语义搜索 / 分屏、语言设置；stdout pageErrors=[]。
- `build/canvas-recovery-delivery-picker/report.json`：**7 项 passed**。真实原生文件夹选择两次各等待 17 秒后选择 / 取消，重新分析两次、切换第二项目、Recent 缓存重开通过。此前项目恢复的陈旧缓存场景仍由已有单元测试及历史 9 项验收覆盖，此轮未冒称重跑陈旧缓存注入。
- `build/canvas-recovery-delivery-wheel-native/report.json`：最终 wheel 独立安装到 `build/wheel-venv`，从源码目录外运行，普通 Python **5 项 passed**。实际资源来自 site-packages，JS 与最终冻结包 / frontend build 哈希一致。
- 两个隔离测试项目的独立 `canvas-test-note` 在重复分析、切换、布局和定位恢复后完整保留。未清空或修改用户实际缓存，未执行被分析源码。

交付程序为 `dist/canvas-recovery/CodeViz/CodeViz.exe`，必须保留整个 CodeViz 目录，仍需系统 WebView2。关闭旧应用后从此新路径启动；原 `dist/project-recovery` 与 `dist/general-python` 未覆盖。以下哈希只对应最终通过版本；前端资源是独立打包数据，仅 exe 哈希不足以标识 UI：

- exe SHA256：`2c152f372b1dbbcb40851371179b4e004bad8907d4178cf87684786946bf4c7e`。
- wheel SHA256：`45ed4921dadf6c125cd9e3c180c44eb1290f385a96f10fb1d68104dd99975eb3`。
- 最终 JS `index-CtDmiUN8.js` SHA256：`86da104e25d9df694d630aa24e9f955d960629cc57b71dfab86db18e4cf5c09a`；CSS `index-CselCIzA.css`。

本轮生产归组：App 图 context / 保持挂载与工作区布局、GraphEditor 相机 / 测量复用、QueryToolbar 与 i18n、后端 `views/layout.py`；测试 CanvasRecovery / GraphEditor / 真实库队列 / 纵向布局、原生画布脚本；README、UX、ARCHITECTURE、CACHE、KNOWN_LIMITATIONS、TEST_STRATEGY 与本记录同步。原通用 Python / 项目恢复未提交修改及用户 AGENTS / routing 文件保留，没有自动 commit / push。

限制不变：保留用户手动位置，不强制重排旧图；断开的同级节点可并列；静态关系不代表真实运行 / 并发；minZoom .25 使极大图未必一屏容纳，允许平移缩放。此交付仅针对已复现画布故障和列明的回归，不宣称任意项目均能完整解析。

最终专用冻结 / wheel 测试窗口均已正常关闭，CDP 9302 / 9303 没有剩余监听；没有关闭用户旧应用。
