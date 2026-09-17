"""Conservative critical-data facts from Python assignment and name syntax."""

import ast

from codeviz.core import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    Confidence,
    DataNode,
    DataRole,
    EdgeType,
    NodeKind,
    SourceAnchor,
    stable_node_id,
)

from .python import AnalysisIssue, ParsedFile
from .symbols import SymbolIndex, SymbolIndexEntry, SymbolReference

_ROLES: dict[str, DataRole] = {
    "input": DataRole.INPUT, "inputs": DataRole.INPUT,
    "target": DataRole.TARGET, "targets": DataRole.TARGET,
    "label": DataRole.TARGET, "labels": DataRole.TARGET,
    "mask": DataRole.MASK,
    "feature": DataRole.FEATURE, "features": DataRole.FEATURE,
    "hidden_state": DataRole.HIDDEN_STATE,
    "prediction": DataRole.PREDICTION, "predictions": DataRole.PREDICTION,
    "pred": DataRole.PREDICTION, "logits": DataRole.PREDICTION,
    "loss": DataRole.LOSS,
    "parameter": DataRole.PARAMETER, "parameters": DataRole.PARAMETER,
    "gradient": DataRole.GRADIENT, "gradients": DataRole.GRADIENT,
}


class _DataVisitor(ast.NodeVisitor):
    def __init__(self, parsed: ParsedFile, index: SymbolIndex, graph: CodeGraph,
                 data_by_symbol: dict[str, str]) -> None:
        self.parsed = parsed
        self.index = index
        self.graph = graph
        self.data_by_symbol = data_by_symbol
        self.origins: dict[str, set[str]] = {}
        self.scope_id = next(entry.id for entry in index.entries.values()
                             if entry.kind == NodeKind.MODULE and entry.file_path == parsed.relative_path)
        self.children: dict[tuple[str, str, NodeKind, int], SymbolIndexEntry] = {}
        for entry in index.entries.values():
            if entry.parent_symbol is not None:
                self.children[(entry.parent_symbol, entry.name, entry.kind,
                               entry.source_anchor.start_line)] = entry
        self.steps = {
            (node.source_anchors[0].start_line, node.source_anchors[0].start_column,
             node.metadata["statement_type"]): node.id
            for node in graph.nodes.values()
            if node.kind in (NodeKind.FLOW_STEP, NodeKind.CONTROL_BLOCK)
            and node.file_path == parsed.relative_path
        }
        self.refs = {
            (ref.scope_symbol_id, ref.source_anchor.start_line,
             ref.source_anchor.start_column, ref.name, ref.qualifier): ref
            for ref in index.references
            if ref.source_anchor.file_path == parsed.relative_path
        }

    def _visit_definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
                          kind: NodeKind) -> None:
        entry = self.children.get((self.scope_id, node.name, kind, node.lineno))
        if entry is None:
            return
        previous = self.scope_id
        previous_origins = self.origins
        self.scope_id = entry.id
        self.origins = {}
        for statement in node.body:
            self.visit(statement)
        self.scope_id = previous
        self.origins = previous_origins

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        parent = self.index.entries[self.scope_id]
        self._visit_definition(node, NodeKind.METHOD if parent.kind == NodeKind.CLASS
                               else NodeKind.FUNCTION)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        parent = self.index.entries[self.scope_id]
        self._visit_definition(node, NodeKind.METHOD if parent.kind == NodeKind.CLASS
                               else NodeKind.FUNCTION)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_definition(node, NodeKind.CLASS)

    def _reads(self, expression: ast.AST | None) -> set[str]:
        if expression is None:
            return set()
        found: set[str] = set()
        for node in ast.walk(expression):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                name, qualifier = node.id, None
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                name, qualifier = node.attr, ast.unparse(node.value)
            else:
                continue
            reference: SymbolReference | None = self.refs.get(
                (self.scope_id, node.lineno, node.col_offset, name, qualifier)
            )
            if reference is not None and reference.resolved_definition_id is not None:
                target_id = reference.resolved_definition_id
                if target_id in self.data_by_symbol:
                    found.add(self.data_by_symbol[target_id])
                else:
                    found.update(self.origins.get(target_id, ()))
        return found

    def _bindings(self, target: ast.AST) -> set[str]:
        found: set[str] = set()
        for node in ast.walk(target):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                name, kind = node.id, None
            elif (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
                  and isinstance(node.value, ast.Name) and node.value.id == "self"):
                name, kind = node.attr, NodeKind.ATTRIBUTE
            else:
                continue
            exact = [entry for entry in self.index.find_definitions(name)
                     if entry.file_path == self.parsed.relative_path
                     and entry.source_anchor.start_line == node.lineno
                     and entry.source_anchor.start_column == node.col_offset
                     and (kind is None or entry.kind == kind)]
            if not exact and kind is None:
                exact = [entry for entry in self.index.find_definitions(name)
                         if entry.parent_symbol == self.scope_id]
            for entry in exact:
                found.add(entry.id)
        return found

    def _writes(self, target: ast.AST) -> set[str]:
        return {self.data_by_symbol[binding] for binding in self._bindings(target)
                if binding in self.data_by_symbol}

    def _remember(self, bindings: set[str], reads: set[str]) -> None:
        for binding in bindings:
            if binding not in self.data_by_symbol:
                self.origins[binding] = set(reads)

    def _record(self, statement: ast.stmt, reads: set[str], writes: set[str] | None = None) -> None:
        step = self.steps.get((statement.lineno, statement.col_offset, type(statement).__name__))
        if step is None:
            return
        anchor = self.graph.nodes[step].source_anchors[0]
        for data_id in sorted(reads):
            self.graph.add_edge(CodeEdge(step, data_id, EdgeType.READS, source_anchor=anchor))
        for data_id in sorted(writes or ()):
            self.graph.add_edge(CodeEdge(step, data_id, EdgeType.WRITES, source_anchor=anchor))
            for input_id in sorted(reads - {data_id}):
                self.graph.add_edge(CodeEdge(
                    input_id, data_id, EdgeType.DATA_FLOW, Confidence.PROBABLE,
                    source_anchor=anchor, metadata={"basis": "static assignment"},
                ))

    def visit_Assign(self, node: ast.Assign) -> None:
        bindings = set().union(*(self._bindings(target) for target in node.targets))
        reads = self._reads(node.value)
        writes = {self.data_by_symbol[binding] for binding in bindings
                  if binding in self.data_by_symbol}
        self._record(node, reads, writes)
        self._remember(bindings, reads)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        bindings = self._bindings(node.target)
        reads = self._reads(node.value)
        self._record(node, reads, self._writes(node.target))
        self._remember(bindings, reads)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        bindings = self._bindings(node.target)
        writes = self._writes(node.target)
        previous = set().union(*(self.origins.get(binding, set()) for binding in bindings))
        reads = self._reads(node.value) | writes | previous
        self._record(node, reads, writes)
        self._remember(bindings, reads)

    def visit_Return(self, node: ast.Return) -> None:
        self._record(node, self._reads(node.value))

    def visit_Expr(self, node: ast.Expr) -> None:
        self._record(node, self._reads(node.value))

    def visit_If(self, node: ast.If) -> None:
        self._record(node, self._reads(node.test))
        before = {key: set(value) for key, value in self.origins.items()}
        for statement in node.body:
            self.visit(statement)
        true_origins = self.origins
        self.origins = {key: set(value) for key, value in before.items()}
        for statement in node.orelse:
            self.visit(statement)
        false_origins = self.origins
        self.origins = {key: true_origins[key] & false_origins[key]
                        for key in true_origins.keys() & false_origins.keys()}

    def visit_While(self, node: ast.While) -> None:
        self._record(node, self._reads(node.test))
        before = {key: set(value) for key, value in self.origins.items()}
        for statement in (*node.body, *node.orelse):
            self.visit(statement)
        self.origins = before

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        self._record(node, self._reads(node.iter), self._writes(node.target))
        before = {key: set(value) for key, value in self.origins.items()}
        for statement in (*node.body, *node.orelse):
            self.visit(statement)
        self.origins = before

    visit_AsyncFor = visit_For


