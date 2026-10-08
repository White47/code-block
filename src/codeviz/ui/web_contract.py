"""Explicit JSON-safe payloads for the bundled desktop web UI."""

from enum import Enum
from math import isfinite
from pathlib import Path
from typing import TypeAlias, cast

from codeviz.core import (
    SourceAnchor,
    VisibleEdge,
    VisibleGraph,
    VisibleNode,
)
from codeviz.navigation import RevealTarget
from codeviz.search import SearchResponse, SearchResult
from codeviz.views.state import ViewState

from .controller import AnalysisStatus, SourceDocument
from .presenter import InspectorData, OverviewData, ProblemData

SCHEMA_VERSION = 1
JsonScalar: TypeAlias = None | bool | str | int | float
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def _json_clone(value: object, active: set[int] | None = None) -> JsonValue:
    """Validate and clone a value without relying on JSON encoder coercions."""
    if isinstance(value, Enum):
        raise TypeError("JSON payloads cannot contain enum values")
    if value is None:
        return None
    if type(value) is bool:
        return value
    if type(value) is str:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        number = value
        if not isfinite(number):
            raise ValueError("JSON payload numbers must be finite")
        return number
    if type(value) not in (list, dict):
        raise TypeError("JSON payloads may contain only JSON-native values")

    seen = active if active is not None else set()
    identity = id(value)
    if identity in seen:
        raise ValueError("JSON payloads cannot contain cyclic containers")
    seen.add(identity)
    try:
        if type(value) is list:
            return [_json_clone(item, seen) for item in cast(list[object], value)]
        result: JsonObject = {}
        for key, item in cast(dict[object, object], value).items():
            if type(key) is not str:
                raise TypeError("JSON object keys must be strings")
            result[key] = _json_clone(item, seen)
        return result
    finally:
        seen.remove(identity)


def _json_object(value: object) -> JsonObject:
    cloned = _json_clone(value)
    if type(cloned) is not dict:
        raise TypeError("JSON payload must be an object")
    return cloned


def _point_payload(value: object, field_name: str) -> list[int | float]:
    if (not isinstance(value, (tuple, list)) or len(value) != 2 or
            any(isinstance(item, bool) or not isinstance(item, (int, float)) or
                not isfinite(item) for item in value)):
        raise ValueError(f"{field_name} requires finite numeric x/y values")
    return [value[0], value[1]]


def _finite_number_payload(value: object, field_name: str) -> int | float:
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not isfinite(value)):
        raise ValueError(f"{field_name} must be finite")
    return value


def _anchor_payload(anchor: SourceAnchor) -> JsonObject:
    if type(anchor.file_path) is not str:
        raise ValueError("file_path must be a string")
    for field_name in ("start_line", "end_line"):
        value = getattr(anchor, field_name)
        if type(value) is not int or value < 1:
            raise ValueError(f"{field_name} must be a positive integer")
    for field_name in ("start_column", "end_column"):
        value = getattr(anchor, field_name)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"{field_name} must be a nonnegative integer or None")
    return _json_object({
        "filePath": anchor.file_path,
        "startLine": anchor.start_line,
        "startColumn": anchor.start_column,
        "endLine": anchor.end_line,
        "endColumn": anchor.end_column,
    })


def _anchors_payload(anchors: tuple[SourceAnchor, ...]) -> list[JsonValue]:
    return [_anchor_payload(anchor) for anchor in anchors]


