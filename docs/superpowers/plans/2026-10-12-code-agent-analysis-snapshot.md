# B3 Immutable Analysis Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Process amendment (2026-10-15):** process is governed by the repository `AGENTS.md`. Do not load `subagent-driven-development` or `executing-plans`; do not run a per-task spec/quality review pair. Technical requirements below are unchanged.

**Goal:** 将已有分析事实投影为有界、不可变、无I/O的查询快照，供后续只读工具使用，不复用可变分析图或AST。

**Architecture:** 每个分析上下文一个快照，绑定root/ignore/epoch/generation；多个RequestScope可复用，request ID不进入快照。白名单新值对象复制，不保留原record、graph、metadata、AST或UI状态；不重新解析，不创建核心IR ID。

**Tech Stack:** 现有Python 3.11+、标准库frozen/slots dataclass、unittest/subTest、Ruff/mypy。没有新框架、源码读取、网络或SDK。

---

## 1. 前置与文件

B1/B2本机实现及独立复审已通过；938项Python全量无失败、58环境跳过，415项前端通过。平台/最终包发布门禁仍待补齐，不称跨平台已验收。本计划处于架构评审，未授权B3生产代码先行。

Create `src/codeviz/assistant/snapshot.py`、`tests/test_assistant_snapshot.py`。既有assistant exports可保持不变，调用方从snapshot模块导入，避免无必要改A公共API。`snapshot.py`只承担值投影与小范围查询，不引入Provider/B4/B5；如需要拆文件，先提出明确职责和架构复审，不自行扩大。

## 2. 不可变白名单记录

以下全部是新frozen/slots记录，文本字段为严格str，可UTF8编码；ID非空且无控制字符。枚举必须对应实际枚举类型，bool严格bool，int严格int排除bool/float，集合严格tuple。公共构造器校验本记录局部类型/范围；builder验证跨记录关联和预算。

| 记录 | 精确字段 |
| --- | --- |
| SnapshotBinding | project_root:str, ignore_paths:tuple[str,...], project_epoch:str, analysis_generation:int |
| SnapshotLimits | max_records:int=200000, max_anchors:int=200000, max_text_bytes:int=16777216 |
| FileRecord | relative_path:str, module_name:str, line_count:int, was_parsed:bool, content_hash:str\|None |
| AnchorRecord | file_path:str, start_line:int, start_column:int\|None, end_line:int, end_column:int\|None |
| SymbolRecord | id:str, name:str, qualified_name:str, kind:NodeKind, scope:str, file_path:str, source_anchor:AnchorRecord, parent_symbol:str\|None, definition:bool |
| CodeNodeRecord | id:str, kind:NodeKind, name:str, qualified_name:str, scope:str, language:str, source_anchors:tuple[AnchorRecord,...], origin:NodeOrigin, role:EntityRole, confidence:Confidence, file_path:str\|None |
| CodeEdgeRecord | original_index:int, source:str, target:str, type:EdgeType, confidence:Confidence, source_anchor:AnchorRecord\|None, possible_targets:tuple[str,...] |
| ComponentRecord | id:str, display_name:str, code_name:str\|None, role:str\|None, confidence:Confidence, evidence:tuple[str,...], child_component_ids:tuple[str,...], code_symbol_ids:tuple[str,...], source_anchors:tuple[AnchorRecord,...] |
| RelationRecord | original_index:int, source:str, target:str, type:EdgeType, confidence:Confidence, evidence:tuple[str,...], source_anchors:tuple[AnchorRecord,...] |

Scope沿用A的epoch ASCII opaque格式及generation `0..2**63-1`；record的original_index严格非负int。root为可信调用方提供的绝对路径str：Windows主机用PureWindowsPath，POSIX用PurePosixPath做词法检查，不resolve/stat/exists，也不声称验证canonical身份。ignore_paths为无重复的已规范化项目相对前缀tuple，无glob；不重扫或应用ignore规则。file/anchor path采用core分隔符规范化，拒绝绝对/drive/.. /NUL/空，规范化后文件不得重复。

FileRecord line_count>=0，成功>=1；hash为None或64位小写hex。不补算hash。was_parsed严格复制ParsedFile.was_parsed语义：显式parsed_success优先，否则判断tree是否存在；不能仅以tree=None判失败，不遍历或保留AST。合法合成成功记录可缺hash，但只能作为静态位置，不能成为B2成功缓存或可信citation。failed/null文件保留inventory，不能据此虚构source。

