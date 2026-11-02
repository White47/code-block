# 代码块 · CodeViz

一个帮你读懂 Python 项目的桌面工具。

拿到一个陌生项目，常常要先翻目录、找入口，再在几个文件之间来回跳，才能大概拼出它在做什么。函数名和调用关系都看到了，主线却不一定清楚。

我想用代码块解决的，就是这一步：先把项目里能确认的结构和流程整理出来，让你知道从哪里看起、哪些模块在配合、某一步对应哪段代码。图中的节点可以继续展开，也可以直接打开源码，边看整体，边核对细节。

目前主要支持 Python，也针对 PyTorch 的模型组件和训练流程做了额外处理。普通脚本、自己的小项目，同样可以拿来分析。

## 下载和运行

[查看 Releases](https://github.com/White47/code-block/releases) · [直接下载 Windows x64 桌面包](https://github.com/White47/code-block/raw/refs/heads/main/releases/CodeViz-Windows-x64-20261102.zip)

下载 ZIP 后完整解压，运行 `CodeViz/CodeViz.exe`。请保留整个 `CodeViz` 文件夹，不要只拿出 exe，也不要在压缩包里直接运行。

桌面包已经包含 Python 和界面资源，使用时不用另外安装 Python 或 Node.js。Windows 需要有 Microsoft Edge WebView2 Runtime。校验文件和具体版本说明见 [下载说明](releases/README.md)。

## 可以用它做什么

- **先找主线。** 从项目概览查看入口、主要职责、任务流程和功能架构，先建立整体印象。
- **顺着图往下看。** 按需展开组件和内部流程，查看调用、数据或控制关系。图默认纵向布局，同级分支并列。
- **回到真实代码。** 选中节点查看它来自哪个文件、函数和代码范围，再打开源码或放到侧边对照。
- **找一个具体东西。** 搜索类、函数、方法或变量，区分同名符号，查看它附近的关系。
- **直接整理和修改文件。** 左侧文件树支持展开、新建、改名、删除和项目内移动；源码页支持语法高亮、查找替换、编辑与显式保存。
- **借助 AI 继续理解。** 在你授权的范围内生成项目说明，再围绕其中的模块、步骤和引用追问。

## 第一次使用

1. 点击“打开项目”，选择本地 Python 项目文件夹。
2. 点击“分析项目”，等待本地静态分析完成。
3. 从“项目概览”开始，切换“任务流程”和“功能架构”，有多个入口时可以分别选择。
4. 单击节点看说明和来源，双击或按 Enter 查看实现；需要对照代码时，选择“打开源码”或“在侧边打开”。
5. 想查某个函数或变量时，用左侧搜索定位，再沿着局部关系继续看。

第一次可以挑一个自己熟悉的小项目，先对照源码看看图表达了什么。看长图时用滚轮上下浏览，也可以平移、缩放或适应画布。标签页上的 `×` 和 `Ctrl+W` 可以关闭当前页面。

界面默认简体中文，在“设置 → 界面语言”里可以切换英文。手动修改源码并保存后，已有分析会标为过期，需要重新分析才能更新图。

## AI 怎么用

本地分析和浏览源码不需要 API。想要更详细的项目解释时，可以在“设置 → AI 设置”里填写服务商地址、模型和自己的 API 密钥，再核对并授权可发送的文件范围。

之后主动点击“AI 分析项目”。有效说明会和已有任务图配合展示，完整解释及覆盖范围放在可折叠区域；你也可以围绕某个条目继续提问。能对应到可信源码的引用可以打开查看，无法确认的内容会保留为推断或未知。

AI 请求会把授权范围内的必要上下文发送给你配置的服务商，并可能产生费用。密钥只保存在当前进程内存中，退出后需要重新输入。AI 负责分析和解释，源码修改仍由你在编辑器里主动完成。具体操作见 [助手使用指南](docs/CODE_AGENT_GUIDE.md)。

## 目前的边界

项目还在迭代。我更关心的是，第一次看到代码的人能不能沿着图找到主线，知道每一步在做什么，再准确回到源码。现在的图和说明还不能保证每个项目都能讲清楚，也欢迎直接反馈看不懂的地方。

本地分析只读取源码，不会运行你的项目。Python 的动态调用、反射、复杂配置和运行时行为，有些只能给出候选或留下缺口；图中的置信度需要结合源码一起看。实际张量形状、完整梯度图和复杂分布式训练，目前也不属于完整支持范围。

AI 的回答受授权文件、检索范围和预算影响，生成了项目说明并不代表模型读过全部文件。比如 nanoGPT 中由配置动态选择的数据集，准备脚本与训练入口之间没有证据的对应关系，仍会保留为不确定项。

当前桌面发布以 Windows x64 为主，Linux 和 macOS 的原生桌面界面尚未完成。更具体的范围见 [Python 支持说明](docs/PYTHON_SUPPORT_MATRIX.md)、[已知限制](docs/KNOWN_LIMITATIONS.md) 和 [本轮交付记录](docs/HUMAN_UNDERSTANDING_DELIVERY.md)。

## 从源码运行与参与开发

<details>
<summary>展开开发环境、检查命令和相关文档</summary>

需要 Windows、Python 3.11+、Node.js 22 和 WebView2 Runtime。下面的基础环境用于本地界面和静态分析开发，首次安装需要下载依赖。

在仓库根目录执行：

```powershell
py -m venv .desktop-venv
.\.desktop-venv\Scripts\python.exe -m pip install -e ".[desktop,dev]"
npm --prefix frontend ci
npm --prefix frontend run build
.\.desktop-venv\Scripts\python.exe -m codeviz.ui.desktop
```

AI 开发与完整打包还需要已有的可选 AI、packaging 依赖。实际 SDK 检查使用指定隔离环境，安装方式和环境要求见 [测试策略](docs/TEST_STRATEGY.md)；桌面构建入口是 [build-desktop.ps1](scripts/build-desktop.ps1)。

后端检查：

```powershell
.\.desktop-venv\Scripts\python.exe -m ruff check src tests
.\.desktop-venv\Scripts\python.exe -m mypy
.\.desktop-venv\Scripts\python.exe -m mypy --platform linux
.\.desktop-venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

前端检查：

```powershell
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run build
```

浏览器中的 Vite 开发页面用于前端调试，完整项目操作通过原生桌面窗口进行。日常修改优先验证受影响模块，阶段结束再执行完整门禁；环境跳过与实际通过分别记录。

- [产品目标](docs/PRD.md)：项目想解决什么，以及功能边界。
- [系统架构](docs/ARCHITECTURE.md) · [目录职责](docs/PROJECT_STRUCTURE.md)：代码如何组织。
- [源码编辑](docs/SOURCE_EDITING.md) · [项目文件树](docs/PROJECT_EXPLORER.md)：编辑、保存与文件操作规则。
- [测试策略](docs/TEST_STRATEGY.md) · [质量检查](docs/QUALITY_GATES.md)：怎样验证改动。
- [MVP 验收](docs/MVP_ACCEPTANCE.md)：需要打通的使用路径。

GitHub Actions 会检查 Python 3.11 / 3.12、前端质量、wheel 和 Windows 打包；实际 SDK 检查在隔离环境中运行。云端打包产物可在对应成功运行的 Artifacts 中下载，按当前设置保留 14 天。

</details>

## 反馈

如果图里只剩一堆名字，或者某个节点点开后仍然不知道它在做什么，可以直接在 Issues 里反馈。项目类型、操作步骤、报错，以及一个便于复现的小例子，都会很有帮助。这也是代码块接下来想继续改好的地方。
