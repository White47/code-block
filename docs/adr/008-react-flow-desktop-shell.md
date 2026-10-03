# ADR-008 — React Flow 与 pywebview 桌面壳

状态：Accepted，Windows 技术桌面验收通过；人工理解度与物理断网验证仍待后续。日期：2026-09-29；验收更新：2026-10-03。

## 背景

Tkinter MVP 已验证分析、查询、导航和源码回溯边界，但复杂图交互、编辑器式标签页和细节岛需要更适合图形应用的 UI。Python 分析核心仍应是唯一事实来源；浏览器层不得重新解释图、推断搜索定位目标或直接读取项目文件。

## 决定

采用 bundled React、TypeScript、Vite 和 React Flow 作为展示层，以 pywebview 创建原生桌面窗口。生产构建不使用 CDN 或远程运行时资源。Python 在每次启动时把打包后的 `frontend/dist` 通过仅绑定 loopback 的静态资源服务器发布到操作系统分配的随机端口，再把该随机 URL 交给 pywebview。服务器不绑定 `0.0.0.0`、局域网或公网地址，不提供项目源码、分析数据或应用 API。

所有应用调用继续通过 pywebview 的进程内 JavaScript bridge 完成，不使用 HTTP bridge，也不让静态服务器接收 bridge 请求。bridge 只接收有界输入并返回显式 JSON DTO；Python 负责搜索结果的 `RevealTarget`、视图选择和源码访问校验。响应统一使用带 `schemaVersion` 的成功 / 失败 envelope，未知 metadata 与 query context 字段在 Python 白名单边界丢弃，非有限数值拒绝序列化。

实施安全补充：不把 DesktopBridge 对象作为 `js_api` 交给框架；仅 expose 固定 26 项方法。对本应用窗口的 API dispatcher 校验精确方法名、必需 / 可选参数数量、列表参数容器与有界安全 callback token，拒绝点路径和内部对象遍历；同时修补已导入 Edge renderer 的缓存 dispatcher 引用。此保护不声称覆盖框架独立的 native alert / console / drop 分支。版本锁定 pywebview 6.2.1，升级时必须重跑实际 dispatcher 的安全回归。

`ignore_paths` 属于项目分析设置，不属于语言等全局 UI preferences。Tkinter 入口和实现暂时保留，直到 Task 6 桌面验收通过。

## 安全与生命周期

静态服务器使用随机 loopback 端口，生命周期与桌面进程一致；不得开放远程监听、目录列表、写入、上传或动态路由。应用关闭时停止服务器。生产包只提供构建产物，Content Security Policy 与资源审计应阻止远程脚本、远程样式及 CDN 回退。bridge 不执行被分析项目，也不把 AST、任意 Python 对象或未白名单 metadata 交给 React。

## 权衡

loopback URL 避免 `file://` 下模块加载、路由和 WebView 安全策略差异，同时增加一个必须正确关闭的本地静态服务器。随机端口和回环绑定降低暴露面，但并不把静态服务器当作可信 API 边界；敏感操作仍只存在于 pywebview bridge。新增 Node 构建链、pywebview 和 WebView2 平台依赖，换取成熟图交互与可测试的组件化界面。