def _metadata_payload(metadata: dict[str, object]) -> JsonObject:
    """Copy only UI-supported metadata with a known JSON representation."""
    result: dict[str, object] = {}
    string_fields = {
        "data_role": "dataRole",
        "dtype": "dtype",
        "device": "device",
        "layer_type": "layerType",
        "container_kind": "containerKind",
        "shared_with_id": "sharedWithId",
        "target_definition_id": "targetDefinitionId",
        "symbol_id": "symbolId",
        "entry_point_id": "entryPointId",
        "entry_kind": "entryKind",
        "statement_type": "statementType",
        "condition": "condition",
        "partial": "partial",
        "definition_code_name": "definitionCodeName",
        "instance_context": "instanceContext",
    }
    integer_fields = {
        "repeat_count": "repeatCount",
        "hidden_count": "hiddenCount",
        "structure_level": "structureLevel",
    }
    boolean_fields = {"has_children": "hasChildren", "definition_projection": "definitionProjection",
                      "can_open_detail": "canOpenDetail"}
    for source, target in string_fields.items():
        value = metadata.get(source)
        if isinstance(value, str):
            result[target] = value
    for source, target in integer_fields.items():
        value = metadata.get(source)
        if type(value) is int:
            result[target] = value
    for source, target in boolean_fields.items():
        value = metadata.get(source)
        if type(value) is bool:
            result[target] = value
    shape = metadata.get("shape")
    if (isinstance(shape, (tuple, list))
            and all(item is None or type(item) is int for item in shape)):
        result["shape"] = list(shape)
    return _json_object(result)


def _node_payload(node: VisibleNode) -> JsonObject:
    return _json_object({
        "id": node.id,
        "displayName": node.display_name,
        "semanticComponentIds": list(node.semantic_component_ids),
        "codeNodeIds": list(node.code_node_ids),
        "sourceAnchors": _anchors_payload(node.source_anchors),
        "memberIds": list(node.member_ids),
        "codeName": node.code_name,
        "role": node.role,
        "confidence": node.confidence.value,
        "metadata": _metadata_payload(node.metadata),
    })


def _edge_payload(edge: VisibleEdge) -> JsonObject:
    return _json_object({
        "source": edge.source,
        "target": edge.target,
        "type": edge.type.value,
        "codeEdgeIds": list(edge.code_edge_ids),
        "semanticComponentIds": list(edge.semantic_component_ids),
        "sourceAnchors": _anchors_payload(edge.source_anchors),
        "confidence": edge.confidence.value,
    })


def _query_context_payload(context: dict[str, object]) -> JsonObject:
    result: dict[str, object] = {}
    string_fields = {
        "root_id": "rootId",
        "selected_id": "selectedId",
        "direction": "direction",
        "control_mode": "controlMode",
    }
    for source, target in string_fields.items():
        value = context.get(source)
        if isinstance(value, str):
            result[target] = value
    hops = context.get("hops")
    if type(hops) is int:
        result["hops"] = hops
    depth = context.get("structure_depth")
    if type(depth) is int:
        result["structureDepth"] = depth
    for source, target in (
        ("has_more", "hasMore"),
        ("budget_truncated", "budgetTruncated"),
        ("can_expand", "canExpand"),
        ("depth_limit", "depthLimit"),
    ):
        value = context.get(source)
        if type(value) is bool:
            result[target] = value
    return _json_object(result)


def _hidden_counts_payload(hidden_counts: dict[str, int]) -> dict[str, int]:
    if type(hidden_counts) is not dict:
        raise ValueError("hiddenCounts must be an object")
    result: dict[str, int] = {}
    for key, value in hidden_counts.items():
        if type(key) is not str or type(value) is not int or value < 0:
            raise ValueError(
                "hiddenCounts requires string keys and nonnegative integer values")
        result[key] = value
    return result


def graph_payload(graph: VisibleGraph) -> JsonObject:
    """Serialize the bounded VisibleGraph contract, never underlying analysis objects."""
    data: dict[str, object] = {
        "viewKind": graph.view_kind.value,
        "nodes": [_node_payload(node) for node in graph.nodes],
        "rootIds": list(graph.root_ids),
        "edges": [_edge_payload(edge) for edge in graph.edges],
        "aggregations": [
            {"id": item.id, "memberIds": list(item.member_ids)}
            for item in graph.aggregations
        ],
        "layoutKind": graph.layout_kind,
        "layoutHints": {
            node_id: _point_payload(position, "layout hint")
            for node_id, position in graph.layout_hints.items()
        },
        "legend": [
            {"type": item.type.value, "label": item.label, "style": item.style}
            for item in graph.legend
        ],
        "hiddenCounts": _hidden_counts_payload(graph.hidden_counts),
        "queryContext": _query_context_payload(graph.query_context),
        "maxNodes": graph.max_nodes,
        "maxEdges": graph.max_edges,
        "issues": [
            {
                "code": item.code,
                "message": item.message,
                "viewKind": item.view_kind.value,
            }
            for item in graph.issues
        ],
    }
    return _json_object(data)


