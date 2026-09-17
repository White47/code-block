"""Build conservative call and flow facts without executing project code."""

import ast
from collections import defaultdict
from dataclasses import dataclass

from codeviz.core import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    Confidence,
    DataNode,
    EdgeType,
    EntityRole,
    NodeKind,
    NodeOrigin,
    SourceAnchor,
    stable_node_id,
)

from .call_cycles import find_call_cycles
from .control_flow import add_control_flow
from .data_flow import add_critical_data
from .graph_build import build_symbol_graph
from .python import AnalysisIssue, ParsedFile
from .resolution import ResolutionResult
from .symbols import SymbolIndexEntry, SymbolReference


@dataclass(frozen=True, slots=True)
class FlowAnalysis:
    graph: CodeGraph
    data_nodes: tuple[DataNode, ...] = ()
    call_cycles: tuple[tuple[str, ...], ...] = ()
    issues: tuple[AnalysisIssue, ...] = ()


class _FlowVisitor(ast.NodeVisitor):
    def __init__(self, parsed: ParsedFile, resolution: ResolutionResult, graph: CodeGraph,
                 issues: list[AnalysisIssue]) -> None:
        self.parsed = parsed
        self.index = resolution.symbol_index
        self.graph = graph
        self.issues = issues
        self.step_id: str | None = None
        self.scope_id = next(entry.id for entry in self.index.entries.values()
                             if entry.kind == NodeKind.MODULE and entry.file_path == parsed.relative_path)
        self.children: dict[tuple[str, str, NodeKind, int], SymbolIndexEntry] = {}
        for entry in self.index.entries.values():
            if entry.parent_symbol is not None:
                self.children[(entry.parent_symbol, entry.name, entry.kind,
                               entry.source_anchor.start_line)] = entry
        self.calls_seen: dict[str, int] = defaultdict(int)
        self.references: dict[tuple[str, int, int | None, str, str | None], SymbolReference] = {}
        for reference in self.index.references:
            if reference.source_anchor.file_path == parsed.relative_path and reference.scope_symbol_id:
                self.references[(reference.scope_symbol_id, reference.source_anchor.start_line,
                                 reference.source_anchor.start_column, reference.name,
                                 reference.qualifier)] = reference
        self.steps = {
            (node.source_anchors[0].start_line, node.source_anchors[0].start_column,
             node.metadata["statement_type"]): node.id
            for node in graph.nodes.values()
            if node.kind in (NodeKind.FLOW_STEP, NodeKind.CONTROL_BLOCK)
            and node.file_path == parsed.relative_path
        }

    def visit(self, node: ast.AST) -> None:
        previous_step = self.step_id
        if isinstance(node, ast.stmt):
            self.step_id = self.steps.get((node.lineno, node.col_offset,
                                           type(node).__name__), previous_step)
        try:
            super().visit(node)
        finally:
            self.step_id = previous_step

    def _visit_definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
                          kind: NodeKind) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in (*node.args.defaults,
                            *(item for item in node.args.kw_defaults if item is not None)):
                self.visit(default)
            for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                if argument.annotation is not None:
                    self.visit(argument.annotation)
            if node.returns is not None:
                self.visit(node.returns)
        else:
            for base in node.bases:
                self.visit(base)
            for keyword in node.keywords:
                self.visit(keyword.value)
        entry = self.children.get((self.scope_id, node.name, kind, node.lineno))
        if entry is None:
            return
        previous = self.scope_id
        previous_step = self.step_id
        self.scope_id = entry.id
        self.step_id = None
        for statement in node.body:
            self.visit(statement)
        self.scope_id = previous
        self.step_id = previous_step

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        parent = self.index.entries[self.scope_id]
        kind = NodeKind.METHOD if parent.kind == NodeKind.CLASS else NodeKind.FUNCTION
        self._visit_definition(node, kind)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        parent = self.index.entries[self.scope_id]
        kind = NodeKind.METHOD if parent.kind == NodeKind.CLASS else NodeKind.FUNCTION
        self._visit_definition(node, kind)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_definition(node, NodeKind.CLASS)

    def _dynamic_call(self, call_id: str, name: str, anchor: SourceAnchor,
                      binding_id: str | None = None,
                      possible_targets: tuple[str, ...] = (),
                      issue_code: str = "DYNAMIC_CALL_UNRESOLVED") -> None:
        owner = self.index.entries[self.scope_id]
        qualified_name = f"{owner.qualified_name}.{name}"
        unresolved_id = stable_node_id(
            self.parsed.relative_path, qualified_name, NodeKind.UNRESOLVED_SYMBOL,
            owner.qualified_name, occurrence_key=f"callsite:{call_id}",
        )
        reason = ("receiver may dispatch to an override" if possible_targets else
                  "callable value has no statically proven function target")
        self.graph.add_node(CodeNode(
            id=unresolved_id, kind=NodeKind.UNRESOLVED_SYMBOL,
            name=name, qualified_name=qualified_name, scope=owner.qualified_name,
            language="python", source_anchors=(anchor,),
            origin=NodeOrigin.SYNTHETIC, confidence=Confidence.UNRESOLVED,
            metadata={"reason": reason, "binding_id": binding_id},
        ))
        self.graph.add_edge(CodeEdge(
            call_id, unresolved_id, EdgeType.CALLS,
            Confidence.PROBABLE if possible_targets else Confidence.UNRESOLVED,
            source_anchor=anchor, possible_targets=possible_targets,
        ))
        self.issues.append(AnalysisIssue(
            issue_code, reason, self.parsed.relative_path, anchor,
        ))

    def visit_Call(self, node: ast.Call) -> None:
        expression = node.func
        name = expression.id if isinstance(expression, ast.Name) else (
            expression.attr if isinstance(expression, ast.Attribute) else ast.unparse(expression)
        )
        qualifier = ast.unparse(expression.value) if isinstance(expression, ast.Attribute) else None
        occurrence = self.calls_seen[self.scope_id]
        self.calls_seen[self.scope_id] += 1
        owner = self.index.entries[self.scope_id]
        anchor = SourceAnchor(self.parsed.relative_path, node.lineno,
                              start_column=node.col_offset, end_line=node.end_lineno,
                              end_column=node.end_col_offset)
        node_id = stable_node_id(self.parsed.relative_path, f"{owner.qualified_name}.{name}",
                                 NodeKind.CALL_SITE, owner.qualified_name,
                                 role=EntityRole.INVOCATION,
                                 occurrence_key=f"scope:{self.scope_id}:call:{occurrence}")
        self.graph.add_node(CodeNode(
            id=node_id, kind=NodeKind.CALL_SITE, name=name,
            qualified_name=f"{owner.qualified_name}.{name}", scope=owner.qualified_name,
            language="python", role=EntityRole.INVOCATION, source_anchors=(anchor,),
            metadata={"scope_symbol_id": self.scope_id},
        ))
        self.graph.add_edge(CodeEdge(self.step_id or self.scope_id, node_id, EdgeType.CONTAINS,
                                     source_anchor=anchor))
        reference = self.references.get((self.scope_id, expression.lineno,
                                         expression.col_offset, name, qualifier))
        if reference is not None:
            if reference.resolved_definition_id is not None:
                target = self.graph.nodes[reference.resolved_definition_id]
                if target.kind == NodeKind.METHOD and qualifier in ("self", "cls"):
                    self._dynamic_call(node_id, name, anchor, target.id,
                                       (target.id,), "DYNAMIC_DISPATCH_UNRESOLVED")
                elif target.kind in (NodeKind.FUNCTION, NodeKind.METHOD):
                    self.graph.add_edge(CodeEdge(node_id, target.id, EdgeType.CALLS,
                                                 source_anchor=anchor))
                elif target.kind == NodeKind.CLASS:
                    self.graph.add_edge(CodeEdge(node_id, target.id, EdgeType.CALLS,
                                                 Confidence.PROBABLE, source_anchor=anchor))
                else:
                    self._dynamic_call(node_id, name, anchor, target.id)
            elif reference.possible_target_ids:
                callable_ids = tuple(target_id for target_id in reference.possible_target_ids
                                     if self.graph.nodes[target_id].kind in
                                     (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS,
                                      NodeKind.EXTERNAL_SYMBOL))
                if callable_ids:
                    self.graph.add_edge(CodeEdge(
                        node_id, callable_ids[0], EdgeType.CALLS, Confidence.PROBABLE,
                        source_anchor=anchor, possible_targets=callable_ids,
                    ))
                else:
                    self._dynamic_call(node_id, name, anchor)
            elif reference.unresolved_symbol_id is not None:
                self.graph.add_edge(CodeEdge(
                    node_id, reference.unresolved_symbol_id, EdgeType.CALLS,
                    Confidence.UNRESOLVED, source_anchor=anchor,
                ))
            else:
                self._dynamic_call(node_id, name, anchor)
        else:
            self._dynamic_call(node_id, name, anchor)
        self.generic_visit(node)


def build_project_flow(files: tuple[ParsedFile, ...], resolution: ResolutionResult) -> FlowAnalysis:
    graph = build_symbol_graph(resolution)
    issues: list[AnalysisIssue] = []
    add_control_flow(files, resolution.symbol_index, graph, issues)
    data_nodes = add_critical_data(files, resolution.symbol_index, graph, issues)
    for parsed in files:
        if parsed.tree is not None and any(entry.kind == NodeKind.MODULE
                                           and entry.file_path == parsed.relative_path
                                           for entry in resolution.symbol_index.entries.values()):
            try:
                _FlowVisitor(parsed, resolution, graph, issues).visit(parsed.tree)
            except Exception as error:
                issues.append(AnalysisIssue(
                    "FLOW_ERROR", f"{type(error).__name__}: {error}",
                    parsed.relative_path, SourceAnchor(parsed.relative_path, 1),
                ))
    return FlowAnalysis(graph, data_nodes, find_call_cycles(graph), tuple(issues))
