"""Bounded human task-flow and functional-architecture projections."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING, Literal

from codeviz.analysis.human_tasks import (
    ArtifactAccessFact,
    ArtifactAccessKind,
    HumanEntryKind,
    HumanTaskEntryFact,
    StaticTaskFact,
    StaticTaskKind,
)
from codeviz.analysis.human_understanding import HumanOperationKind
from codeviz.core import Confidence, EdgeType, NodeKind, NodeOrigin, SourceAnchor
from codeviz.semantic import TrainingRole

if TYPE_CHECKING:
    from codeviz.analysis.model import ModuleInstance, ModuleInvocation
    from codeviz.analysis.project import ProjectAnalysis
    from codeviz.core import CodeEdge, CodeGraph, CodeNode

    from .human_understanding import HumanUnderstanding

MAX_FLOWS = 32
MAX_FLOW_STEPS = 16
MAX_FLOW_RELATIONS = 32
MAX_ARCHITECTURE_AREAS = 12
MAX_ARCHITECTURE_RELATIONS = 24
MAX_SUMMARY_REFERENCES = 8
_SCOPE_KINDS = frozenset({
    NodeKind.MODULE, NodeKind.CLASS, NodeKind.FUNCTION,
    NodeKind.METHOD, NodeKind.LEXICAL_SCOPE,
})
_CONFIDENCE_STRENGTH = {
    Confidence.UNRESOLVED: 0,
    Confidence.PROBABLE: 1,
    Confidence.EXACT: 2,
}
_TASK_TITLES = {
    StaticTaskKind.EVALUATION_HELPER: "Evaluate model behavior",
    StaticTaskKind.LEARNING_RATE_UPDATE: "Update learning rate",
    StaticTaskKind.GRADIENT_ACCUMULATION: "Accumulate gradients",
    StaticTaskKind.INPUT_ENCODING: "Encode input",
    StaticTaskKind.TOKEN_SAMPLING: "Sample next token",
    StaticTaskKind.OUTPUT_DECODING: "Decode output",
    StaticTaskKind.RESIDUAL_MERGE: "Merge residual path",
    StaticTaskKind.LAST_TOKEN_SELECTION: "Select final token state",
}
_TRAINING_TITLES = {
    TrainingRole.TRAINING_MODE: "Enable training behavior",
    TrainingRole.EVALUATION_MODE: "Enable evaluation behavior",
    TrainingRole.FORWARD_PASS: "Run model forward",
    TrainingRole.LOSS_CALCULATION: "Calculate training loss",
    TrainingRole.CLEAR_GRADIENTS: "Clear gradients",
    TrainingRole.BACKWARD_PASS: "Backpropagate loss",
    TrainingRole.PARAMETER_UPDATE: "Update parameters",
    TrainingRole.LR_UPDATE: "Update learning rate",
    TrainingRole.DISABLE_GRADIENT: "Disable gradient tracking",
    TrainingRole.EPOCH_LOOP: "Repeat training epochs",
    TrainingRole.BATCH_LOOP: "Iterate over batches",
    TrainingRole.CHECKPOINT_SAVE: "Save training checkpoint",
    TrainingRole.CHECKPOINT_LOAD: "Load training checkpoint",
}


class SummaryRelationKind(StrEnum):
    CONTROL_NEXT = "control_next"
    BRANCH_TRUE = "branch_true"
    BRANCH_FALSE = "branch_false"
    LOOP_BACK = "loop_back"
    CONTAINS = "contains"
    DATA_ARTIFACT = "data_artifact"
    STATIC_CALL = "static_call"
    MODEL_USE = "model_use"
    SHARED = "shared"


def _refs(values: object, *, required: bool = False) -> bool:
    return bool(
        type(values) is tuple
        and (not required or values)
        and len(values) <= MAX_SUMMARY_REFERENCES
        and len(set(values)) == len(values)
        and all(type(value) is str and value for value in values)
    )


@dataclass(frozen=True, slots=True)
class TaskStep:
    id: str
    kind: Literal["task", "decision", "loop", "result"]
    title: str
    basis: str
    confidence: Confidence
    owner_symbol_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    code_node_ids: tuple[str, ...]
    source_anchors: tuple[SourceAnchor, ...]
    source_location_count: int
    code_name: str | None = None

    def __post_init__(self) -> None:
        if (type(self.id) is not str or not self.id
                or self.kind not in ("task", "decision", "loop", "result")
                or type(self.title) is not str or not self.title
                or type(self.basis) is not str or not self.basis
                or not isinstance(self.confidence, Confidence)
                or not _refs(self.owner_symbol_ids, required=True)
                or not _refs(self.evidence_ids, required=True)
                or not _refs(self.code_node_ids, required=True)
                or type(self.source_anchors) is not tuple
                or not 1 <= len(self.source_anchors) <= MAX_SUMMARY_REFERENCES
                or any(not isinstance(anchor, SourceAnchor) for anchor in self.source_anchors)
                or len(set(self.source_anchors)) != len(self.source_anchors)
                or type(self.source_location_count) is not int
                or self.source_location_count < len(self.source_anchors)
                or (self.code_name is not None
                    and (type(self.code_name) is not str or not self.code_name))):
            raise ValueError("invalid task step")


@dataclass(frozen=True, slots=True)
class SummaryRelation:
    source: str
    target: str
    kind: SummaryRelationKind
    confidence: Confidence
    evidence_ids: tuple[str, ...]
    code_node_ids: tuple[str, ...]
    source_anchors: tuple[SourceAnchor, ...]
    sharing_kind: Literal["module_instance", "parameter"] | None = None
    sharing_attributes: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        if (type(self.source) is not str or not self.source
                or type(self.target) is not str or not self.target
                or self.source == self.target
                or not isinstance(self.kind, SummaryRelationKind)
                or not isinstance(self.confidence, Confidence)
                or not _refs(self.evidence_ids)
                or not _refs(self.code_node_ids)
                or (not self.evidence_ids and not self.code_node_ids)
                or type(self.source_anchors) is not tuple
                or len(self.source_anchors) > MAX_SUMMARY_REFERENCES
                or any(not isinstance(anchor, SourceAnchor) for anchor in self.source_anchors)
                or len(set(self.source_anchors)) != len(self.source_anchors)
                or self.sharing_kind not in (None, "module_instance", "parameter")):
            raise ValueError("invalid summary relation")
        if self.kind is SummaryRelationKind.SHARED:
            if self.sharing_kind is None:
                raise ValueError("shared relations require a sharing kind")
            if ((self.sharing_kind == "parameter")
                    != (type(self.sharing_attributes) is tuple
                        and len(self.sharing_attributes) == 2
                        and all(type(item) is str and item
                                for item in self.sharing_attributes))):
                raise ValueError("parameter sharing requires exactly two attributes")
        elif self.sharing_kind is not None or self.sharing_attributes is not None:
            raise ValueError("only shared relations carry sharing metadata")


@dataclass(frozen=True, slots=True)
class TaskFlow:
    id: str
    entry_symbol_id: str
    title: str
    steps: tuple[TaskStep, ...]
    relations: tuple[SummaryRelation, ...]
    incomplete: bool
    omitted_count: int

    def __post_init__(self) -> None:
        step_ids = {step.id for step in self.steps}
        if (type(self.id) is not str or not self.id
                or type(self.entry_symbol_id) is not str or not self.entry_symbol_id
                or type(self.title) is not str or not self.title
                or type(self.steps) is not tuple or len(self.steps) > MAX_FLOW_STEPS
                or any(not isinstance(step, TaskStep) for step in self.steps)
                or len(step_ids) != len(self.steps)
                or type(self.relations) is not tuple
                or len(self.relations) > MAX_FLOW_RELATIONS
                or any(not isinstance(relation, SummaryRelation)
                       or relation.source not in step_ids or relation.target not in step_ids
                       for relation in self.relations)
                or type(self.incomplete) is not bool
                or type(self.omitted_count) is not int or self.omitted_count < 0
                or self.incomplete != (self.omitted_count > 0)):
            raise ValueError("invalid task flow")


@dataclass(frozen=True, slots=True)
class FunctionalArchitecture:
    areas: tuple[TaskStep, ...]
    relations: tuple[SummaryRelation, ...]
    incomplete: bool
    omitted_count: int
    id: str = "project-architecture"

    def __post_init__(self) -> None:
        area_ids = {area.id for area in self.areas}
        if (self.id != "project-architecture"
                or type(self.areas) is not tuple
                or len(self.areas) > MAX_ARCHITECTURE_AREAS
                or any(not isinstance(area, TaskStep) for area in self.areas)
                or len(area_ids) != len(self.areas)
                or type(self.relations) is not tuple
                or len(self.relations) > MAX_ARCHITECTURE_RELATIONS
                or any(not isinstance(relation, SummaryRelation)
                       or relation.source not in area_ids or relation.target not in area_ids
                       for relation in self.relations)
                or type(self.incomplete) is not bool
                or type(self.omitted_count) is not int or self.omitted_count < 0
                or self.incomplete != (self.omitted_count > 0)):
            raise ValueError("invalid functional architecture")


@dataclass(frozen=True, slots=True)
class _EvaluationOccurrence:
    fact: StaticTaskFact
    incoming_call: CodeNode
    call_edge: CodeEdge
    caller_statement_id: str
    model_call_id: str | None
    loss_call_id: str | None
    evaluation_step_id: str
    loss_step_id: str | None


def _summary_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _bounded_control_text(value: object, max_bytes: int = 120) -> str | None:
    if type(value) is not str:
        return None
    text = " ".join(value.split())
    if not text:
        return None
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    suffix = "…"
    while text and len((text + suffix).encode("utf-8")) > max_bytes:
        text = text[:-1]
    return text.rstrip() + suffix if text else None


def _control_title(node: CodeNode) -> str:
    statement_type = node.metadata.get("statement_type")
    condition = _bounded_control_text(node.metadata.get("condition"))
    if statement_type == "If":
        return f"Condition: {condition}" if condition is not None else "Conditional path"
    if statement_type == "While":
        if condition == "True":
            return "Iteration loop"
        return f"Loop while: {condition}" if condition is not None else "Iteration loop"
    if statement_type in ("For", "AsyncFor"):
        return f"Loop: {condition}" if condition is not None else "Iteration loop"
    return "Control path"


def _edge_kind(edge_type: EdgeType) -> SummaryRelationKind | None:
    return {
        EdgeType.CONTROL_FLOW: SummaryRelationKind.CONTROL_NEXT,
        EdgeType.BRANCH_TRUE: SummaryRelationKind.BRANCH_TRUE,
        EdgeType.BRANCH_FALSE: SummaryRelationKind.BRANCH_FALSE,
        EdgeType.LOOP_BACK: SummaryRelationKind.LOOP_BACK,
    }.get(edge_type)


def _contained_calls(graph: CodeGraph, node_id: str) -> tuple[CodeNode, ...]:
    call_ids = {
        edge.target for edge in graph.edges
        if edge.source == node_id and edge.type is EdgeType.CONTAINS
    }
    return tuple(graph.nodes[item] for item in call_ids
                 if item in graph.nodes and graph.nodes[item].kind is NodeKind.CALL_SITE)


def _call_target(graph: CodeGraph, call_id: str) -> tuple[CodeNode | None, Confidence]:
    edges = [edge for edge in graph.edges
             if edge.source == call_id and edge.type is EdgeType.CALLS]
    if len(edges) != 1:
        return None, Confidence.UNRESOLVED
    return graph.nodes.get(edges[0].target), edges[0].confidence


def _flow_nodes(graph: CodeGraph, root_id: str) -> tuple[CodeNode, ...]:
    control_types = {
        EdgeType.CONTROL_FLOW, EdgeType.BRANCH_TRUE,
        EdgeType.BRANCH_FALSE, EdgeType.LOOP_BACK,
    }
    successors: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.type in control_types:
            successors.setdefault(edge.source, []).append(edge.target)
    ordered: list[CodeNode] = []
    pending = list(successors.get(root_id, ()))
    seen: set[str] = set()
    while pending:
        node_id = pending.pop(0)
        if node_id in seen:
            continue
        seen.add(node_id)
        node = graph.nodes.get(node_id)
        if node is None or node.kind not in (NodeKind.FLOW_STEP, NodeKind.CONTROL_BLOCK):
            continue
        ordered.append(node)
        pending.extend(item for item in successors.get(node_id, ()) if item not in seen)
    return tuple(ordered)


def _control_path(
    graph: CodeGraph,
    source_id: str,
    target_id: str,
) -> tuple[CodeEdge, ...] | None:
    control_types = {
        EdgeType.CONTROL_FLOW, EdgeType.BRANCH_TRUE,
        EdgeType.BRANCH_FALSE, EdgeType.LOOP_BACK,
    }
    by_source: dict[str, list[CodeEdge]] = {}
    for edge in graph.edges:
        if edge.type in control_types:
            by_source.setdefault(edge.source, []).append(edge)
    pending: list[tuple[str, tuple[CodeEdge, ...]]] = [(source_id, ())]
    visited = {source_id}
    while pending and len(visited) <= 256:
        current, path = pending.pop(0)
        for edge in by_source.get(current, ()):
            next_path = (*path, edge)
            if edge.target == target_id:
                return next_path
            if edge.target not in visited:
                visited.add(edge.target)
                pending.append((edge.target, next_path))
    return None


def _control_statement_id(graph: CodeGraph, node_id: str) -> str:
    node = graph.nodes.get(node_id)
    if node is None or node.kind is not NodeKind.CALL_SITE:
        return node_id
    parents = [
        edge.source for edge in graph.edges
        if edge.target == node_id and edge.type is EdgeType.CONTAINS
        and edge.source in graph.nodes
        and graph.nodes[edge.source].kind in (NodeKind.FLOW_STEP, NodeKind.CONTROL_BLOCK)
    ]
    return parents[0] if len(parents) == 1 else node_id


def _strict_anchor_contains(parent: SourceAnchor, child: SourceAnchor) -> bool:
    if (parent.file_path != child.file_path or parent.end_line is None
            or child.end_line is None or parent == child):
        return False
    if child.start_line < parent.start_line or child.end_line > parent.end_line:
        return False
    if (child.start_line == parent.start_line and parent.start_column is not None
            and (child.start_column is None or child.start_column < parent.start_column)):
        return False
    if (child.end_line == parent.end_line and parent.end_column is not None
            and (child.end_column is None or child.end_column > parent.end_column)):
        return False
    return True


def _statement_data_witness(
    graph: CodeGraph,
    source_statement: str,
    target_statement: str,
) -> tuple[str, ...]:
    written = {
        edge.target for edge in graph.edges
        if edge.source == source_statement and edge.type is EdgeType.WRITES
    }
    read = {
        edge.target for edge in graph.edges
        if edge.source == target_statement and edge.type is EdgeType.READS
    }
    return tuple(sorted(written & read))


def _call_data_dependency(graph: CodeGraph, source_call_id: str, target_call_id: str) -> bool:
    source_statement = _control_statement_id(graph, source_call_id)
    target_statement = _control_statement_id(graph, target_call_id)
    return bool(_statement_data_witness(graph, source_statement, target_statement))


def _anchors(nodes: tuple[CodeNode, ...]) -> tuple[SourceAnchor, ...]:
    return tuple(dict.fromkeys(
        anchor for node in nodes for anchor in node.source_anchors
    ))


def _code_nodes_for_anchor(
    analysis: ProjectAnalysis,
    anchor: SourceAnchor,
    fallback_id: str,
) -> tuple[str, ...]:
    if analysis.flow is None:
        return ()
    graph = analysis.flow.graph
    exact = tuple(
        node.id for node in graph.nodes.values()
        if anchor in node.source_anchors
        and node.kind in (NodeKind.CALL_SITE, NodeKind.FLOW_STEP, NodeKind.CONTROL_BLOCK)
    )
    if exact:
        return exact[:MAX_SUMMARY_REFERENCES]
    return (fallback_id,) if fallback_id in graph.nodes else ()


def _model_area_title(instance: ModuleInstance) -> str:
    if (instance.target_definition_id is not None
            or instance.confidence is Confidence.UNRESOLVED):
        return f"Model component: {instance.name}"
    layer = instance.layer_type.rsplit(".", 1)[-1]
    if layer == "Embedding":
        return "Look up embedding vectors"
    if layer in ("LayerNorm", "RMSNorm"):
        return "Normalize activations"
    if layer == "Linear":
        return "Project model features"
    if "Dropout" in layer:
        return "Regularize activations"
    if layer in ("GELU", "ReLU", "SiLU"):
        return "Transform activations"
    if instance.container_kind == "ModuleDict":
        return "Organize named model components"
    if layer in ("ModuleList", "Sequential") \
            or instance.container_kind in ("ModuleList", "Sequential"):
        return (f"Repeat model blocks ({instance.repeat_count} configured)"
                if instance.repeat_count is not None and instance.repeat_count > 1
                else "Organize model components")
    if "Attention" in layer:
        return "Apply attention"
    return f"Model component: {instance.name}"


def _build_architecture(analysis: ProjectAnalysis, base: object) -> FunctionalArchitecture:
    if analysis.model is None or analysis.flow is None:
        return FunctionalArchitecture((), (), False, 0)
    from .human_understanding import HumanUnderstanding, _valid_generic_root

    if not isinstance(base, HumanUnderstanding):
        raise TypeError("human summary base is required")
    graph = analysis.flow.graph
    instances = {instance.id: instance for instance in analysis.model.instances}
    endpoint_ids: list[str] = []
    for instance in analysis.model.instances:
        if instance.shared_with_id is not None and instance.shared_with_id in instances:
            endpoint_ids.extend((instance.id, instance.shared_with_id))
    for fact in analysis.model.parameter_sharings:
        if (fact.assigned_instance_id in instances
                and fact.source_instance_id in instances):
            endpoint_ids.extend((fact.assigned_instance_id, fact.source_instance_id))
    artifact_endpoint_ids: list[str] = []
    artifact_facts_by_key: dict[str, list[ArtifactAccessFact]] = {}
    for artifact_fact in analysis.human_tasks.artifacts:
        if not artifact_fact.artifact_key.startswith("dynamic:"):
            artifact_facts_by_key.setdefault(
                artifact_fact.artifact_key, [],
            ).append(artifact_fact)
    for grouped in artifact_facts_by_key.values():
        writers = [fact for fact in grouped if fact.kind is ArtifactAccessKind.WRITE]
        readers = [fact for fact in grouped if fact.kind is ArtifactAccessKind.READ]
        if len(writers) == 1 and readers:
            artifact_endpoint_ids.extend((
                writers[0].owner_symbol_id,
                *(reader.owner_symbol_id for reader in readers),
            ))
    local_calls: list[tuple[str, str, CodeEdge, CodeNode]] = []
    for edge in graph.edges:
        if edge.type is not EdgeType.CALLS or edge.target not in analysis.symbol_index.entries:
            continue
        call = graph.nodes.get(edge.source)
        target = analysis.symbol_index.entries[edge.target]
        caller_id = call.metadata.get("scope_symbol_id") if call is not None else None
        if (call is None or call.kind is not NodeKind.CALL_SITE
                or type(caller_id) is not str
                or caller_id not in analysis.symbol_index.entries
                or analysis.symbol_index.entries[caller_id].kind not in _SCOPE_KINDS
                or target.kind not in _SCOPE_KINDS):
            continue
        local_calls.append((caller_id, edge.target, edge, call))
    generic_scope_ids = [
        candidate.root_symbol_id for candidate in analysis.generic_entries
        if _valid_generic_root(analysis, candidate)
    ]
    training_scope_ids = (
        [entry.id for entry in analysis.training.entry_points
         if entry.id in analysis.symbol_index.entries]
        if analysis.training is not None else []
    )
    task_scope_ids = [
        entry.root_symbol_id for entry in analysis.human_tasks.entries
        if entry.root_symbol_id in analysis.symbol_index.entries
    ]
    definition_scope_ids = [
        definition.symbol_id for definition in analysis.model.definitions
        if definition.symbol_id in analysis.symbol_index.entries
    ]
    invocation_scope_ids = [
        scope_id for invocation in analysis.model.invocations
        if (call := graph.nodes.get(invocation.call_site_id)) is not None
        and type(scope_id := call.metadata.get("scope_symbol_id")) is str
        and scope_id in analysis.symbol_index.entries
        and analysis.symbol_index.entries[scope_id].kind in _SCOPE_KINDS
    ]
    sorted_instances = sorted(
        analysis.model.instances,
        key=lambda item: (
            item.source_anchor.file_path, item.source_anchor.start_line,
            item.source_anchor.start_column
            if item.source_anchor.start_column is not None else -1,
            item.id,
        ),
    )
    candidate_keys = list(dict.fromkeys((
        *(("model", item) for item in endpoint_ids),
        *(("scope", item) for item in (
            *artifact_endpoint_ids,
            *training_scope_ids, *task_scope_ids, *generic_scope_ids,
            *definition_scope_ids, *invocation_scope_ids,
            *(caller for caller, _, _, _ in local_calls),
            *(target for _, target, _, _ in local_calls),
        )),
        *(("model", item.id) for item in sorted_instances),
    )))
    selected_keys = candidate_keys[:MAX_ARCHITECTURE_AREAS]
    areas: list[TaskStep] = []
    area_by_instance: dict[str, str] = {}
    area_by_scope: dict[str, str] = {}
    cards = {card.owner_symbol_id: card for card in base.responsibilities}
    training_titles = {
        entry.id: {
            "Train": "Train model", "Evaluate": "Evaluate model",
            "Inference": "Run model inference", "Export": "Export model",
        }.get(entry.kind.value, "Model workflow")
        for entry in analysis.training.entry_points
    } if analysis.training is not None else {}
    task_titles = {
        entry.root_symbol_id: (
            "Generate output" if entry.kind is HumanEntryKind.GENERATION else "Prepare data"
        ) for entry in analysis.human_tasks.entries
    }
    for candidate_kind, candidate_id in selected_keys:
        if candidate_kind == "model":
            candidate_instance = instances.get(candidate_id)
            if candidate_instance is None:
                continue
            preferred = candidate_instance.target_definition_id
            owner_id = (preferred if preferred in analysis.symbol_index.entries
                        and analysis.symbol_index.entries[preferred].kind in _SCOPE_KINDS
                        else candidate_instance.owner_definition_id)
            if (owner_id not in analysis.symbol_index.entries
                    or analysis.symbol_index.entries[owner_id].kind not in _SCOPE_KINDS):
                continue
            code_node_ids = _code_nodes_for_anchor(
                analysis, candidate_instance.source_anchor,
                candidate_instance.owner_definition_id,
            )
            if not code_node_ids:
                continue
            area_id = _summary_id("area", "model", candidate_instance.id)
            area_by_instance[candidate_instance.id] = area_id
            areas.append(TaskStep(
                area_id, "task", _model_area_title(candidate_instance),
                f"model:{candidate_instance.layer_type}", candidate_instance.confidence,
                tuple(dict.fromkeys((owner_id, candidate_instance.owner_definition_id))),
                (candidate_instance.id,), code_node_ids,
                (candidate_instance.source_anchor,), 1, candidate_instance.name,
            ))
            continue
        scope = analysis.symbol_index.entries.get(candidate_id)
        if scope is None or scope.kind not in _SCOPE_KINDS or scope.id not in graph.nodes:
            continue
        card = cards.get(scope.id)
        if card is not None and card.responsibility is not None:
            title = card.responsibility
            basis = "source_author_statement"
            confidence = card.confidence
            evidence = (card.author_fact_id,) if card.author_fact_id is not None else ()
        else:
            title = (
                training_titles.get(scope.id)
                or task_titles.get(scope.id)
                or ("Define model structure" if scope.id in definition_scope_ids else None)
                or ("Program entry" if scope.id in generic_scope_ids else "Program task")
            )
            basis = "unknown"
            confidence = Confidence.UNRESOLVED
            evidence = (scope.id,)
        area_id = _summary_id("area", "scope", scope.id)
        area_by_scope[scope.id] = area_id
        areas.append(TaskStep(
            area_id, "task", title, basis, confidence, (scope.id,), evidence,
            (scope.id,), (scope.source_anchor,), 1,
        ))
    relations: list[SummaryRelation] = []
    relation_candidates = 0
    for instance in analysis.model.instances:
        target_id = instance.shared_with_id
        if target_id is None or target_id not in instances:
            continue
        relation_candidates += 1
        if instance.id not in area_by_instance or target_id not in area_by_instance:
            continue
        shared_target = instances[target_id]
        relations.append(SummaryRelation(
            area_by_instance[instance.id], area_by_instance[target_id],
            SummaryRelationKind.SHARED,
            min((instance.confidence, shared_target.confidence),
                key=_CONFIDENCE_STRENGTH.__getitem__),
            (instance.id, shared_target.id),
            tuple(dict.fromkeys((
                *_code_nodes_for_anchor(
                    analysis, instance.source_anchor, instance.owner_definition_id,
                ),
                *_code_nodes_for_anchor(
                    analysis, shared_target.source_anchor,
                    shared_target.owner_definition_id,
                ),
            )))[:MAX_SUMMARY_REFERENCES],
            tuple(dict.fromkeys((
                instance.source_anchor, shared_target.source_anchor,
            ))),
            "module_instance", None,
        ))
    for fact in analysis.model.parameter_sharings:
        relation_candidates += 1
        if (fact.assigned_instance_id not in area_by_instance
                or fact.source_instance_id not in area_by_instance):
            continue
        assigned = instances[fact.assigned_instance_id]
        source = instances[fact.source_instance_id]
        relations.append(SummaryRelation(
            area_by_instance[fact.assigned_instance_id],
            area_by_instance[fact.source_instance_id],
            SummaryRelationKind.SHARED, fact.confidence, (fact.id,),
            tuple(dict.fromkeys((
                *_code_nodes_for_anchor(
                    analysis, assigned.source_anchor, assigned.owner_definition_id,
                ),
                *_code_nodes_for_anchor(
                    analysis, source.source_anchor, source.owner_definition_id,
                ),
            )))[:MAX_SUMMARY_REFERENCES],
            (fact.source_anchor,), "parameter",
            (fact.assigned_attribute, fact.source_attribute),
        ))
    static_pairs: dict[tuple[str, str], list[tuple[CodeEdge, CodeNode]]] = {}
    for caller_id, target_id, edge, call in local_calls:
        static_pairs.setdefault((caller_id, target_id), []).append((edge, call))
    relation_candidates += len(static_pairs)
    for (caller_id, target_id), occurrences in static_pairs.items():
        if caller_id == target_id:
            continue
        source_area = area_by_scope.get(caller_id)
        target_area = area_by_scope.get(target_id)
        if source_area is None or target_area is None:
            continue
        anchors = tuple(dict.fromkeys(
            anchor for _, call in occurrences for anchor in call.source_anchors
        ))
        relations.append(SummaryRelation(
            source_area, target_area, SummaryRelationKind.STATIC_CALL,
            min((edge.confidence for edge, _ in occurrences),
                key=_CONFIDENCE_STRENGTH.__getitem__),
            tuple(dict.fromkeys(
                call.id for _, call in occurrences
            ))[:MAX_SUMMARY_REFERENCES], tuple(dict.fromkeys(
                item for edge, _ in occurrences for item in (edge.source, edge.target)
                if item in graph.nodes
            ))[:MAX_SUMMARY_REFERENCES],
            anchors[:MAX_SUMMARY_REFERENCES],
        ))
    model_uses: dict[tuple[str, str], list[ModuleInvocation]] = {}
    for invocation in analysis.model.invocations:
        call = graph.nodes.get(invocation.call_site_id)
        scope_id = call.metadata.get("scope_symbol_id") if call is not None else None
        if type(scope_id) is str:
            model_uses.setdefault((scope_id, invocation.instance_id), []).append(invocation)
    relation_candidates += len(model_uses)
    for (scope_id, instance_id), invocation_values in model_uses.items():
        source_area = area_by_scope.get(scope_id)
        target_area = area_by_instance.get(instance_id)
        if source_area is None or target_area is None:
            continue
        invocations = invocation_values
        relations.append(SummaryRelation(
            source_area, target_area, SummaryRelationKind.MODEL_USE,
            min((item.confidence for item in invocations),
                key=_CONFIDENCE_STRENGTH.__getitem__),
            tuple(item.id for item in invocations[:MAX_SUMMARY_REFERENCES]),
            tuple(dict.fromkeys(
                item.call_site_id for item in invocations
                if item.call_site_id in graph.nodes
            ))[:MAX_SUMMARY_REFERENCES],
            tuple(dict.fromkeys(
                item.source_anchor for item in invocations
            ))[:MAX_SUMMARY_REFERENCES],
        ))
    artifacts_by_key: dict[str, list[ArtifactAccessFact]] = {}
    dynamic_artifacts = [
        fact for fact in analysis.human_tasks.artifacts
        if fact.artifact_key.startswith("dynamic:")
    ]
    for artifact_fact in analysis.human_tasks.artifacts:
        if not artifact_fact.artifact_key.startswith("dynamic:"):
            artifacts_by_key.setdefault(
                artifact_fact.artifact_key, [],
            ).append(artifact_fact)
    artifact_gaps = int(
        any(fact.kind is ArtifactAccessKind.WRITE for fact in dynamic_artifacts)
        and any(fact.kind is ArtifactAccessKind.READ for fact in dynamic_artifacts)
    )
    for grouped in artifacts_by_key.values():
        writers = [fact for fact in grouped if fact.kind is ArtifactAccessKind.WRITE]
        readers = [fact for fact in grouped if fact.kind is ArtifactAccessKind.READ]
        if len(writers) > 1:
            artifact_gaps += 1
            continue
        if len(writers) != 1:
            continue
        writer = writers[0]
        for reader in readers:
            relation_candidates += 1
            if writer.owner_symbol_id == reader.owner_symbol_id:
                continue
            source_area = area_by_scope.get(writer.owner_symbol_id)
            target_area = area_by_scope.get(reader.owner_symbol_id)
            if source_area is None or target_area is None:
                continue
            code_ids = tuple(dict.fromkeys((
                writer.control_node_id, reader.control_node_id,
                *((writer.callsite_id,) if writer.callsite_id in graph.nodes else ()),
                *((reader.callsite_id,) if reader.callsite_id in graph.nodes else ()),
            )))[:MAX_SUMMARY_REFERENCES]
            relations.append(SummaryRelation(
                source_area, target_area, SummaryRelationKind.DATA_ARTIFACT,
                min((writer.confidence, reader.confidence),
                    key=_CONFIDENCE_STRENGTH.__getitem__),
                (writer.id, reader.id), code_ids,
                (writer.source_anchor, reader.source_anchor),
            ))
    kept_relations = tuple(relations[:MAX_ARCHITECTURE_RELATIONS])
    omitted = (
        len(candidate_keys) - len(selected_keys)
        + relation_candidates - len(kept_relations)
        + artifact_gaps
        + analysis.human_tasks.omitted_fact_count
        + sum(issue.code in (
            "MODEL_PARAMETER_SHARING_UNRESOLVED",
            "MODEL_PARAMETER_SHARING_TRUNCATED",
        ) for issue in analysis.model.issues)
    )
    return FunctionalArchitecture(
        tuple(areas), kept_relations, omitted > 0, omitted,
    )


def _generic_flow(analysis: ProjectAnalysis, root_id: str) -> TaskFlow | None:
    if analysis.flow is None:
        return None
    graph = analysis.flow.graph
    root = analysis.symbol_index.entries.get(root_id)
    if root is None or root.kind not in _SCOPE_KINDS:
        return None
    raw_nodes = _flow_nodes(graph, root_id)
    if not raw_nodes:
        return None
    responsibility = {
        card.owner_symbol_id: card for card in build_human_summary_base(analysis).responsibilities
    }
    operations = analysis.human_understanding.operations
    groups: list[
        tuple[str, list[CodeNode], tuple[str, ...], str, Confidence, tuple[str, ...]]
    ] = []
    group_kinds: list[Literal["task", "decision", "loop", "result"]] = []
    raw_to_group: dict[str, int] = {}
    for node in raw_nodes:
        if node.kind is NodeKind.CONTROL_BLOCK:
            statement_type = node.metadata.get("statement_type")
            step_kind: Literal["decision", "loop"] = (
                "decision" if statement_type == "If" else "loop"
            )
            title = _control_title(node)
            groups.append((
                title, [node], (root_id,), "unknown", node.confidence, (node.id,),
            ))
            group_kinds.append(step_kind)
            raw_to_group[node.id] = len(groups) - 1
            continue
        calls = _contained_calls(graph, node.id)
        local_targets: list[tuple[CodeNode, Confidence]] = []
        primitives: set[HumanOperationKind] = set()
        confidence = Confidence.EXACT
        evidence_ids: list[str] = []
        for call in calls:
            target, call_confidence = _call_target(graph, call.id)
            confidence = min((confidence, call_confidence), key=_CONFIDENCE_STRENGTH.__getitem__)
            if target is not None and target.id in analysis.symbol_index.entries:
                entry = analysis.symbol_index.entries[target.id]
                if entry.kind in _SCOPE_KINDS:
                    local_targets.append((target, call_confidence))
                    continue
            if target is not None and target.origin is NodeOrigin.EXTERNAL:
                primitive = {
                    "len": HumanOperationKind.COUNT,
                    "sum": HumanOperationKind.SUM,
                    "print": HumanOperationKind.STANDARD_OUTPUT,
                }.get(target.name)
                if primitive is not None:
                    primitives.add(primitive)
        for fact in operations:
            if (fact.owner_symbol_id == root_id
                    and any(anchor.start_line == fact.source_anchor.start_line
                            and anchor.file_path == fact.source_anchor.file_path
                            for anchor in node.source_anchors)):
                evidence_ids.append(fact.id)
        group: tuple[
            str, list[CodeNode], tuple[str, ...], str, Confidence, tuple[str, ...]
        ]
        if local_targets:
            target = local_targets[0][0]
            card = responsibility.get(target.id)
            title = (card.responsibility if card is not None and card.responsibility
                     else "Run local task")
            basis = ("source_author_statement" if card is not None and card.responsibility
                     else "unknown")
            if card is not None and card.author_fact_id is not None:
                evidence_ids.append(card.author_fact_id)
                confidence = min(
                    (confidence, card.confidence),
                    key=_CONFIDENCE_STRENGTH.__getitem__,
                )
            group = (
                title, [node], (target.id, root_id), basis, confidence,
                tuple(dict.fromkeys(evidence_ids)),
            )
        elif primitives & {HumanOperationKind.COUNT, HumanOperationKind.SUM}:
            group = (
                "Calculate statistics", [node], (root_id,), "unknown", confidence,
                tuple(dict.fromkeys(evidence_ids)),
            )
        elif HumanOperationKind.STANDARD_OUTPUT in primitives:
            group = (
                "Display output", [node], (root_id,), "unknown", confidence,
                tuple(dict.fromkeys(evidence_ids)),
            )
        else:
            continue
        if group[0] == "Display output":
            output_index = next(
                (index for index, existing in enumerate(groups)
                 if existing[0] == "Display output"),
                None,
            )
            if output_index is not None:
                existing = groups[output_index]
                existing[1].append(node)
                groups[output_index] = (
                    *existing[:4],
                    min((existing[4], group[4]),
                        key=_CONFIDENCE_STRENGTH.__getitem__),
                    tuple(dict.fromkeys((*existing[5], *group[5]))),
                )
                raw_to_group[node.id] = output_index
                continue
        if (groups and group[0] == "Calculate statistics"
                and groups[-1][0] == "Calculate statistics"):
            groups[-1][1].append(node)
            groups[-1] = (*groups[-1][:5], tuple(dict.fromkeys(
                (*groups[-1][5], *group[5]),
            )))
            raw_to_group[node.id] = len(groups) - 1
            continue
        groups.append(group)
        group_kinds.append("result" if group[0] == "Display output" else "task")
        raw_to_group[node.id] = len(groups) - 1
    steps: list[TaskStep] = []
    for index, (title, nodes, owners, basis, confidence, group_evidence) in enumerate(groups):
        node_tuple = tuple(nodes)
        anchors = _anchors(node_tuple)
        evidence = tuple(dict.fromkeys((
            *group_evidence,
            *(fact.id for fact in operations
              if fact.owner_symbol_id == root_id
              and any(anchor.start_line == fact.source_anchor.start_line
                      and anchor.file_path == fact.source_anchor.file_path
                      for anchor in anchors)),
        )))[:MAX_SUMMARY_REFERENCES] or tuple(
            node.id for node in node_tuple[:MAX_SUMMARY_REFERENCES]
        )
        steps.append(TaskStep(
            _summary_id("step", root_id, *(node.id for node in node_tuple), str(index)),
            group_kinds[index],
            title, basis, confidence, tuple(dict.fromkeys(owners))[:MAX_SUMMARY_REFERENCES],
            evidence, tuple(node.id for node in node_tuple)[:MAX_SUMMARY_REFERENCES],
            anchors[:MAX_SUMMARY_REFERENCES], len(anchors),
        ))
    relations: list[SummaryRelation] = []
    seen_relations: set[tuple[str, str, SummaryRelationKind]] = set()
    for edge in graph.edges:
        kind = _edge_kind(edge.type)
        if kind is None or edge.source not in raw_to_group or edge.target not in raw_to_group:
            continue
        source_index, target_index = raw_to_group[edge.source], raw_to_group[edge.target]
        if source_index == target_index:
            continue
        key = (steps[source_index].id, steps[target_index].id, kind)
        if key in seen_relations:
            continue
        seen_relations.add(key)
        relations.append(SummaryRelation(
            key[0], key[1], kind, edge.confidence, (edge.source, edge.target),
            (edge.source, edge.target),
            (edge.source_anchor,) if edge.source_anchor is not None else (),
        ))
    omitted = max(0, len(steps) - MAX_FLOW_STEPS) + max(
        0, len(relations) - MAX_FLOW_RELATIONS,
    )
    kept_steps = tuple(steps[:MAX_FLOW_STEPS])
    kept_ids = {step.id for step in kept_steps}
    kept_relations = tuple(
        relation for relation in relations
        if relation.source in kept_ids and relation.target in kept_ids
    )[:MAX_FLOW_RELATIONS]
    dropped_relations = len(relations) - len(kept_relations)
    omitted = max(omitted, len(steps) - len(kept_steps) + dropped_relations)
    if not kept_steps:
        return None
    root_card = responsibility.get(root_id)
    return TaskFlow(
        _summary_id("flow", "generic", root_id), root_id,
        root_card.responsibility
        if root_card is not None and root_card.responsibility else "Program workflow",
        kept_steps, kept_relations, omitted > 0, omitted,
    )


def _preferred_task_owners(
    analysis: ProjectAnalysis,
    fact: StaticTaskFact | ArtifactAccessFact,
) -> tuple[str, ...]:
    targets = fact.target_symbol_ids if isinstance(fact, StaticTaskFact) else ()
    preferred = next((
        target for target in targets
        if (entry := analysis.symbol_index.entries.get(target)) is not None
        and entry.kind in _SCOPE_KINDS
    ), None)
    return tuple(dict.fromkeys((preferred, fact.owner_symbol_id))) \
        if preferred is not None else (fact.owner_symbol_id,)


def _task_entry_flow(
    analysis: ProjectAnalysis,
    entry: HumanTaskEntryFact,
) -> TaskFlow | None:
    if analysis.flow is None:
        return None
    graph = analysis.flow.graph
    owner = analysis.symbol_index.entries.get(entry.root_symbol_id)
    if owner is None or owner.kind not in _SCOPE_KINDS:
        return None
    tasks = {fact.id: fact for fact in analysis.human_tasks.tasks}
    artifacts = {fact.id: fact for fact in analysis.human_tasks.artifacts}
    facts: list[StaticTaskFact | ArtifactAccessFact] = []
    omitted = 0
    for fact_id in entry.evidence_fact_ids:
        fact = tasks.get(fact_id) or artifacts.get(fact_id)
        if fact is None:
            omitted += 1
        else:
            facts.append(fact)
    order = {
        node.id: index for index, node in enumerate(_flow_nodes(graph, entry.root_symbol_id))
    }
    generation_order = {
        StaticTaskKind.INPUT_ENCODING: 0,
        StaticTaskKind.TOKEN_SAMPLING: 1,
        StaticTaskKind.OUTPUT_DECODING: 2,
    }
    facts.sort(key=lambda fact: (
        generation_order.get(fact.kind, 3)
        if entry.kind is HumanEntryKind.GENERATION and isinstance(fact, StaticTaskFact)
        else order.get(fact.control_node_id, len(order)),
        fact.id,
    ))
    steps: list[TaskStep] = []
    control_ids: list[str] = []
    step_facts: list[StaticTaskFact | ArtifactAccessFact] = []
    for fact in facts:
        control = graph.nodes.get(fact.control_node_id)
        if control is None:
            omitted += 1
            continue
        if isinstance(fact, StaticTaskFact):
            title = _TASK_TITLES[fact.kind]
            if (fact.kind is StaticTaskKind.OUTPUT_DECODING
                    and any(operation.kind is HumanOperationKind.STANDARD_OUTPUT
                            and operation.owner_symbol_id == fact.owner_symbol_id
                            and operation.source_anchor.file_path == fact.source_anchor.file_path
                            and operation.source_anchor.start_line == fact.source_anchor.start_line
                            for operation in analysis.human_understanding.operations)):
                title = "Decode and display output"
            basis = f"static_task:{fact.kind.value}"
            code_ids = tuple(dict.fromkeys((
                fact.control_node_id,
                *(callsite for callsite in fact.callsite_ids if callsite in graph.nodes),
            )))[:MAX_SUMMARY_REFERENCES]
            kind: Literal["task", "result"] = (
                "result" if fact.kind is StaticTaskKind.OUTPUT_DECODING else "task"
            )
        else:
            title = ("Read data artifact" if fact.kind is ArtifactAccessKind.READ
                     else "Write data artifact")
            basis = f"static_task:artifact_{fact.kind.value}"
            code_ids = tuple(dict.fromkeys((
                fact.control_node_id,
                *((fact.callsite_id,) if fact.callsite_id in graph.nodes else ()),
            )))[:MAX_SUMMARY_REFERENCES]
            kind = "result" if fact.kind is ArtifactAccessKind.WRITE else "task"
        if not code_ids:
            omitted += 1
            continue
        steps.append(TaskStep(
            _summary_id("step", entry.id, fact.id), kind, title, basis,
            fact.confidence, _preferred_task_owners(analysis, fact),
            (fact.id,), code_ids, (fact.source_anchor,), 1,
        ))
        control_ids.append(fact.control_node_id)
        step_facts.append(fact)
    relations: list[SummaryRelation] = []
    for index in range(len(steps) - 1):
        path = _control_path(graph, control_ids[index], control_ids[index + 1])
        if path is None:
            source_fact, target_fact = step_facts[index], step_facts[index + 1]
            if (entry.kind is HumanEntryKind.GENERATION
                    and isinstance(source_fact, StaticTaskFact)
                    and isinstance(target_fact, StaticTaskFact)
                    and source_fact.kind is StaticTaskKind.TOKEN_SAMPLING
                    and target_fact.kind is StaticTaskKind.OUTPUT_DECODING):
                incoming = [
                    edge for edge in graph.edges
                    if edge.type is EdgeType.CALLS
                    and edge.target == source_fact.owner_symbol_id
                    and (call := graph.nodes.get(edge.source)) is not None
                    and call.kind is NodeKind.CALL_SITE
                    and call.metadata.get("scope_symbol_id") == target_fact.owner_symbol_id
                ]
                if len(incoming) == 1:
                    call_edge = incoming[0]
                    source_statement = _control_statement_id(graph, call_edge.source)
                    target_statement = target_fact.control_node_id
                    data_ids = _statement_data_witness(
                        graph, source_statement, target_statement,
                    )
                    caller_path = _control_path(
                        graph, source_statement, target_statement,
                    )
                    if (data_ids and caller_path is not None
                            and not any(edge.type is EdgeType.LOOP_BACK
                                        for edge in caller_path)):
                        special = next((edge for edge in caller_path if edge.type in (
                            EdgeType.BRANCH_TRUE, EdgeType.BRANCH_FALSE,
                        )), caller_path[0])
                        relations.append(SummaryRelation(
                            steps[index].id, steps[index + 1].id,
                            _edge_kind(special.type)
                            or SummaryRelationKind.CONTROL_NEXT,
                            min((
                                source_fact.confidence, target_fact.confidence,
                                call_edge.confidence,
                                *(edge.confidence for edge in caller_path),
                            ), key=_CONFIDENCE_STRENGTH.__getitem__),
                            (source_fact.id, target_fact.id),
                            tuple(dict.fromkeys((
                                call_edge.source, source_statement, target_statement,
                                *data_ids,
                                *(item for item in target_fact.callsite_ids
                                  if item in graph.nodes),
                            )))[:MAX_SUMMARY_REFERENCES],
                            tuple(dict.fromkeys((
                                source_fact.source_anchor, target_fact.source_anchor,
                                *(edge.source_anchor for edge in caller_path
                                  if edge.source_anchor is not None),
                            )))[:MAX_SUMMARY_REFERENCES],
                        ))
                        continue
            if (entry.kind is HumanEntryKind.GENERATION
                    and isinstance(source_fact, StaticTaskFact)
                    and isinstance(target_fact, StaticTaskFact)
                    and source_fact.kind is StaticTaskKind.INPUT_ENCODING
                    and target_fact.kind is StaticTaskKind.TOKEN_SAMPLING
                    and target_fact.owner_symbol_id in source_fact.target_symbol_ids):
                relations.append(SummaryRelation(
                    steps[index].id, steps[index + 1].id,
                    SummaryRelationKind.STATIC_CALL,
                    min((source_fact.confidence, target_fact.confidence),
                        key=_CONFIDENCE_STRENGTH.__getitem__),
                    (source_fact.id, target_fact.id),
                    tuple(dict.fromkeys((
                        *(item for item in source_fact.callsite_ids if item in graph.nodes),
                        target_fact.control_node_id,
                    )))[:MAX_SUMMARY_REFERENCES],
                    (source_fact.source_anchor, target_fact.source_anchor),
                ))
                continue
            omitted += 1
            continue
        special = next((edge for edge in path if edge.type in (
            EdgeType.BRANCH_TRUE, EdgeType.BRANCH_FALSE, EdgeType.LOOP_BACK,
        )), path[0])
        relation_kind = _edge_kind(special.type) or SummaryRelationKind.CONTROL_NEXT
        relations.append(SummaryRelation(
            steps[index].id, steps[index + 1].id, relation_kind,
            min((edge.confidence for edge in path), key=_CONFIDENCE_STRENGTH.__getitem__),
            tuple(dict.fromkeys((
                *steps[index].evidence_ids, *steps[index + 1].evidence_ids,
            )))[:MAX_SUMMARY_REFERENCES],
            tuple(dict.fromkeys(
                item for edge in path for item in (edge.source, edge.target)
                if item in graph.nodes
            ))[:MAX_SUMMARY_REFERENCES],
            tuple(dict.fromkeys(
                edge.source_anchor for edge in path if edge.source_anchor is not None
            ))[:MAX_SUMMARY_REFERENCES],
        ))
    omitted += max(0, len(steps) - MAX_FLOW_STEPS)
    kept_steps = tuple(steps[:MAX_FLOW_STEPS])
    kept_ids = {step.id for step in kept_steps}
    available_relations = [
        relation for relation in relations
        if relation.source in kept_ids and relation.target in kept_ids
    ]
    omitted += len(relations) - len(available_relations)
    omitted += max(0, len(available_relations) - MAX_FLOW_RELATIONS)
    if not kept_steps:
        return None
    return TaskFlow(
        _summary_id("flow", entry.kind.value, entry.root_symbol_id),
        entry.root_symbol_id,
        "Generate output" if entry.kind is HumanEntryKind.GENERATION else "Prepare data",
        kept_steps, tuple(available_relations[:MAX_FLOW_RELATIONS]),
        omitted > 0, omitted,
    )


def _training_flow(analysis: ProjectAnalysis, entry_id: str) -> TaskFlow | None:
    if analysis.training is None or analysis.flow is None:
        return None
    graph = analysis.flow.graph
    entry = next((item for item in analysis.training.entry_points
                  if item.id == entry_id), None)
    owner = analysis.symbol_index.entries.get(entry_id)
    if (entry is None or owner is None or owner.kind not in _SCOPE_KINDS
            or entry.source_anchor != owner.source_anchor):
        return None
    all_events = {event.id: event for event in analysis.training.events}
    selected_ids = {event_id for event_id in entry.flow_node_ids if event_id in all_events}
    events = {event_id: all_events[event_id] for event_id in selected_ids}
    joins = {join.id: join for join in analysis.training.joins
             if join.entry_point_id == entry_id}

    def predecessor_events(predecessor_id: str, seen: set[str] | None = None) -> set[str]:
        if predecessor_id in events:
            return {predecessor_id}
        join = joins.get(predecessor_id)
        if join is None:
            return set()
        visited = set() if seen is None else seen
        if predecessor_id in visited:
            return set()
        visited.add(predecessor_id)
        return set().union(*(
            predecessor_events(item, visited) for item in join.control_predecessor_ids
        )) if join.control_predecessor_ids else set()

    dependencies = {
        event.id: set().union(*(
            predecessor_events(item) for item in event.control_predecessor_ids
        )) if event.control_predecessor_ids else set()
        for event in events.values()
    }
    ordered_ids: list[str] = []
    remaining = set(events)
    while remaining:
        ready = sorted(
            (event_id for event_id in remaining
             if not (dependencies[event_id] & remaining)),
            key=lambda event_id: (events[event_id].role.value, event_id),
        )
        if not ready:
            ready = [min(remaining)]
        ordered_ids.extend(ready)
        remaining.difference_update(ready)

    entry_flow_nodes = _flow_nodes(graph, entry_id)
    entry_flow_node_ids = {node.id for node in entry_flow_nodes}
    evaluation_occurrences: list[_EvaluationOccurrence] = []
    evaluation_gaps = 0
    evaluation_facts = sorted(
        (fact for fact in analysis.human_tasks.tasks
         if fact.kind is StaticTaskKind.EVALUATION_HELPER
         and fact.owner_symbol_id in analysis.symbol_index.entries),
        key=lambda fact: fact.id,
    )
    for fact in evaluation_facts:
        incoming_edges = sorted(
            (edge for edge in graph.edges
             if edge.type is EdgeType.CALLS and edge.target == fact.owner_symbol_id
             and (call := graph.nodes.get(edge.source)) is not None
             and call.kind is NodeKind.CALL_SITE
             and call.metadata.get("scope_symbol_id") == entry_id
             and _control_statement_id(graph, call.id) in entry_flow_node_ids),
            key=lambda edge: edge.source,
        )
        fact_calls = [callsite for callsite in fact.callsite_ids if callsite in graph.nodes]
        non_mode_calls = [
            callsite for callsite in fact_calls
            if graph.nodes[callsite].name not in ("eval", "train")
        ]
        data_pairs = [
            (source_call, target_call)
            for source_call in non_mode_calls for target_call in non_mode_calls
            if source_call != target_call
            and _call_data_dependency(graph, source_call, target_call)
            and _control_path(
                graph, _control_statement_id(graph, source_call),
                _control_statement_id(graph, target_call),
            ) is not None
        ]
        model_call_id, loss_call_id = data_pairs[0] if len(data_pairs) == 1 else (None, None)
        for incoming_edge in incoming_edges:
            incoming_call = graph.nodes[incoming_edge.source]
            evaluation_step_id = _summary_id(
                "step", entry_id, fact.id, incoming_call.id, "evaluation",
            )
            loss_step_id = (_summary_id(
                "step", entry_id, fact.id, incoming_call.id, "loss",
            ) if loss_call_id is not None else None)
            evaluation_occurrences.append(_EvaluationOccurrence(
                fact, incoming_call, incoming_edge,
                _control_statement_id(graph, incoming_call.id),
                model_call_id, loss_call_id, evaluation_step_id, loss_step_id,
            ))
            if loss_step_id is None:
                evaluation_gaps += 1

    decision_ids = tuple(dict.fromkeys((
        control_id for event_id in ordered_ids
        for control_id, _ in events[event_id].branch_path
        if control_id in graph.nodes
    )))
    evaluation_decision_ids: list[str] = []
    for occurrence in evaluation_occurrences:
        enclosing_branches = [
            (len(path), node.id)
            for node in entry_flow_nodes
            if node.kind is NodeKind.CONTROL_BLOCK
            and (path := _control_path(
                graph, node.id, occurrence.caller_statement_id,
            )) is not None
            and path
            and path[0].type in (EdgeType.BRANCH_TRUE, EdgeType.BRANCH_FALSE)
        ]
        if enclosing_branches:
            evaluation_decision_ids.append(min(enclosing_branches)[1])
    evaluation_decisions = tuple(dict.fromkeys(evaluation_decision_ids))
    decision_ids = tuple(dict.fromkeys((*decision_ids, *evaluation_decisions)))
    candidates: list[TaskStep] = []
    step_by_event: dict[str, str] = {}
    step_by_control: dict[str, str] = {}
    step_by_task: dict[str, str] = {}
    omitted = len(entry.flow_node_ids) - len(events) + evaluation_gaps
    for control_id in decision_ids:
        node = graph.nodes[control_id]
        scope_id = node.metadata.get("scope_symbol_id")
        owner_id = scope_id if type(scope_id) is str and scope_id in analysis.symbol_index.entries \
            else entry_id
        anchors = node.source_anchors
        if not anchors:
            omitted += 1
            continue
        step_id = _summary_id("step", entry_id, "decision", control_id)
        step_by_control[control_id] = step_id
        candidates.append(TaskStep(
            step_id, "decision", _control_title(node), "unknown",
            node.confidence, (owner_id,), (control_id,), (control_id,),
            anchors[:MAX_SUMMARY_REFERENCES], len(anchors),
        ))
    for event_id in ordered_ids:
        event = events[event_id]
        if event.source_node_id is None or event.source_node_id not in graph.nodes:
            omitted += 1
            continue
        owners: tuple[str, ...] = (entry_id,)
        if (event.role is TrainingRole.FORWARD_PASS and entry.model_ids
                and entry.model_ids[0] in analysis.symbol_index.entries
                and analysis.symbol_index.entries[entry.model_ids[0]].kind in _SCOPE_KINDS):
            owners = (entry.model_ids[0], entry_id)
        step_id = _summary_id("step", entry_id, event.id)
        step_by_event[event.id] = step_id
        event_node = graph.nodes[event.source_node_id]
        event_title = _TRAINING_TITLES[event.role]
        event_basis = f"training:{event.role.value}"
        if (event.role is TrainingRole.BATCH_LOOP
                and event_node.kind is NodeKind.CONTROL_BLOCK
                and event_node.metadata.get("statement_type") == "While"):
            event_title = _control_title(event_node)
            event_basis = "unknown"
        candidates.append(TaskStep(
            step_id,
            "loop" if event.role in (TrainingRole.EPOCH_LOOP, TrainingRole.BATCH_LOOP)
            else "result" if event.role in (
                TrainingRole.CHECKPOINT_SAVE, TrainingRole.PARAMETER_UPDATE,
            ) else "task",
            event_title, event_basis,
            event.confidence, owners, (event.id,), (event.source_node_id,),
            (event.source_anchor,), 1,
        ))
    entry_tasks = [
        fact for fact in analysis.human_tasks.tasks
        if fact.owner_symbol_id == entry_id
    ]
    for fact in entry_tasks:
        if fact.control_node_id not in graph.nodes:
            omitted += 1
            continue
        step_id = _summary_id("step", entry_id, fact.id)
        step_by_task[fact.id] = step_id
        code_ids = tuple(dict.fromkeys((
            fact.control_node_id,
            *(item for item in fact.callsite_ids if item in graph.nodes),
        )))[:MAX_SUMMARY_REFERENCES]
        candidates.append(TaskStep(
            step_id,
            "loop" if fact.kind is StaticTaskKind.GRADIENT_ACCUMULATION else "task",
            _TASK_TITLES[fact.kind], f"static_task:{fact.kind.value}",
            fact.confidence, _preferred_task_owners(analysis, fact),
            (fact.id,), code_ids, (fact.source_anchor,), 1,
        ))
    for occurrence in evaluation_occurrences:
        fact = occurrence.fact
        confidence = min(
            (fact.confidence, occurrence.call_edge.confidence),
            key=_CONFIDENCE_STRENGTH.__getitem__,
        )
        evaluation_code_ids = tuple(dict.fromkeys((
            occurrence.incoming_call.id,
            fact.control_node_id,
            *((occurrence.model_call_id,) if occurrence.model_call_id is not None else ()),
            *(item for item in fact.callsite_ids if item in graph.nodes),
        )))[:MAX_SUMMARY_REFERENCES]
        evaluation_anchors = tuple(dict.fromkeys((
            *occurrence.incoming_call.source_anchors,
            fact.source_anchor,
            *(anchor for node_id in evaluation_code_ids
              for anchor in graph.nodes[node_id].source_anchors),
        )))
        candidates.append(TaskStep(
            occurrence.evaluation_step_id, "task", "Evaluate model",
            f"static_task:{fact.kind.value}", confidence,
            (fact.owner_symbol_id, entry_id), (fact.id,), evaluation_code_ids,
            evaluation_anchors[:MAX_SUMMARY_REFERENCES], len(evaluation_anchors),
        ))
        if occurrence.loss_step_id is not None and occurrence.loss_call_id is not None:
            loss_node = graph.nodes[occurrence.loss_call_id]
            loss_code_ids = (occurrence.incoming_call.id, occurrence.loss_call_id)
            loss_anchors = tuple(dict.fromkeys((
                *occurrence.incoming_call.source_anchors, *loss_node.source_anchors,
            )))
            candidates.append(TaskStep(
                occurrence.loss_step_id, "result", "Collect evaluation loss",
                f"static_task:{fact.kind.value}", confidence,
                (fact.owner_symbol_id, entry_id), (fact.id,), loss_code_ids,
                loss_anchors[:MAX_SUMMARY_REFERENCES], len(loss_anchors),
            ))
    event_ranks = {
        step_by_event[event_id]: index * 3 + 1
        for index, event_id in enumerate(ordered_ids)
        if event_id in step_by_event
    }
    candidate_ranks: dict[str, float] = {
        step_id: float(rank) for step_id, rank in event_ranks.items()
    }
    for control_id, step_id in step_by_control.items():
        dependent_ranks = [
            event_ranks[step_by_event[event.id]]
            for event in events.values()
            if step_by_event.get(event.id) in event_ranks
            and any(item == control_id for item, _ in event.branch_path)
        ]
        candidate_ranks[step_id] = min(dependent_ranks, default=0) - 1
    for fact in entry_tasks:
        task_step = step_by_task.get(fact.id)
        if task_step is None:
            continue
        contained_ranks = [
            event_ranks[step_by_event[event.id]]
            for event in events.values()
            if step_by_event.get(event.id) in event_ranks
            and event.source_node_id is not None
            and (path := _control_path(
                graph, fact.control_node_id,
                _control_statement_id(graph, event.source_node_id),
            )) is not None
            and any(edge.type is EdgeType.BRANCH_TRUE for edge in path)
        ]
        candidate_ranks[task_step] = min(
            contained_ranks, default=len(ordered_ids) * 3 + 1,
        ) - 1
    for occurrence in evaluation_occurrences:
        successor_ranks = [
            event_ranks[step_by_event[event.id]]
            for event in events.values()
            if step_by_event.get(event.id) in event_ranks
            and event.source_node_id is not None
            and _control_path(
                graph, occurrence.caller_statement_id,
                _control_statement_id(graph, event.source_node_id),
            ) is not None
        ]
        successor_rank = min(successor_ranks, default=len(ordered_ids) * 3 + 1)
        evaluation_rank = successor_rank - 0.6
        candidate_ranks[occurrence.evaluation_step_id] = evaluation_rank
        if occurrence.loss_step_id is not None:
            candidate_ranks[occurrence.loss_step_id] = successor_rank - 0.3
        for control_id, control_step_id in step_by_control.items():
            path = _control_path(graph, control_id, occurrence.caller_statement_id)
            if path is not None and any(
                edge.type in (EdgeType.BRANCH_TRUE, EdgeType.BRANCH_FALSE)
                for edge in path
            ):
                candidate_ranks[control_step_id] = min(
                    candidate_ranks.get(control_step_id, evaluation_rank - 1),
                    evaluation_rank - 1,
                )
    candidates.sort(key=lambda step: (candidate_ranks.get(step.id, 0), step.id))
    kept_steps = tuple(candidates[:MAX_FLOW_STEPS])
    kept_ids = {step.id for step in kept_steps}
    omitted += len(candidates) - len(kept_steps)
    relations: list[SummaryRelation] = []

    def add_relation(
        source: str | None,
        target: str | None,
        kind: SummaryRelationKind,
        evidence: tuple[str, ...],
        confidence: Confidence,
    ) -> None:
        nonlocal omitted
        if source is None or target is None or source == target:
            omitted += 1
            return
        if source not in kept_ids or target not in kept_ids:
            omitted += 1
            return
        source_event = next((events[item] for item in evidence if item in events), None)
        target_event = next((events[item] for item in reversed(evidence) if item in events), None)
        code_ids = tuple(dict.fromkeys(
            event.source_node_id for event in (source_event, target_event)
            if event is not None and event.source_node_id in graph.nodes
        ))
        anchors = tuple(dict.fromkeys(
            event.source_anchor for event in (source_event, target_event)
            if event is not None
        ))
        relation = SummaryRelation(
            source, target, kind, confidence,
            tuple(dict.fromkeys(evidence))[:MAX_SUMMARY_REFERENCES],
            code_ids[:MAX_SUMMARY_REFERENCES], anchors[:MAX_SUMMARY_REFERENCES],
        )
        if relation not in relations:
            relations.append(relation)

    def add_cfg_relation(
        source: str | None,
        target: str | None,
        path: tuple[CodeEdge, ...],
        evidence: tuple[str, ...],
        confidence: Confidence,
        kind: SummaryRelationKind | None = None,
    ) -> None:
        nonlocal omitted
        if (source is None or target is None or source == target
                or source not in kept_ids or target not in kept_ids or not path):
            omitted += 1
            return
        special = next((edge for edge in path if edge.type in (
            EdgeType.BRANCH_TRUE, EdgeType.BRANCH_FALSE, EdgeType.LOOP_BACK,
        )), path[0])
        relation_kind = kind or _edge_kind(special.type) or SummaryRelationKind.CONTROL_NEXT
        relation = SummaryRelation(
            source, target, relation_kind, confidence,
            tuple(dict.fromkeys(evidence))[:MAX_SUMMARY_REFERENCES],
            tuple(dict.fromkeys(
                item for edge in path for item in (edge.source, edge.target)
                if item in graph.nodes
            ))[:MAX_SUMMARY_REFERENCES],
            tuple(dict.fromkeys(
                edge.source_anchor for edge in path if edge.source_anchor is not None
            ))[:MAX_SUMMARY_REFERENCES],
        )
        if relation not in relations:
            relations.append(relation)

    for event_id in ordered_ids:
        event = events[event_id]
        target = step_by_event.get(event.id)
        for predecessor_id in event.control_predecessor_ids:
            for predecessor in sorted(predecessor_events(predecessor_id)):
                source_event = events[predecessor]
                add_relation(
                    step_by_event.get(predecessor), target,
                    SummaryRelationKind.CONTROL_NEXT,
                    (predecessor, event.id),
                    min((source_event.confidence, event.confidence),
                        key=_CONFIDENCE_STRENGTH.__getitem__),
                )
        if event.parent_loop_id is not None and event.parent_loop_id in events:
            parent = events[event.parent_loop_id]
            add_relation(
                step_by_event.get(parent.id), target, SummaryRelationKind.CONTAINS,
                (parent.id, event.id),
                min((parent.confidence, event.confidence),
                    key=_CONFIDENCE_STRENGTH.__getitem__),
            )
        for predecessor in event.loop_back_predecessor_ids:
            if predecessor in events:
                source_event = events[predecessor]
                add_relation(
                    step_by_event.get(predecessor), target,
                    SummaryRelationKind.LOOP_BACK, (predecessor, event.id),
                    min((source_event.confidence, event.confidence),
                        key=_CONFIDENCE_STRENGTH.__getitem__),
                )
        for control_id, branch in event.branch_path:
            add_relation(
                step_by_control.get(control_id), target,
                SummaryRelationKind.BRANCH_TRUE if branch else SummaryRelationKind.BRANCH_FALSE,
                (event.id,), event.confidence,
            )
    container_step_by_node = dict(step_by_control)
    for event in events.values():
        if (event.source_node_id is not None
                and event.source_node_id in graph.nodes
                and graph.nodes[event.source_node_id].kind is NodeKind.CONTROL_BLOCK
                and event.id in step_by_event):
            container_step_by_node[event.source_node_id] = step_by_event[event.id]
    hierarchy_children = [
        (child_id, child_step, child_id, graph.nodes[child_id].confidence)
        for child_id, child_step in step_by_control.items()
        if child_id in graph.nodes
    ]
    hierarchy_children.extend(
        (fact.control_node_id, task_step, fact.id, fact.confidence)
        for fact in entry_tasks
        if (task_step := step_by_task.get(fact.id)) is not None
        and fact.control_node_id in graph.nodes
    )
    for child_id, child_step, child_evidence, child_confidence in hierarchy_children:
        child_node = graph.nodes[child_id]
        if len(child_node.source_anchors) != 1:
            omitted += 1
            continue
        child_anchor = child_node.source_anchors[0]
        parents: list[tuple[int, int, str, tuple[CodeEdge, ...]]] = []
        for parent_node in entry_flow_nodes:
            if (parent_node.kind is not NodeKind.CONTROL_BLOCK
                    or len(parent_node.source_anchors) != 1
                    or not _strict_anchor_contains(
                        parent_node.source_anchors[0], child_anchor,
                    )):
                continue
            path = _control_path(graph, parent_node.id, child_id)
            if (path is None or not path
                    or path[0].type not in (EdgeType.BRANCH_TRUE, EdgeType.BRANCH_FALSE)
                    or any(edge.type is EdgeType.LOOP_BACK for edge in path)):
                continue
            anchor = parent_node.source_anchors[0]
            parents.append((
                (anchor.end_line or anchor.start_line) - anchor.start_line,
                ((anchor.end_column or 0) - (anchor.start_column or 0)
                 if anchor.end_line == anchor.start_line else 0),
                parent_node.id, path,
            ))
        if not parents:
            continue
        _, _, parent_id, parent_path = min(parents)
        parent_step = container_step_by_node.get(parent_id)
        if parent_step is None:
            omitted += 1
            continue
        add_cfg_relation(
            parent_step, child_step, parent_path, (parent_id, child_evidence),
            min((graph.nodes[parent_id].confidence, child_confidence),
                key=_CONFIDENCE_STRENGTH.__getitem__),
            SummaryRelationKind.CONTAINS,
        )
    for occurrence in evaluation_occurrences:
        fact = occurrence.fact
        occurrence_confidence = min(
            (fact.confidence, occurrence.call_edge.confidence),
            key=_CONFIDENCE_STRENGTH.__getitem__,
        )
        decision_paths = [
            (control_id, path)
            for control_id in decision_ids
            if (path := _control_path(
                graph, control_id, occurrence.caller_statement_id,
            )) is not None
            and any(edge.type in (EdgeType.BRANCH_TRUE, EdgeType.BRANCH_FALSE)
                    for edge in path)
        ]
        if decision_paths:
            control_id, path = min(decision_paths, key=lambda item: len(item[1]))
            add_cfg_relation(
                step_by_control.get(control_id), occurrence.evaluation_step_id,
                path, (control_id, fact.id), occurrence_confidence,
            )
        else:
            omitted += 1
        if (occurrence.loss_step_id is not None
                and occurrence.model_call_id is not None
                and occurrence.loss_call_id is not None):
            internal_path = _control_path(
                graph, _control_statement_id(graph, occurrence.model_call_id),
                _control_statement_id(graph, occurrence.loss_call_id),
            )
            if internal_path is not None:
                add_cfg_relation(
                    occurrence.evaluation_step_id, occurrence.loss_step_id,
                    internal_path, (fact.id,), occurrence_confidence,
                    SummaryRelationKind.CONTROL_NEXT,
                )
            else:
                omitted += 1
        successor_paths = [
            (event, path)
            for event in events.values()
            if event.source_node_id is not None
            and (path := _control_path(
                graph, occurrence.caller_statement_id,
                _control_statement_id(graph, event.source_node_id),
            )) is not None
            and not any(edge.type is EdgeType.LOOP_BACK for edge in path)
        ]
        if successor_paths:
            successor, path = min(
                successor_paths,
                key=lambda item: (ordered_ids.index(item[0].id), len(item[1])),
            )
            add_cfg_relation(
                occurrence.loss_step_id or occurrence.evaluation_step_id,
                step_by_event.get(successor.id), path,
                (fact.id, successor.id),
                min((occurrence_confidence, successor.confidence),
                    key=_CONFIDENCE_STRENGTH.__getitem__),
            )
        else:
            omitted += 1
    for fact in entry_tasks:
        if fact.kind is not StaticTaskKind.GRADIENT_ACCUMULATION:
            continue
        task_step = step_by_task.get(fact.id)
        if task_step is None:
            continue
        task_parent_node = graph.nodes.get(fact.control_node_id)
        if (task_parent_node is None
                or task_parent_node.kind is not NodeKind.CONTROL_BLOCK
                or len(task_parent_node.source_anchors) != 1):
            omitted += 1
            continue
        child_count = 0
        for event_id in ordered_ids:
            event = events[event_id]
            event_step = step_by_event.get(event.id)
            if event.source_node_id is None or event_step is None:
                continue
            task_event_node = graph.nodes.get(event.source_node_id)
            if (task_event_node is None or len(task_event_node.source_anchors) != 1
                    or not _strict_anchor_contains(
                        task_parent_node.source_anchors[0],
                        task_event_node.source_anchors[0],
                    )):
                continue
            path = _control_path(
                graph, fact.control_node_id,
                _control_statement_id(graph, event.source_node_id),
            )
            if (path is None or not path or path[0].type is not EdgeType.BRANCH_TRUE
                    or any(edge.type is EdgeType.LOOP_BACK for edge in path)):
                omitted += 1
                continue
            add_cfg_relation(
                task_step, event_step, path, (fact.id, event.id),
                min((fact.confidence, event.confidence),
                    key=_CONFIDENCE_STRENGTH.__getitem__),
                SummaryRelationKind.CONTAINS,
            )
            child_count += 1
        if child_count == 0:
            omitted += 1
    omitted += max(0, len(relations) - MAX_FLOW_RELATIONS)
    if not kept_steps:
        return None
    title = {
        "Train": "Train model",
        "Evaluate": "Evaluate model",
        "Inference": "Run model inference",
        "Export": "Export model",
    }.get(entry.kind.value, "Model workflow")
    return TaskFlow(
        _summary_id("flow", "training", entry_id), entry_id, title,
        kept_steps, tuple(relations[:MAX_FLOW_RELATIONS]), omitted > 0, omitted,
    )


def _scope_belongs_to(
    analysis: ProjectAnalysis,
    scope_id: str,
    ancestor_id: str,
) -> bool:
    current = analysis.symbol_index.entries.get(scope_id)
    seen: set[str] = set()
    while current is not None and current.id not in seen:
        if current.id == ancestor_id:
            return True
        seen.add(current.id)
        current = analysis.symbol_index.entries.get(current.parent_symbol or "")
    return False


def _model_flow(analysis: ProjectAnalysis, definition_id: str) -> TaskFlow | None:
    if analysis.model is None or analysis.flow is None:
        return None
    graph = analysis.flow.graph
    definition = next((item for item in analysis.model.definitions
                       if item.symbol_id == definition_id), None)
    if definition is None or definition_id not in graph.nodes:
        return None
    owned_instances = [item for item in analysis.model.instances
                       if item.owner_definition_id == definition_id]
    owned_tasks = [item for item in analysis.human_tasks.tasks
                   if _scope_belongs_to(analysis, item.owner_symbol_id, definition_id)]
    if not owned_instances and not owned_tasks:
        return None
    instance_by_id = {item.id: item for item in owned_instances}
    shared_ids = list(dict.fromkeys(
        item for instance in owned_instances
        for item in (instance.id, instance.shared_with_id)
        if item is not None and item in instance_by_id
    ))
    parameter_ids = list(dict.fromkeys(
        item for fact in analysis.model.parameter_sharings
        if fact.model_definition_id == definition_id
        for item in (fact.assigned_instance_id, fact.source_instance_id)
        if item in instance_by_id
    ))
    ordered_instances = [
        instance_by_id[item] for item in dict.fromkeys((
            *shared_ids, *parameter_ids,
            *(item.id for item in sorted(
                owned_instances,
                key=lambda value: (
                    value.source_anchor.file_path, value.source_anchor.start_line,
                    value.source_anchor.start_column
                    if value.source_anchor.start_column is not None else -1,
                    value.id,
                ),
            )),
        ))
    ]
    steps: list[TaskStep] = [TaskStep(
        _summary_id("step", definition_id, "model-root"), "task",
        "Model structure", "model:definition", definition.confidence,
        (definition_id,), (definition_id,), (definition_id,),
        (definition.source_anchor,), 1,
    )]
    step_by_instance: dict[str, str] = {}
    for instance in ordered_instances:
        code_ids = _code_nodes_for_anchor(
            analysis, instance.source_anchor, instance.owner_definition_id,
        )
        if not code_ids:
            continue
        preferred = instance.target_definition_id
        owner_id = (preferred if preferred in analysis.symbol_index.entries
                    and analysis.symbol_index.entries[preferred].kind in _SCOPE_KINDS
                    else definition_id)
        step_id = _summary_id("step", definition_id, instance.id)
        step_by_instance[instance.id] = step_id
        steps.append(TaskStep(
            step_id, "task", _model_area_title(instance),
            f"model:{instance.layer_type}", instance.confidence,
            tuple(dict.fromkeys((owner_id, definition_id))),
            (instance.id,), code_ids, (instance.source_anchor,), 1, instance.name,
        ))
    step_by_task: dict[str, str] = {}
    for fact in sorted(owned_tasks, key=lambda item: item.id):
        if fact.control_node_id not in graph.nodes:
            continue
        step_id = _summary_id("step", definition_id, fact.id)
        step_by_task[fact.id] = step_id
        code_ids = tuple(dict.fromkeys((
            fact.control_node_id,
            *(item for item in fact.callsite_ids if item in graph.nodes),
        )))[:MAX_SUMMARY_REFERENCES]
        steps.append(TaskStep(
            step_id, "task", _TASK_TITLES[fact.kind],
            f"static_task:{fact.kind.value}", fact.confidence,
            _preferred_task_owners(analysis, fact), (fact.id,), code_ids,
            (fact.source_anchor,), 1,
        ))
    kept_steps = tuple(steps[:MAX_FLOW_STEPS])
    kept_ids = {step.id for step in kept_steps}
    omitted = len(steps) - len(kept_steps)
    root_step_id = steps[0].id
    relations: list[SummaryRelation] = []
    invocations_by_instance: dict[str, list[ModuleInvocation]] = {}
    for invocation in analysis.model.invocations:
        if invocation.instance_id in step_by_instance:
            invocations_by_instance.setdefault(invocation.instance_id, []).append(invocation)
    for instance in ordered_instances:
        target_step = step_by_instance.get(instance.id)
        if target_step not in kept_ids:
            continue
        invocations = invocations_by_instance.get(instance.id, [])
        if invocations:
            code_ids = tuple(dict.fromkeys(
                item.call_site_id for item in invocations
                if item.call_site_id in graph.nodes
            ))[:MAX_SUMMARY_REFERENCES]
            anchors = tuple(dict.fromkeys(
                item.source_anchor for item in invocations
            ))[:MAX_SUMMARY_REFERENCES]
            relations.append(SummaryRelation(
                root_step_id, target_step, SummaryRelationKind.MODEL_USE,
                min((item.confidence for item in invocations),
                    key=_CONFIDENCE_STRENGTH.__getitem__),
                tuple(item.id for item in invocations[:MAX_SUMMARY_REFERENCES]),
                code_ids, anchors,
            ))
        else:
            relations.append(SummaryRelation(
                root_step_id, target_step, SummaryRelationKind.CONTAINS,
                instance.confidence, (instance.id,),
                _code_nodes_for_anchor(
                    analysis, instance.source_anchor, instance.owner_definition_id,
                ), (instance.source_anchor,),
            ))
        for member_id in instance.member_instance_ids:
            member_step = step_by_instance.get(member_id or "")
            if (member_id is not None and member_step is not None
                    and member_step in kept_ids):
                relations.append(SummaryRelation(
                    target_step, member_step, SummaryRelationKind.CONTAINS,
                    instance.confidence, (instance.id, member_id),
                    _code_nodes_for_anchor(
                        analysis, instance.source_anchor, instance.owner_definition_id,
                    ), (instance.source_anchor,),
                ))
    for instance in owned_instances:
        if (instance.shared_with_id is not None
                and instance.id in step_by_instance
                and instance.shared_with_id in step_by_instance
                and step_by_instance[instance.id] in kept_ids
                and step_by_instance[instance.shared_with_id] in kept_ids):
            relations.append(SummaryRelation(
                step_by_instance[instance.id], step_by_instance[instance.shared_with_id],
                SummaryRelationKind.SHARED, instance.confidence,
                (instance.id, instance.shared_with_id),
                _code_nodes_for_anchor(
                    analysis, instance.source_anchor, instance.owner_definition_id,
                ), (instance.source_anchor,), "module_instance", None,
            ))
    for sharing_fact in analysis.model.parameter_sharings:
        source = step_by_instance.get(sharing_fact.assigned_instance_id)
        target = step_by_instance.get(sharing_fact.source_instance_id)
        if source in kept_ids and target in kept_ids:
            relations.append(SummaryRelation(
                source, target, SummaryRelationKind.SHARED, sharing_fact.confidence,
                (sharing_fact.id,), (), (sharing_fact.source_anchor,), "parameter",
                (sharing_fact.assigned_attribute, sharing_fact.source_attribute),
            ))
    for fact_id, task_step in step_by_task.items():
        if task_step in kept_ids:
            relations.append(SummaryRelation(
                root_step_id, task_step, SummaryRelationKind.CONTAINS,
                next(item.confidence for item in owned_tasks if item.id == fact_id),
                (definition_id, fact_id), (definition_id,),
                (definition.source_anchor,),
            ))
    available = [relation for relation in relations
                 if relation.source in kept_ids and relation.target in kept_ids]
    omitted += len(relations) - len(available)
    omitted += max(0, len(available) - MAX_FLOW_RELATIONS)
    return TaskFlow(
        _summary_id("flow", "model", definition_id), definition_id,
        "Model structure", kept_steps, tuple(available[:MAX_FLOW_RELATIONS]),
        omitted > 0, omitted,
    )


def build_human_summary_base(analysis: ProjectAnalysis) -> HumanUnderstanding:
    """Late import keeps the DTO modules acyclic."""
    from .human_understanding import _build_human_understanding_base

    return _build_human_understanding_base(analysis)


def build_human_summary(analysis: ProjectAnalysis) -> HumanUnderstanding:
    """Project persisted facts into bounded summary DTOs without source or AST reads."""
    from .human_understanding import _valid_generic_root

    base = build_human_summary_base(analysis)
    root_ids = [
        candidate.root_symbol_id for candidate in analysis.generic_entries
        if _valid_generic_root(analysis, candidate)
    ]
    generic_flows = tuple(
        flow for root_id in dict.fromkeys(root_ids)
        if (flow := _generic_flow(analysis, root_id)) is not None
    )
    task_flows = tuple(
        flow for entry in analysis.human_tasks.entries
        if (flow := _task_entry_flow(analysis, entry)) is not None
    )
    training_flows = tuple(
        flow for entry in analysis.training.entry_points
        if (flow := _training_flow(analysis, entry.id)) is not None
    ) if analysis.training is not None else ()
    model_flows = tuple(
        flow for definition in analysis.model.definitions
        if (flow := _model_flow(analysis, definition.symbol_id)) is not None
    ) if analysis.model is not None else ()
    flows = (*training_flows, *task_flows, *model_flows, *generic_flows)
    omitted = max(0, len(flows) - MAX_FLOWS)
    kept = flows[:MAX_FLOWS]
    return replace(
        base,
        architecture=_build_architecture(analysis, base),
        flows=kept,
        default_flow_id=kept[0].id if kept else None,
        omitted_flow_count=omitted,
    )
