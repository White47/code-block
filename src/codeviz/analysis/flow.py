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
from .lexical_scopes import (
    ImplicitScope,
    ImplicitScopeVisitor,
    ScopeLocations,
    definition_expressions,
)
from .python import AnalysisIssue, ParsedFile
from .relations import add_relations
from .resolution import ResolutionResult
from .symbols import SymbolReference
from .value_candidates import CandidateValue, ValueCandidates


@dataclass(frozen=True, slots=True)
class FlowAnalysis:
    graph: CodeGraph
    data_nodes: tuple[DataNode, ...] = ()
    call_cycles: tuple[tuple[str, ...], ...] = ()
    issues: tuple[AnalysisIssue, ...] = ()


class _FlowVisitor(ImplicitScopeVisitor):
    def __init__(self, parsed: ParsedFile, resolution: ResolutionResult, graph: CodeGraph,
                 issues: list[AnalysisIssue],
                 values: dict[ast.Call, CandidateValue] | None = None) -> None:
        self.parsed = parsed
        self.index = resolution.symbol_index
        self.graph = graph
        self.issues = issues
        self.values = values or {}
        self.step_id: str | None = None
        self.scope_id = next(entry.id for entry in self.index.entries.values()
                             if entry.kind == NodeKind.MODULE and entry.file_path == parsed.relative_path)
        assert parsed.tree is not None
        self.locations = ScopeLocations(parsed.tree, self.index, parsed.relative_path)
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

    def _visit_definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        for expression in definition_expressions(node):
            self.visit(expression)
        entry_id = self.locations.owners.get(node)
        entry = self.index.entries.get(entry_id) if entry_id is not None else None
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

    def _visit_implicit_body(self, node: ImplicitScope, body: tuple[ast.AST, ...]) -> None:
        previous_step = self.step_id
        self.step_id = None
        try:
            super()._visit_implicit_body(node, body)
        finally:
            self.step_id = previous_step

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_definition(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_definition(node)

    def _dynamic_call(self, call_id: str, name: str, anchor: SourceAnchor,
                      binding_id: str | None = None,
                      possible_targets: tuple[str, ...] = (),
                      issue_code: str = "DYNAMIC_CALL_UNRESOLVED",
                      metadata: dict[str, object] | None = None) -> None:
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
            metadata=metadata or {},
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
        value = self.values.get(node)
        if value is not None and (value.indirect or value.unknown or value.may_unbound
                                  or value.truncated):
            callable_ids = tuple(target for target in value.targets
                                 if self.graph.nodes[target].kind in
                                 (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS,
                                  NodeKind.EXTERNAL_SYMBOL))
            metadata: dict[str, object] = {
                "unknown": value.unknown or len(callable_ids) != len(value.targets),
                "may_unbound": value.may_unbound, "truncated": value.truncated,
                "evidence": "bounded call-position value candidates",
            }
            if callable_ids:
                self.graph.add_edge(CodeEdge(
                    node_id, callable_ids[0], EdgeType.CALLS, Confidence.PROBABLE,
                    source_anchor=anchor, possible_targets=callable_ids, metadata=metadata,
                ))
                if qualifier in ("self", "cls"):
                    self.issues.append(AnalysisIssue(
                        "DYNAMIC_DISPATCH_UNRESOLVED", "receiver may dispatch to an override",
                        self.parsed.relative_path, anchor,
                    ))
            else:
                self._dynamic_call(node_id, name, anchor, metadata=metadata)
        elif reference is not None:
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
    relations = add_relations(files, resolution, graph, issues)
    add_control_flow(files, resolution.symbol_index, graph, issues)
    data_nodes = add_critical_data(files, resolution.symbol_index, graph, issues)
    for parsed in files:
        if parsed.tree is not None and any(entry.kind == NodeKind.MODULE
                                           and entry.file_path == parsed.relative_path
                                           for entry in resolution.symbol_index.entries.values()):
            try:
                values = ValueCandidates(parsed, resolution, graph, relations, issues).analyze()
                _FlowVisitor(parsed, resolution, graph, issues, values).visit(parsed.tree)
            except Exception as error:
                issues.append(AnalysisIssue(
                    "FLOW_ERROR", f"{type(error).__name__}: {error}",
                    parsed.relative_path, SourceAnchor(parsed.relative_path, 1),
                ))
    return FlowAnalysis(graph, data_nodes, find_call_cycles(graph), tuple(issues))
