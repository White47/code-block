"""Source-backed semantic graph assembled from independent static analyses."""

import ast
from dataclasses import dataclass

from codeviz.analysis.model import ModelAnalysis
from codeviz.analysis.python import AnalysisIssue, ParsedFile
from codeviz.analysis.training import TrainingAnalysis, TrainingEvent
from codeviz.core import (
    Confidence,
    EdgeType,
    NodeKind,
    SemanticComponent,
    SemanticGraph,
    SemanticRelation,
    SourceAnchor,
    stable_node_id,
)
from codeviz.semantic.training import TrainingRole


@dataclass(frozen=True, slots=True)
class SemanticAnalysis:
    graph: SemanticGraph
    issues: tuple[AnalysisIssue, ...] = ()


def _relation(source: str, target: str, kind: EdgeType, confidence: Confidence,
              evidence: str, anchor: SourceAnchor) -> SemanticRelation:
    return SemanticRelation(source, target, kind, confidence, (evidence,), (anchor,))


def _attention_candidate(parsed: ParsedFile, class_node: ast.ClassDef,
                         qualified_name: str,
                         definition_id: str,
                         forward_id: str | None) -> SemanticComponent | None:
    forward = next((item for item in class_node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == "forward"), None)
    if forward is None:
        return None
    assignments = {target.id.casefold(): item.value for item in ast.walk(forward)
                   if isinstance(item, (ast.Assign, ast.AnnAssign)) and item.value is not None
                   for target in (item.targets if isinstance(item, ast.Assign) else (item.target,))
                   if isinstance(target, ast.Name)}
    def named(prefixes: tuple[str, ...]) -> set[str]:
        return {name for name in assignments
                if any(name == prefix or name.startswith(prefix + "_")
                       for prefix in prefixes)}
    queries, keys, values = named(("q", "query")), named(("k", "key")), named(("v", "value"))
    if not (queries and keys and values):
        return None
    scores = {name for name, value in assignments.items()
              if any(isinstance(part, ast.BinOp) and isinstance(part.op, ast.MatMult)
                     and any(isinstance(operand, ast.Name) and operand.id.casefold() in queries
                             for operand in ast.walk(part.left))
                     and any(isinstance(operand, ast.Name) and operand.id.casefold() in keys
                             for operand in ast.walk(part.right))
                     for part in ast.walk(value))}
    softmax = any(isinstance(item, ast.Call) and item.args
                  and ((isinstance(item.func, ast.Attribute) and item.func.attr == "softmax")
                       or (isinstance(item.func, ast.Name) and item.func.id == "softmax"))
                  and isinstance(item.args[0], ast.Name)
                  and item.args[0].id.casefold() in scores
                  for item in ast.walk(forward))
    if not softmax:
        return None
    anchor = SourceAnchor(parsed.relative_path, forward.lineno,
                          start_column=forward.col_offset,
                          end_line=forward.end_lineno,
                          end_column=forward.end_col_offset)
    component_id = stable_node_id(parsed.relative_path,
                                  f"{qualified_name}.attention-like-candidate",
                                  NodeKind.FLOW_STEP, definition_id)
    return SemanticComponent(
        component_id, "Attention-like Component", class_node.name,
        "AttentionLikeCandidate", Confidence.PROBABLE,
        ("Q/K/V-named assignments feed a matrix-product score and softmax in forward; "
         "this is a structural candidate, not verified attention execution",),
        code_symbol_ids=(forward_id,) if forward_id else (), source_anchors=(anchor,),
    )


def _event_component(event: TrainingEvent,
                     child_ids: tuple[str, ...]) -> SemanticComponent:
    return SemanticComponent(
        event.id, event.role.value, event.code_name, event.role.value,
        event.confidence, event.evidence, child_ids,
        code_symbol_ids=(event.source_node_id,) if event.source_node_id else (),
        source_anchors=(event.source_anchor,),
        metadata={"entry_point_id": event.entry_point_id,
                  "parent_loop_id": event.parent_loop_id},
    )


