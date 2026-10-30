# B2 Cache Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Process amendment (2026-10-15):** process is governed by the repository `AGENTS.md`. Do not load `subagent-driven-development` or `executing-plans`; do not run a per-task spec/quality review pair. Technical requirements below are unchanged.

**Goal:** 让分析缓存保留实际解析字节哈希，拒绝图事实与保存指纹错配，升级旧缓存而不丢用户状态。

**Architecture:** codec保存来源字段但不保存AST/源码；CacheStore在写入前和加载后验证完整文件清单及来源一致性。磁盘freshness继续由controller现有流程处理，不在load_analysis重新扫描或读取源码。

**Tech Stack:** 现有Python dataclass/JSON/cache/unittest；无新依赖、UI改动或网络调用。

---

## 前置与范围

2026-10-12 architect只读审查放行本合同；生产实现必须等B1规格和质量审查通过。B1的ParsedFile.content_hash是实际AST来源，而独立fingerprint不是它的替代品。本计划仅B2，不实施B3/B4/B5或侧栏。

文件：修改 `src/codeviz/persistence/{codec,store,fingerprint}.py`；测试 `tests/test_{analysis_version,persistence_codec,persistence_store,persistence_controller}.py`。保留现有未提交修改，不自动commit/push，不修改cache外层schema/视图/注释格式。

## 锁定合同

### Codec

parsed-file记录严格七key：`@`, `relative_path`, `module_name`, `line_count`, `issues`, `was_parsed`, `content_hash`。encode与decode两侧均要求hash为None或严格str、64位小写hex；缺key/额外key拒绝。恢复时 `tree=None`，保留was_parsed与hash，不保存AST、raw bytes或decoded source。独立codec可保留人工构造的null成功记录，严格来源要求在store边界执行。

### Store

新增 `_validate_analysis_sources(analysis: ProjectAnalysis, fingerprint: ProjectFingerprint) -> None`，写入前（encode/写文件前）及加载后（decode之后、返回之前）调用。

- 验证analysis与fingerprint所有路径为规范项目相对 `.py` 路径；使用SourceAnchor规范化后与原值比较，不能悄悄修正坏路径。拒绝重复，各自完整集合完全相同；analysis tuple顺序不要求一致。
- 任意非null解析hash必须等于对应fingerprint hash。
- `was_parsed=True` 必须有非null hash；失败文件允许匹配hash或None。null失败文件仍在清单，但不能为Agent提供可信源码证据。后续fingerprint成功不得自动补填解析hash。
- 空analysis与空fingerprint清单可接受。完整集合是现有discovery/ignore策略中的文件集合，不扩展为所有磁盘文件。
- save也验证手工构造fingerprint：用 `ProjectFingerprint.from_payload(fingerprint.to_payload())` 复核字段/hash/重复等，补规范路径与当前版本检查；校验失败不得覆盖已有cache。
- 新验证错误使用固定CacheError消息，例如 `analysis cache source inventory mismatch` / `analysis cache source hash mismatch`，不包含路径、hash值或底层异常字符串。

### Versions与加载顺序

AnalysisVersion=4、SemanticVersion=3、ParserVersion保持现有Python版本。外层 `_SCHEMA_VERSION=1` 不变。

加载顺序：envelope→fingerprint格式→version gate→decode→source consistency→return。v3即便携带坏analysis payload，也必须在decode前CacheVersionMismatch；不删、不重写旧cache/view-state/annotations。

controller无需改动：保存拒绝保留in-memory图、标Partial、旧cache不覆盖；invalid v4 load报告cache issue；v3 load为Outdated并保留布局/注释。内部一致而磁盘已变化的缓存仍可供旧图查看，再由controller标Outdated。load不能为验证一致性重新读取磁盘。

这不证明恶意改写cache的真实性，不证明全图深不可变；后者属于B3。

## Task 1：Codec与版本测试先行

- [x] test_writer补真实分析round-trip保留hash、无AST/源码marker、null合同、encode/decode非法hash与strict schema；更新当前版本断言为4/3，保留v2/semantic/parser旧版本拒绝，新增v3解码前拒绝且用户状态保留。

```python
analysis = analyze_project(root)
restored = decode_analysis(encode_analysis(analysis))
self.assertIsNone(restored.files[0].tree)
self.assertEqual(restored.files[0].content_hash, analysis.files[0].content_hash)
self.assertEqual(ANALYSIS_VERSION, "4")
self.assertEqual(SEMANTIC_VERSION, "3")
```

- [x] 定向运行codec/version unittest确认真实RED，再由core_implementer修改codec和常量；不删除或放宽旧版本测试。
- [x] encode与decode共用严格hash校验，serialize只追加来源hash，不通过AST/模块名猜hash，也不把fingerprint字段回填到ParsedFile。

## Task 2：一致性与中心回归

- [x] test_writer先补store：匹配/空集合接受；重复/缺失/多余/非法path拒绝；手工坏fingerprint拒绝；成功null拒绝，失败null或失败匹配hash接受；hash mismatch拒绝。save/load都覆盖，失败保留旧cache及state/notes原字节。
- [x] controller中心回归：分析期间短暂改变源码，再恢复原字节；before/after fingerprint相同，但实际parsed hash不同，拒绝保存缓存、保留结果并显示Partial/issue。

```python
old_cache = store.analysis_path(root).read_bytes()
with self.assertRaises(CacheError):
    store.save_analysis(root, changed_analysis, original_fingerprint)
self.assertEqual(store.analysis_path(root).read_bytes(), old_cache)
```

- [x] 真实RED后core_implementer仅修改允许的三个persistence生产文件，实现validator调用顺序及固定错误，现有controller不迁移、不在load读取源码。
- [x] 测试fixture使用实际解析hash或明确failed/null，standalone codec的合法合成值不冒称来源证明；旧fixture/断言不删除、不以补造来源修正。
- [x] 主代理独立复跑四文件41项通过；core另报告173项直接消费者回归、5环境跳过，后续主代理全量门禁独立核验，不把报告当最终证明。

## Task 3：审查与阶段记录

- [x] 独立规格审查通过后独立quality review通过，无Critical/Important/Minor遗留，源码序列化/集合/hash/旧状态保留合同逐项核验。
- [x] 主代理最后实跑：Python938项、0失败、58环境跳过、37.674秒；前端24文件415项全部通过、57.64秒，lint/typecheck/build通过并保留既有大chunk警告；Ruff及Windows/Linux mypy67源码文件通过。未做无关完整打包或重复前端小步全量。
- [x] 进度、CACHE_SPEC与KNOWN_LIMITATIONS同步analysis4/semantic3/schema1；无源码副本/API调用/Git推送。平台/native包未验证项明确保留。
- [x] [B3不可变快照计划](2026-10-12-code-agent-analysis-snapshot.md)建立并完成architect最终复审，无B3生产或测试先行偷跑。

当前状态：B2本机实现、完整适用门禁和两阶段独立审查完成。B3计划已最终批准，可接续TDD；B3/B4/B5/API/聊天UI未标完成。Linux native/Windows symlink与最终PC包仍为发布门禁，未自动commit/push。
