"""Core, UI-independent domain types."""

from .graph import CodeGraph
from .identity import EntityRole, NodeKind, SourceAnchor, stable_node_id
from .model import (
    CodeEdge,
    CodeNode,
    Confidence,
    DataNode,
    DataRole,
    EdgeType,
    EntryPoint,
    EntryPointKind,
    NodeOrigin,
    SemanticComponent,
    SemanticGraph,
    SemanticRelation,
    ViewKind,
    VisibleAggregation,
    VisibleEdge,
    VisibleGraph,
    VisibleLegendItem,
    VisibleNode,
)

__all__ = [
    "CodeEdge", "CodeGraph", "CodeNode", "Confidence", "DataNode", "DataRole",
    "EdgeType", "EntityRole", "EntryPoint", "EntryPointKind", "NodeKind",
    "NodeOrigin", "SemanticComponent", "SemanticGraph", "SemanticRelation",
    "SourceAnchor", "ViewKind", "VisibleAggregation", "VisibleEdge", "VisibleGraph",
    "VisibleLegendItem", "VisibleNode", "stable_node_id",
]