def add_critical_data(files: tuple[ParsedFile, ...], index: SymbolIndex,
                      graph: CodeGraph, issues: list[AnalysisIssue]) -> tuple[DataNode, ...]:
    data_nodes: list[DataNode] = []
    by_symbol: dict[str, str] = {}
    for entry in index.entries.values():
        if (entry.kind not in (NodeKind.PARAMETER, NodeKind.VARIABLE, NodeKind.ATTRIBUTE)
                or "imported_from" in entry.metadata):
            continue
        role = _ROLES.get(entry.name.casefold())
        if role is None:
            continue
        node_id = stable_node_id(entry.file_path, entry.qualified_name, NodeKind.DATA,
                                 entry.scope, occurrence_key=f"symbol:{entry.id}")
        data_nodes.append(DataNode(node_id, entry.name, role, entry.source_anchor,
                                   confidence=Confidence.PROBABLE))
        by_symbol[entry.id] = node_id
        graph.add_node(CodeNode(
            id=node_id, kind=NodeKind.DATA, name=entry.name,
            qualified_name=entry.qualified_name, scope=entry.scope,
            language="python", source_anchors=(entry.source_anchor,),
            confidence=Confidence.PROBABLE,
            metadata={"data_role": role.value, "basis": "name heuristic", "symbol_id": entry.id},
        ))
        graph.add_edge(CodeEdge(entry.id, node_id, EdgeType.CONTAINS,
                                source_anchor=entry.source_anchor))
    for parsed in files:
        if parsed.tree is not None and any(entry.kind == NodeKind.MODULE
                                           and entry.file_path == parsed.relative_path
                                           for entry in index.entries.values()):
            try:
                _DataVisitor(parsed, index, graph, by_symbol).visit(parsed.tree)
            except Exception as error:
                issues.append(AnalysisIssue(
                    "DATA_FLOW_ERROR", f"{type(error).__name__}: {error}",
                    parsed.relative_path, SourceAnchor(parsed.relative_path, 1),
                ))
    return tuple(data_nodes)
