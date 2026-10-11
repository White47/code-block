# 项目资源管理器合同

状态：基础功能及随后添加的树内移动于 2026-10-11 完成规格、独立质量与源码／隔离安装 wheel／EXE 三入口原生验收。最终证据见 [验收记录](PROJECT_EXPLORER_VALIDATION.md)。完整设计与任务门禁见 [设计](superpowers/specs/2026-10-09-project-explorer-design.md)、[移动扩展](superpowers/specs/2026-10-11-explorer-move.md) 和 [实施计划](superpowers/plans/2026-10-09-project-explorer.md)。

## 用户功能

在现有左侧 Explorer 展示真实项目目录，支持展开／折叠、点击打开文本、侧边打开、空文件／目录创建、同目录重命名、项目内移动和系统回收站删除。未 Analyze 的项目也能浏览。保留图视图、搜索、设置与最近项目，不另起完整 IDE。树内移动在基础功能通过后加入。

## 使用

关闭旧应用后运行 `dist/project-explorer/CodeViz/CodeViz.exe`，保留整个 CodeViz 目录及 `_internal`，不要只复制 exe。打开项目后，在左侧展开目录、点击文件；右键提供新建、重命名、删除、侧边打开和“移动到…”。也可把树内文件／目录拖到目标目录；固定提示区显示允许／禁止，不推动树行。菜单目标输入项目相对目录，留空代表根目录，已加载目录可快捷选择。

有未保存修改时先选择保存／放弃／取消；失败保留草稿及菜单目标输入，不自动重复移动。移动不自动修改 Python import；修改后的旧图标为过期，需要用户显式重新分析。删除仅进入系统回收站，恢复在系统中进行。

`.git`、`.venv`、`venv`、`node_modules` 显示但首版只读；根目录不可改名／删除；包含保护项的祖先不能通过整目录操作绕过限制。链接、二进制或不能安全解码的文件有明确提示，不执行或自动用外部程序打开。

## Bridge 契约

保持 schemaVersion=1 的 success/error envelope，新增接口均只接受一个有界对象。

| 方法 | 请求 | 返回 data |
| --- | --- | --- |
| `list_project_directory` | projectPath, directory, projectEpoch?, cursor? | projectPath, projectEpoch, directory, directoryRevision, entries, nextCursor |
| `create_project_entry` | projectPath, projectEpoch, parent, parentRevision, name, kind | operation, oldPath, newPath, affectedPaths |
| `prepare_project_entry_operation` | projectPath, projectEpoch, path, revision, operation；rename 仅增加 newName，move 仅增加 destinationDirectory/destinationRevision，delete 不加目标字段 | operationToken, operation, path, newPath, affectedPaths |
| `apply_project_entry_operation` | projectPath, projectEpoch, operationToken | operation, oldPath, newPath, affectedPaths |

directory／parent 为项目相对路径，根为 `""`；只有根目录初次只读请求允许省略 epoch，其余必须绑定当前打开身份。entry 提供 path/name/kind/revision/readOnlyReason 和 canOpen/canCreate/canRename/canDelete；能力字段用于展示，不构成后端授权。

`get_session` 另提供后端实际平台的 `fileSystemCaseSensitive`。Windows 的受影响文档、pending 保存/打开、标签与侧边身份比较忽略大小写并统一分隔符，Linux 保持精确；老 host 缺字段时按精确比较兼容。归一化只用于匹配，不改变原始源码路径或文档 key。迁移多个大小写别名时去重目标标签身份；后端失效受影响保存 revision 使用相同的平台边界。

每页最多 200 项，扫描／排序与子树核验有独立上限。游标绑定目录版本，token 为后端签发、有效期 60 秒、单次消费。新建独占而不覆盖；prepare 与 apply 两次检查身份、内容、父链及子树。过期／冲突要求刷新后由用户再次操作，不自动重试写操作。

移动扩展复用 prepare/apply，不新增任意写路径接口。`destinationDirectory` 是项目相对目录，根为 `""`；源 basename 不改变。令牌绑定源子树、源父目录及目标目录／父链快照。同项目同卷无覆盖移动，没有复制再删除 fallback，不修改 import。目标能力按 directory＋canCreate 判断：含保护兄弟子目录不等于安全落点不可用；来源整体含保护项仍不可移动。

## 编辑与身份

不把源码保存改成任意文件写接口。原 Python coding-cookie／BOM／编码与权限保留保持，常见文本采用独立安全解码策略与 5 MB 上限。UTF-8／UTF-8 BOM／UTF-16 BOM 必须原始 bytes 精确往返；UTF-32、二进制／控制数据和不支持的编码拒绝。CR-only／混合 EOL 维持只读。

重命名／删除前处理目标和后代的 dirty／pending 草稿，保存／放弃／取消对话框与现有编辑器一致。guard 保存完成后重新准备操作；保存失败、取消或文件操作失败均不得丢草稿／关闭相关视图。重命名成功更新标签和文件身份，删除成功关闭关联视图；只释放受影响 model／baseline，保留无关草稿。旧路径迟到响应不得重建已释放文档。

已分析项目在变更后 Outdated，保留旧图、布局和注释；无分析仍 NotAnalyzed。不自行改写旧图的 SourceAnchor，不修改 import，不自动 Analyze 或执行代码。新图必须显式重新分析取得。

原生创建/改名/删除可能已改变磁盘后才失败。此类不确定结果必须失效受影响 revision、标记已有分析 Outdated 并刷新目录读取，保留草稿和标签；不能自动重试写操作或删除新建文件回滚。明确尚未创建的前置失败保持原状态。

## 删除与恢复

原生 Windows 仅使用现代 IFileOperation recycle-only 标志和失败即停止语义，同时检查取消和逐项结果。不支持、权限失败、取消及不确定结果均不转永久删除；无 unlink/rmtree/旧系统接口 fallback。仅在系统回收站恢复，不承诺应用内 Undo。[微软接口说明](https://learn.microsoft.com/zh-cn/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperation-setoperationflags)。

所有路径校验与并发限制遵循 [SOURCE_EDITING](SOURCE_EDITING.md) 的既有边界；多次检查缩小但不能消除非协作外部进程的 TOCTOU，不宣称绝对 CAS。系统不提供跨项目操作、覆盖冲突目标或批量永久清理。
