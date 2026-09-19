# UX 规范

## 1. 布局

采用 VSCode 风格：左侧 Activity Bar / Sidebar，中间 Editor Area，右侧统一 Inspector。Activity 包含 Explorer、Search、Architecture、Execution、Symbols、Settings。

Editor 以 Tab 展示 Overview、Architecture、Training Flow、Source files；支持 Open to Side / Split，同时查看 Graph 与 Source。

应用保留操作系统原生标题栏和窗口关闭按钮。Overview 是固定首页；图、源码和 Problems 标签在右侧显示 `×`，也可以用 `Ctrl+W` 关闭当前标签。关闭标签不退出应用，不清除分析结果，重新打开时可继续阅读。

## 2. Overview

通过选择本地项目文件夹打开项目，并提供 Recent Projects。首屏显示项目名、Language、Framework、Entry Points、Models、Main Areas、Analysis Health，以及 Analyze Project、Open Architecture、Open Training Flow、Browse Symbols。多个模型和入口并列列出；选择入口时进入对应的独立 Execution Flow。

## 3. 交互

- 单击：Select、Inspector、邻居高亮。
- 双击 / Enter：Drill Down / 进入下一层。
- Open Source / Open to Side：独立动作，不与 Drill Down 混淆。
- 返回、面包屑和当前视图状态必须可用。
- 关闭标签：点击标签右侧 `×` 或按 `Ctrl+W`；Overview 不可关闭。
- 纵向导航：鼠标滚轮、Page Up / Page Down 滚动长图；重置项目布局会清除手动节点位置，但不清除分析与展开状态。

## 4. Inspector

统一分区：Overview（易读名、真实名、类型、语义角色、confidence、说明）；Structure / Flow；Relations；Data；Source（file/class/function/line range、片段、Open Source）。

节点默认只显示易读名称和真实代码名；路径、行号和局部关系放 Inspector。对大组件，Inspector 默认最多列出 12 条带易读名称的可见关系与 12 行源码预览，并提示未展开数量；Open Source 仍可查看完整文件，局部图负责继续探索关系，不在侧栏堆叠内部 ID。

## 5. 视图语义

Architecture 主关系为组件 + 数据流，Execution 主关系为控制流 + 数据流，Calls 主关系为 CALLS，Gradient 主关系为 GRADIENT_FLOW。Data Flow、Calls、Control、Loop、Gradient 使用不同视觉语义，并有 Legend / 过滤器。

首版中 Architecture、Execution、Symbol 和 Data Flow 均默认使用居中的单列纵向阅读顺序，优先让小图在一屏内完整可读；高度超出时通过纵向滚动查看，不缩小到难以阅读。回环与开发者手动移动的节点仍可使用水平空间。用户拖动后保存位置，不强制重排。

## 6. 语言

默认为简体中文，Settings 中可在简体中文和 English 之间即时切换。语言是应用级偏好，保存在应用数据目录的独立、有大小限制的配置文件中，不写入被分析项目。切换只改变应用导航、按钮、状态、图例和检查器标题；真实源码、符号名、路径、行号和问题代码保持原样。

## 7. 搜索

支持变量、参数、函数、方法、类、文件、模块、属性和语义匹配（如“反向传播”）。结果提供 Reveal in Graph、Open Source；默认展示 1-hop + 必要语义节点，并支持上游、下游和更多 hops。