AnchorRecord本地验证1<=start<=end、列None或非负int、同一行endcol>=startcol。builder额外验证anchor指向inventory中的成功文件且end<=file.line_count；symbol anchor匹配file_path，node各anchor匹配其file_path。列真实行长度及UTF8字符边界须B4读文本验证，本批不能声称完成。

## 3. 构建合同

`build_snapshot(analysis: ProjectAnalysis, *, binding: SnapshotBinding, limits: SnapshotLimits=SnapshotLimits()) -> AnalysisSnapshot`。AnalysisSnapshot只能通过builder构造，只保留新记录及不可变tuple，不给模型完整原分析引用。构建期间可信调用方保证集合不并发修改，本批不增全局锁/监听器/缓存。

逐字段访问明确白名单，不能asdict、通用deepcopy或遍历原dataclass所有字段。数据来源分别为：analysis.files；symbol_index.entries.values()；flow.graph.nodes.values()/edges；semantic.graph.components/relations/roots。flow/semantic None时为空。不复制metadata、references、scope declarations、issues、resolution/model/training、源码、AST、视图或cache对象。已有evidence文本仅静态分析说明，不是运行证明。

文件按路径排序，symbol/node/component按ID排序；边/关系保留原顺序与重复项，每项original_index严格 `type(value) is int and value >= 0`，builder从原collection零起始序号赋值，不因查询过滤/截断重新编号。独立symbol/code/component命名空间各自唯一，共享跨命名空间ID合法；原字典key须等于record.id。

代码endpoints/possible_targets必须存在于代码节点集合；possible_targets仅允许uncertain CALLS，不作为确定终点。semantic roots/children/endpoints存在于组件集合；组件须有code IDs/anchors/children之一，关系须非空evidence和anchors，GRADIENT_FLOW不得Exact。parent_symbol与code_symbol_ids可在partial分析中无对应记录，仅验证ID格式，不创建替代节点或source。

External/Synthetic节点可查询；已有合法anchors只复制，不改变origin、不继承/制造source。只有External/Synthetic节点及本来允许空source_anchor的代码边可保留空tuple/None；origin=SOURCE的代码节点必须有非空合法anchors。CodeNodeRecord的name和language也必须非空，保留现有CodeNode约束，不给无源事实放宽权限。

SnapshotLimits三项strict非负int，零是实际零预算。max_records汇总六类记录，max_anchors计所有复制anchor出现次数；max_text_bytes按固定白名单字段顺序累积UTF8字节，包含binding/roots/重复ID引用/evidence/anchor路径。字符串逐字符检查编码并计长，超限立即停止，不先对巨大字符串整串encode；record/anchor副本构造前计数。此为逻辑预算，不是Python heap精确测量；超限整体失败，不发布截断快照。

2026-10-12独立质量审查发现空字符串tuple可绕过字节计账，architect批准最小修订：固定`MAX_SEQUENCE_ENTRIES=200_000`，不增加SnapshotLimits字段、不改变UTF8精确计账。builder累计所有白名单字符串tuple出现次数，包括ignore_paths、possible_targets、component/relation evidence、child_component_ids、code_symbol_ids、semantic roots；重复元素、空字符串、同tuple在不同字段出现均重复计数。严格tuple类型检查后，先按len整体预留，再遍历元素或构造副本；恰好200000通过、超限固定AgentError(budget_exceeded)。公共记录构造器对每个字符串tuple字段先检查同一固定单字段上限，再验证元素/构造set；与builder检查不重复扣累计。anchors仍独立受max_anchors约束。这是逻辑基数限额，不宣称精确heap限额或所有独立构造器耗时保证。

同轮修订：matches_scope在读取损坏RequestScope缺失slot时转换固定AgentError(invalid_input)并from None；即使epoch/generation已不匹配，也必须完整验证全部三字段，不能隐藏缺失request_id。

现有边/关系无ID，B3不生成core IR ID，EvidenceRecord.code_edge_id暂为None。B5未来权威映射可使用 `(epoch,generation,collection_kind,original_index)` 对应快照record及验证证据；不是模型可提供或改写的标识，本批不实施此映射。

## 4. 小范围公共查询

