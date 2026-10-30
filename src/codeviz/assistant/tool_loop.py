"""Private C6 transcript construction for the service-owned provider loop.

This module only projects already-typed B5 results.  It owns no dispatch,
receipt authority, freshness decision, budget, or provider resource.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from .provider import ProviderDraft
from .snapshot import AnalysisSnapshot, AnchorRecord
from .source_policy import validate_source_path
from .tool_contracts import (
    EntityRef,
    ExcerptToolResult,
    LocalRelationsResult,
    RelationKey,
    SymbolSearchResult,
)

_REPAIR_CODES = (
    "invalid_schema", "unprovided_id", "invalid_evidence", "relation_required",
    "invalid_relation", "static_requires_ids",
)
_ANSWER_GUIDANCE = (
    "Use basis=static only with nonempty evidenceIds from receiptEvidenceIds supporting that exact claim. "
    "Unsupported content must be explicitly inference/unknown or omitted. "
    "Never invent IDs or present unsupported facts as verified. "
    "Unknown claim text must include 'unable to confirm' or '无法确认'."
)
_FINAL_GUIDANCE = (
    "Return valid JSON using the required schema and only provided evidence IDs. "
    "Be compact, avoid repeating evidence, preserve proof, and state coverage gaps. "
    + _ANSWER_GUIDANCE
)


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class TrustedTarget:
    target: EntityRef | RelationKey
    anchors: tuple[AnchorRecord, ...]


@dataclass(frozen=True, slots=True)
class ToolProjection:
    content: str
    source_spans: tuple[tuple[int, int, str], ...]
    metadata_spans: tuple[tuple[int, int, str], ...]
    targets: tuple[TrustedTarget, ...]


@dataclass(frozen=True, slots=True)
class EvidenceIdEntry:
    """Private service-selected guidance, never receipt or claim authority."""

    evidence_id: str
    relative_path: str
    target: EntityRef | RelationKey | None
    relation: bool

    def __post_init__(self) -> None:
        if (type(self.evidence_id) is not str or not 1 <= len(self.evidence_id) <= 512
                or type(self.relation) is not bool
                or (self.target is not None and type(self.target) not in (EntityRef, RelationKey))
                or (self.relation and type(self.target) is not RelationKey)):
            raise ValueError("Invalid receipt guidance.")
        self.evidence_id.encode("utf-8")
        validate_source_path(self.relative_path)
        if self.target is not None:
            self.target.__post_init__()


def _guidance(catalog: tuple[EvidenceIdEntry, ...],
              codes: tuple[str, ...] | None = None) -> ToolProjection:
    if (type(catalog) is not tuple
            or any(type(item) is not EvidenceIdEntry for item in catalog)):
        raise ValueError("Invalid receipt guidance.")
    if tuple(sorted({item.evidence_id for item in catalog})) != tuple(
            item.evidence_id for item in catalog):
        raise ValueError("Invalid receipt guidance.")
    text = (_FINAL_GUIDANCE if codes is None else
            "Repair to valid JSON; preserve proof and coverage gaps. Keep all seven sections "
            "in order: summary, entry, steps, branches, relations, evidence, unknowns; empty "
            "claims are permitted. Use at most ONE concise claim in total, at most ONE distinct "
            "receipt ID; claim text <=80 UTF-8 bytes. Avoid repeating evidence prose. "
            + _ANSWER_GUIDANCE)
    text += (" ref.id names a graph/symbol target, not a receipt. Only receiptEvidenceIds "
             "may be cited in evidenceIds, subject to local proof validation. ")
    if not catalog:
        text += "Use inference/unknown only, evidenceIds=[], relationEvidenceId=null. "
    if not any(item.relation for item in catalog):
        text += "Relational claims must use inference and relationEvidenceId=null. "
    if codes is not None:
        text += "If a citation cannot fit compactly, use honest inference/unknown without IDs. "
    parts = [text + "\n{"]
    metadata: list[tuple[int, int, str]] = []
    for index, (label, entries) in enumerate((
            ("receiptEvidenceIds", catalog),
            ("relationEvidenceIds", tuple(item for item in catalog if item.relation)))):
        parts.append(("," if index else "") + _json(label) + ":[")
        for ordinal, item in enumerate(entries):
            item.__post_init__()
            if ordinal:
                parts.append(",")
            start = sum(map(len, parts))
            # Attribute the complete list entry, including its JSON whitespace.
            # Scalar receipt IDs elsewhere retain their existing provenance.
            encoded = " " + _json(item.evidence_id)
            parts.append(encoded)
            metadata.append((start, start + len(encoded), item.relative_path))
        parts.append("]")
    parts.append("}")
    if codes is not None:
        parts.append("\nValidation codes: " + ",".join(dict.fromkeys(codes)) + ".")
    return ToolProjection("".join(parts), (), tuple(metadata), ())


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _anchor(value: AnchorRecord) -> dict[str, object]:
    value.__post_init__()
    return {
        "filePath": value.file_path,
        "startLine": value.start_line,
        "startColumn": value.start_column,
        "endLine": value.end_line,
        "endColumn": value.end_column,
    }


def _ref(value: EntityRef) -> dict[str, str]:
    value.__post_init__()
    return {"kind": value.kind, "id": value.id}


def _answer_schema() -> dict[str, object]:
    def obj(properties: dict[str, object]) -> dict[str, object]:
        return {"type": "object", "properties": properties, "required": list(properties),
                "additionalProperties": False}

    claim = obj({
        "text": {"type": "string"},
        "basis": {"type": "string", "enum": ["static", "inference", "unknown"]},
        "evidenceIds": {"type": "array", "items": {"type": "string"}},
        "relationEvidenceId": {"type": ["string", "null"]},
    })
    section = obj({
        "kind": {"type": "string", "enum": [
            "summary", "entry", "steps", "branches", "relations", "evidence", "unknowns",
        ]},
        "claims": {"type": "array", "items": claim},
    })
    return obj({"sections": {"type": "array", "items": section}})


def _with_provenance(payload: object, files: tuple[str, ...], *, source: str | None = None
                     ) -> tuple[str, tuple[tuple[int, int, str], ...],
                                tuple[tuple[int, int, str], ...]]:
    prefix = '{"result":' + _json(payload) + ',"provenance":['
    parts = [prefix]
    metadata: list[tuple[int, int, str]] = []
    for ordinal, path in enumerate(files):
        if ordinal:
            parts.append(",")
        marker = _json({"file": path, "ordinal": ordinal})
        start = sum(len(item) for item in parts)
        parts.append(marker)
        metadata.append((start, start + len(marker), path))
    parts.append("]")
    source_spans: list[tuple[int, int, str]] = []
    if source is not None:
        parts.append(',"source":')
        encoded = _json(source)
        start = sum(len(item) for item in parts) + 1
        parts.append(encoded)
        if len(encoded) > 2:
            source_spans.append((start, start + len(encoded) - 2, files[0]))
    parts.append("}")
    return "".join(parts), tuple(source_spans), tuple(metadata)


def _omitted_projection(kind: str, omitted_count: int) -> ToolProjection:
    payload = {
        "kind": "retrieval_gap",
        "omittedCount": omitted_count,
        "omittedTypes": [kind],
        "coverageGap": "Retrieval content was omitted because the input budget was insufficient.",
    }
    content, source, metadata = _with_provenance(payload, ())
    return ToolProjection(content, source, metadata, ())


def _metadata_files(snapshot: AnalysisSnapshot, refs: tuple[EntityRef, ...],
                    relations: tuple[RelationKey, ...] = ()) -> tuple[str, ...]:
    """Resolve provenance from immutable B3 facts, never projected anchors alone."""
    if type(snapshot) is not AnalysisSnapshot:
        raise ValueError("Missing metadata provenance.")
    symbols = {item.id: item for item in snapshot.symbol_inventory}
    codes = {item.id: item for item in snapshot.code_node_inventory}
    components = {item.id: item for item in snapshot.component_inventory}
    files: set[str] = set()
    pending = list(refs)
    seen: set[EntityRef] = set()

    def anchors(values: tuple[AnchorRecord, ...]) -> None:
        if not values:
            raise ValueError("Unknown metadata provenance.")
        files.update(anchor.file_path for anchor in values)

    for key in relations:
        inventory = (snapshot.code_edge_inventory if key.collection_kind == "code"
                     else snapshot.relation_inventory)
        if key.original_index >= len(inventory):
            raise ValueError("Unknown relation provenance.")
        edge = inventory[key.original_index]
        if edge.original_index != key.original_index:
            raise ValueError("Unknown relation provenance.")
        pending.extend((EntityRef(key.collection_kind, edge.source),
                        EntityRef(key.collection_kind, edge.target)))
        if key.collection_kind == "code":
            code_edge = snapshot.code_edge_inventory[key.original_index]
            anchors(() if code_edge.source_anchor is None else (code_edge.source_anchor,))
            pending.extend(EntityRef("code", identifier) for identifier in code_edge.possible_targets)
        else:
            anchors(snapshot.relation_inventory[key.original_index].source_anchors)
    while pending:
        ref = pending.pop()
        if ref in seen:
            continue
        seen.add(ref)
        if ref.kind == "semantic":
            component = components.get(ref.id)
            if component is None:
                raise ValueError("Unknown component provenance.")
            anchors(component.source_anchors)
            pending.extend(EntityRef("semantic", child) for child in component.child_component_ids)
            for identifier in component.code_symbol_ids:
                if identifier in symbols:
                    pending.append(EntityRef("symbol", identifier))
                if identifier in codes:
                    pending.append(EntityRef("code", identifier))
                if identifier not in symbols and identifier not in codes:
                    raise ValueError("Unknown linked provenance.")
        elif ref.kind == "symbol":
            symbol = symbols.get(ref.id)
            if symbol is None:
                raise ValueError("Unknown symbol provenance.")
            anchors((symbol.source_anchor,))
            files.add(symbol.file_path)
        else:
            code = codes.get(ref.id)
            if code is None:
                raise ValueError("Unknown code provenance.")
            # An origin flag alone cannot authorize source-free identifiers.
            anchors(code.source_anchors)
            if code.file_path is not None:
                files.add(code.file_path)
            if ref.id in symbols:
                pending.append(EntityRef("symbol", ref.id))
    return tuple(sorted(files))


def project_tool_result(
    result: SymbolSearchResult | LocalRelationsResult | ExcerptToolResult,
    *, snapshot: AnalysisSnapshot,
    maximum_bytes: int | None = None,
) -> ToolProjection:
    """Create bounded canonical content plus its complete provenance-file closure."""
    if maximum_bytes is not None and (type(maximum_bytes) is not int or maximum_bytes < 1):
        raise ValueError("Invalid projection budget.")
    if type(result) is SymbolSearchResult:
        result.__post_init__()
        def search_projection(count: int) -> ToolProjection:
            retained = result.results[:count]
            omitted = len(result.results) - len(retained)
            payload: dict[str, object] = {
                "kind": "symbol_search",
                "results": [{
                    "ref": _ref(item.ref), "name": item.name,
                    "qualifiedName": item.qualified_name, "kind": item.kind,
                    "scope": item.scope, "anchor": _anchor(item.anchor),
                    "confidence": item.confidence.value, "rank": item.rank,
                } for item in retained],
                "totalCount": result.total_count,
                "truncated": result.truncated or omitted > 0,
            }
            if result.truncated:
                payload["coverageGap"] = (
                    "Symbol search returned a bounded page; additional matches may exist."
                )
            if omitted:
                payload.update({
                    "omittedCount": omitted,
                    "omittedTypes": ["symbol_search_result"],
                    "coverageGap": (
                        "Symbol search returned a bounded page, and retained matches were also "
                        "omitted by the input budget."
                        if result.truncated else
                        "Some symbol matches were omitted by the input budget."
                    ),
                })
            targets = tuple(TrustedTarget(item.ref, (item.anchor,)) for item in retained)
            files = tuple(sorted(set(_metadata_files(
                snapshot, tuple(item.ref for item in retained)
            )) | {item.anchor.file_path for item in retained}))
            content, source, metadata = _with_provenance(payload, files)
            return ToolProjection(content, source, metadata, targets)

        projection = search_projection(len(result.results))
        if maximum_bytes is None or len(projection.content.encode("utf-8")) <= maximum_bytes:
            return projection
        retained_count = 0
        for count in range(1, len(result.results) + 1):
            candidate = search_projection(count)
            if len(candidate.content.encode("utf-8")) > maximum_bytes:
                break
            projection, retained_count = candidate, count
        if retained_count == 0:
            projection = search_projection(0)
        if len(projection.content.encode("utf-8")) > maximum_bytes:
            raise ValueError("Projection budget is too small for an honest omission.")
        return projection
    if type(result) is LocalRelationsResult:
        result.__post_init__()
        def relations_projection(node_indexes: tuple[int, ...],
                                 relation_indexes: tuple[int, ...]) -> ToolProjection:
            nodes = tuple(result.nodes[index] for index in node_indexes)
            relations = tuple(result.relations[index] for index in relation_indexes)
            omitted_nodes = len(result.nodes) - len(nodes)
            omitted_relations = len(result.relations) - len(relations)
            payload: dict[str, object] = {
                "kind": "local_relations", "status": result.status,
                "nodes": [{
                    "ref": _ref(item.ref), "name": item.name, "kind": item.kind,
                    "origin": None if item.origin is None else item.origin.value,
                    "confidence": item.confidence.value,
                    "anchors": [_anchor(anchor) for anchor in item.anchors],
                    "distance": item.distance,
                } for item in nodes],
                "relations": [{
                    "key": {"collectionKind": item.key.collection_kind,
                            "originalIndex": item.key.original_index},
                    "source": _ref(item.source), "target": _ref(item.target),
                    "type": item.type.value, "confidence": item.confidence.value,
                    "anchors": [_anchor(anchor) for anchor in item.anchors],
                } for item in relations],
                "truncated": result.truncated or bool(omitted_nodes or omitted_relations),
            }
            if result.truncated:
                payload["coverageGap"] = (
                    "Local relation retrieval was truncated at its source; additional facts "
                    "may exist."
                )
            if omitted_nodes or omitted_relations:
                omitted_types = []
                if omitted_nodes:
                    omitted_types.append("relation_node")
                if omitted_relations:
                    omitted_types.append("relation_edge")
                payload.update({
                    "omittedCount": {
                        "nodes": omitted_nodes,
                        "relations": omitted_relations,
                    },
                    "omittedTypes": omitted_types,
                    "coverageGap": (
                        "Local relation retrieval was truncated before input-budget projection, "
                        "and retained facts were also omitted by the input budget."
                        if result.truncated else
                        "Some local relation facts were omitted by the input budget."
                    ),
                })
            # Relation proof remains preferred over endpoint-node proof.
            targets = tuple(
                [TrustedTarget(item.key, item.anchors) for item in relations]
                + [TrustedTarget(item.ref, item.anchors) for item in nodes]
            )
            files = tuple(sorted(set(_metadata_files(snapshot,
                tuple(item.ref for item in nodes)
                + tuple(ref for item in relations for ref in (item.source, item.target)),
                tuple(item.key for item in relations))) |
                {anchor.file_path for item in nodes for anchor in item.anchors}
                | {anchor.file_path for item in relations for anchor in item.anchors}
            ))
            content, source, metadata = _with_provenance(payload, files)
            return ToolProjection(content, source, metadata, targets)

        all_nodes = tuple(range(len(result.nodes)))
        all_relations = tuple(range(len(result.relations)))
        projection = relations_projection(all_nodes, all_relations)
        if maximum_bytes is None or len(projection.content.encode("utf-8")) <= maximum_bytes:
            return projection
        kept_nodes: list[int] = []
        kept_relations: list[int] = []
        projection = relations_projection((), ())
        for collection, index in (
            *(("relation", index) for index in all_relations),
            *(("node", index) for index in all_nodes),
        ):
            nodes = tuple(kept_nodes + ([index] if collection == "node" else []))
            relations = tuple(kept_relations + ([index] if collection == "relation" else []))
            candidate = relations_projection(nodes, relations)
            if len(candidate.content.encode("utf-8")) <= maximum_bytes:
                projection = candidate
                if collection == "node":
                    kept_nodes.append(index)
                else:
                    kept_relations.append(index)
        if len(projection.content.encode("utf-8")) > maximum_bytes:
            raise ValueError("Projection budget is too small for an honest omission.")
        return projection
    if type(result) is ExcerptToolResult:
        result.__post_init__()
        if not result.text:
            content, source, metadata = _with_provenance({
                "kind": "retrieval_gap",
                "coverageGap": "No source characters were disclosed; no citable evidence is available.",
            }, ())
            if maximum_bytes is not None and len(content.encode("utf-8")) > maximum_bytes:
                raise ValueError("Projection budget is too small for an honest gap.")
            return ToolProjection(content, source, metadata, ())
        payload = {
            "kind": "source_excerpt", "evidenceId": result.evidence_id,
            "relativePath": result.relative_path,
            "returnedAnchor": _anchor(result.returned_anchor),
            "truncated": result.truncated,
        }
        content, source, metadata = _with_provenance(
            payload, (result.relative_path,), source=result.text)
        projection = ToolProjection(content, source, metadata, ())
        if maximum_bytes is not None and len(content.encode("utf-8")) > maximum_bytes:
            projection = _omitted_projection("source_excerpt", 1)
            if len(projection.content.encode("utf-8")) > maximum_bytes:
                raise ValueError("Projection budget is too small for an honest omission.")
        return projection
    raise ValueError("Invalid typed tool result.")


def choose_target(anchor: AnchorRecord, candidates: tuple[TrustedTarget, ...]
                  ) -> EntityRef | RelationKey | None:
    """Choose only a target that B5 can prove from this returned source span."""
    anchor.__post_init__()
    if (anchor.start_column is None or anchor.end_column is None
            or (anchor.start_line, anchor.start_column) >=
               (anchor.end_line, anchor.end_column)):
        return None
    start = (anchor.start_line, anchor.start_column)
    end = (anchor.end_line, anchor.end_column)
    for candidate in candidates:
        for outer in candidate.anchors:
            outer.__post_init__()
            if (outer.file_path == anchor.file_path
                    and outer.start_column is not None and outer.end_column is not None
                    and start <= (outer.start_line, outer.start_column)
                    and (outer.end_line, outer.end_column) <= end):
                return candidate.target
    return None


class ToolLoopTranscript:
    """Mutable request-local logical transcript; every emitted draft is fresh bytes."""

    def __init__(self, draft: ProviderDraft) -> None:
        self._deadline = draft.deadline
        self._logical = self._parse(draft.body)
        self._call_ids: set[str] = set()
        messages = cast(list[dict[str, object]], self._logical["messages"])
        for message in messages:
            if message.get("role") == "assistant":
                calls = message.get("toolCalls")
                if type(calls) is not list:
                    raise ValueError("Invalid assistant tool history.")
                for value in calls:
                    if type(value) is not dict or type(value.get("id")) is not str:
                        raise ValueError("Invalid assistant tool history.")
                    self._call_ids.add(cast(str, value["id"]))

    @staticmethod
    def _parse(raw: bytes) -> dict[str, object]:
        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result

        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=unique,
                           parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        required = {"version", "mode", "messages", "tools", "answerSchema", "sourceTags"}
        if (type(value) is not dict or not required <= set(value)
                or not set(value) <= required | {"metadataTags", "interpretationTags"}
                or value["version"] != 1 or value["mode"] not in ("text", "tools", "final")
                or type(value["messages"]) is not list or not 1 <= len(value["messages"]) <= 40
                or type(value["tools"]) is not list or type(value["sourceTags"]) is not list
                or type(value.get("metadataTags", [])) is not list):
            raise ValueError()
        return cast(dict[str, object], value)

    @classmethod
    def maybe_create(cls, draft: ProviderDraft) -> "ToolLoopTranscript | None":
        try:
            return cls(draft)
        except (AttributeError, KeyError, TypeError, ValueError, UnicodeError, RecursionError):
            return None

    @property
    def mode(self) -> str:
        return cast(str, self._logical["mode"])

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        names: list[str] = []
        for value in cast(list[object], self._logical["tools"]):
            if type(value) is not dict or type(value.get("name")) is not str:
                raise ValueError("Invalid tool definition.")
            name = cast(str, value["name"])
            if (name not in ("search_symbols", "read_source_excerpt", "get_local_relations")
                    or name in names):
                raise ValueError("Invalid tool definition.")
            names.append(name)
        return tuple(names)

    def _draft_from(self, logical: dict[str, object], output_limit: int) -> ProviderDraft:
        raw = _json(logical).encode("utf-8")
        if len(raw) > 256 * 1024 or not 1 <= len(cast(list[object], logical["messages"])) <= 40:
            raise ValueError("Provider transcript capacity exceeded.")
        return ProviderDraft(raw, output_limit, self._deadline)

    def _draft(self, output_limit: int) -> ProviderDraft:
        return self._draft_from(self._logical, output_limit)

    def current(self, output_limit: int) -> ProviderDraft:
        return self._draft(output_limit)

    def add_project_results(self, results: tuple[ToolProjection, ...], output_limit: int) -> ProviderDraft:
        """Forward internal typed retrieval as inert data, without synthetic arguments."""
        messages = cast(list[dict[str, object]], self._logical["messages"])
        source = cast(list[dict[str, object]], self._logical["sourceTags"])
        metadata = cast(list[dict[str, object]], self._logical.setdefault("metadataTags", []))
        for result in results:
            pointer = f"/messages/{len(messages)}/content"
            messages.append({"role": "user", "content": result.content})
            for tags, spans in ((source, result.source_spans), (metadata, result.metadata_spans)):
                for start, end, path in spans:
                    tags.append({"pointer": pointer, "start": start, "end": end, "file": path})
        for tags in (source, metadata):
            tags.sort(key=lambda item: (item["pointer"], item["start"], item["end"], item["file"]))
        return self._draft(output_limit)

    def add_tool_results(self, calls: tuple[ToolCall, ...],
                         results: tuple[ToolProjection, ...], output_limit: int) -> ProviderDraft:
        if not calls or len(calls) != len(results):
            raise ValueError("Invalid tool exchange.")
        messages = cast(list[dict[str, object]], self._logical["messages"])
        wire_calls = []
        for call in calls:
            if (not call.call_id or call.call_id in self._call_ids
                    or call.name not in ("search_symbols", "read_source_excerpt",
                                         "get_local_relations")):
                raise ValueError("Invalid tool call.")
            self._call_ids.add(call.call_id)
            wire_calls.append({"id": call.call_id, "name": call.name,
                               "arguments": _json(call.arguments)})
        messages.append({"role": "assistant", "content": None, "toolCalls": wire_calls})
        source_tags = cast(list[dict[str, object]], self._logical["sourceTags"])
        metadata_tags = cast(list[dict[str, object]], self._logical.setdefault("metadataTags", []))
        for call, result in zip(calls, results, strict=True):
            pointer = f'/messages/{len(messages)}/content'
            messages.append({"role": "tool", "toolCallId": call.call_id,
                             "name": call.name, "content": result.content})
            for start, end, path in result.source_spans:
                source_tags.append({"pointer": pointer, "start": start, "end": end, "file": path})
            for start, end, path in result.metadata_spans:
                metadata_tags.append({"pointer": pointer, "start": start, "end": end, "file": path})
        for tags in (source_tags, metadata_tags):
            tags.sort(key=lambda item: (item["pointer"], item["start"], item["end"], item["file"]))
        return self._draft(output_limit)

    def _append_guidance(self, catalog: tuple[EvidenceIdEntry, ...],
                         codes: tuple[str, ...] | None = None) -> None:
        projection = _guidance(catalog, codes)
        messages = cast(list[dict[str, object]], self._logical["messages"])
        pointer = f"/messages/{len(messages)}/content"
        messages.append({"role": "user", "content": projection.content})
        tags = cast(list[dict[str, object]], self._logical.setdefault("metadataTags", []))
        for start, end, path in projection.metadata_spans:
            tags.append({"pointer": pointer, "start": start, "end": end, "file": path})
        tags.sort(key=lambda item: (item["pointer"], item["start"], item["end"], item["file"]))

    def final(self, output_limit: int,
              catalog: tuple[EvidenceIdEntry, ...] = ()) -> ProviderDraft:
        self._logical["mode"] = "final"
        self._logical["tools"] = []
        self._logical["answerSchema"] = _answer_schema()
        self._append_guidance(catalog)
        return self._draft(output_limit)

    def preview_final(self, output_limit: int,
                      catalog: tuple[EvidenceIdEntry, ...] = ()) -> ProviderDraft:
        """Build the logical final draft without changing transcript state."""
        clone = ToolLoopTranscript(self.current(output_limit))
        return clone.final(output_limit, catalog)

    def preview_tool_results_final(
        self,
        calls: tuple[ToolCall, ...],
        results: tuple[ToolProjection, ...],
        output_limit: int,
        catalog: tuple[EvidenceIdEntry, ...] = (),
    ) -> ProviderDraft:
        """Preview an exchange followed by final mode without mutating this transcript."""
        clone = object.__new__(ToolLoopTranscript)
        clone._deadline = self._deadline
        clone._logical = self._parse(_json(self._logical).encode("utf-8"))
        clone._call_ids = set(self._call_ids)
        clone.add_tool_results(calls, results, output_limit)
        return clone.final(output_limit, catalog)

    def preview_completion(
        self, final_limit: int, repair_limit: int,
        catalog: tuple[EvidenceIdEntry, ...] = (),
    ) -> tuple[ProviderDraft, ProviderDraft]:
        """Preview final and the largest service-emittable unique repair prompt."""
        clone = ToolLoopTranscript(self.current(final_limit))
        final = clone.final(final_limit, catalog)
        return final, clone.repair(_REPAIR_CODES, repair_limit, catalog)

    def preview_tool_results_completion(
        self, calls: tuple[ToolCall, ...], results: tuple[ToolProjection, ...],
        final_limit: int, repair_limit: int,
        catalog: tuple[EvidenceIdEntry, ...] = (),
    ) -> tuple[ProviderDraft, ProviderDraft]:
        """Plan the repeated input without consuming results or call IDs."""
        clone = ToolLoopTranscript(self.current(final_limit))
        clone.add_tool_results(calls, results, final_limit)
        return clone.preview_completion(final_limit, repair_limit, catalog)

    def repair(self, codes: tuple[str, ...], output_limit: int,
               catalog: tuple[EvidenceIdEntry, ...] = ()) -> ProviderDraft:
        if (not codes or len(codes) > 61 or any(code not in _REPAIR_CODES for code in codes)):
            raise ValueError("Invalid repair codes.")
        self._append_guidance(catalog, codes)
        return self._draft(output_limit)
