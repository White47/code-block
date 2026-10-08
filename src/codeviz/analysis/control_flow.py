"""Structural, intentionally partial Python control-flow edges."""

import ast
from collections import defaultdict
from dataclasses import dataclass, replace
from enum import StrEnum

from codeviz.core import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    EdgeType,
    NodeKind,
    SourceAnchor,
    stable_node_id,
)

from .python import AnalysisIssue, ParsedFile
from .symbols import SymbolIndex, SymbolIndexEntry


class _ExitKind(StrEnum):
    NORMAL = "normal"
    RETURN = "return"
    RAISE = "raise"
    BREAK = "break"
    CONTINUE = "continue"


@dataclass(frozen=True, slots=True)
class _Exit:
    node_id: str
    edge_type: EdgeType = EdgeType.CONTROL_FLOW
    kind: _ExitKind = _ExitKind.NORMAL


_MAX_CFG_NODES = 10_000
_MAX_CONTINUATION_NODES = 2_000


class _ControlBudgetExceeded(RuntimeError):
    def __init__(self, anchor: SourceAnchor) -> None:
        self.anchor = anchor
        super().__init__("CFG node/continuation budget reached; remaining paths are omitted")


class _ControlBuilder:
    def __init__(self, parsed: ParsedFile, index: SymbolIndex, graph: CodeGraph) -> None:
        self.parsed = parsed
        self.index = index
        self.graph = graph
        self.ordinal: dict[str, int] = defaultdict(int)
        self.children: dict[tuple[str, str, NodeKind, int], SymbolIndexEntry] = {}
        for entry in index.entries.values():
            if entry.parent_symbol is not None:
                self.children[(entry.parent_symbol, entry.name, entry.kind,
                               entry.source_anchor.start_line)] = entry
        self.continuation = ""
        self.ordinals: dict[tuple[str, ast.AST], int] = {}
        self.primary: dict[tuple[str, ast.AST], str] = {}
        self.created: dict[tuple[str, ast.AST, str], str] = {}
        self.scopes_seen: set[str] = set()
        self.node_count = 0
        self.continuation_count = 0

    def _node(self, owner_id: str, statement: ast.AST, kind: NodeKind) -> str:
        key = (owner_id, statement, self.continuation)
        if key in self.created:
            return self.created[key]
        owner = self.index.entries[owner_id]
        source_key = (owner_id, statement)
        clone = source_key in self.primary
        anchor = SourceAnchor(
            self.parsed.relative_path, getattr(statement, "lineno", 1),
            start_column=getattr(statement, "col_offset", None),
            end_line=getattr(statement, "end_lineno", None),
            end_column=getattr(statement, "end_col_offset", None),
        )
        if (self.node_count >= _MAX_CFG_NODES or
                (clone and self.continuation_count >= _MAX_CONTINUATION_NODES)):
            raise _ControlBudgetExceeded(anchor)
        if source_key not in self.ordinals:
            self.ordinals[source_key] = self.ordinal[owner_id]
            self.ordinal[owner_id] += 1
        ordinal = self.ordinals[source_key]
        name = type(statement).__name__
        occurrence = f"scope:{owner_id}:step:{ordinal}"
        if clone:
            occurrence += f":continuation:{self.continuation}"
        node_id = stable_node_id(
            self.parsed.relative_path, f"{owner.qualified_name}.{name}", kind,
            owner.qualified_name, occurrence_key=occurrence,
        )
        metadata: dict[str, object] = {"statement_type": name}
        if isinstance(statement, (ast.If, ast.While)):
            metadata["condition"] = ast.unparse(statement.test)
        elif isinstance(statement, (ast.For, ast.AsyncFor)):
            metadata["condition"] = f"{ast.unparse(statement.target)} in {ast.unparse(statement.iter)}"
        elif isinstance(statement, ast.Match):
            metadata["condition"] = ast.unparse(statement.subject)
        elif isinstance(statement, (ast.Try, ast.TryStar)):
            metadata["partial"] = "implicit exception matching and propagation are approximated"
        if clone:
            metadata["canonical_step_id"] = self.primary[source_key]
            metadata["continuation_context"] = self.continuation
            self.continuation_count += 1
        else:
            self.primary[source_key] = node_id
        self.graph.add_node(CodeNode(
            id=node_id, kind=kind, name=name,
            qualified_name=f"{owner.qualified_name}.{name}", scope=owner.qualified_name,
            language="python", source_anchors=(anchor,), metadata=metadata,
        ))
        self.graph.add_edge(CodeEdge(owner_id, node_id, EdgeType.CONTAINS,
                                     source_anchor=anchor))
        self.created[key] = node_id
        self.node_count += 1
        return node_id

    def finish(self) -> None:
        # Existing call/data/training consumers index a statement by its source
        # location. Keep its original representative last, rather than a clone.
        if self.continuation_count:
            for node_id in self.primary.values():
                self.graph.nodes[node_id] = self.graph.nodes.pop(node_id)

    def _connect(self, exits: list[_Exit], target: str,
                 edge_type: EdgeType | None = None) -> None:
        anchor = self.graph.nodes[target].source_anchors[0]
        for exit_point in exits:
            self.graph.add_edge(CodeEdge(exit_point.node_id, target,
                                         edge_type or exit_point.edge_type,
                                         source_anchor=anchor))

    def _definition(self, statement: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
                    owner_id: str) -> None:
        parent_kind = self.index.entries[owner_id].kind
        kind = (NodeKind.CLASS if isinstance(statement, ast.ClassDef) else
                NodeKind.METHOD if parent_kind == NodeKind.CLASS else NodeKind.FUNCTION)
        entry = self.children.get((owner_id, statement.name, kind, statement.lineno))
        if entry is not None and entry.id not in self.scopes_seen:
            self.scopes_seen.add(entry.id)
            previous = self.continuation
            self.continuation = ""
            try:
                self.sequence(statement.body, entry.id, [_Exit(entry.id)])
            finally:
                self.continuation = previous

    def sequence(self, statements: list[ast.stmt], owner_id: str,
                 incoming: list[_Exit]) -> list[_Exit]:
        normal = [item for item in incoming if item.kind == _ExitKind.NORMAL]
        abrupt = [item for item in incoming if item.kind != _ExitKind.NORMAL]
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self._definition(statement, owner_id)
                continue
            # Retain source nodes for unreachable statements, but never revive
            # their exits as a reachable continuation of an earlier return.
            result = self._statement(statement, owner_id, normal)
            if not normal:
                continue
            normal = [item for item in result if item.kind == _ExitKind.NORMAL]
            abrupt.extend(item for item in result if item.kind != _ExitKind.NORMAL)
        return normal + abrupt

    def _finally(self, statements: list[ast.stmt], owner_id: str,
                 pending: list[_Exit], header: str) -> list[_Exit]:
        restored: list[_Exit] = []
        groups = [(kind, [item for item in pending if item.kind == kind])
                  for kind in _ExitKind]
        groups = [(kind, exits) for kind, exits in groups if exits]
        if not groups:
            self.sequence(statements, owner_id, [])
            return []
        previous = self.continuation
        try:
            for number, (kind, exits) in enumerate(groups):
                self.continuation = (previous if number == 0 else
                                     f"{previous}/finally:{header}:{kind.value}")
                cleanup = self.sequence(statements, owner_id,
                                        [replace(item, kind=_ExitKind.NORMAL) for item in exits])
                restored.extend(replace(item, kind=kind) if item.kind == _ExitKind.NORMAL else item
                                for item in cleanup)
        finally:
            self.continuation = previous
        return restored

    @staticmethod
    def _irrefutable(pattern: ast.pattern) -> bool:
        if isinstance(pattern, ast.MatchAs):
            return pattern.pattern is None or _ControlBuilder._irrefutable(pattern.pattern)
        if isinstance(pattern, ast.MatchOr):
            return any(_ControlBuilder._irrefutable(child) for child in pattern.patterns)
        return False

    def _match(self, statement: ast.Match, owner_id: str, header: str) -> list[_Exit]:
        unmatched = [_Exit(header)]
        completed: list[_Exit] = []
        for case in statement.cases:
            condition = self._node(owner_id, case.pattern, NodeKind.CONTROL_BLOCK)
            self.graph.nodes[condition].metadata["condition"] = (
                f"{ast.unparse(statement.subject)} matches {ast.unparse(case.pattern)}"
                + (f" if {ast.unparse(case.guard)}" if case.guard is not None else "")
            )
            self._connect(unmatched, condition)
            body = self.sequence(case.body, owner_id,
                                 [_Exit(condition, EdgeType.BRANCH_TRUE)] if unmatched else [])
            completed.extend(body)
            unmatched = ([_Exit(condition, EdgeType.BRANCH_FALSE)]
                         if unmatched and (case.guard is not None or
                                           not self._irrefutable(case.pattern)) else [])
        return completed + unmatched

    def _statement(self, statement: ast.stmt, owner_id: str,
                   incoming: list[_Exit]) -> list[_Exit]:
        control = isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While,
                                          ast.Try, ast.TryStar, ast.Match))
        node_id = self._node(owner_id, statement,
                             NodeKind.CONTROL_BLOCK if control else NodeKind.FLOW_STEP)
        self._connect(incoming, node_id)
        if isinstance(statement, ast.If):
            return (self.sequence(statement.body, owner_id,
                                  [_Exit(node_id, EdgeType.BRANCH_TRUE)])
                    + (self.sequence(statement.orelse, owner_id,
                                     [_Exit(node_id, EdgeType.BRANCH_FALSE)])
                       if statement.orelse else [_Exit(node_id, EdgeType.BRANCH_FALSE)]))
        if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            body = self.sequence(statement.body, owner_id,
                                 [_Exit(node_id, EdgeType.BRANCH_TRUE)])
            self._connect([item for item in body if item.kind in
                           (_ExitKind.NORMAL, _ExitKind.CONTINUE)], node_id, EdgeType.LOOP_BACK)
            # The false/exhaustion edge alone enters else; break bypasses it.
            normal = self.sequence(statement.orelse, owner_id,
                                   [_Exit(node_id, EdgeType.BRANCH_FALSE)])
            return (normal + [replace(item, kind=_ExitKind.NORMAL) for item in body
                              if item.kind == _ExitKind.BREAK]
                    + [item for item in body if item.kind in (_ExitKind.RETURN, _ExitKind.RAISE)])
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            return self.sequence(statement.body, owner_id, [_Exit(node_id)])
        if isinstance(statement, (ast.Try, ast.TryStar)):
            body = self.sequence(statement.body, owner_id,
                                 [_Exit(node_id, EdgeType.BRANCH_TRUE)])
            pending = self.sequence(statement.orelse, owner_id,
                                    [item for item in body if item.kind == _ExitKind.NORMAL])
            pending.extend(item for item in body if item.kind != _ExitKind.NORMAL)
            raised = [replace(item, kind=_ExitKind.NORMAL) for item in body
                      if item.kind == _ExitKind.RAISE]
            for handler in statement.handlers:
                # Keep the unmatched raise alternative. Handler selection and
                # implicit exception sources are conservative possible paths.
                pending.extend(self.sequence(handler.body, owner_id,
                                             [_Exit(node_id, EdgeType.BRANCH_FALSE), *raised]))
            return (self._finally(statement.finalbody, owner_id, pending, node_id)
                    if statement.finalbody else pending)
        if isinstance(statement, ast.Match):
            return self._match(statement, owner_id, node_id)
        kind = (_ExitKind.RETURN if isinstance(statement, ast.Return) else
                _ExitKind.RAISE if isinstance(statement, ast.Raise) else
                _ExitKind.BREAK if isinstance(statement, ast.Break) else
                _ExitKind.CONTINUE if isinstance(statement, ast.Continue) else
                _ExitKind.NORMAL)
        return [_Exit(node_id, kind=kind)]


