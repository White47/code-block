# UX 规范

## 1. 布局

采用 VSCode 风格：左侧 Activity Bar / Sidebar，中间 Editor Area，右侧统一 Inspector。Activity 包含 Explorer、Search、Architecture、Execution、Symbols、Settings。

Editor 以 Tab 展示 Overview、Architecture、Training Flow、Source files；支持 Open to Side / Split，同时查看 Graph 与 Source。

## 2. Overview

通过选择本地项目文件夹打开项目，并提供 Recent Projects。首屏显示项目名、Language、Framework、Entry Points、Models、Main Areas、Analysis Health，以及 Analyze Project、Open Architecture、Open Training Flow、Browse Symbols。多个模型和入口并列列出；选择入口时进入对应的独立 Execution Flow。

## 3. 交互

- 单击：Select、Inspector、邻居高亮。
- 双击 / Enter：Drill Down / 进入下一层。
- Open Source / Open to Side：独立动作，不与 Drill Down 混淆。
- 返回、面包屑和当前视图状态必须可用。

## 4. Inspector

统一分区：Overview（易读名、真实名、类型、语义角色、confidence、说明）；Structure / Flow；Relations；Data；Source（file/class/function/line range、片段、Open Source）。

节点默认只显示易读名称和真实代码名；路径、行号和完整关系放 Inspector。

## 5. 视图语义

Architecture 主关系为组件 + 数据流，Execution 主关系为控制流 + 数据流，Calls 主关系为 CALLS，Gradient 主关系为 GRADIENT_FLOW。Data Flow、Calls、Control、Loop、Gradient 使用不同视觉语义，并有 Legend / 过滤器。

Architecture 用层级布局，Execution 用流程布局，Calls / Data Flow 用有向布局。用户拖动后保存位置，不强制重排。

## 6. 搜索

支持变量、参数、函数、方法、类、文件、模块、属性和语义匹配（如“反向传播”）。结果提供 Reveal in Graph、Open Source；默认展示 1-hop + 必要语义节点，并支持上游、下游和更多 hops。