| API | 合同 |
| --- | --- |
| binding / semantic_roots | 不可变binding及tuple[str,...] |
| get_file(relative_path) / get_symbol(id) / get_code_node(id) / get_component(id) | 验证参数，合法未命中None |
| files / symbols / code_nodes / components (*,limit=100) | 排序结果前limit项，tuple |
| code_edges(*,node_id=None,limit=100) | 仅source/target等于指定ID的直接边；None为全记录前limit项 |
| relations(*,component_id=None,limit=100) | 仅source/target等于指定ID的直接关系；None为全记录前limit项 |
| matches_scope(scope:RequestScope) | 全scope类型/字段先验证；只比epoch/generation，合法不同request ID可复用；合法不匹配False |

limit strictint `0..1000`，零仍先验证其他filter，再返回空tuple。不存在的合法filter为空tuple，非法ID/path/type抛AgentError(invalid_input)。无多hop、搜索语言、分页、SourceReader或UI动作；B5接续。matches_scope仅绑定匹配，不证明current/取消/外传授权或磁盘fresh。

## Task 1：投影与隔离测试先行

- [ ] test_writer用unittest及typed synthetic tuples提供六类记录/roots准确投影、跨命名空间共享ID、source对象修改不影响快照、frozen/slots、metadata访问哨兵/AST不遍历、无I/O哨兵；独立测试从snapshot模块查API，缺API先明确FAIL不collection error。

```python
snapshot = build_snapshot(analysis, binding=binding)
self.assertEqual(snapshot.get_symbol("function-id").qualified_name, "app.main")
analysis.symbol_index.entries.clear()
self.assertEqual(snapshot.get_symbol("function-id").qualified_name, "app.main")
```

- [ ] 运行 `.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_snapshot.py -v`，观察真实RED，随后core_implementer实现白名单复制及不可变查询。
- [ ] cache无AST等价fixture（tree=None,parsed_success=True）仍能构建，和live facts结果相同；保留failed/null file且不补anchor/hash，External/Synthetic的空anchor、合法anchor均按来源复制。

## Task 2：结构/预算/查询风险等价类

- [ ] 补scope复用及不匹配、root/ignore/path/ID/tuple/enum/bool/hash非法、dictkey不匹配、各namespace重复、anchor bool/float/倒置/越界/unknown/failed文件/path不匹配；不要通过解析器生成每个synthetic事实。
- [ ] 悬空代码endpoints/possible_targets、非法CALLS候选、semantic roots/children/endpoints、缺provenance/evidence、Exact gradient；合法未解析code IDs仍保留。
- [ ] 查询排序/重复边/原ordinal/limit零与无效filter、未命中；三类预算零/边界/超限与UTF8中文重复计数。采用约14风险组及subTest，不增加新测试框架/复用可变全局fixture。

```python
scope = RequestScope(binding.project_epoch, binding.analysis_generation, "request-2")
self.assertTrue(snapshot.matches_scope(scope))
self.assertEqual(snapshot.code_edges(node_id="unknown-id", limit=0), ())
with self.assertRaises(AgentError):
    snapshot.code_edges(node_id="", limit=0)
```

- [ ] 验证非法输入只返回AgentError固定消息（invalid_input/budget_exceeded），无路径/源码/原字段/底层异常泄漏，from None；不增加deadline/cancel/registry生命周期。
- [ ] 回归百万空evidence、所有字符串tuple字段及跨字段共享/重复出现的200000边界、超限先于非法元素校验/副本分配；精确UTF8/record/anchor账本不变。每个RequestScope缺失slot及合法不匹配但缺字段均返回固定错误。

## Task 3：门禁与接续

- [ ] 独立规格审查通过后再独立quality review，修复不得跳过TDD/复审；只查看本批文件，避免重做B1/B2。
- [ ] 阶段内snapshot/foundations与persistence直接消费者定向回归，Ruff/mypy Windows/Linux；本子项目结束完整Python适用门禁，未改前端保留当前里程碑415项及build有效结果，不重复无关native包。
- [ ] 更新实际进度与测试增量；不称快照为授权/fresh/有效citation或完整Agent，不写用户源码，不装SDK、不调用API、不推送。
- [ ] 接续B4安全授权片段完整计划及architect复审，不重复请求已批准产品方向。

当前状态：B3详细合同经architect最终复审批准；已明确SOURCE节点anchors/name/language约束、零起始原ordinal和缓存was_parsed语义。尚未写B3测试或生产代码，下一步test_writer真实RED后core_implementer实施。