def add_control_flow(files: tuple[ParsedFile, ...], index: SymbolIndex,
                     graph: CodeGraph, issues: list[AnalysisIssue]) -> None:
    """Add basic per-scope sequence, branch, and loop facts to the graph."""
    for parsed in files:
        if parsed.tree is None:
            continue
        for node in ast.walk(parsed.tree):
            reason = None
            if isinstance(node, (ast.Try, ast.TryStar)):
                reason = "try CFG implicit exception matching and propagation are approximated"
            elif isinstance(node, ast.Match):
                reason = "match CFG pattern/guard runtime matching and side effects are approximated"
            elif isinstance(node, ast.With):
                reason = "with CFG context-manager exception suppression is not modeled"
            elif isinstance(node, (ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith, ast.Await)):
                reason = "async CFG suspension, scheduling and cancellation are not modeled"
            elif isinstance(node, (ast.Yield, ast.YieldFrom, ast.GeneratorExp)):
                reason = "generator CFG suspension and resumption are not modeled"
            if reason is not None:
                issues.append(AnalysisIssue(
                    "CFG_PARTIAL", reason, parsed.relative_path,
                    SourceAnchor(parsed.relative_path, getattr(node, "lineno", 1),
                                 start_column=getattr(node, "col_offset", None),
                                 end_line=getattr(node, "end_lineno", None),
                                 end_column=getattr(node, "end_col_offset", None)),
                ))
        module = next((entry for entry in index.entries.values()
                       if entry.kind == NodeKind.MODULE and entry.file_path == parsed.relative_path), None)
        if module is not None:
            builder = _ControlBuilder(parsed, index, graph)
            try:
                builder.sequence(parsed.tree.body, module.id, [_Exit(module.id)])
            except _ControlBudgetExceeded as error:
                # The exception stops this file's traversal, not just the scope
                # that exhausted the budget. Later definitions still have graph
                # roots/callpoints, but their CFG may be entirely omitted.
                issues.append(AnalysisIssue(
                    "CFG_PARTIAL",
                    f"file-level {error}; remaining paths and scopes may be omitted "
                    f"(budget reached at line {error.anchor.start_line})",
                    parsed.relative_path,
                    SourceAnchor(parsed.relative_path, 1, end_line=parsed.line_count),
                ))
            except Exception as error:
                issues.append(AnalysisIssue(
                    "CONTROL_FLOW_ERROR", f"{type(error).__name__}: {error}",
                    parsed.relative_path, SourceAnchor(parsed.relative_path, 1),
                ))
            finally:
                builder.finish()
