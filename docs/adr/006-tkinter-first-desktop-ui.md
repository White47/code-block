# ADR-006：Phase 8 先用 Tkinter 构建本地桌面 UI

- Status：Accepted for Phase 8
- Date：2026-09-18

## Context

当前仓库是 Python 3.11+ 标准库项目，无现成前端、桌面壳或图形依赖。Phase 7 已提供限额的 UI 无关 VisibleGraph。用户要求 VSCode 风格本地项目阅读、Graph / Source 分栏和静态分析安全边界。

## Decision

采用 Python 自带的 Tkinter / ttk 与 Canvas，实现本地单进程桌面 MVP；在当前目标环境实测 Tk 8.6 可导入。Controller / Presenter 与 Tk 绘制层分离，不让 UI 解析源码、绕过 View Builder 或执行用户项目。暂不引入本地 HTTP 服务、Node 构建链或 PySide6。

## Consequences

无新增运行时依赖，源码不暴露到监听端口；可在用户当前 Windows 环境交付首版。代价是复杂图渲染、平台字体和自动化视觉 QA 需额外精力，图形能力不足时仍可替换壳。Tkinter 运行时可用性需在启动时检查并给出清晰错误；CI 只做无显示器可运行的单元 / 导入测试。

参考：[Python tkinter](https://docs.python.org/3/library/tkinter.html)、[Qt for Python](https://doc.qt.io/qtforpython-6/)、[Python http.server 安全限制](https://docs.python.org/3/library/http.server.html)。
