# CodeViz Windows 桌面包

[下载 2026-11-02 完整 Windows x64 压缩包](https://github.com/White47/code-block/raw/refs/heads/main/releases/CodeViz-Windows-x64-20261102.zip)

解压后运行 `CodeViz/CodeViz.exe`，保留整个 `CodeViz` 文件夹。无需另外安装 Python 或 Node；Windows 需有 WebView2 Runtime。

此包为本地已完成小型 Python / nanoGPT 原生交互验收的版本，与源码提交 `9b797125279004d2127d473c0088ad4c4c0d89ed` 的应用代码对应。同目录 `.sha256` 文件可校验下载内容。

验证范围：后端1752通过/106跳过；前端669通过/2项待实现；Ruff、Windows/Linux mypy、前端 lint/typecheck/build、两项目原生交互和固定离线 SDK 探针通过。未执行被分析源码、未使用个人密钥或追加真实 API 请求；真实 AI 回答质量与用户理解效果未据此验收。

本包通过 Git 直接保存在 main，避免 GitHub 状态接口限流影响本轮下载交付。现有 Actions 仍会从源码重新构建并上传临时 artifacts，其结果不替代本包的校验值。详见[交付记录](../docs/HUMAN_UNDERSTANDING_DELIVERY.md)。
