"""Pure presentation data for Overview, Inspector and Problems."""

from dataclasses import dataclass
from pathlib import Path

from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import Confidence, EdgeType, SourceAnchor, VisibleGraph


@dataclass(frozen=True, slots=True)
class OverviewChoice:
    id: str
    name: str
    source_anchor: SourceAnchor


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
    return OverviewData(
        project_root.name, str(project_root), "Python" if analysis.files else "Unknown",
        "PyTorch" if definitions else None,
        tuple(OverviewChoice(item.symbol_id, item.qualified_name, item.source_anchor)
              for item in definitions),
        tuple(OverviewChoice(item.id, item.name, item.source_anchor) for item in entries),
        areas,
        HealthData(sum(item.was_parsed for item in analysis.files), len(analysis.files),
                   resolved, counts[Confidence.EXACT], counts[Confidence.PROBABLE],
                   counts[Confidence.UNRESOLVED], len(analysis.issues)),
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
    children = tuple(dict.fromkeys(
        child for component_id in node.semantic_component_ids
        if component_id in components
        for child in components[component_id].child_component_ids
    ))
    relations = tuple(
        RelationData(edge.type, edge.target if edge.source == selected_id else edge.source,
                     "out" if edge.source == selected_id else "in", edge.confidence)
        for edge in view.edges if selected_id in (edge.source, edge.target)
    )
    data = tuple((key, str(value)) for key, value in node.metadata.items()
                 if key in ("data_role", "shape", "dtype", "device", "layer_type",
                            "repeat_count", "shared_with_id") and value is not None)
    return InspectorData(node.id, node.display_name, node.code_name, node.role,
                         node.confidence, evidence, children, relations, data,
                         node.source_anchors, node.member_ids, bool(node.source_anchors))


def build_problems(analysis: ProjectAnalysis) -> tuple[ProblemData, ...]:
    return tuple(ProblemData(item.code, item.message, item.file_path,
                             item.source_anchor.start_line if item.source_anchor else None)
                 for item in analysis.issues)
