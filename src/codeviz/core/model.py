"""Graph and semantic value objects."""

from dataclasses import dataclass, field
from enum import StrEnum

from .identity import EntityRole, NodeKind, SourceAnchor


class Confidence(StrEnum):
    EXACT = "Exact"
    PROBABLE = "Probable"
    UNRESOLVED = "Unresolved"


class NodeOrigin(StrEnum):
    SOURCE = "Source"
    SYNTHETIC = "Synthetic"
    EXTERNAL = "External"


class EdgeType(StrEnum):
    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    EXPORTS = "EXPORTS"
    CALLS = "CALLS"
    INHERITS = "INHERITS"
    IMPLEMENTS = "IMPLEMENTS"
    CONTROL_FLOW = "CONTROL_FLOW"
    BRANCH_TRUE = "BRANCH_TRUE"
    BRANCH_FALSE = "BRANCH_FALSE"
    LOOP_BACK = "LOOP_BACK"
    READS = "READS"
    WRITES = "WRITES"
    PASSES_TO = "PASSES_TO"
    RETURNS_TO = "RETURNS_TO"
    DATA_FLOW = "DATA_FLOW"
    GRADIENT_FLOW = "GRADIENT_FLOW"


class DataRole(StrEnum):
    INPUT = "INPUT"
    TARGET = "TARGET"
    MASK = "MASK"
    FEATURE = "FEATURE"
    HIDDEN_STATE = "HIDDEN_STATE"
    PREDICTION = "PREDICTION"
    LOSS = "LOSS"
    PARAMETER = "PARAMETER"
    GRADIENT = "GRADIENT"
    OTHER = "OTHER"


class EntryPointKind(StrEnum):
    TRAIN = "Train"
    EVALUATE = "Evaluate"
    INFERENCE = "Inference"
    EXPORT = "Export"


class ViewKind(StrEnum):
    OVERVIEW = "Overview"
    ARCHITECTURE = "Architecture"
    EXECUTION = "Execution"
    CALLS = "Calls"
    DATA_FLOW = "DataFlow"
    GRADIENT = "Gradient"
    SYMBOL = "Symbol"


@dataclass(frozen=True, slots=True)
class CodeNode:
    id: str
    kind: NodeKind
    name: str
    qualified_name: str
    scope: str
    language: str
    source_anchors: tuple[SourceAnchor, ...] = ()
    origin: NodeOrigin = NodeOrigin.SOURCE
    role: EntityRole = EntityRole.DEFINITION
    confidence: Confidence = Confidence.EXACT
    file_path: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.name or not self.language:
            raise ValueError("node id, name and language are required")
        if self.origin == NodeOrigin.SOURCE and not self.source_anchors:
            raise ValueError("source nodes require a source anchor")
        if self.source_anchors:
            first_path = self.source_anchors[0].file_path
            if self.file_path is None:
                object.__setattr__(self, "file_path", first_path)
            if any(anchor.file_path != self.file_path for anchor in self.source_anchors):
                raise ValueError("node anchors must match file_path")


@dataclass(frozen=True, slots=True)
class CodeEdge:
    source: str
    target: str
    type: EdgeType
    confidence: Confidence = Confidence.EXACT
    source_anchor: SourceAnchor | None = None
    possible_targets: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source or not self.target:
            raise ValueError("edge endpoints are required")
        if self.possible_targets and (self.type != EdgeType.CALLS or self.confidence == Confidence.EXACT):
            raise ValueError("possible targets require an uncertain CALLS edge")


@dataclass(frozen=True, slots=True)
class SemanticComponent:
    id: str
    display_name: str
    code_name: str | None = None
    role: str | None = None
    confidence: Confidence = Confidence.UNRESOLVED
    evidence: tuple[str, ...] = ()
    child_component_ids: tuple[str, ...] = ()
    code_symbol_ids: tuple[str, ...] = ()
    source_anchors: tuple[SourceAnchor, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code_symbol_ids and not self.source_anchors and not self.child_component_ids:
            raise ValueError("semantic components require provenance")


@dataclass(frozen=True, slots=True)
class DataNode:
    id: str
    name: str
    role: DataRole
    source_anchor: SourceAnchor
    shape: tuple[int | None, ...] | None = None
    dtype: str | None = None
    device: str | None = None
    confidence: Confidence = Confidence.UNRESOLVED


@dataclass(frozen=True, slots=True)
class EntryPoint:
    id: str
    name: str
    kind: EntryPointKind
    source_anchor: SourceAnchor
    flow_node_ids: tuple[str, ...] = ()
    model_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VisibleNode:
    id: str
    display_name: str
    semantic_component_ids: tuple[str, ...] = ()
    code_node_ids: tuple[str, ...] = ()
    source_anchors: tuple[SourceAnchor, ...] = ()
    member_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not any((self.semantic_component_ids, self.code_node_ids, self.source_anchors, self.member_ids)):
            raise ValueError("visible nodes require provenance")


@dataclass(frozen=True, slots=True)
class VisibleEdge:
    source: str
    target: str
    type: EdgeType
    code_edge_ids: tuple[str, ...] = ()
    semantic_component_ids: tuple[str, ...] = ()
    source_anchors: tuple[SourceAnchor, ...] = ()
    confidence: Confidence = Confidence.EXACT

    def __post_init__(self) -> None:
        if not self.source or not self.target:
            raise ValueError("visible edge endpoints are required")
        if not any((self.code_edge_ids, self.semantic_component_ids, self.source_anchors)):
            raise ValueError("visible edges require provenance")


@dataclass(frozen=True, slots=True)
class VisibleGraph:
    view_kind: ViewKind
    nodes: tuple[VisibleNode, ...]
    root_ids: tuple[str, ...] = ()
    edges: tuple[VisibleEdge, ...] = ()

    def __post_init__(self) -> None:
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("visible node IDs must be unique")
        if any(root not in ids for root in self.root_ids):
            raise ValueError("visible roots must exist in nodes")
        if any(edge.source not in ids or edge.target not in ids for edge in self.edges):
            raise ValueError("visible edges must join visible nodes")
