# Phase 10 收敛报告 — 本地缓存、过期反馈与状态恢复

日期：2026-09-18。目标工作区：`E:\Codex\代码块`。阶段分支：`phase10-cache-persistence`。

## 实现摘要与改动

| 范围 | 文件 | 结果 |
| --- | --- | --- |
| 缓存契约 | `src/codeviz/persistence/{codec,fingerprint,store}.py`、`analysis/python.py`、`source_handles.py` | 标准库 JSON 白名单快照、内容 SHA-256 指纹与分析 / parser / rules 版本；不存 AST 或源码；原子写入与大小限制；复用句柄路径核验 |
| 会话与用户数据 | `ui/controller.py`、`views/state.py`、`ui/presenter.py` | 有效缓存自动加载；变化标 Outdated、保留旧图并允许显式重分析；失败保留旧缓存；View State / Annotations / Recent Projects 分离 |
| 桌面界面 | `ui/tkapp.py` | 总览、图、Inspector、源码显示过期提示；Explorer 最近项目；标签 / 当前图 / 选中节点 / 手动布局重开恢复；关闭时取消定时回调 |
| 回归验证 | `tests/test_persistence_{codec,store,controller}.py`、`tests/test_ui_tkapp.py` | 往返等价、部分解析结果、改 / 增 / 删 / Ignore Paths / 版本、损坏缓存、原子写失败、独立标注、真实 Tk 恢复与过期提示 |
| 规范 | `README.md`、`docs/{CACHE_SPEC,SECURITY_AND_PRIVACY,IMPLEMENTATION_PLAN,KNOWN_LIMITATIONS,QUALITY_GATES,PHASE10_DESIGN,PHASE10_PLAN}.md`、`docs/adr/007-json-local-cache.md`、本文 | 记录最小技术选择、验收、边界与限制 |

## 验证

- 本地门禁：`python -m ruff check src tests`、`python -m mypy`、`python -m mypy --platform linux`、`python -B -m unittest discover -s tests -q`、`python -m pip wheel . --no-deps -w dist`、`git diff --check` 均通过；共 198 项测试，2 项既有跳过。Windows Tk 交互测试实际运行通过。
- 本阶段新增失败：曾发现原子写中途失败遗留临时文件、`null` JSON 被误当作缓存不存在、最近项目改变旧 UI 测试的首项假设、恢复布局的可空路径类型问题；已分别用回归测试和局部修复解决。
- 既有跳过：当前 Windows 环境的 2 个符号链接权限测试仍跳过；不把跳过计入新失败。真实 Ubuntu / Python 3.11、3.12 的 GitHub Actions 须在合入 `main` 后另行确认。

## 已知后置

没有文件监视或增量解析；打开项目时仍需全量比对源码内容指纹。Annotations 只有独立持久化 API，尚无桌面编辑器；Ignore Paths 暂通过 Controller API 配置，桌面 Settings 表单后置。缩放、平移和关系过滤字段已预留，但当前 Canvas 无对应控件。缓存不可核验或无安全文件句柄路径的平台保守降级，不伪称 Ready。更大的项目与三条黄金路径整体可用性由 Phase 11 验收。
