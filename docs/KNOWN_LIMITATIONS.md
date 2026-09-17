# 已知限制

## 当前工作区限制

Phase 0 初次审计发现工作区为空且无 Git 仓库；用户随后确认从空目录新建。Phase 1 已建立 Python 标准库领域核心，Phase 2 已加入一次性 Python 文件发现、AST 解析与词法符号索引，Phase 3 已加入独立 Resolver；仍无调用 / 控制 / 数据流、PyTorch 规则、图 UI、状态管理或缓存。指定连接器的 `workspace_info` 仍返回内部错误，其绑定身份未验证；当前阶段的目录与 Git 检查来自直接读取目标路径。

Phase 3 能处理常见绝对 / 相对导入、`src` 布局、词法作用域与显式成员，但不是完整 Python 解释器。星号导入只报 issue，不推断导出名；动态 import、运行时属性绑定、条件 / 顺序敏感的名称重绑定、推导式细分作用域和复杂嵌套 `nonlocal` 仍可能保持 Unresolved 或 Probable。缺少 `__init__.py` 的 namespace package 尚无专门合成包节点；重复定义的发生序号在插入更早的同名定义后可能改变。当前不生成 Code Graph 的调用 / 控制 / 数据流边。符号链接测试在当前 Windows 环境因无法创建测试链接而跳过，代码仍明确不遍历链接。单文件默认 5 MB 上限；项目级文件数和总字节数上限尚未配置，分析大项目仍需谨慎。

## MVP 限制

- Python + PyTorch 优先，其他语言只保留 Adapter 接口。
- 静态推断不等于 runtime observation；shape / dtype / device 只展示静态可确认值。
- DDP、FSDP、DeepSpeed、复杂 distributed、自定义 autograd 深层分析、复杂 hooks、完整 autograd graph、动态 graph surgery 后置。
- `.ipynb`、YAML、JSON、TOML、Hydra 等只预留接口，首版重点 `.py`。
- 复杂动态 import、monkey patch、反射和运行时生成代码可能保持 Unresolved。
- 语义组件不是类 / 函数的一一映射，某些 Probable 结果必须保留原名。
