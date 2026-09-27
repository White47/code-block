# UX 规范

## 1. 布局

采用 VSCode 风格：左侧 Activity Bar / Sidebar，中间 Editor Area，右侧统一 Inspector。Activity 包含 Explorer、Search、Architecture、Execution、Symbols、Settings。

Editor 以 Tab 展示 Overview、Architecture、Training Flow、Source files；支持 Open to Side / Split，同时查看 Graph 与 Source。

应用保留操作系统原生标题栏和窗口关闭按钮。Overview 是固定首页；图、源码和 Problems 标签在右侧显示 `×`，也可以用 `Ctrl+W` 关闭当前标签。关闭标签不退出应用，不清除分析结果，重新打开时可继续阅读。

## 2. Overview

通过选择本地项目文件夹打开项目，并提供 Recent Projects。首屏显示项目名、Language、Framework、Entry Points、Models、Main Areas、Analysis Health，以及 Analyze Project、Open Architecture、Open Training Flow、Browse Symbols。多个模型和入口并列列出；选择入口时进入对应的独立 Execution Flow。

## 3. 交互

- 主图采用 A+B 交互：A 是稳定的主纵向流程；单击只执行 Select、Inspector 和邻居高亮，不改变主图。对普通且可展开的节点，双击、Enter 或节点上的 `+` 在画布空白处打开 B 细节岛，不替换主图，也不改变主图根节点或展开历史。聚合节点执行同样动作时打开隐藏成员侧栏供选择 / 重新定位，不生成细节岛；不可展开节点不响应这些展开动作，用户可继续通过 Inspector 的 Open Source 查看源码。
- 每个视图最多同时打开三个细节岛；岛可拖动、关闭，并以虚线连回来源节点。达到上限时提示先关闭一个，不自动淘汰已有岛。岛内节点可更新统一 Inspector 并打开对应源码，但不能继续嵌套新岛。
- Open Source / Open to Side：独立动作，不与打开细节岛混淆。细节岛标题中的 Open Source 默认打开来源节点；先选择岛内节点后，该动作打开所选岛内节点的源码。
- 关闭当前选中的细节岛后，选择返回主图来源节点；Escape 关闭当前活动岛，主图选择不受影响。
- 返回、面包屑和当前视图状态必须可用；细节查询失败或失效只移除 / 跳过对应岛并显示提示，不能清空主图。
- 关闭标签：点击标签右侧 `×` 或按 `Ctrl+W`；Overview 不可关闭。
- 画布导航：`Ctrl+滚轮` 以指针为锚在 25%–250% 间缩放；普通滚轮和 Page Up / Page Down 纵向滚动，水平 / 垂直滚动条保持全部已绘制内容可达。左键拖动画布空白处或中键拖动可平移；拖动主节点或细节岛只移动对应对象。
- 工具栏 Fit 将当前主图与细节岛适配到窗口，100% 恢复 1:1 缩放并尽量保留当前中心；缩放百分比实时显示。重置项目布局清除手动主节点位置，并把已打开岛恢复到自动放置，但不清除分析、展开状态、标签或各视图 viewport。

## 4. Inspector

统一分区：Overview（易读名、真实名、类型、语义角色、confidence、说明）；Structure / Flow；Relations；Data；Source（file/class/function/line range、片段、Open Source）。

节点默认只显示易读名称和真实代码名；路径、行号和局部关系放 Inspector。对大组件，Inspector 默认最多列出 12 条带易读名称的可见关系与 12 行源码预览，并提示未展开数量；Open Source 仍可查看完整文件，局部图负责继续探索关系，不在侧栏堆叠内部 ID。

## 5. 视图语义

Architecture 主关系为组件 + 数据流，Execution 主关系为控制流 + 数据流，Calls 主关系为 CALLS，Gradient 主关系为 GRADIENT_FLOW。Data Flow、Calls、Control、Loop、Gradient 使用不同视觉语义，并有 Legend；通用关系过滤器仍属后续能力。

Architecture、Execution、Symbol 和 Data Flow 使用确定性的分层纵向布局：入口在上、结果在下，同层分支向左右展开，汇合节点尽量回到前驱重心。布局顺序由关系与 Stable ID 决定，不使用随机坐标；回环不重复生成节点。用户拖动主节点后保存位置，手动位置优先于自动布局。

每个图视图分别保存 zoom / pan，细节岛按视图保存来源节点与场景位置；重新打开项目时恢复有效状态。重新分析或加载旧结果时，引用已消失节点的岛会被安全清理。Outdated 状态允许继续浏览旧图，并在 Overview、图和源码区域保留警告。

## 6. 语言

默认为简体中文，Settings 中可在简体中文和 English 之间即时切换。语言是应用级偏好，保存在应用数据目录的独立、有大小限制的配置文件中，不写入被分析项目。切换只改变应用导航、按钮、状态、图例和检查器标题；真实源码、符号名、路径、行号和问题代码保持原样。

## 7. 搜索

支持变量、参数、函数、方法、类、文件、模块、属性和语义匹配（如“反向传播”）。结果提供 Reveal in Graph、Open Source；默认展示 1-hop + 必要语义节点，并支持上游、下游和更多 hops。
