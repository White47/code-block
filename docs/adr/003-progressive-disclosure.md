# ADR-003：Progressive Disclosure

- Status：Accepted
- Date：2026-09-17

## Context

Full Graph 对首次用户和大项目都不可读；用户需要从项目概览逐层进入组件、内部流程和源码。

## Decision

首版提供四级能力：Project / Model Overview、Major Components、Internal Flow / Subcomponents、Source；允许直接跳级。所有视图由 View Builder 生成局部 Visible Graph，默认节点数控制在几十到约 100。

## Consequences

Graph Query、聚合、折叠、面包屑、返回和状态保存成为核心能力；不能把 Full Graph 直接交给渲染层。
