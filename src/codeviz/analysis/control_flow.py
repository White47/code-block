"""Structural, intentionally partial Python control-flow edges."""

import ast
from collections import defaultdict
from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class _Exit:
    node_id: str
    edge_type: EdgeType = EdgeType.CONTROL_FLOW


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

    def _node(self, owner_id: str, statement: ast.stmt, kind: NodeKind) -> str:
        owner = self.index.entries[owner_id]
        ordinal = self.ordinal[owner_id]
        self.ordinal[owner_id] += 1
        name = type(statement).__name__
        anchor = SourceAnchor(
            self.parsed.relative_path, statement.lineno,
            start_column=statement.col_offset,
            end_line=statement.end_lineno,
            end_column=statement.end_col_offset,
        )
        node_id = stable_node_id(
            self.parsed.relative_path, f"{owner.qualified_name}.{name}", kind,
            owner.qualified_name, occurrence_key=f"scope:{owner_id}:step:{ordinal}",
        )
        metadata: dict[str, object] = {"statement_type": name}
        if isinstance(statement, (ast.If, ast.While)):
            metadata["condition"] = ast.unparse(statement.test)
        elif isinstance(statement, (ast.For, ast.AsyncFor)):
            metadata["condition"] = f"{ast.unparse(statement.target)} in {ast.unparse(statement.iter)}"
        elif isinstance(statement, (ast.Try, ast.TryStar)):
            metadata["partial"] = "exception/finally paths simplified"
        self.graph.add_node(CodeNode(
            id=node_id, kind=kind, name=name,
            qualified_name=f"{owner.qualified_name}.{name}", scope=owner.qualified_name,
            language="python", source_anchors=(anchor,), metadata=metadata,
        ))
        self.graph.add_edge(CodeEdge(owner_id, node_id, EdgeType.CONTAINS,
                                     source_anchor=anchor))
        return node_id

    def _connect(self, exits: list[_Exit], target: str) -> None:
        anchor = self.graph.nodes[target].source_anchors[0]
        for exit_point in exits:
            self.graph.add_edge(CodeEdge(exit_point.node_id, target, exit_point.edge_type,
                                         source_anchor=anchor))

    def sequence(self, statements: list[ast.stmt], owner_id: str,
                 incoming: list[_Exit], loop_header: str | None = None,
                 break_exits: list[_Exit] | None = None) -> list[_Exit]:
        exits = incoming
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                parent_kind = self.index.entries[owner_id].kind
                kind = (NodeKind.CLASS if isinstance(statement, ast.ClassDef) else
                        NodeKind.METHOD if parent_kind == NodeKind.CLASS else NodeKind.FUNCTION)
                entry = self.children.get((owner_id, statement.name, kind, statement.lineno))
                if entry is not None:
                    self.sequence(statement.body, entry.id, [_Exit(entry.id)])
                continue

            if isinstance(statement, ast.If):
                header = self._node(owner_id, statement, NodeKind.CONTROL_BLOCK)
                self._connect(exits, header)
                true_exits = self.sequence(statement.body, owner_id,
                                           [_Exit(header, EdgeType.BRANCH_TRUE)],
                                           loop_header, break_exits)
                false_exits = (self.sequence(statement.orelse, owner_id,
                                              [_Exit(header, EdgeType.BRANCH_FALSE)],
                                              loop_header, break_exits)
                               if statement.orelse else [_Exit(header, EdgeType.BRANCH_FALSE)])
                exits = true_exits + false_exits
                continue

            if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                header = self._node(owner_id, statement, NodeKind.CONTROL_BLOCK)
                self._connect(exits, header)
                this_breaks: list[_Exit] = []
                body_exits = self.sequence(statement.body, owner_id,
                                           [_Exit(header, EdgeType.BRANCH_TRUE)],
                                           header, this_breaks)
                for tail in body_exits:
                    self.graph.add_edge(CodeEdge(tail.node_id, header, EdgeType.LOOP_BACK,
                                                 source_anchor=self.graph.nodes[header].source_anchors[0]))
                normal_exits = (self.sequence(statement.orelse, owner_id,
                                               [_Exit(header, EdgeType.BRANCH_FALSE)],
                                               loop_header, break_exits)
                                if statement.orelse else [_Exit(header, EdgeType.BRANCH_FALSE)])
                exits = normal_exits + this_breaks
                continue

            if isinstance(statement, (ast.With, ast.AsyncWith)):
                header = self._node(owner_id, statement, NodeKind.FLOW_STEP)
                self._connect(exits, header)
                exits = self.sequence(statement.body, owner_id, [_Exit(header)],
                                      loop_header, break_exits)
                continue

            if isinstance(statement, (ast.Try, ast.TryStar)):
                header = self._node(owner_id, statement, NodeKind.CONTROL_BLOCK)
                self._connect(exits, header)
                normal = self.sequence(statement.body, owner_id,
                                       [_Exit(header, EdgeType.BRANCH_TRUE)],
                                       loop_header, break_exits)
                if statement.orelse:
                    normal = self.sequence(statement.orelse, owner_id, normal,
                                           loop_header, break_exits)
                handled: list[_Exit] = []
                for handler in statement.handlers:
                    handled.extend(self.sequence(handler.body, owner_id,
                                                 [_Exit(header, EdgeType.BRANCH_FALSE)],
                                                 loop_header, break_exits))
                exits = normal + handled
                if statement.finalbody:
                    exits = self.sequence(statement.finalbody, owner_id, exits,
                                          loop_header, break_exits)
                continue

            step = self._node(owner_id, statement, NodeKind.FLOW_STEP)
            self._connect(exits, step)
            if isinstance(statement, (ast.Return, ast.Raise)):
                exits = []
            elif isinstance(statement, ast.Continue):
                if loop_header is not None:
                    self.graph.add_edge(CodeEdge(step, loop_header, EdgeType.LOOP_BACK,
                                                 source_anchor=self.graph.nodes[loop_header].source_anchors[0]))
                exits = []
            elif isinstance(statement, ast.Break):
                if break_exits is not None:
                    break_exits.append(_Exit(step))
                exits = []
            else:
                exits = [_Exit(step)]
        return exits


def add_control_flow(files: tuple[ParsedFile, ...], index: SymbolIndex,
                     graph: CodeGraph, issues: list[AnalysisIssue]) -> None:
    """Add basic per-scope sequence, branch, and loop facts to the graph."""
    for parsed in files:
        if parsed.tree is None:
            continue
        module = next((entry for entry in index.entries.values()
                       if entry.kind == NodeKind.MODULE and entry.file_path == parsed.relative_path), None)
        if module is not None:
            try:
                _ControlBuilder(parsed, index, graph).sequence(parsed.tree.body, module.id,
                                                               [_Exit(module.id)])
            except Exception as error:
                issues.append(AnalysisIssue(
                    "CONTROL_FLOW_ERROR", f"{type(error).__name__}: {error}",
                    parsed.relative_path, SourceAnchor(parsed.relative_path, 1),
                ))
