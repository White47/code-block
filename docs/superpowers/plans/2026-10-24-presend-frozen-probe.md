# 冻结包发送前失败定位

> 用户授权自主排错；执行以 AGENTS.md 为准。架构 Linnaeus 已批准固定私有离线入口；不改生产协议，不调用真实 API。

## 证据与边界

用户截图为新版 pre_send，WMI仅路径元数据确认进程运行 assistant-hardening-20261024/frozen-final。
当前地址 :443 是已应用规范化值，不能归因于地址。源码实际 transport 构造/关闭离线通过；
冻结依赖静态闭包尚未发现缺项，但这不能替代冻结进程执行。假 HTTP 既有测试没有覆盖真实 transport 初始化。
SDK 环境自定义头的合成注入能造成 pre_send，当前进程/User/Machine 该变量的存在性均为 false；不能据此宣称用户根因。

## Task P1：固定 GUI-free 冻结探针

Files: scripts/assistant_offline_probe.py（新增）、scripts/desktop_entry.py（私有路由）、tests/test_assistant_offline_probe.py。

- [x] 先测缺失入口/脚本失败；测试精确 argv、拒绝额外输入而不回显、固定异常类型脱敏。
- [x] 仅新增冻结私有参数 `--_codeviz-assistant-offline-probe --fake-only`，禁止其他参数、路径、URL、密钥、源码输入。
- [x] 先 guard，默认禁用 runtime，再 `_load_sdk_modules()`，真实 TLS transport 仅创建/关闭，然后替换仅 HTTP transport，固定惰性凭据及两个 source-free 连接 probe，最终 shutdown/释放。
- [x] 复用 assistant_smoke_host._helper 和 FixtureHTTP，不访问用户工程或原始源码；输出仅固定 stage/type/check 枚举、请求/外联计数和释放状态。所有失败与 CLI 误用不输出原始错误文本/argv。
- [x] 输出失败时保持 fail-closed，不修改生产 wire proof、预算或安全限制。探针不等于 native 或真实 API 验收。

## Task P2：实际构件定位与最小修复

已复现根因：冻结进程在 `ssl.SSLContext.__new__` 抛 `FileNotFoundError`，请求/外联均为0。
构建解释器的 ssl.py 含运行时补丁：每次构造均从同目录加载 `codex-rejected-cas.pem`；
旧冻结包未带该文件。spec 仅在构建解释器存在此固定文件时原样收集到冻结 ssl.py 同级，
标准解释器不新增策略。不改 SSLContext，不绕过证书/主机名校验，不更换系统信任根。
新增 spec 执行回归覆盖有/无运行时策略文件；最终以真实冻结进程探针为准。
阶段门禁发现 `test_packaging_layout.py:95` 将数据列表写死为旧的两项；随新增策略更新
该精确 AST 预期为原列表加 `*ssl_data`，不删旧资源断言，并由有/无文件的执行测试补强。

- [x] 定向测试及源码/安装包离线探针后，复用现有环境离线构建 build/assistant-presend-20261024 下的新 exe；不覆盖正在运行的试用包。
- [x] 在实际 exe 运行私有探针；只有可重现证据才修代码。若涉及 SDK/core 多模块不变量，按 mandatory routing 执行。
- [x] 根因已复现并修复；fixed候选冻结进程真实 TLS 构造/关闭及两次 source-free 假请求通过，外联0、资源释放。

## Task P3：阶段收尾

唯一 Pasteur 边界审查发现自检 shutdown 异常会越过后续清理且沿用 resourcesReleased=true。
已补单项红绿回归：捕获该异常、标记释放失败，并继续关闭 transport。未改语义核，不做第二复审。
最终 helper 变动后重建 final 构件并按边界重跑适用门禁；旧1611项全量通过不冒充此快照结果。

- [x] 唯一增量阶段审查，改动模块/消费者定向测试及最终 Python 全量1612项（1476通过/136跳过）、Ruff通过；未变 src 的双平台 mypy102及未变前端591通过/2todo复用。
- [x] 最终 frozen-final exe 与源码一致，冻结probe请求2/外联0/释放通过，保存 package-bytes.json 与 frozen-final-receipt.json；阶段断点最多5行，无密钥、付费请求、源码上传、提交/推送。
