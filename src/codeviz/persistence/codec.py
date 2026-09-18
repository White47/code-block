"""Whitelist-only JSON conversion for analysis facts; never serialize AST or source."""

from dataclasses import fields, is_dataclass
from enum import Enum
from math import isfinite
from types import UnionType
from typing import Any, Union, cast, get_args, get_origin, get_type_hints

from codeviz.analysis.flow import FlowAnalysis
from codeviz.analysis.model import (
    ModelAnalysis,
    ModelDefinition,
    ModuleInstance,
    ModuleInvocation,
)
from codeviz.analysis.project import ProjectAnalysis
from codeviz.analysis.python import AnalysisIssue, ParsedFile
from codeviz.analysis.resolution import ResolutionResult
from codeviz.analysis.symbols import SymbolIndex, SymbolIndexEntry, SymbolReference
from codeviz.analysis.training import (
    TrainingAnalysis,
    TrainingControlJoin,
    TrainingEvent,
)
from codeviz.core import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    Confidence,
    DataNode,
    DataRole,
    EdgeType,
    EntityRole,
    EntryPoint,
    EntryPointKind,
    NodeKind,
    NodeOrigin,
    SemanticComponent,
    SemanticGraph,
    SemanticRelation,
    SourceAnchor,
)
from codeviz.semantic.components import SemanticAnalysis
from codeviz.semantic.training import TrainingRole

_TYPES = {item.__name__: item for item in (
    ProjectAnalysis, AnalysisIssue, SymbolIndexEntry, SymbolReference,
    ResolutionResult, FlowAnalysis, ModelDefinition, ModuleInstance,
    ModuleInvocation, ModelAnalysis, TrainingEvent, TrainingControlJoin,
    TrainingAnalysis, SemanticAnalysis, CodeNode, CodeEdge, SemanticComponent,
    SemanticRelation, SemanticGraph, DataNode, EntryPoint, SourceAnchor,
)}
_ENUMS = {item.__name__: item for item in (
    Confidence, NodeKind, EntityRole, NodeOrigin, EdgeType, DataRole,
    EntryPointKind, TrainingRole,
)}
_MAX_DEPTH = 80


def _encode(value: object, depth: int = 0) -> object:
    if depth > _MAX_DEPTH:
        raise ValueError("analysis cache nesting limit exceeded")
    child = depth + 1
    if isinstance(value, Enum):
        enum = type(value)
        if enum.__name__ not in _ENUMS or _ENUMS[enum.__name__] is not enum:
            raise ValueError("unregistered enum in analysis cache")
        return {"@": "enum", "type": enum.__name__, "value": value.value}
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("non-finite number in analysis cache")
        return value
    if isinstance(value, ParsedFile):
        return {"@": "parsed-file", "relative_path": value.relative_path,
                "module_name": value.module_name, "line_count": value.line_count,
                "issues": _encode(value.issues, child), "was_parsed": value.was_parsed}
    if isinstance(value, CodeGraph):
        return {"@": "code-graph", "nodes": _encode(tuple(value.nodes.values()), child),
                "edges": _encode(tuple(value.edges), child)}
    if isinstance(value, SymbolIndex):
        return {"@": "symbol-index", "entries": _encode(tuple(value.entries.values()), child),
                "references": _encode(tuple(value.references), child),
                "scope_declarations": _encode(value.scope_declarations, child)}
    if is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        if cls.__name__ not in _TYPES or _TYPES[cls.__name__] is not cls:
            raise ValueError("unregistered analysis type")
        return {"@": "object", "type": cls.__name__,
                "fields": {field.name: _encode(getattr(value, field.name), child)
                           for field in fields(value)}}
    if isinstance(value, tuple):
        return {"@": "tuple", "items": [_encode(item, child) for item in value]}
    if isinstance(value, list):
        return [_encode(item, child) for item in value]
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError("analysis cache mapping keys must be strings")
        return {"@": "map", "items": [[key, _encode(item, child)]
                                        for key, item in value.items()]}
    raise ValueError(f"unsupported analysis cache value: {type(value).__name__}")


def _matches(value: object, hint: object) -> bool:
    if hint in (Any, object):
        return True
    origin = get_origin(hint)
    args = get_args(hint)
    if origin in (Union, UnionType):
        return any(_matches(value, arg) for arg in args)
    if origin is tuple:
        if not isinstance(value, tuple):
            return False
        if len(args) == 2 and args[1] is Ellipsis:
            return all(_matches(item, args[0]) for item in value)
        return len(args) == len(value) and all(
            _matches(item, arg) for item, arg in zip(value, args, strict=True))
    if origin is list:
        return isinstance(value, list) and all(_matches(item, args[0]) for item in value)
    if origin is dict:
        return isinstance(value, dict) and all(
            _matches(key, args[0]) and _matches(item, args[1])
            for key, item in value.items())
    if hint is float:
        return type(value) in (float, int)
    if hint in (bool, int, str):
        return type(value) is hint
    return isinstance(hint, type) and isinstance(value, hint)


