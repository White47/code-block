"""Pure C6 navigation target construction from already resolved B5 evidence.

Receipt issuance, current publication identity, disk freshness, and graph
reveal authorization belong to the surrounding navigation-check job.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, NoReturn

from codeviz.core import SourceAnchor, ViewKind
from codeviz.navigation import RevealTarget

from .contracts import RequestScope
from .response import ResolvedEvidence
from .snapshot import AnalysisSnapshot, AnchorRecord, SnapshotBinding
from .source_text import MonacoRange
from .tool_contracts import EntityRef, RelationKey

NavigationAction = Literal["source", "graph"]


class NavigationError(ValueError):
    """Fixed failure: no unverified navigation target is returned."""

    def __init__(self) -> None:
        super().__init__("Assistant navigation is unavailable.")


def _unavailable() -> NoReturn:
    raise NavigationError() from None


def _valid_time(value: str) -> bool:
    if type(value) is not str or not 1 <= len(value) <= 64:
        return False
    if not (value.endswith("Z") or value.endswith("+00:00")):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    offset = parsed.utcoffset()
    return offset is not None and offset.total_seconds() == 0


def _same_anchor(anchor: SourceAnchor, returned: AnchorRecord) -> bool:
    return (type(anchor) is SourceAnchor and type(returned) is AnchorRecord
            and (anchor.file_path, anchor.start_line, anchor.start_column,
                 anchor.end_line, anchor.end_column)
            == (returned.file_path, returned.start_line, returned.start_column,
                returned.end_line, returned.end_column))


def _provenance_matches(snapshot: AnalysisSnapshot, resolved: ResolvedEvidence) -> bool:
    proof = resolved.proof
    target = proof.target
    if target is None:
        return proof.status == "source_only" and proof.provenance is None
    if proof.status != "bound":
        return False
    if type(target) is EntityRef:
        if target.kind == "symbol":
            symbol = snapshot.get_symbol(target.id)
            return symbol is not None and symbol.definition and proof.provenance == symbol
        if target.kind == "code":
            code_node = snapshot.get_code_node(target.id)
            return code_node is not None and proof.provenance == code_node
        if target.kind == "semantic":
            component = snapshot.get_component(target.id)
            return component is not None and proof.provenance == component
        return False
    if type(target) is not RelationKey:
        return False
    ordinal = target.original_index
    if target.collection_kind == "code":
        edges = snapshot.code_edge_inventory
        return (type(ordinal) is int and 0 <= ordinal < len(edges)
                and edges[ordinal].original_index == ordinal
                and proof.provenance == edges[ordinal])
    if target.collection_kind == "semantic":
        relations = snapshot.relation_inventory
        return (type(ordinal) is int and 0 <= ordinal < len(relations)
                and relations[ordinal].original_index == ordinal
                and proof.provenance == relations[ordinal])
    return False


@dataclass(frozen=True, slots=True)
class NavigationTarget:
    evidence_id: str
    scope: RequestScope
    binding: SnapshotBinding
    action: NavigationAction
    ast_anchor: SourceAnchor
    monaco_range: MonacoRange | None
    reveal: RevealTarget | None
    checked_at: str
    conversation_id: str
    conversation_revision: int
    config_revision: int
    consent_revision: int


def build_navigation_target(
    resolved: ResolvedEvidence,
    snapshot: AnalysisSnapshot,
    scope: RequestScope,
    action: NavigationAction,
    *,
    checked_at: str,
    conversation_id: str,
    conversation_revision: int,
    config_revision: int,
    consent_revision: int,
    reveal: RevealTarget | None = None,
) -> NavigationTarget:
    """Bind navigation to trusted local admission and existing reveal facts.

    The caller must pass evidence freshly resolved by ActualEvidenceResolver.
    This pure step can check consistency with the snapshot, but cannot prove
    receipt ownership or check the current disk/editor state by itself.
    Revisions and reveal must come from the local navigation-check job, never
    model output. Relation-to-node mappings are unavailable in these facts and
    remain fail-closed. This function never constructs a graph reveal.
    """
    try:
        if (type(resolved) is not ResolvedEvidence or type(snapshot) is not AnalysisSnapshot
                or type(scope) is not RequestScope or action not in ("source", "graph")
                or not _valid_time(checked_at) or not snapshot.matches_scope(scope)):
            _unavailable()
        if (type(conversation_id) is not str or not conversation_id
                or len(conversation_id) > 16_384
                or any(type(value) is not int or value < 0 for value in
                       (conversation_revision, config_revision, consent_revision))):
            _unavailable()
        conversation_id.encode("utf-8")
        record, proof = resolved.record, resolved.proof
        if (record.scope != scope or proof.scope != scope
                or proof.snapshot_binding != snapshot.binding
                or record.content_hash != proof.content_hash
                or record.truncated != proof.truncated
                or not _same_anchor(record.anchor, proof.returned_anchor)
                or not _provenance_matches(snapshot, resolved)):
            _unavailable()
        if action == "graph":
            if (proof.status != "bound" or type(proof.target) is not EntityRef
                    or type(reveal) is not RevealTarget
                    or type(reveal.view_kind) is not ViewKind
                    or reveal.reason is not None
                    or type(reveal.selected_id) is not str
                    or reveal.selected_id != proof.target.id
                    or (reveal.root_id is not None
                        and (type(reveal.root_id) is not str or not reveal.root_id))):
                _unavailable()
        file = snapshot.get_file(record.anchor.file_path)
        if (file is None or not file.was_parsed or file.content_hash != record.content_hash
                or record.anchor.end_line is None
                or not 1 <= record.anchor.start_line <= record.anchor.end_line <= file.line_count):
            _unavailable()
        anchor = record.anchor
        # B5 stores UTF-8 byte columns but no verified source text. Converting
        # those offsets to Monaco UTF-16 coordinates here would be a guess.
        ast_anchor = SourceAnchor(anchor.file_path, anchor.start_line,
                                  anchor.start_column, anchor.end_line, anchor.end_column)
        return NavigationTarget(proof.evidence_id, scope, snapshot.binding, action,
                                ast_anchor, None, reveal if action == "graph" else None,
                                checked_at, conversation_id, conversation_revision,
                                config_revision, consent_revision)
    except Exception:
        pass
    _unavailable()
