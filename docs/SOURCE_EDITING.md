# 源码编辑合同（Monaco MVP）

用户于 2026-10-08 确认从只读源码查看扩展为可编辑，并选择当前工作区。该功能不改变静态分析、不执行代码、不上传源码的边界。本文接口已经 architect 复审批准，作为本轮实施合同。

## 数据与接口

保留旧 `read_source(anchor)`、SourceAnchor、图与分析事实的格式。新增编辑专用接口，不把旧的 UTF-8 replacement 解码文本用于保存。

```typescript
interface EditableSource {
  projectPath: string;
  filePath: string;
  text: string;
  revision: string;
  encoding: string;
  newline: 'LF' | 'CRLF' | 'CR' | 'Mixed';
  readOnlyReason: string | null;
}
interface SaveSourceRequest {
  projectPath: string;
  filePath: string;
  text: string;
  revision: string;
}
// open_source_document(projectPath, anchor) -> EditableSource
// save_source_document(request) -> EditableSource
// approve_close(requestId) / cancel_close(requestId): each takes 1 argument
```

projectPath 必须匹配当前已打开项目的真实身份；filePath 是项目相对路径，不接受绝对路径、路径穿越、NUL、网络路径或非 `.py` 文件。revision 绑定规范化项目／文件身份、原始 bytes 指纹与项目打开 epoch；A → B → A 后旧 revision 也失效，而非行号或修改时间单独决定。服务在内存保留有界的原始编码／版本 metadata，不把完整源码写入 Analysis Cache 或 View State。

## 保存规则

- 只保存当前项目内已存在且安全可核验的常规 `.py` 文件，读写上限 5,000,000 bytes。
- 编辑读取必须使用 Python 源码编码检测并严格解码；不以 replacement 字符偷偷改写原数据。支持的可往返编码、BOM 和一致换行保留；混合换行或无法安全往返时只读并说明原因。
- 路径各级的 symlink／junction 与多硬链接文件不可编辑；目录或目标身份变化拒绝保存。旧只读查看策略保持兼容。
- 保存前以及同目录临时文件完成后再次核验目标原始内容、文件身份、项目和父目录。外部修改／删除／替换时返回 `SOURCE_CONFLICT`；不提供强制覆盖按钮。
- 临时文件独占创建、flush/fsync，保留权限／ACL，经原子替换提交；写入／权限／replace 失败不截断旧文件，清理的只能是本次独占临时文件。只读文件不可通过替换绕过，不能为成功保存降级权限。全部 reparse traversal 拒绝。
- 无换行文件采用 LF 默认值，但不自动追加末尾换行；本轮 CR-only 明确只读，避免 Monaco 换行规范化造成反复 dirty；LF／CRLF 支持编辑。
- 串行化应用内保存；保存成功但前端在等待时又输入了新文本，只更新 baseline revision，不能将新草稿误标为 clean。
- 非协作的外部进程与恶意并发路径替换不能靠跨平台 Python 检查实现绝对 CAS。须缩短检查至替换窗口、失败关闭并如实记录剩余竞态，不宣称系统级排他锁。

新增明确错误至少包括 `SOURCE_CONFLICT`、`SOURCE_PROJECT_CHANGED`、`SOURCE_READ_ONLY`；非法请求仍为 `INVALID_REQUEST`，实际 IO 失败使用安全的保存失败提示，不泄漏源码、绝对路径或系统 traceback。

## 分析与草稿

一份项目／文件对应一份共享内存草稿；不同 SourceAnchor、多个标签和 split 共享文本与保存版本。不同项目不能串稿。Monaco model 的撤销历史在普通标签切换时保留；最终释放文档视图或项目切换通过 guard 后才 dispose。

成功保存使已有分析标记 Outdated 并保留旧图、布局与注释；未分析的项目仍为 NotAnalyzed。不自动 Analyze，不导入／运行被编辑源码。未保存和 Outdated 时提示图与源码行定位可能已过期，不伪称 graph 已同步草稿。

保存返回提交文本与新 baseline；前端随后刷新 session 获取 Outdated，不通过 hydrate 清空草稿。迟到的保存成功不得把后来输入标为已保存。

## 关闭与导航

- 未保存标签显示 `●`；Ctrl+S 和保存按钮都走同一版本校验保存。
- 关闭最后一个该文件的 source 视图、替换／关闭侧边源码、换项目、重新分析或会重建 workspace 的设置操作，先处理相关 dirty 或 pending 草稿。普通图／标签切换保留草稿而无需弹窗。
- 对话框提供“保存／放弃／取消”；批量保存逐个执行，任一冲突／失败都停止后续破坏性导航，保留剩余草稿。取消不调用项目 mutation 或销毁窗口。
- 原生窗口关闭先取消该次 OS 关闭并发出请求，前端完成上述 guard 后才能批准关闭；使用单次请求身份防止旧批准关闭新会话。callback 快速返回，JS／destroy 另线程执行，避免 UI 线程死锁；渲染器失败或超时不可误当保存成功。
- requestId 绑定窗口／renderer epoch，`approve_close` 与 `cancel_close` 单次消费；批准后仅放行对应一次关闭。渲染器未 ready 或请求失败时保持窗口打开。
- 实际派发使用后台 `run_js`（不是内部含 eval 的 `evaluate_js`）；事件为 `codeviz:close-request`，载荷 `{requestId}`，前端挂载 listener 后设置 `__codevizCloseReady`。原生销毁失败发送 `codeviz:close-failed`，只有对应批准中的请求可以解冻，旧事件不能解除新请求的锁。
- `beforeunload` 仅为额外防护，不代替 pywebview 原生关闭事件。
- 既有 40 标签限制不得静默丢弃 dirty 文件；明确拒绝或先 guard 被移出的源码视图。

## 离线与安全

Monaco 固定版本、Python language、editor Worker 和字体随 Vite／wheel／冻结包本地分发。CSP 仅放行同源 Worker，继续禁止远程资源、任意 frame、popup 和导航；不使用 CDN、远程 loader、unsafe-eval 或 blob Worker 放宽策略。

首版不含终端、调试器、语言服务器、自动保存、自动重构、新建任意文件。彩色语法不等于 Python/Pylance 语义推断。强制结束进程时恢复未保存草稿不在本轮范围。

## 验收

临时 fixture 覆盖编码／BOM／EOL、外部冲突、旧项目／旧 revision、链接与路径越界、写入故障、Outdated 与注释保留。前端覆盖 shared draft、in-flight 编辑、Ctrl+S、所有 save/discard/cancel 入口和迟到完成。实际 PC 验收必须观察 Monaco token 颜色、编辑、保存后磁盘 bytes、OS 关闭取消、离线 Worker；mock 编辑器只能证明状态逻辑，不能证明实际高亮和 PC 运行。
