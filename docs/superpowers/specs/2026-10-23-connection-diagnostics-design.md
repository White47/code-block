# 连接检查安全诊断

## 目标和证据

用户截图仅证明 `AI_PROTOCOL_INCOMPATIBLE`，不能证明服务商缺少能力。
当前 SDK 与连接 runner 把发送前校验、HTTP/SSE、结果验证汇总为同一错误码。
本次先补可验证的定位维度，不猜测真实失败原因、不使用用户密钥请求 API。

## 决策

采用架构代理 Nash 的结构化方案，不把机器字段编码进 `message`，也不开放原始日志。
旧 `{code, message}` 保持有效，仅 protocol 错误可增加可选字段：

```json
{"diagnostic":{"boundary":"dispatch","httpStatus":404}}
```

`boundary` 的封闭枚举是 `pre_send`、`dispatch`、`result_validation`。
`httpStatus` 可省略，只允许 dispatch 且为精确整数 100..599，布尔值不接受。
未知异常不提取任意 `.code` 或 `.diagnostic`；只接收已知精确异常类型。
所有字段严格校验，禁止正文、异常字符串、地址、模型、密钥、问题或源码。

## 分工与归属

- issues.py：不可变封闭诊断类型、SafeIssue 序列化，默认旧形状不变。
- sdk_provider.py：发送前证明归 pre_send；HTTP/传输/SSE 归 dispatch；完整流后内容验证归 result_validation。
- connection.py：prepared/admission 归 pre_send；tools/final 语义验证归 result_validation；保留 SDK 已确认诊断。
- service.py：只从精确受信异常提取诊断，未知异常保留无诊断 protocol。
- frontend assistant types/poll/ui：兼容旧形状，严格复制新字段，仅用本地中英翻译展示阶段和数字状态。

HTTP 401/403、429、timeout、budget 仍用原有专用错误码。资源归属、两次 probe、授权、计量、取消与关闭顺序、预算和输入输出协议不变。

## 阶段复审收尾约束

唯一阶段复审发现两处诊断误标：最终结果中的 usage 顺序错误不得使用默认 pre_send；
SDK 异步上下文进入前也可能已经实际发送，不能仅凭上下文是否 yield 判定发送前。
后者必须依据适配器拥有的实际发送阶段，post-stream 的本地结果验证优先于 dispatch。
修复仅改变诊断定位，不改语义核或协议接受条件；补失败用例后跑门禁，不再派第二次独立复审。

## 验收和非目标

离线测试覆盖三个边界、HTTP 状态、旧响应兼容、恶意字段拒绝及清理不回归。
本次不加依赖，不改 parser/resolver/Graph IR/source handles，不自动重试或增加调用。
离线门禁不能证明用户真实连接已修复；真实请求只由用户在应用中主动触发。
后续只需反馈固定阶段/数字状态，无需密钥或响应正文。
