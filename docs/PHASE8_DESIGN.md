# Phase 8 桌面 UI 设计

日期：2026-09-18。产品交互以已批准的 `UX_SPEC.md`、`MVP_ACCEPTANCE.md` 为准；本文只锁定当前仓库需要的工程实现边界。

## 方案选择

考虑了三种方式：

1. **Tkinter / ttk（采用）**：当前 Python 环境已验证有 Tk 8.6；不增加运行时依赖、不启动本地服务，源码仍只在本机进程中读取。代价是图形样式与自动化截图能力比浏览器 / Qt 有限。
2. 本地浏览器页面 + Python HTTP 服务：前端排版与测试方便，但会引入服务端访问控制、浏览器来源及静态资源分发边界；Python 官方文档也提示 `http.server` 只有基本安全检查，不适合直接作为生产服务。
3. PySide6 / Qt：桌面图形能力强，但引入新的二进制依赖、打包与许可评估；Phase 8 的 MVP 没有足够收益抵消迁移成本。

Tkinter 与 ttk 是 Python 官方提供的 Tcl/Tk 接口；PySide6 是 Qt 官方 Python 绑定。选择依据见 [Python tkinter 文档](https://docs.python.org/3/library/tkinter.html)、[Python http.server 安全提示](https://docs.python.org/3/library/http.server.html) 与 [Qt for Python 文档](https://doc.qt.io/qtforpython-6/)。这是一项本阶段实现决定，不改变底层领域模型；未来可更换 UI 壳。

## 视觉与布局

采用深色“精密工作台”：暖石墨背景、青绿色焦点、琥珀色警告、紧凑边框和高对比文本；标题使用 Windows 可用的 Bahnschrift（其他平台回退），代码使用 Cascadia Code（其他平台等宽回退）。布局依照既定规范：窄 Activity Bar、可切换 Sidebar、中央左/右 Editor Notebook、右侧 Inspector、底部状态栏。此处借鉴 VSCode 的信息结构，不复制其品牌资产。

首屏 Overview 展示项目路径、Python / PyTorch 识别结果、模型与入口列表、主区域、分析健康度及 Analyze / Open Architecture / Open Training Flow / Browse Symbols 动作。分析前允许先打开目录；真正解析必须点击 Analyze。Search / Symbols 的完整定位闭环属 Phase 9，Settings 的持久化属 Phase 10；Phase 8 入口明确标记尚未实现，而不返回假结果。

## 交互与数据流

`ui/controller.py` 保存当前目录、一次性分析结果与展开 / 手动位置状态，不依赖 Tk；标签页和当前选中项仅在 Tk 壳中管理。`ui/presenter.py` 从 ProjectAnalysis / VisibleGraph 生成 Overview、Inspector、Problems 纯数据；保留 confidence / evidence / SourceAnchor，源码内容由 Controller 安全读取。`ui/tkapp.py` 只负责绘制与事件转发，不解析 AST 或判断框架语义。

图 Canvas 接收 Phase 7 的局部 VisibleGraph；节点只显示易读名与真实代码名。单击选择并高亮一跳，Inspector 同步显示 Overview / Structure / Relations / Data / Source；双击或 Enter 展开容器，Open Source 和 Open to Side 是独立按钮。边按 Contains、Control、Data、Loop、Gradient 使用不同颜色 / 线型，图例只显示当前实际关系。手动拖动节点更新 ViewState；本阶段会话内保留位置，Phase 10 再落盘。

Editor Notebook 展示 Overview、Architecture、Training Flow、Source；右侧 Notebook 用于 Open to Side。源码查看只按 SourceAnchor 打开真实文件，行号与定位高亮准确，不提供编辑。Problems 独立显示分析 issue。图只渲染 Phase 7 已限额输出，不直接绘制 Full Graph。

## 安全、错误与测试

文件选择限本地目录；任何源码跳转先检查项目相对路径与解析后的真实路径仍在项目根内，打开后再按文件句柄核对最终路径与身份，拒绝符号链接越界及路径替换。UI 不执行或导入被分析项目，不修改源码，不上传源码，不保存完整源码副本。分析在后台线程进行，Tk 控件只在主线程更新；失败与部分成功以显式状态 / Problems 显示，重新分析期间隐藏旧 Overview 与图。

控制器与 Presenter 用纯 Python fixture 测试；文件越界与分析失败必须有回归测试。Tk 视图做可导入和无显示器 CI 的组件映射测试，Windows 上再做一次可见手工烟测。质量门禁沿用 unittest、Ruff、mypy 和离线 wheel。

## 与后续阶段的边界

Phase 8 打通架构与训练两条可交互路径，源码可查看；Phase 9 实现真实搜索、符号消歧和 Reveal in Graph；Phase 10 实现缓存、Outdated、持久 ViewState / Recent Projects。若 Tkinter 的交互性能在真实项目中达不到目标，保留 Controller / Presenter 和 VisibleGraph 契约，仅替换 UI 壳。
