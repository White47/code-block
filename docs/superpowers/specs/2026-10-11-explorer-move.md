# 项目树内部移动扩展

日期：2026-10-11。此前 architect 已审查并批准此最小扩展；实际实施须等基础资源管理器 spec、quality 与原生验收通过。用户明确要求基础完成后再加入拖拽。

## 接口

沿用 `prepare_project_entry_operation`，增加 `operation: 'move'`，请求包含 `projectPath, projectEpoch, path, revision, destinationDirectory, destinationRevision`。目标根目录用空字符串，不接受 `newName`；后端计算目标路径为目标目录加源 basename。apply 仍只接收当前项目身份与一次性 operationToken，返回 operation/oldPath/newPath/affectedPaths。

令牌同时绑定源条目、源子树及父链，和目标目录快照及父链；prepare 与 apply 都重新核验。保留 60 秒到期、单次消费、大小/条目/深度预算。按 rename/move/delete 显式分派，移动不能落入删除分支。

## 安全边界

只做同项目、同卷、无覆盖移动，复用原生 no-replace rename。不提供复制再删除 fallback，不自动改 import。拒绝根目录、自身/后代、同路径、大小写冲突、重名、非安全目标、保护路径/保护源子树、链接及硬链接。目标含另一个保护子目录不意味着不能在安全落点新增文件。

顺序必须是：锁定导航 → 在文件操作队列外等待 pending 保存并完成相关 dirty 文档的保存/放弃/取消 → 文件操作入队 → 重新读取源条目和目标目录版本 → prepare/apply。保存本身使用同一队列，不能在文件操作队列内嵌套等待保存。放弃只在操作成功后兑现。失败保留草稿、model 与标签，不自动重试写操作。如果目标新路径已有活跃文档身份，明确拒绝，不能覆盖内存草稿。

成功迁移源前缀下的源码标签与侧边身份，废止旧 baseline 和迟到响应；失效旧/新父目录及相关子树缓存，保留无关草稿。原分析标 Outdated，图与注释保持，不自动分析。

## 交互

仅接受本树内部 dragstart 建立的 activeDrag：payload 的 epoch、组件 nonce、源路径逐字段匹配；外部 text/path、其他树、过期 epoch、重放均拒绝。dragend、Escape、卸载、项目切换清理 activeDrag。目标 hover 有允许/禁止反馈，drop 经同一 dirty guard 后才提交。执行中不可伪称 Escape 已取消。

提供“移动到…”菜单作为无障碍替代，选择当前项目的目标目录；与拖拽使用同一提交流程，不另建写接口。

最小目标选择器为项目相对目录文本框（根目录留空），附已加载目录的快捷选择，不为此扫描完整目录树。源条目使用 canRename 作为写能力提示，目标使用 canCreate；两者只影响交互提示，后端仍重新授权。保留基础阶段平台路径策略，保存后以 fresh 实际条目的 path/revision 准备移动；目标版本同样在 guard 完成后取得。

## 验收

- 文件和多层目录移入目录/根：basename、bytes、标签/侧边身份、无关草稿、旧 revision 失效、Outdated。
- dirty 保存/放弃/取消、pending 保存、冲突与移动失败均不丢数据；自身保存后使用新目录版本。
- 自身/后代/同路径、重名、保护/链接、跨卷、预算超限均无写入。
- prepare 后源变更、目标替换/同名新增、旧 epoch、过期/重放令牌均拒绝，无覆盖 fallback。
- 外部/伪造/旧 nonce 拖拽拒绝、Escape 清理、hover 反馈；提交期间导航/原生关闭锁定。
- 成功后刷新失败只重试读取，不重复移动；独立 reviewer 与真实 PC 文件/目录移动验证必需。