def overview_payload(data: OverviewData) -> JsonObject:
    payload: dict[str, object] = {
        "projectName": data.project_name,
        "projectPath": data.project_path,
        "language": data.language,
        "framework": data.framework,
        "models": [
            {"id": item.id, "name": item.name,
             "sourceAnchor": _anchor_payload(item.source_anchor),
             **({"initialStructureDepth": item.initial_structure_depth}
                if item.initial_structure_depth is not None else {})}
            for item in data.models
        ],
        "entries": [
            {"id": item.id, "name": item.name,
             "viewKind": item.view_kind.value, "confidence": item.confidence.value,
             "evidence": list(item.evidence), "detectionKind": item.detection_kind,
             "sourceAnchor": _anchor_payload(item.source_anchor)}
            for item in data.entries
        ],
        "functions": [{"id": item.id, "name": item.name,
                       "sourceAnchor": _anchor_payload(item.source_anchor)}
                      for item in data.functions],
        "modules": [{"id": item.id, "name": item.name,
                     "sourceAnchor": _anchor_payload(item.source_anchor)}
                    for item in data.modules],
        "classes": [{"id": item.id, "name": item.name,
                     "sourceAnchor": _anchor_payload(item.source_anchor)}
                    for item in data.classes],
        "mainAreas": list(data.main_areas),
        "health": {
            "filesParsed": data.health.files_parsed,
            "filesTotal": data.health.files_total,
            "symbolsResolved": data.health.symbols_resolved,
            "exact": data.health.exact,
            "probable": data.health.probable,
            "unresolved": data.health.unresolved,
            "issueCount": data.health.issue_count,
        },
    }
    return _json_object(payload)


def inspector_payload(data: InspectorData) -> JsonObject:
    return _json_object({
        "id": data.id,
        "displayName": data.display_name,
        "codeName": data.code_name,
        "role": data.role,
        "confidence": data.confidence.value,
        "evidence": list(data.evidence),
        "children": list(data.children),
        "relations": [
            {
                "type": item.type.value,
                "otherId": item.other_id,
                "direction": item.direction,
                "confidence": item.confidence.value,
            }
            for item in data.relations
        ],
        "data": [list(item) for item in data.data],
        "sourceAnchors": _anchors_payload(data.source_anchors),
        "memberIds": list(data.member_ids),
        "canOpenSource": data.can_open_source,
    })


def problem_payload(data: ProblemData) -> JsonObject:
    return _json_object({
        "code": data.code,
        "message": data.message,
        "filePath": data.file_path,
        "line": data.line,
        "canOpenSource": data.can_open_source,
    })


def source_payload(document: SourceDocument) -> JsonObject:
    return _json_object({
        "filePath": document.file_path,
        "text": document.text,
        "startLine": document.start_line,
        "endLine": document.end_line,
    })


def session_payload(
    project_path: Path | None,
    status: AnalysisStatus,
    outdated_reasons: tuple[str, ...],
    cache_issue: str | None,
    recent_projects: tuple[Path, ...],
    has_analysis: bool = False,
) -> JsonObject:
    """Serialize project-session status without consulting mutable stores."""
    return _json_object({
        "projectPath": str(project_path) if project_path is not None else None,
        "status": status.value,
        "hasAnalysis": has_analysis,
        "outdatedReasons": list(outdated_reasons),
        "cacheIssue": cache_issue,
        "recentProjects": [str(path) for path in recent_projects],
    })


