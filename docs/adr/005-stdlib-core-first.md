# ADR-005：从空目录新建时先使用 Python 标准库

- Status：Accepted
- Date：2026-09-17

## Context

用户确认 `<workspace>` 就是新项目目录，建立文档前没有现有源码或技术栈。Phase 1 只需要领域对象、稳定 ID、图容器和可验证的序列化；不需要图渲染或桌面壳。

## Decision

Phase 1 以 Python 3.11+ 的 `dataclasses`、`StrEnum`、`hashlib`、`json` 和 `unittest` 建立独立的 `src/codeviz/core` 包。使用已有的 setuptools 作为打包后端；当前不新增运行时依赖。图 UI、状态管理和缓存格式到对应阶段再选择。

## Consequences

核心事实模型可独立测试，不依赖 PyTorch 或 UI。若后续引入其他语言 Adapter，可继续复用语言中立的图模型。标准库选择不预先决定桌面壳或图形库。
