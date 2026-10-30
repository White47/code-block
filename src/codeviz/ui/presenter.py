"""Pure presentation data for Overview, Inspector and Problems."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import (
    Confidence,
    EdgeType,
    NodeKind,
    SourceAnchor,
    ViewIssue,
    ViewKind,
    VisibleGraph,
)
from codeviz.views.structure_identity import StructureResolver


@dataclass(frozen=True, slots=True)
class OverviewChoice:
    id: str
    name: str
    source_anchor: SourceAnchor
    view_kind: ViewKind = ViewKind.EXECUTION
    confidence: Confidence = Confidence.EXACT
    evidence: tuple[str, ...] = ()
    detection_kind: str | None = None
    initial_structure_depth: Literal[1, 2] | None = None


@dataclass(frozen=True, slots=True)
class HealthData:
    files_parsed: int
    files_total: int
    symbols_resolved: int
    exact: int
    probable: int
    unresolved: int
    issue_count: int


@dataclass(frozen=True, slots=True)
class OverviewData:
    project_name: str
    project_path: str
    language: str
    framework: str | None
    models: tuple[OverviewChoice, ...]
    entries: tuple[OverviewChoice, ...]
    main_areas: tuple[str, ...]
    health: HealthData
    functions: tuple[OverviewChoice, ...] = ()
    modules: tuple[OverviewChoice, ...] = ()
    classes: tuple[OverviewChoice, ...] = ()


@dataclass(frozen=True, slots=True)
class RelationData:
    type: EdgeType
    other_id: str
    direction: str
    confidence: Confidence


@dataclass(frozen=True, slots=True)
class InspectorData:
    id: str
    display_name: str
    code_name: str | None
    role: str | None
    confidence: Confidence
    evidence: tuple[str, ...]
    children: tuple[str, ...]
    relations: tuple[RelationData, ...]
    data: tuple[tuple[str, str], ...]
    source_anchors: tuple[SourceAnchor, ...]
    member_ids: tuple[str, ...]
    can_open_source: bool


@dataclass(frozen=True, slots=True)
class ProblemData:
    code: str
    message: str
    file_path: str
    line: int | None
    can_open_source: bool = True


def build_overview(analysis: ProjectAnalysis, project_root: Path) -> OverviewData:
    definitions = analysis.model.definitions if analysis.model else ()
    entries = analysis.training.entry_points if analysis.training else ()
    components = analysis.semantic.graph.components if analysis.semantic else ()
    counts = {confidence: sum(item.confidence == confidence for item in components)
              for confidence in Confidence}
    resolved = sum(reference.resolved_definition_id is not None
                   for reference in analysis.symbol_index.references)
    areas = tuple(dict.fromkeys(
        Path(item.relative_path).parts[0] if len(Path(item.relative_path).parts) > 1
        else "Project root" for item in analysis.files
    ))
    resolver = StructureResolver(analysis)
    instances = {item.id: item for item in analysis.model.instances} if analysis.model else {}

    def initial_depth(root_id: str) -> Literal[1, 2]:
        if resolver.query is None:
            return 1
        return 2 if any(
            (instance := instances.get(child)) is not None
            and instance.owner_definition_id == root_id
            and instance.container_kind in ("ModuleDict", "ModuleList")
            and any(member in instances for member in instance.member_instance_ids)
            for child in resolver.query.children(root_id)
        ) else 1

    def catalog(kinds: tuple[NodeKind, ...]) -> tuple[OverviewChoice, ...]:
        return tuple(OverviewChoice(item.id, item.qualified_name, item.source_anchor,
                                    ViewKind.FUNCTION_FLOW)
                     for item in analysis.symbol_index.entries.values()
                     if item.definition and item.kind in kinds)

    generic = tuple(OverviewChoice(
        item.id, analysis.symbol_index.entries[item.root_symbol_id].qualified_name,
        item.source_anchor, ViewKind.FUNCTION_FLOW, item.confidence,
        item.evidence, item.detection_kind,
    ) for item in analysis.generic_entries if item.root_symbol_id in analysis.symbol_index.entries)
    return OverviewData(
        project_root.name, str(project_root), "Python" if analysis.files else "Unknown",
        "PyTorch" if definitions else None,
        tuple(OverviewChoice(item.symbol_id, item.qualified_name, item.source_anchor,
                             initial_structure_depth=initial_depth(item.symbol_id))
              for item in definitions),
        (*generic, *(OverviewChoice(item.id, item.name, item.source_anchor) for item in entries)),
        areas,
        HealthData(sum(item.was_parsed for item in analysis.files), len(analysis.files),
                   resolved, counts[Confidence.EXACT], counts[Confidence.PROBABLE],
                   counts[Confidence.UNRESOLVED], len(analysis.issues)),
        catalog((NodeKind.FUNCTION, NodeKind.METHOD)), catalog((NodeKind.MODULE,)),
        catalog((NodeKind.CLASS,)),
    )


def build_inspector(view: VisibleGraph, selected_id: str,
                    analysis: ProjectAnalysis) -> InspectorData:
    node = next(item for item in view.nodes if item.id == selected_id)
    components = ({item.id: item for item in analysis.semantic.graph.components}
                  if analysis.semantic else {})
    evidence = tuple(dict.fromkeys(
        line for component_id in node.semantic_component_ids
        if component_id in components for line in components[component_id].evidence
    ))
    evidence = (*evidence, *(line for entry in analysis.generic_entries
                             if entry.root_symbol_id == selected_id for line in entry.evidence))
    if view.view_kind == ViewKind.CALLS:
        evidence = (*evidence, "Static call sites and static targets; not a runtime execution trace")
    if view.view_kind == ViewKind.FUNCTION_FLOW:
        evidence = (*evidence, "Static scope CFG and callpoints; not a runtime execution trace")
    if node.metadata.get("definition_projection"):
        evidence = (*evidence, "Definition structure projected in a static instance context")
    anchors = node.source_anchors
    target_id = node.metadata.get("target_definition_id")
    target = components.get(target_id) if isinstance(target_id, str) else None
    if (view.view_kind == ViewKind.ARCHITECTURE and target is not None
            and target.role == "ModelDefinition"):
        anchors = tuple(dict.fromkeys((*anchors, *target.source_anchors)))
        evidence = (*evidence, "Local structure refers to a source-backed model definition")
    if (node.metadata.get("container_kind") == "ModuleList"
            and node.metadata.get("repeat_count") is None):
        evidence = (*evidence, "Repetition count is unknown in static analysis; members are templates")
    children = tuple(dict.fromkeys(
        child for component_id in node.semantic_component_ids
        if component_id in components
        for child in components[component_id].child_component_ids
    ))
    if view.view_kind in (ViewKind.ARCHITECTURE, ViewKind.PROJECT_STRUCTURE):
        children = tuple(edge.target for edge in view.edges
                         if edge.source == selected_id and edge.type == EdgeType.CONTAINS)
    relations = tuple(
        RelationData(edge.type, edge.target if edge.source == selected_id else edge.source,
                     "out" if edge.source == selected_id else "in", edge.confidence)
        for edge in view.edges if selected_id in (edge.source, edge.target)
    )
    data = tuple((key, str(value)) for key, value in node.metadata.items()
                 if key in ("data_role", "shape", "dtype", "device", "layer_type",
                            "repeat_count", "shared_with_id", "statement_type", "condition",
                            "partial") and value is not None)
    return InspectorData(node.id, node.display_name, node.code_name, node.role,
                         node.confidence, evidence, children, relations, data,
                         anchors, node.member_ids, bool(anchors))


def build_problems(
    analysis: ProjectAnalysis,
    view_issues: tuple[ViewIssue, ...] = (),
) -> tuple[ProblemData, ...]:
    analysis_problems = tuple(
        ProblemData(item.code, item.message, item.file_path,
                    item.source_anchor.start_line if item.source_anchor else None)
        for item in analysis.issues)
    view_problems = tuple(
        ProblemData(item.code, item.message, f"{item.view_kind.value} view", None, False)
        for item in view_issues)
    return (*analysis_problems, *view_problems)
