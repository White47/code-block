# Python 分析支持矩阵

本表区分语法读取、静态事实、可见图与运行语义。计划项不等于已经支持，完成后必须附测试 / 实测记录。支持边界以分析器实际 Python 运行时为准；当前桌面开发环境为 3.12，兼容基线为 3.11。

| 特性 | 基线事实 | 本次验收目标 | 状态 |
| --- | --- | --- | --- |
| 普通模块、类、函数、async def | AST / SymbolIndex 已有 | Overview 可浏览；任意具名函数 / 方法可打开 FunctionFlow | Task1 需求 / 质量通过 |
| main guard、__main__.py | 普通符号可读，概览遗漏 | 保存入口结构依据，明确非运行证明 | Task1 需求 / 质量通过 |
| 普通库，无自动入口 | 可搜索符号 | 人工选择函数作为流程根，不伪装入口 | Task1 需求 / 质量通过 |
| lambda | 参数 / body 作用域缺口 | 参数局部、默认值外围、调用不归外层 | Task2 需求 / 质量通过 |
| 列表 / 集合 / 字典推导式、generator expression | 存在外围绑定误判 | 首 iterable 外围、后续局部、class namespace 隔离 | Task2 需求 / 质量通过 |
| match 捕获 | 捕获变量缺绑定 | 同 enclosing scope，OR / as / star / rest / guard | 绑定通过；值来源明确 Partial |
| global / nonlocal / 闭包 | 基础解析已存在 | 新隐式作用域加入后不回退；冲突声明 Partial | Task2 需求 / 质量通过 |
| 导入别名、相对导入、重导出 | 常见结构已存在 | 明确图关系；保持未解析诊断 | Task3 需求 / 质量通过 |
| 实例方法、cls、继承 / super、函数别名 | 不完整 | 有界候选、保守置信度，不凭同名连 Exact | Task3 需求 / 质量通过 |
| if / for / while / break / continue | 基础 CFG 已存在 | 新流程可见、预算截断、准确源码 | Task4B 需求 / 质量通过 |
| match 控制结构 | CFG 不完整 | 顺序 case / guard / default 分支，保留条件摘要 | Task4B 需求 / 质量通过 |
| try / except / else / finally | 提前退出路径简化 | 已知正常 / return / raise / break / continue 分离；隐式异常 Partial | Task4B 需求 / 质量通过 |
| 普通参数 / 局部变量 / 返回值 | 默认关键数据偏 ML | 按需过程内 may def-use；调用未知 / 捕获绑定 / 来源截断 Partial | Task4A 需求 / 质量通过；finally 来源保守近似 |
| async / await / generator | AST 能读 | 不声称并发时序或实际迭代；限制可见 | 静态结构范围 |
| 动态 import、反射、monkey patch、动态生成代码 | 不能可靠确定 | 维持 Probable / Unresolved 和依据 | 明确非目标 |
| 第三方库实现 | 默认 External | 不执行或扫描整个环境；外部节点清晰 | 保持 |
| 缓存恢复，无 AST | 已有缓存图 | 新流程仍可查看，旧分析升级保留注释 / 布局 | Task1 通过；最新 wheel / 冻结包原生复测通过 |

## 质量门槛

### 当前整合状态（2026-10-05）

Task1、Task2、Task3、Task4A / 4B 的需求与质量均通过独立审查；整体复审曾发现的候选嵌套循环累计访问缺口已修复、重建重验，并获最终整体批准。最新后端 611 测试 / 49 跳过、零失败；前端 43 测试通过。固定 Click / Requests / HTTPX 源码范围及 ML 夹具静态验证、最新冻结包普通 / ML 各五项原生交互、隔离 wheel 普通五项交互均通过。整体结论、产物 SHA 与历史失败限定见 GENERAL_PYTHON_VALIDATION.md；批准仅限本表静态范围，不代表完整 Python 运行语义或跨函数值传播。

- Exact 名称引用表示定义归属，不保证变量已绑定或调用实际发生。
- 静态 CALLS、控制结构、数据依赖分别表达，不冒充 runtime trace。
- Python 语法版本不兼容与用户语法错误应分开说明；不承诺未来语法被旧 AST 接受。
- 自建夹具证明具体边界；真实仓库抽样证明实用性，不用节点数代替正确性。
- 每次阶段报告标明通过、跳过、未验证。计划任务只有满足验收证据才转为完成。

实施任务见 GENERAL_PYTHON_PLAN.md；研究依据见 research/2026-10-03-general-python-analysis.md。
