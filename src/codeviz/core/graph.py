"""Validated Code Graph IR container."""

from dataclasses import asdict

from .identity import EntityRole, NodeKind, SourceAnchor
from .model import CodeEdge, CodeNode, Confidence, EdgeType, NodeOrigin


class CodeGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, CodeNode] = {}
        self.edges: list[CodeEdge] = []

    def add_node(self, node: CodeNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"duplicate node ID: {node.id}")
        self.nodes[node.id] = node

    def add_edge(self, edge: CodeEdge) -> None:
        targets = (edge.target, *edge.possible_targets)
        if edge.source not in self.nodes or any(target not in self.nodes for target in targets):
            raise ValueError("edge refers to an unregistered node")
        self.edges.append(edge)

    def to_dict(self) -> dict[str, object]:
        return {
            "nodes": [asdict(node) for node in self.nodes.values()],
            "edges": [asdict(edge) for edge in self.edges],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "CodeGraph":
        graph = cls()
        for raw in payload["nodes"]:
            node = dict(raw)
            node["kind"] = NodeKind(node["kind"])
            node["origin"] = NodeOrigin(node["origin"])
            node["role"] = EntityRole(node["role"])
            node["confidence"] = Confidence(node["confidence"])
            node["source_anchors"] = tuple(SourceAnchor(**anchor) for anchor in node["source_anchors"])
            graph.add_node(CodeNode(**node))
        for raw in payload["edges"]:
            edge = dict(raw)
            edge["type"] = EdgeType(edge["type"])
            edge["confidence"] = Confidence(edge["confidence"])
            edge["source_anchor"] = (
                SourceAnchor(**edge["source_anchor"]) if edge["source_anchor"] else None
            )
            edge["possible_targets"] = tuple(edge["possible_targets"])
            graph.add_edge(CodeEdge(**edge))
        return graph