def _decode(value: object, depth: int = 0) -> object:
    if depth > _MAX_DEPTH:
        raise ValueError("analysis cache nesting limit exceeded")
    child = depth + 1
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("non-finite number in analysis cache")
        return value
    if isinstance(value, list):
        return [_decode(item, child) for item in value]
    if not isinstance(value, dict) or type(value.get("@")) is not str:
        raise ValueError("invalid analysis cache object")
    kind = value["@"]
    if kind == "tuple" and set(value) == {"@", "items"} and isinstance(value["items"], list):
        return tuple(_decode(item, child) for item in value["items"])
    if kind == "map" and set(value) == {"@", "items"} and isinstance(value["items"], list):
        restored: dict[str, object] = {}
        for pair in value["items"]:
            if (not isinstance(pair, list) or len(pair) != 2 or
                    type(pair[0]) is not str or pair[0] in restored):
                raise ValueError("invalid analysis cache mapping")
            restored[pair[0]] = _decode(pair[1], child)
        return restored
    if kind == "enum" and set(value) == {"@", "type", "value"}:
        name = value["type"]
        if type(name) is not str or name not in _ENUMS or type(value["value"]) is not str:
            raise ValueError("unknown analysis enum")
        try:
            return _ENUMS[name](value["value"])
        except ValueError as error:
            raise ValueError("invalid analysis enum value") from error
    if kind == "parsed-file" and set(value) == {
        "@", "relative_path", "module_name", "line_count", "issues", "was_parsed"
    }:
        issues = _decode(value["issues"], child)
        if (type(value["relative_path"]) is not str or type(value["module_name"]) is not str
                or type(value["line_count"]) is not int or type(value["was_parsed"]) is not bool
                or not _matches(issues, tuple[AnalysisIssue, ...])):
            raise ValueError("invalid parsed-file cache record")
        return ParsedFile(value["relative_path"], value["module_name"], None,
                          value["line_count"], cast(tuple[AnalysisIssue, ...], issues),
                          value["was_parsed"])
    if kind == "code-graph" and set(value) == {"@", "nodes", "edges"}:
        nodes, edges = _decode(value["nodes"], child), _decode(value["edges"], child)
        if not _matches(nodes, tuple[CodeNode, ...]) or not _matches(edges, tuple[CodeEdge, ...]):
            raise ValueError("invalid code graph cache record")
        graph = CodeGraph()
        for node in cast(tuple[CodeNode, ...], nodes):
            graph.add_node(node)
        for edge in cast(tuple[CodeEdge, ...], edges):
            graph.add_edge(edge)
        return graph
    if kind == "symbol-index" and set(value) == {
        "@", "entries", "references", "scope_declarations"
    }:
        entries = _decode(value["entries"], child)
        references = _decode(value["references"], child)
        declarations = _decode(value["scope_declarations"], child)
        if (not _matches(entries, tuple[SymbolIndexEntry, ...]) or
                not _matches(references, tuple[SymbolReference, ...]) or
                not _matches(declarations, dict[str, dict[str, str]])):
            raise ValueError("invalid symbol index cache record")
        index = SymbolIndex()
        for entry in cast(tuple[SymbolIndexEntry, ...], entries):
            index.add_entry(entry)
        index.references.extend(cast(tuple[SymbolReference, ...], references))
        index.scope_declarations.update(cast(dict[str, dict[str, str]], declarations))
        return index
    if kind == "object" and set(value) == {"@", "type", "fields"}:
        name, data = value["type"], value["fields"]
        if type(name) is not str or name not in _TYPES or not isinstance(data, dict):
            raise ValueError("unknown analysis cache type")
        cls = _TYPES[name]
        hints = get_type_hints(cls)
        if set(data) != {field.name for field in fields(cls)}:
            raise ValueError("analysis cache fields do not match schema")
        decoded = {key: _decode(item, child) for key, item in data.items()}
        if any(not _matches(item, hints[key]) for key, item in decoded.items()):
            raise ValueError("analysis cache field has invalid type")
        try:
            return cast(Any, cls)(**decoded)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid analysis cache domain value") from error
    raise ValueError("unknown analysis cache record")


def encode_analysis(analysis: ProjectAnalysis) -> dict[str, object]:
    """Produce JSON-safe data, omitting every ParsedFile AST by construction."""
    return cast(dict[str, object], _encode(analysis))


def decode_analysis(payload: object) -> ProjectAnalysis:
    """Rebuild only registered domain facts; reject malformed or unknown tags."""
    restored = _decode(payload)
    if not isinstance(restored, ProjectAnalysis):
        raise ValueError("analysis cache root is not ProjectAnalysis")
    return restored