def build_semantic_analysis(files: tuple[ParsedFile, ...], model: ModelAnalysis,
                            training: TrainingAnalysis) -> SemanticAnalysis:
    """Combine source facts without treating static guesses as runtime observations."""
    components: list[SemanticComponent] = []
    relations: list[SemanticRelation] = []
    roots: list[str] = []
    issues: list[AnalysisIssue] = []
    parsed_by_path = {item.relative_path: item for item in files}
    class_nodes = {(item.relative_path, node.name, node.lineno): node
                   for item in files if item.tree is not None
                   for node in ast.walk(item.tree) if isinstance(node, ast.ClassDef)}
    for definition in model.definitions:
        instances = [item for item in model.instances
                     if item.owner_definition_id == definition.symbol_id]
        member_ids = {child for item in instances for child in item.member_instance_ids
                      if child is not None}
        child_ids = tuple(item.id for item in instances if item.id not in member_ids)
        components.append(SemanticComponent(
            definition.symbol_id, definition.qualified_name.rsplit(".", 1)[-1],
            definition.qualified_name, "ModelDefinition", definition.confidence,
            definition.evidence, child_ids, (definition.symbol_id,),
            (definition.source_anchor,),
        ))
        roots.append(definition.symbol_id)
        for instance in instances:
            children = tuple(child for child in instance.member_instance_ids if child is not None)
            components.append(SemanticComponent(
                instance.id, instance.name, instance.qualified_name,
                "ModuleInstance", instance.confidence, instance.evidence,
                children,
                (instance.target_definition_id,) if instance.target_definition_id else (),
                (instance.source_anchor,),
                {"layer_type": instance.layer_type,
                 "container_kind": instance.container_kind,
                 "repeat_count": instance.repeat_count,
                 "shared_with_id": instance.shared_with_id},
            ))
            parent = next((owner.id for owner in instances
                           if instance.id in owner.member_instance_ids), definition.symbol_id)
            relations.append(_relation(parent, instance.id, EdgeType.CONTAINS,
                                       instance.confidence, "static module assignment",
                                       instance.source_anchor))
        parsed = parsed_by_path.get(definition.source_anchor.file_path)
        node = class_nodes.get((definition.source_anchor.file_path,
                                definition.qualified_name.rsplit(".", 1)[-1],
                                definition.source_anchor.start_line))
        if parsed is not None and node is not None:
            try:
                candidate = _attention_candidate(parsed, node, definition.qualified_name,
                                                 definition.symbol_id,
                                                 definition.forward_symbol_id)
                if candidate is not None:
                    components.append(candidate)
                    relations.append(_relation(definition.symbol_id, candidate.id,
                                               EdgeType.CONTAINS, Confidence.PROBABLE,
                                               "attention-like structural candidate in forward",
                                               candidate.source_anchors[0]))
            except Exception as error:
                issues.append(AnalysisIssue("SEMANTIC_RULE_ERROR",
                                            f"{type(error).__name__}: {error}",
                                            parsed.relative_path, definition.source_anchor))
    events = {event.id: event for event in training.events}
    for entry in training.entry_points:
        entry_events = [events[event_id] for event_id in entry.flow_node_ids if event_id in events]
        children = tuple(event.id for event in entry_events if event.parent_loop_id is None)
        components.append(SemanticComponent(
            entry.id, entry.name, entry.name, "EntryPoint", Confidence.EXACT,
            (f"named {entry.kind.value} entry point",), children,
            (entry.id,), (entry.source_anchor,), {"entry_kind": entry.kind.value},
        ))
        roots.append(entry.id)
        for model_id in entry.model_ids:
            if model_id in {definition.symbol_id for definition in model.definitions}:
                relations.append(_relation(entry.id, model_id, EdgeType.CALLS,
                                           Confidence.PROBABLE,
                                           "entry constructs or references this model statically",
                                           entry.source_anchor))
        for event in entry_events:
            components.append(_event_component(
                event, tuple(child.id for child in entry_events
                             if child.parent_loop_id == event.id)))
            relations.append(_relation(event.parent_loop_id or entry.id, event.id,
                                       EdgeType.CONTAINS, event.confidence,
                                       "event occurs lexically within entry or loop",
                                       event.source_anchor))
        for left, right in zip(entry_events, entry_events[1:], strict=False):
            relations.append(_relation(left.id, right.id, EdgeType.CONTROL_FLOW,
                                       Confidence.PROBABLE,
                                       "lexical order only; branches and runtime paths may differ",
                                       right.source_anchor))
        for loop in entry_events:
            if loop.role not in (TrainingRole.EPOCH_LOOP, TrainingRole.BATCH_LOOP):
                continue
            direct_children = [event for event in entry_events if event.parent_loop_id == loop.id]
            if direct_children:
                relations.append(_relation(direct_children[-1].id, loop.id,
                                           EdgeType.LOOP_BACK, Confidence.PROBABLE,
                                           "static loop syntax; iteration count and exit are not observed",
                                           loop.source_anchor))
        for index, event in enumerate(entry_events):
            if event.role != TrainingRole.BACKWARD_PASS:
                continue
            next_update = next((candidate for candidate in entry_events[index + 1:]
                                if candidate.parent_loop_id == event.parent_loop_id
                                and candidate.role == TrainingRole.PARAMETER_UPDATE), None)
            if next_update is not None:
                relations.append(_relation(event.id, next_update.id,
                                           EdgeType.GRADIENT_FLOW, Confidence.PROBABLE,
                                           "static backward/optimizer pattern; actual gradients not observed",
                                           event.source_anchor))
    return SemanticAnalysis(SemanticGraph(tuple(components), tuple(relations), tuple(roots)),
                            tuple(issues))
