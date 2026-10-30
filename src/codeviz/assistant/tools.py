"""Request-local tools and metadata receipts; wire accounting belongs to C."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast

from codeviz.core import Confidence

from .budget import BudgetLedger, BudgetReservation
from .contracts import AgentError, RequestScope
from .evidence import EvidenceRecord, EvidenceRegistry
from .failed_evidence import FailedEvidenceSnapshot
from .index_tools import _Authorization, _Scan, get_local_relations, search_symbols
from .snapshot import (
    AnalysisSnapshot,
    AnchorRecord,
    CodeEdgeRecord,
    CodeNodeRecord,
    ComponentRecord,
    RelationRecord,
    SymbolRecord,
)
from .source_excerpts import (
    ExcerptRequest,
    SourceExcerpt,
    SourceExcerptReader,
    SourceReadState,
)
from .source_policy import SourceConsent, SourceExcerptError, validate_source_path
from .tool_contracts import (
    EntityRef,
    EvidenceBinding,
    ExcerptToolResult,
    LocalRelationsResult,
    RelationKey,
    RelationsRequest,
    SearchRequest,
    SymbolSearchResult,
)


@dataclass(frozen=True, slots=True)
class _Receipt:
    evidence_id: str
    scope: RequestScope
    requested_anchor: AnchorRecord
    returned_anchor: AnchorRecord
    content_hash: str
    truncated: bool


_Provenance = SymbolRecord | CodeNodeRecord | ComponentRecord | CodeEdgeRecord | RelationRecord


@dataclass(frozen=True, slots=True)
class _RetentionManifest:
    logical_json: bytes
    evidence_ids: frozenset[str]


def _normalized_relation_seed(snapshot: AnalysisSnapshot, seed: EntityRef) -> EntityRef | None:
    """Map a search symbol only when the published code definition is identical."""
    if seed.kind != "symbol":
        return seed
    symbol = snapshot.get_symbol(seed.id)
    code = snapshot.get_code_node(seed.id)
    if (symbol is None or code is None or not symbol.definition
            or (symbol.id, symbol.kind, symbol.name, symbol.qualified_name,
                symbol.scope, symbol.file_path, (symbol.source_anchor,))
            != (code.id, code.kind, code.name, code.qualified_name,
                code.scope, code.file_path, code.source_anchors)):
        return None
    return EntityRef("code", code.id)


def _provenance_json(record: _Provenance | None) -> dict[str, object] | None:
    """Explicit B3 whitelist; each binding counts every shared occurrence."""
    if record is None:
        return None
    if isinstance(record, SymbolRecord):
        return {"id": record.id, "name": record.name, "qualified_name": record.qualified_name,
                "kind": record.kind.value, "scope": record.scope, "file_path": record.file_path,
                "source_anchor": _anchor_json(record.source_anchor),
                "parent_symbol": record.parent_symbol, "definition": record.definition}
    if isinstance(record, CodeNodeRecord):
        return {"id": record.id, "kind": record.kind.value, "name": record.name,
                "qualified_name": record.qualified_name, "scope": record.scope,
                "language": record.language, "origin": record.origin.value, "role": record.role.value,
                "confidence": record.confidence.value, "file_path": record.file_path,
                "source_anchors": [_anchor_json(a) for a in record.source_anchors]}
    if isinstance(record, ComponentRecord):
        return {"id": record.id, "display_name": record.display_name, "code_name": record.code_name,
                "role": record.role, "confidence": record.confidence.value, "evidence": record.evidence,
                "child_component_ids": record.child_component_ids, "code_symbol_ids": record.code_symbol_ids,
                "source_anchors": [_anchor_json(a) for a in record.source_anchors]}
    common: dict[str, object] = {"original_index": record.original_index, "source": record.source,
                                "target": record.target, "type": record.type.value,
                                "confidence": record.confidence.value}
    if isinstance(record, CodeEdgeRecord):
        common.update(source_anchor=None if record.source_anchor is None else _anchor_json(record.source_anchor),
                      possible_targets=record.possible_targets)
    else:
        common.update(evidence=record.evidence,
                      source_anchors=[_anchor_json(a) for a in record.source_anchors])
    return common


def _target_json(target: EntityRef | RelationKey | None) -> dict[str, object] | None:
    if target is None:
        return None
    if isinstance(target, EntityRef):
        return {"kind": target.kind, "id": target.id}
    return {"collection_kind": target.collection_kind, "original_index": target.original_index}


def _contains(receipt: _Receipt, target: AnchorRecord) -> bool:
    actual = receipt.returned_anchor
    if (actual.file_path != target.file_path or actual.start_column is None
            or actual.end_column is None
            or (actual.start_line, actual.start_column) >= (actual.end_line, actual.end_column)):
        return False
    if target.start_column is None or target.end_column is None:
        requested = receipt.requested_anchor
        return (not receipt.truncated
                and requested.file_path == target.file_path
                and (requested.start_column is None or requested.end_column is None)
                and requested.start_line <= target.start_line <= target.end_line <= requested.end_line
                and actual.start_line <= target.start_line <= target.end_line <= actual.end_line
                and (actual.start_line < target.start_line or actual.start_column == 0))
    return ((actual.start_line, actual.start_column) <= (target.start_line, target.start_column)
            and (target.end_line, target.end_column) <= (actual.end_line, actual.end_column))


def _anchor_json(anchor: AnchorRecord) -> dict[str, object]:
    return {"file_path": anchor.file_path, "start_line": anchor.start_line,
            "start_column": anchor.start_column, "end_line": anchor.end_line,
            "end_column": anchor.end_column}


def _scope_json(scope: RequestScope) -> dict[str, object]:
    return {"project_epoch": scope.project_epoch, "analysis_generation": scope.analysis_generation,
            "request_id": scope.request_id}


def _size(payload: dict[str, object]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _schema(payload: object, required: set[str], optional: set[str]) -> dict[str, object]:
    if type(payload) is not dict:
        raise AgentError()
    value = cast(dict[str, object], payload)
    if any(type(key) is not str for key in value) or not required <= value.keys():
        raise AgentError()
    if not value.keys() <= required | optional:
        raise AgentError()
    return value


class ToolSession:
    def __init__(self, snapshot: AnalysisSnapshot, *, scope: RequestScope,
                 consent: SourceConsent, ledger: BudgetLedger, registry: EvidenceRegistry,
                 current_state: Callable[[], SourceReadState], reader: SourceExcerptReader) -> None:
        if (type(snapshot) is not AnalysisSnapshot or type(scope) is not RequestScope
                or type(consent) is not SourceConsent or not isinstance(ledger, BudgetLedger)
                or not isinstance(registry, EvidenceRegistry) or not callable(current_state)
                or not isinstance(reader, SourceExcerptReader)):
            raise AgentError()
        scope.__post_init__()
        consent.__post_init__()
        if not snapshot.matches_scope(scope) or consent.binding != snapshot.binding:
            raise SourceExcerptError("scope_mismatch")
        self._snapshot = snapshot
        self._scope = scope
        self._consent = consent
        self._ledger: BudgetLedger | None = ledger
        self._registry = registry
        self._current_state: Callable[[], SourceReadState] | None = current_state
        self._reader: SourceExcerptReader | None = reader
        self._closed = False
        self._receipts: dict[str, _Receipt] = {}
        self._bindings: dict[tuple[str, EntityRef | RelationKey | None], EvidenceBinding] = {}
        self._private_bytes = 0
        self._retention_manifest: _RetentionManifest | None = None

    def _state(self) -> None:
        if self._closed:
            raise AgentError("evidence_unavailable")
        try:
            current_state = self._current_state
            if current_state is None:
                raise AgentError("evidence_unavailable")
            state = current_state()
            if type(state) is not SourceReadState:
                raise SourceExcerptError("verification_failed")
            state.__post_init__()
        except AgentError as error:
            if error.code in ("cancelled", "timeout"):
                raise
            raise SourceExcerptError("verification_failed") from None
        except Exception:
            raise SourceExcerptError("verification_failed") from None
        if self._closed:
            raise AgentError("evidence_unavailable")
        if state.scope != self._scope:
            raise SourceExcerptError("scope_mismatch")
        if state.consent != self._consent:
            raise SourceExcerptError("consent_required")
        if state.cancelled:
            raise AgentError("cancelled")

    def _checkpoint(self) -> None:
        if self._retention_manifest is not None:
            raise AgentError("evidence_unavailable")
        try:
            self._state()
            ledger = self._ledger
            if ledger is None:
                raise AgentError("evidence_unavailable")
            ledger.reserve(BudgetReservation())
            self._state()
        except BaseException:
            self.close()
            raise

    def _capacity(self, additional_bytes: int, additional_entries: int = 1) -> None:
        if (len(self._receipts) + len(self._bindings) + self._registry.entry_count
                + additional_entries > 1000
                or self._private_bytes + self._registry.byte_count + additional_bytes > 2 * 1024 * 1024):
            raise AgentError("registry_full")

    def search_symbols(self, request: SearchRequest) -> SymbolSearchResult:
        self._checkpoint()
        result = search_symbols(self._snapshot, self._consent, request, checkpoint=self._checkpoint)
        self._checkpoint()
        return result

    def get_local_relations(self, request: RelationsRequest) -> LocalRelationsResult:
        self._checkpoint()
        result = get_local_relations(self._snapshot, self._consent, request, checkpoint=self._checkpoint)
        self._checkpoint()
        return result

    def _verify_receipt(self, receipt: _Receipt) -> None:
        record = self._registry.lookup(receipt.evidence_id, scope=self._scope,
                                       expected_file_hash=receipt.content_hash)
        a = record.anchor
        r = receipt.returned_anchor
        requested = receipt.requested_anchor
        both_explicit = requested.start_column is not None and requested.end_column is not None
        file = self._snapshot.get_file(r.file_path)
        if (receipt.scope != self._scope or file is None or file.content_hash != receipt.content_hash
                or not file.was_parsed or not r.file_path.casefold().endswith(".py")
                or r.file_path not in self._consent.allowed_files
                or r.start_column is None or r.end_column is None
                or requested.file_path != r.file_path
                or r.start_line != requested.start_line
                or r.start_column != (requested.start_column if both_explicit else 0)
                or not requested.start_line <= r.end_line <= requested.end_line
                or (both_explicit and r.end_line == requested.end_line and requested.end_column is not None
                    and r.end_column > requested.end_column)
                or any(r.file_path == ignored or r.file_path.startswith(ignored + "/")
                       for ignored in self._snapshot.binding.ignore_paths)
                or (a.file_path, a.start_line, a.start_column, a.end_line, a.end_column)
                != (r.file_path, r.start_line, r.start_column, r.end_line, r.end_column)
                or record.truncated != receipt.truncated or record.confidence is not Confidence.EXACT
                or record.symbol_id is not None or record.code_edge_id is not None
                or record.semantic_id is not None):
            raise AgentError("evidence_unavailable")

    def read_source_excerpt(self, request: ExcerptRequest) -> SourceExcerpt:
        self._checkpoint()
        if type(request) is not ExcerptRequest:
            raise AgentError()
        request.__post_init__()
        a = request.anchor
        rebuilt = ExcerptRequest(AnchorRecord(a.file_path, a.start_line, a.start_column,
                                              a.end_line, a.end_column))
        try:
            reader = self._reader
            if reader is None:
                raise AgentError("evidence_unavailable")
            excerpt = reader.read_excerpt(rebuilt)
            if type(excerpt) is not SourceExcerpt:
                raise AgentError("evidence_unavailable")
            excerpt.__post_init__()
            if (excerpt.requested_anchor != rebuilt.anchor
                    or excerpt.relative_path != rebuilt.anchor.file_path
                    or excerpt.returned_anchor.file_path != excerpt.relative_path):
                raise AgentError("evidence_unavailable")
            ExcerptToolResult(excerpt.evidence_id, excerpt.relative_path, excerpt.text,
                              excerpt.returned_anchor, excerpt.truncated)
            receipt = _Receipt(excerpt.evidence_id, excerpt.scope, excerpt.requested_anchor,
                               excerpt.returned_anchor, excerpt.content_hash, excerpt.truncated)
            self._verify_receipt(receipt)
            if receipt.evidence_id in self._receipts:
                raise AgentError("evidence_unavailable")
            size = _size({"key": receipt.evidence_id,
                          "evidence_id": receipt.evidence_id, "scope": _scope_json(receipt.scope),
                          "requested_anchor": _anchor_json(receipt.requested_anchor),
                          "returned_anchor": _anchor_json(receipt.returned_anchor),
                          "content_hash": receipt.content_hash, "truncated": receipt.truncated})
            self._capacity(size)
            self._checkpoint()
            self._verify_receipt(receipt)
            self._capacity(size)
            self._receipts[receipt.evidence_id] = receipt
            self._private_bytes += size
            return excerpt
        except (AgentError, SourceExcerptError):
            self.close()
            raise
        except Exception:
            self.close()
            raise SourceExcerptError("verification_failed") from None
        except BaseException:
            self.close()
            raise

    def dispatch(self, name: str, payload: object) -> SymbolSearchResult | LocalRelationsResult | ExcerptToolResult:
        self._checkpoint()
        ledger = self._ledger
        if ledger is None:
            raise AgentError("evidence_unavailable")
        ledger.reserve(BudgetReservation(tool_calls=1))
        if type(name) is not str:
            raise AgentError()
        result: SymbolSearchResult | LocalRelationsResult | ExcerptToolResult
        if name == "search_symbols":
            p = _schema(payload, {"query"}, {"limit"})
            result = self.search_symbols(SearchRequest(cast(str, p["query"]), cast(int, p.get("limit", 20))))
        elif name == "get_local_relations":
            p = _schema(payload, {"seed"}, {"hops", "max_nodes", "max_edges"})
            seed = _schema(p["seed"], {"kind", "id"}, set())
            ref = EntityRef(cast(Literal["symbol", "code", "semantic"], seed["kind"]), cast(str, seed["id"]))
            normalized = _normalized_relation_seed(self._snapshot, ref)
            if normalized is None:
                result = LocalRelationsResult("unavailable", (), (), False)
            else:
                result = self.get_local_relations(RelationsRequest(
                    normalized, cast(int, p.get("hops", 2)), cast(int, p.get("max_nodes", 50)),
                    cast(int, p.get("max_edges", 100))))
        elif name == "read_source_excerpt":
            p = _schema(payload, {"file_path", "start_line", "end_line"}, {"start_column", "end_column"})
            path = validate_source_path(cast(str, p["file_path"]))
            anchor = AnchorRecord(path, cast(int, p["start_line"]), cast(int | None, p.get("start_column")),
                                  cast(int, p["end_line"]), cast(int | None, p.get("end_column")))
            excerpt = self.read_source_excerpt(ExcerptRequest(anchor))
            result = ExcerptToolResult(excerpt.evidence_id, excerpt.relative_path, excerpt.text,
                                       excerpt.returned_anchor, excerpt.truncated)
        else:
            raise AgentError()
        self._checkpoint()
        return result

    def bind_evidence(self, evidence_id: str, target: EntityRef | RelationKey | None) -> EvidenceBinding:
        self._checkpoint()
        receipt = self._owned_receipt(evidence_id, target)
        provenance, anchors, confidence = self._target_proof(target)
        if target is not None and not any(_contains(receipt, anchor) for anchor in anchors):
            raise AgentError("evidence_unavailable")
        key = (evidence_id, target)
        existing = self._bindings.get(key)
        if existing is not None:
            self._checkpoint()
            self._verify_receipt(receipt)
            return existing
        binding = EvidenceBinding(evidence_id, self._scope, self._snapshot.binding, target, provenance,
                                  receipt.returned_anchor, receipt.content_hash, confidence,
                                  receipt.truncated, "source_only" if target is None else "bound")
        b = self._snapshot.binding
        size = _size({"key": [evidence_id, _target_json(target)], "evidence_id": evidence_id,
                      "scope": _scope_json(self._scope),
                      "snapshot_binding": {"project_root": b.project_root, "ignore_paths": b.ignore_paths,
                                           "project_epoch": b.project_epoch,
                                           "analysis_generation": b.analysis_generation},
                      "target": _target_json(target), "provenance": _provenance_json(provenance),
                      "returned_anchor": _anchor_json(receipt.returned_anchor),
                      "content_hash": receipt.content_hash, "confidence": confidence.value,
                      "truncated": receipt.truncated, "status": binding.status})
        self._checkpoint()
        self._verify_receipt(receipt)
        self._capacity(size)
        self._bindings[key] = binding
        self._private_bytes += size
        return binding

    def lookup_binding(self, evidence_id: str, target: EntityRef | RelationKey | None) -> EvidenceBinding:
        self._checkpoint()
        receipt = self._owned_receipt(evidence_id, target)
        _, anchors, _ = self._target_proof(target)
        binding = self._bindings.get((evidence_id, target))
        if binding is None or (target is not None and not any(_contains(receipt, a) for a in anchors)):
            raise AgentError("evidence_unavailable")
        self._checkpoint()
        self._verify_receipt(receipt)
        return binding

    def _failed_evidence_snapshot(
        self, evidence_id: str, target: EntityRef | RelationKey | None,
        binding: EvidenceBinding, record: EvidenceRecord,
    ) -> FailedEvidenceSnapshot:
        """Copy one already-verified receipt chain without checkpoints or new authority."""
        receipt = self._receipts.get(evidence_id)
        if (self._closed or self._retention_manifest is not None
                or type(binding) is not EvidenceBinding
                or type(record) is not EvidenceRecord
                or receipt is None
                or self._bindings.get((evidence_id, target)) is not binding
                or binding.evidence_id != evidence_id
                or binding.target != target
                or binding.scope != self._scope
                or binding.snapshot_binding != self._snapshot.binding
                or receipt.scope != self._scope
                or record.scope != self._scope
                or (record.anchor.file_path, record.anchor.start_line,
                    record.anchor.start_column, record.anchor.end_line,
                    record.anchor.end_column)
                != (receipt.returned_anchor.file_path, receipt.returned_anchor.start_line,
                    receipt.returned_anchor.start_column, receipt.returned_anchor.end_line,
                    receipt.returned_anchor.end_column)
                or binding.returned_anchor != receipt.returned_anchor
                or record.content_hash != receipt.content_hash
                or binding.content_hash != receipt.content_hash
                or record.truncated != receipt.truncated
                or binding.truncated != receipt.truncated):
            raise AgentError("evidence_unavailable")
        source_anchor = record.anchor
        evidence = {
            "record": {
                "scope": _scope_json(record.scope),
                "anchor": {
                    "file_path": source_anchor.file_path,
                    "start_line": source_anchor.start_line,
                    "start_column": source_anchor.start_column,
                    "end_line": source_anchor.end_line,
                    "end_column": source_anchor.end_column,
                },
                "content_hash": record.content_hash,
                "confidence": record.confidence.value,
                "symbol_id": record.symbol_id,
                "code_edge_id": record.code_edge_id,
                "semantic_id": record.semantic_id,
                "truncated": record.truncated,
            },
            "receipt": {
                "evidence_id": receipt.evidence_id,
                "scope": _scope_json(receipt.scope),
                "requested_anchor": _anchor_json(receipt.requested_anchor),
                "returned_anchor": _anchor_json(receipt.returned_anchor),
                "content_hash": receipt.content_hash,
                "truncated": receipt.truncated,
            },
        }
        b = binding.snapshot_binding
        binding_payload: dict[str, object] = {
            "binding": {
                "evidence_id": binding.evidence_id,
                "scope": _scope_json(binding.scope),
                "snapshot_binding": {
                    "project_root": b.project_root,
                    "ignore_paths": b.ignore_paths,
                    "project_epoch": b.project_epoch,
                    "analysis_generation": b.analysis_generation,
                },
                "target": _target_json(binding.target),
                "provenance": _provenance_json(binding.provenance),
                "returned_anchor": _anchor_json(binding.returned_anchor),
                "content_hash": binding.content_hash,
                "confidence": binding.confidence.value,
                "truncated": binding.truncated,
                "status": binding.status,
            },
            "chosen_target": _target_json(target),
        }
        return FailedEvidenceSnapshot._from_verified(
            evidence_id, evidence=evidence, binding=binding_payload)

    @staticmethod
    def _retained_state(current_state: Callable[[], SourceReadState], *,
                        scope: RequestScope, consent: SourceConsent) -> None:
        try:
            state = current_state()
            if type(state) is not SourceReadState:
                raise AgentError("evidence_unavailable")
            state.__post_init__()
        except AgentError:
            raise
        except Exception:
            raise AgentError("evidence_unavailable") from None
        if state.scope != scope or state.consent != consent or state.cancelled:
            raise AgentError("evidence_unavailable")

    def _verify_retained_binding(
        self, evidence_id: str, target: EntityRef | RelationKey | None, *,
        current_state: Callable[[], SourceReadState], checkpoint: Callable[[], None],
    ) -> EvidenceBinding:
        """Verify one frozen receipt/binding with a caller-owned navigation gate."""
        if (self._closed or self._retention_manifest is None
                or not callable(current_state) or not callable(checkpoint)):
            raise AgentError("evidence_unavailable")
        self._retained_state(current_state, scope=self._scope, consent=self._consent)
        checkpoint()
        self._retained_state(current_state, scope=self._scope, consent=self._consent)
        if type(evidence_id) is not str or (
                target is not None and type(target) not in (EntityRef, RelationKey)):
            raise AgentError("evidence_unavailable")
        if target is not None:
            try:
                target.__post_init__()
            except (AgentError, ValueError, TypeError, AttributeError):
                raise AgentError("evidence_unavailable") from None
        receipt = self._receipts.get(evidence_id)
        if receipt is None:
            raise AgentError("evidence_unavailable")
        self._verify_receipt(receipt)
        _, anchors, _ = self._target_proof(target, checkpoint=checkpoint)
        binding = self._bindings.get((evidence_id, target))
        if binding is None or (target is not None and not any(
                _contains(receipt, anchor) for anchor in anchors)):
            raise AgentError("evidence_unavailable")
        checkpoint()
        self._retained_state(current_state, scope=self._scope, consent=self._consent)
        self._verify_receipt(receipt)
        return binding

    def _freeze_for_retention(self) -> _RetentionManifest:
        """Permanently disable ordinary tools and freeze complete logical metadata."""
        if self._closed or self._retention_manifest is not None:
            raise AgentError("evidence_unavailable")
        self._checkpoint()
        records = self._registry._snapshot_for_retention()
        for receipt in self._receipts.values():
            self._verify_receipt(receipt)
        payload = {
            "records": {
                evidence_id: {
                    "scope": _scope_json(record.scope),
                    "anchor": {
                        "file_path": record.anchor.file_path,
                        "start_line": record.anchor.start_line,
                        "start_column": record.anchor.start_column,
                        "end_line": record.anchor.end_line,
                        "end_column": record.anchor.end_column,
                    },
                    "content_hash": record.content_hash,
                    "confidence": record.confidence.value,
                    "symbol_id": record.symbol_id,
                    "code_edge_id": record.code_edge_id,
                    "semantic_id": record.semantic_id,
                    "truncated": record.truncated,
                }
                for evidence_id, record in sorted(records.items())
            },
            "receipts": [{
                "evidence_id": receipt.evidence_id,
                "scope": _scope_json(receipt.scope),
                "requested_anchor": _anchor_json(receipt.requested_anchor),
                "returned_anchor": _anchor_json(receipt.returned_anchor),
                "content_hash": receipt.content_hash,
                "truncated": receipt.truncated,
            } for _, receipt in sorted(self._receipts.items())],
            "bindings": [{
                "evidence_id": binding.evidence_id,
                "scope": _scope_json(binding.scope),
                "snapshot_binding": {
                    "project_root": binding.snapshot_binding.project_root,
                    "ignore_paths": binding.snapshot_binding.ignore_paths,
                    "project_epoch": binding.snapshot_binding.project_epoch,
                    "analysis_generation": binding.snapshot_binding.analysis_generation,
                },
                "target": _target_json(binding.target),
                "provenance": _provenance_json(binding.provenance),
                "returned_anchor": _anchor_json(binding.returned_anchor),
                "content_hash": binding.content_hash,
                "confidence": binding.confidence.value,
                "truncated": binding.truncated,
                "status": binding.status,
            } for _, binding in sorted(
                self._bindings.items(), key=lambda item: json.dumps(
                    [item[0][0], _target_json(item[0][1])], ensure_ascii=False,
                    sort_keys=True, separators=(",", ":")))],
        }
        logical_json = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False).encode("utf-8")
        manifest = _RetentionManifest(logical_json, frozenset(
            set(records) | set(self._receipts) |
            {key[0] for key in self._bindings}))
        self._retention_manifest = manifest
        # These request-local objects may close over the job, provider or draft.
        # Frozen verification uses only its new lease's state and checkpoint.
        self._reader = None
        self._ledger = None
        self._current_state = None
        return manifest

    def _owned_receipt(self, evidence_id: str, target: EntityRef | RelationKey | None) -> _Receipt:
        if type(evidence_id) is not str or (target is not None and type(target) not in (EntityRef, RelationKey)):
            raise AgentError("evidence_unavailable")
        if target is not None:
            try:
                target.__post_init__()
            except (AgentError, ValueError, TypeError, AttributeError):
                raise AgentError("evidence_unavailable") from None
        receipt = self._receipts.get(evidence_id)
        if receipt is None:
            raise AgentError("evidence_unavailable")
        try:
            self._verify_receipt(receipt)
        except AgentError:
            self.close()
            raise AgentError("evidence_unavailable") from None
        return receipt

    def _target_proof(self, target: EntityRef | RelationKey | None, *,
                      checkpoint: Callable[[], None] | None = None) -> tuple[
            _Provenance | None, tuple[AnchorRecord, ...], Confidence]:
        if target is None:
            return None, (), Confidence.EXACT
        active_checkpoint = self._checkpoint if checkpoint is None else checkpoint
        auth = _Authorization(self._snapshot, self._consent, _Scan(active_checkpoint))
        record: _Provenance | None = None
        anchors: tuple[AnchorRecord, ...] = ()
        permitted = False
        confidence = Confidence.EXACT
        if isinstance(target, EntityRef):
            if target.kind == "symbol":
                symbol = auth.symbols.get(target.id)
                if symbol is not None:
                    record, anchors = symbol, (symbol.source_anchor,)
                    permitted = symbol.definition and auth.symbol(symbol)
            elif target.kind == "code":
                code = auth.codes.get(target.id)
                if code is not None:
                    record, anchors, confidence = code, code.source_anchors, code.confidence
                    permitted = auth.code(code)
            else:
                component = auth.components.get(target.id)
                if component is not None:
                    record, anchors, confidence = component, component.source_anchors, component.confidence
                    permitted = auth.component(component)
        else:
            inventory = (self._snapshot.code_edge_inventory if target.collection_kind == "code"
                         else self._snapshot.relation_inventory)
            if target.original_index < len(inventory):
                edge = inventory[target.original_index]
                if edge.original_index == target.original_index:
                    record, confidence = edge, edge.confidence
                    if isinstance(edge, CodeEdgeRecord):
                        anchors = () if edge.source_anchor is None else (edge.source_anchor,)
                        source, destination = auth.codes.get(edge.source), auth.codes.get(edge.target)
                        permitted = (auth.anchors(anchors) and source is not None and destination is not None
                                     and auth.code(source) and auth.code(destination))
                        for identifier in edge.possible_targets:
                            auth.scan.step()
                            possible = auth.codes.get(identifier)
                            permitted = permitted and possible is not None and auth.code(possible)
                    else:
                        anchors = edge.source_anchors
                        first, second = auth.components.get(edge.source), auth.components.get(edge.target)
                        permitted = (auth.anchors(anchors) and first is not None and second is not None
                                     and auth.component(first) and auth.component(second))
        active_checkpoint()
        if record is None or not permitted:
            raise AgentError("evidence_unavailable")
        return record, anchors, confidence

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._receipts.clear()
            self._bindings.clear()
            self._private_bytes = 0
            self._retention_manifest = None
            self._registry.clear()


def create_tool_session(snapshot: AnalysisSnapshot, *, scope: RequestScope, consent: SourceConsent,
                        ledger: BudgetLedger, registry: EvidenceRegistry,
                        current_state: Callable[[], SourceReadState]) -> ToolSession:
    reader = SourceExcerptReader(snapshot, scope=scope, consent=consent, ledger=ledger,
                                 registry=registry, current_state=current_state)
    return ToolSession(snapshot, scope=scope, consent=consent, ledger=ledger, registry=registry,
                       current_state=current_state, reader=reader)