def preferences_payload(language: str, issue: str | None) -> JsonObject:
    """Serialize UI preferences; analysis ignore paths intentionally live elsewhere."""
    return _json_object({"language": language, "issue": issue})


def reveal_target_payload(target: RevealTarget) -> JsonObject:
    return _json_object({
        "viewKind": target.view_kind.value if target.view_kind is not None else None,
        "rootId": target.root_id,
        "selectedId": target.selected_id,
        "reason": target.reason,
    })


def _search_result_payload(
    result: SearchResult,
    reveal_target: RevealTarget,
) -> JsonObject:
    return _json_object({
        "id": result.id,
        "sourceKind": result.source_kind,
        "name": result.name,
        "qualifiedName": result.qualified_name,
        "kind": result.kind,
        "scope": result.scope,
        "sourceAnchor": _anchor_payload(result.source_anchor),
        "confidence": result.confidence.value,
        "revealTarget": reveal_target_payload(reveal_target),
    })


def search_response_payload(
    response: SearchResponse,
    reveal_targets: tuple[RevealTarget, ...],
) -> JsonObject:
    """Serialize search results paired with Python-planned graph destinations."""
    if len(response.results) != len(reveal_targets):
        raise ValueError("each search result requires one reveal target")
    return _json_object({
        "results": [
            _search_result_payload(result, target)
            for result, target in zip(response.results, reveal_targets, strict=True)
        ],
        "totalCount": response.total_count,
        "truncated": response.truncated,
    })


def view_state_payload(state: ViewState) -> JsonObject:
    """Serialize every persisted ViewState field with defensive finite checks."""
    return _json_object({
        "expandedIds": list(state.expanded_ids),
        "positions": {
            node_id: _point_payload(position, "position")
            for node_id, position in state.positions.items()
        },
        "activeView": state.active_view.value if state.active_view is not None else None,
        "selectedId": state.selected_id,
        "openTabs": list(state.open_tabs),
        "graphRoots": dict(state.graph_roots),
        "zoom": _finite_number_payload(state.zoom, "zoom"),
        "pan": _point_payload(state.pan, "pan"),
        "filters": list(state.filters),
        "viewportByView": {
            kind: {
                "zoom": _finite_number_payload(viewport.zoom, "viewport zoom"),
                "pan": _point_payload(viewport.pan, "viewport pan"),
            }
            for kind, viewport in state.viewport_by_view.items()
        },
        "detailIslands": [
            {
                "id": island.id,
                "viewKind": island.view_kind.value,
                "sourceNodeId": island.source_node_id,
                "position": (_point_payload(island.position, "detail island position")
                             if island.position is not None else None),
            }
            for island in state.detail_islands
        ],
        "positionsByView": {
            kind: {
                node_id: _point_payload(position, "position by view")
                for node_id, position in positions.items()
            }
            for kind, positions in state.positions_by_view.items()
        },
    })


def success_payload(data: JsonValue,
                    request_id: str | None = None) -> JsonObject:
    payload: dict[str, object] = {
        "ok": True,
        "schemaVersion": SCHEMA_VERSION,
    }
    if request_id is not None:
        payload["requestId"] = request_id
    payload["data"] = _json_clone(data)
    return _json_object(payload)


def error_payload(error: Exception,
                  request_id: str | None = None, *,
                  safe_message: str | None = None) -> JsonObject:
    if isinstance(error, (ValueError, TypeError)):
        code = "INVALID_REQUEST"
        message = (safe_message if isinstance(safe_message, str) and safe_message
                   else "The request is invalid.")
    elif isinstance(error, RuntimeError):
        code = "NOT_READY"
        message = "The requested data is not ready."
    else:
        code = "INTERNAL_ERROR"
        message = "An internal error occurred."
    payload: dict[str, object] = {
        "ok": False,
        "schemaVersion": SCHEMA_VERSION,
    }
    if request_id is not None:
        payload["requestId"] = request_id
    payload["error"] = {"code": code, "message": message}
    return _json_object(payload)
