"""Strict response values and request-local, receipt-backed evidence verification.

Prose is untrusted explanation. Only separately projected local relation facts
describe static analysis; navigation and publication belong to the service.
"""

import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from types import MappingProxyType
from typing import Literal, NoReturn, TypeVar, cast

from codeviz.core import Confidence, EdgeType, SourceAnchor

from ._validation_diagnostics import fact as _diagnostic_fact
from ._validation_diagnostics import location as _location
from .contracts import RequestScope
from .evidence import EvidenceRecord, EvidenceRegistry
from .snapshot import (
    MAX_SEQUENCE_ENTRIES,
    AnalysisSnapshot,
    CodeEdgeRecord,
    CodeNodeRecord,
    ComponentRecord,
    RelationRecord,
    SymbolRecord,
)
from .tool_contracts import EntityRef, RelationKey
from .tool_contracts import EvidenceBinding as B5EvidenceBinding
from .tools import ToolSession

_Observed = TypeVar("_Observed")


def _checked(name: str, operation: Callable[[], _Observed]) -> _Observed:
    """Observe the existing check's outcome; never replace its predicate."""
    try:
        result = operation()
    except ResponseError:
        _diagnostic_fact(name, False)
        raise
    _diagnostic_fact(name, True)
    return result

SectionKind = Literal["summary", "entry", "steps", "branches", "relations", "evidence", "unknowns"]
Basis = Literal["static", "inference", "unknown"]
Category = Literal["call", "control", "data", "structure"]
ChosenTarget = EntityRef | RelationKey | None
IssueCode = Literal[
    "invalid_schema", "unprovided_id", "invalid_evidence", "relation_required",
    "invalid_relation", "static_requires_ids",
]
_KINDS = ("summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")
_ISSUES = ("invalid_schema", "unprovided_id", "invalid_evidence", "relation_required",
           "invalid_relation", "static_requires_ids")
_MAX_BYTES = 2 * 1024 * 1024
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", re.ASCII)
_UNKNOWN_MARKER = re.compile(r"\bunable to confirm\b|无法确认|无法核实", re.IGNORECASE)
_CATEGORIES: Mapping[EdgeType, Category] = MappingProxyType({
    EdgeType.CALLS: "call",
    EdgeType.CONTROL_FLOW: "control", EdgeType.BRANCH_TRUE: "control",
    EdgeType.BRANCH_FALSE: "control", EdgeType.LOOP_BACK: "control",
    EdgeType.READS: "data", EdgeType.WRITES: "data", EdgeType.PASSES_TO: "data",
    EdgeType.RETURNS_TO: "data", EdgeType.DATA_FLOW: "data", EdgeType.GRADIENT_FLOW: "data",
    EdgeType.CONTAINS: "structure", EdgeType.IMPORTS: "structure", EdgeType.EXPORTS: "structure",
    EdgeType.INHERITS: "structure", EdgeType.IMPLEMENTS: "structure",
})


class ResponseError(ValueError):
    """Fixed local validation failure, also recognized by the C2 error table."""

    def __init__(self) -> None:
        self.code = "AI_EVIDENCE_INVALID"
        self.message = "Assistant evidence validation failed."
        super().__init__(self.message)


def _invalid() -> NoReturn:
    # Call outside exception handlers: suppression alone retains __context__.
    raise ResponseError() from None


def _text(value: str, maximum: int, *, prose: bool = False) -> None:
    if type(value) is not str or not 1 <= len(value) <= maximum or not value.strip():
        _invalid()
    valid_utf8 = True
    try:
        value.encode("utf-8")
    except UnicodeError:
        valid_utf8 = False
    if not valid_utf8:
        _invalid()
    if prose:
        if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in value):
            _invalid()
    elif not value.isprintable():
        _invalid()


def _literal(value: str, choices: tuple[str, ...]) -> None:
    if type(value) is not str or value not in choices:
        _invalid()


def _exact(value: object, cls: type) -> None:
    if type(value) is not cls:
        _invalid()
    try:
        validator = getattr(value, "__post_init__", None)
        if validator is not None:
            validator()
    except Exception:
        pass
    else:
        return
    _invalid()


def _items(values: tuple[object, ...], cls: type, maximum: int) -> None:
    if type(values) is not tuple or len(values) > maximum:
        _invalid()
    for value in values:
        _exact(value, cls)


def _strings(values: tuple[str, ...], maximum: int, width: int) -> None:
    if type(values) is not tuple or len(values) > maximum:
        _invalid()
    for value in values:
        _text(value, width)


def _timestamp(value: str) -> None:
    _text(value, 64)
    if _UTC.fullmatch(value) is None:
        _invalid()
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    else:
        return
    _invalid()


def _scope_value(value: RequestScope) -> None:
    _exact(value, RequestScope)
    _text(value.project_epoch, 128)
    _text(value.request_id, 128)


@dataclass(frozen=True, slots=True)
class ModelClaim:
    text: str
    basis: Basis
    evidence_ids: tuple[str, ...]
    relation_evidence_id: str | None

    def __post_init__(self) -> None:
        _checked("textValid", partial(_text, self.text, 2000, prose=True))
        _checked("basisEnumValid", partial(_literal, self.basis, ("static", "inference", "unknown")))
        try:
            _strings(self.evidence_ids, 8, 512)
            if len(set(self.evidence_ids)) != len(self.evidence_ids):
                _invalid()
        except ResponseError:
            _diagnostic_fact("evidenceIdsValid", False)
            raise
        _diagnostic_fact("evidenceIdsValid", True)
        if self.relation_evidence_id is not None:
            _checked("relationIdValid", partial(_text, self.relation_evidence_id, 512))
            if self.relation_evidence_id not in self.evidence_ids:
                _diagnostic_fact("relationIdValid", False)
                _invalid()
        _diagnostic_fact("relationIdValid", True)


@dataclass(frozen=True, slots=True)
class Section:
    kind: SectionKind
    claims: tuple[ModelClaim, ...]

    def __post_init__(self) -> None:
        try:
            _literal(self.kind, _KINDS)
        except ResponseError:
            _diagnostic_fact("sectionOrderValid", False)
            raise
        _items(self.claims, ModelClaim, 20)


@dataclass(frozen=True, slots=True)
class ModelAnswer:
    sections: tuple[Section, ...]

    def __post_init__(self) -> None:
        _sections(self.sections)
        if len(_json_bytes({"sections": [{"kind": s.kind,
                                         "claims": [_claim_json(c) for c in s.claims]}
                                        for s in self.sections]})) > 65536:
            _invalid()


def _sections(sections: tuple[Section, ...]) -> None:
    _items(sections, Section, 7)
    ordered = tuple(s.kind for s in sections) == _KINDS
    _diagnostic_fact("sectionOrderValid", ordered)
    if not ordered:
        _invalid()
    within_limit = sum(len(s.claims) for s in sections) <= 60
    _diagnostic_fact("totalClaimsWithinLimit", within_limit)
    if not within_limit:
        _invalid()


@dataclass(frozen=True, slots=True)
class ResolvedEvidence:
    record: EvidenceRecord
    proof: B5EvidenceBinding

    def __post_init__(self) -> None:
        if type(self.record) is not EvidenceRecord:
            _invalid()
        _scope_value(self.record.scope)
        anchor = self.record.anchor
        if type(anchor) is not SourceAnchor or type(anchor.file_path) is not str:
            _invalid()
        for coordinate in (anchor.start_line, anchor.end_line):
            if type(coordinate) is not int:
                _invalid()
        for coordinate in (anchor.start_column, anchor.end_column):
            if coordinate is not None and type(coordinate) is not int:
                _invalid()
        _exact(anchor, SourceAnchor)
        _text(self.record.content_hash, 64)
        for identifier in (self.record.symbol_id, self.record.code_edge_id, self.record.semantic_id):
            if identifier is not None:
                _text(identifier, 512)
        _exact(self.record, EvidenceRecord)
        _exact(self.proof, B5EvidenceBinding)


@dataclass(frozen=True, slots=True)
class EvidenceCard:
    evidence_id: str
    label: str
    confidence: Confidence
    truncated: bool
    source_available: bool
    graph_available: bool
    unavailable_reason: Literal["navigation_check_required", "source_only"]

    def __post_init__(self) -> None:
        _text(self.evidence_id, 512)
        _text(self.label, _MAX_BYTES)
        _exact(self.confidence, Confidence)
        for value in (self.truncated, self.source_available, self.graph_available):
            _exact(value, bool)
        _literal(self.unavailable_reason, ("navigation_check_required", "source_only"))
        if not self.source_available or (self.graph_available !=
                                        (self.unavailable_reason == "navigation_check_required")):
            _invalid()


@dataclass(frozen=True, slots=True)
class RelationFact:
    evidence_id: str
    source_id: str
    target_id: str
    type: EdgeType
    confidence: Confidence
    candidate_targets: tuple[str, ...]
    category: Category

    def __post_init__(self) -> None:
        for value in (self.evidence_id, self.source_id, self.target_id):
            _text(value, 512)
        _exact(self.type, EdgeType)
        _exact(self.confidence, Confidence)
        _strings(self.candidate_targets, 1000, 512)
        _literal(self.category, ("call", "control", "data", "structure"))
        if _CATEGORIES.get(self.type) != self.category:
            _invalid()
        if self.candidate_targets and (self.type is not EdgeType.CALLS
                                      or self.confidence is Confidence.EXACT):
            _invalid()


@dataclass(frozen=True, slots=True)
class Answer:
    sections: tuple[Section, ...]
    evidence: tuple[EvidenceCard, ...]
    relation_facts: tuple[RelationFact, ...]
    incomplete: bool
    coverage_gaps: tuple[str, ...]
    checked_at: str

    def __post_init__(self) -> None:
        _sections(self.sections)
        _items(self.evidence, EvidenceCard, 480)
        _items(self.relation_facts, RelationFact, 480)
        _exact(self.incomplete, bool)
        _strings(self.coverage_gaps, 20, 200)
        _timestamp(self.checked_at)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: IssueCode
    section_index: int | None
    claim_index: int | None

    def __post_init__(self) -> None:
        _literal(self.code, _ISSUES)
        for value, maximum in ((self.section_index, 6), (self.claim_index, 19)):
            if value is not None and (type(value) is not int or not 0 <= value <= maximum):
                _invalid()
        if self.section_index is None and self.claim_index is not None:
            _invalid()


@dataclass(frozen=True, slots=True)
class ValidationResult:
    answer: Answer | None
    issues: tuple[ValidationIssue, ...]

    def __post_init__(self) -> None:
        _items(self.issues, ValidationIssue, 61)
        if self.answer is not None:
            _exact(self.answer, Answer)
        if (self.answer is None) != bool(self.issues):
            _invalid()


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _diagnostic_fact("duplicateKeysAbsent", False)
            _invalid()
        result[key] = value
    return result


def _constant(value: str) -> NoReturn:
    _diagnostic_fact("finiteNumbersOnly", False)
    _invalid()


def _schema(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict:
        _invalid()
    result = cast(dict[str, object], value)
    if result.keys() != keys:
        _invalid()
    return result


def _array(value: object, maximum: int) -> list[object]:
    if type(value) is not list or len(value) > maximum:
        _invalid()
    return cast(list[object], value)


def parse_model_answer(payload: bytes) -> ModelAnswer:
    """Parse a closed, bounded JSON schema without repairs or retained JSON."""
    try:
        size_valid = type(payload) is bytes and 1 <= len(payload) <= 65536
        _diagnostic_fact("payloadSizeValid", size_valid)
        if not size_valid:
            _invalid()
        try:
            decoded = payload.decode("utf-8", errors="strict")
        except UnicodeError:
            _diagnostic_fact("utf8Valid", False)
            raise
        _diagnostic_fact("utf8Valid", True)
        bom_absent = not decoded.startswith("\ufeff")
        _diagnostic_fact("bomAbsent", bom_absent)
        if not bom_absent:
            _invalid()
        try:
            parsed = json.loads(decoded, object_pairs_hook=_pairs, parse_constant=_constant)
        except json.JSONDecodeError:
            _diagnostic_fact("jsonValid", False)
            raise
        _diagnostic_fact("jsonValid", True)
        _diagnostic_fact("duplicateKeysAbsent", True)
        _diagnostic_fact("rootObject", type(parsed) is dict)
        if type(parsed) is dict:
            value = _checked("rootKeysExact", lambda: _schema(parsed, {"sections"}))
        else:
            value = _schema(parsed, {"sections"})
        sections = []
        if type(value["sections"]) is list:
            _diagnostic_fact("sectionCount", len(value["sections"]))
        for section_index, item in enumerate(
                _checked("sectionsArray", lambda: _array(value["sections"], 7))):
            _location(section_index, None)
            section = _checked("sectionKeysExact", partial(_schema, item, {"kind", "claims"}))
            claims = []
            for claim_index, raw in enumerate(
                    _checked("claimsArray", partial(_array, section["claims"], 20))):
                _location(section_index, claim_index)
                claim = _checked("claimKeysExact", partial(
                    _schema, raw, {"text", "basis", "evidenceIds", "relationEvidenceId"}))
                try:
                    evidence_values = _array(claim["evidenceIds"], 8)
                except ResponseError:
                    _diagnostic_fact("evidenceIdsValid", False)
                    raise
                claims.append(ModelClaim(cast(str, claim["text"]), cast(Basis, claim["basis"]),
                                         cast(tuple[str, ...], tuple(evidence_values)),
                                         cast(str | None, claim["relationEvidenceId"])))
            _location(section_index, None)
            sections.append(Section(cast(SectionKind, section["kind"]), tuple(claims)))
        _location(None, None)
        return ModelAnswer(tuple(sections))
    except Exception:
        pass
    _invalid()


def _target(value: ChosenTarget) -> ChosenTarget:
    if value is None:
        return None
    if type(value) is EntityRef:
        _exact(value, EntityRef)
        _text(value.id, 512)
        return EntityRef(value.kind, value.id)
    if type(value) is RelationKey:
        _exact(value, RelationKey)
        return RelationKey(value.collection_kind, value.original_index)
    _invalid()


def _target_json(value: ChosenTarget) -> dict[str, object] | None:
    if value is None:
        return None
    if isinstance(value, EntityRef):
        return {"kind": value.kind, "id": value.id}
    return {"collectionKind": value.collection_kind, "originalIndex": value.original_index}


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _provenance(snapshot: AnalysisSnapshot, chosen: EntityRef | RelationKey) -> (
        SymbolRecord | CodeNodeRecord | ComponentRecord | CodeEdgeRecord | RelationRecord):
    if isinstance(chosen, EntityRef):
        if chosen.kind == "symbol":
            symbol = snapshot.get_symbol(chosen.id)
            if type(symbol) is not SymbolRecord or not symbol.definition:
                _invalid()
            return symbol
        if chosen.kind == "code":
            node = snapshot.get_code_node(chosen.id)
            if type(node) is not CodeNodeRecord:
                _invalid()
            return node
        component = snapshot.get_component(chosen.id)
        if type(component) is not ComponentRecord:
            _invalid()
        return component
    inventory = (snapshot.code_edge_inventory if chosen.collection_kind == "code"
                 else snapshot.relation_inventory)
    if chosen.original_index >= len(inventory):
        _invalid()
    edge = inventory[chosen.original_index]
    cls = CodeEdgeRecord if chosen.collection_kind == "code" else RelationRecord
    if type(edge) is CodeEdgeRecord:
        if type(edge.possible_targets) is not tuple or len(edge.possible_targets) > 1000:
            _invalid()
    _exact(edge, cls)
    if edge.original_index != chosen.original_index:
        _invalid()
    if isinstance(edge, CodeEdgeRecord):
        identifiers = (edge.source, edge.target, *edge.possible_targets)
        required = frozenset(identifiers)
        code_inventory = snapshot.code_node_inventory
        if type(code_inventory) is not tuple or len(code_inventory) > MAX_SEQUENCE_ENTRIES:
            _invalid()
        selected: dict[str, CodeNodeRecord] = {}
        for code_node in code_inventory:
            if type(code_node) is not CodeNodeRecord or type(code_node.id) is not str:
                _invalid()
            if code_node.id in required:
                if code_node.id in selected:
                    _invalid()
                selected[code_node.id] = code_node
        code_index = MappingProxyType(selected)
        for identifier in required:
            node = code_index.get(identifier)
            if type(node) is not CodeNodeRecord:
                _invalid()
            _exact(node, CodeNodeRecord)
    else:
        for identifier in (edge.source, edge.target):
            component = snapshot.get_component(identifier)
            if type(component) is not ComponentRecord:
                _invalid()
            _exact(component, ComponentRecord)
    return edge


def _resolve_existing_evidence(
    registry: EvidenceRegistry, snapshot: AnalysisSnapshot, scope: RequestScope,
    evidence_id: str, chosen: ChosenTarget, proof: B5EvidenceBinding,
) -> ResolvedEvidence:
    """Strictly validate one already-issued proof; never read, issue, or bind."""
    _exact(proof, B5EvidenceBinding)
    record = registry.lookup(evidence_id, scope=scope,
                             expected_file_hash=proof.content_hash)
    result = ResolvedEvidence(record, proof)
    if (not snapshot.matches_scope(scope) or proof.evidence_id != evidence_id
            or proof.scope != scope or proof.snapshot_binding != snapshot.binding
            or proof.target != chosen or record.scope != scope
            or record.content_hash != proof.content_hash or record.truncated != proof.truncated
            or record.confidence is not Confidence.EXACT or record.symbol_id is not None
            or record.code_edge_id is not None or record.semantic_id is not None):
        _invalid()
    a, b = record.anchor, proof.returned_anchor
    if ((a.file_path, a.start_line, a.start_column, a.end_line, a.end_column) !=
            (b.file_path, b.start_line, b.start_column, b.end_line, b.end_column)):
        _invalid()
    file = snapshot.get_file(a.file_path)
    if (file is None or not file.was_parsed or file.content_hash != proof.content_hash
            or not 1 <= b.start_line <= b.end_line <= file.line_count):
        _invalid()
    if chosen is None:
        if (proof.status != "source_only" or proof.provenance is not None
                or proof.confidence is not Confidence.EXACT):
            _invalid()
    else:
        if proof.status != "bound":
            _invalid()
        expected = _provenance(snapshot, chosen)
        if type(proof.provenance) is not type(expected) or proof.provenance != expected:
            _invalid()
        _exact(expected, type(expected))
        confidence = Confidence.EXACT if isinstance(expected, SymbolRecord) else expected.confidence
        if proof.confidence is not confidence:
            _invalid()
    return result


class ActualEvidenceResolver:
    """Resolve only this request's chosen associations using existing B5 lookup."""

    __slots__ = ("_session", "_registry", "_snapshot", "_scope", "_provided")

    def __init__(self, session: ToolSession, registry: EvidenceRegistry,
                 snapshot: AnalysisSnapshot, scope: RequestScope,
                 provided: Mapping[str, ChosenTarget]) -> None:
        try:
            _exact(session, ToolSession)
            _exact(registry, EvidenceRegistry)
            _exact(snapshot, AnalysisSnapshot)
            _scope_value(scope)
            if not snapshot.matches_scope(scope) or not isinstance(provided, Mapping):
                _invalid()
            # B5 currently has no public composition identity port. These fixed
            # owner identities prevent substituting an unrelated A registry;
            # receipt/authorization/range authority remains exclusively lookup.
            if (session._registry is not registry or session._snapshot is not snapshot
                    or session._scope != scope):
                _invalid()
            copied: dict[str, ChosenTarget] = {}
            size = 2
            associations = 0
            for identifier, raw_target in provided.items():
                associations += 1
                if associations > 1000:
                    _invalid()
                _text(identifier, 512)
                chosen = _target(raw_target)
                if identifier in copied:
                    if copied[identifier] != chosen:
                        _invalid()
                    continue
                size += len(_json_bytes(identifier)) + 1 + len(_json_bytes(_target_json(chosen)))
                if copied:
                    size += 1
                if size > _MAX_BYTES:
                    _invalid()
                copied[identifier] = chosen
            self._session = session
            self._registry = registry
            self._snapshot = snapshot
            self._scope = RequestScope(scope.project_epoch, scope.analysis_generation, scope.request_id)
            self._provided: Mapping[str, ChosenTarget] = MappingProxyType(copied)
            return
        except Exception:
            pass
        _invalid()

    def resolve_evidence(self, evidence_id: str) -> ResolvedEvidence:
        try:
            _text(evidence_id, 512)
            if evidence_id not in self._provided:
                _invalid()
            chosen = self._provided[evidence_id]
            _target(chosen)
            proof = self._session.lookup_binding(evidence_id, chosen)
            return _resolve_existing_evidence(
                self._registry, self._snapshot, self._scope,
                evidence_id, chosen, proof,
            )
        except Exception:
            pass
        _invalid()

    def _provenance(self, chosen: EntityRef | RelationKey) -> (
            SymbolRecord | CodeNodeRecord | ComponentRecord | CodeEdgeRecord | RelationRecord):
        return _provenance(self._snapshot, chosen)


def _card(identifier: str, resolved: ResolvedEvidence) -> EvidenceCard:
    record, proof = resolved.record, resolved.proof
    a = record.anchor
    bound = proof.status == "bound"
    return EvidenceCard(identifier, f"{a.file_path}:{a.start_line}-{a.end_line}",
                        proof.confidence if bound else record.confidence, record.truncated,
                        True, bound, "navigation_check_required" if bound else "source_only")


def _fact(identifier: str, resolved: ResolvedEvidence) -> RelationFact:
    proof = resolved.proof
    if type(proof.target) is not RelationKey or proof.status != "bound":
        _invalid()
    edge = proof.provenance
    if type(edge) not in (CodeEdgeRecord, RelationRecord):
        _invalid()
    edge = cast(CodeEdgeRecord | RelationRecord, edge)
    category = _CATEGORIES.get(edge.type)
    if category is None:
        _invalid()
    return RelationFact(identifier, edge.source, edge.target, edge.type, edge.confidence,
                        edge.possible_targets if isinstance(edge, CodeEdgeRecord) else (), category)


def _claim_json(claim: ModelClaim) -> dict[str, object]:
    return {"text": claim.text, "basis": claim.basis, "evidenceIds": list(claim.evidence_ids),
            "relationEvidenceId": claim.relation_evidence_id}


def _card_json(card: EvidenceCard) -> dict[str, object]:
    return {"evidenceId": card.evidence_id, "label": card.label, "confidence": card.confidence.value,
            "truncated": card.truncated, "sourceAvailable": card.source_available,
            "graphAvailable": card.graph_available, "unavailableReason": card.unavailable_reason}


def _fact_json(fact: RelationFact) -> dict[str, object]:
    return {"evidenceId": fact.evidence_id, "sourceId": fact.source_id, "targetId": fact.target_id,
            "type": fact.type.value, "confidence": fact.confidence.value,
            "candidateTargets": list(fact.candidate_targets), "category": fact.category}


def _answer_json(answer: Answer) -> dict[str, object]:
    return {"sections": [{"kind": s.kind, "claims": [_claim_json(c) for c in s.claims]}
                         for s in answer.sections],
            "evidence": [_card_json(c) for c in answer.evidence],
            "relationFacts": [_fact_json(f) for f in answer.relation_facts],
            "incomplete": answer.incomplete, "coverageGaps": list(answer.coverage_gaps),
            "checkedAt": answer.checked_at}


def validate_model_answer(
    model: ModelAnswer, *, resolver: ActualEvidenceResolver,
    checked_at: str, incomplete: bool = False, coverage_gaps: tuple[str, ...] = (),
) -> ValidationResult:
    """Validate once against current receipts; never read, bind or dispatch."""
    invalid_schema = False
    try:
        _exact(model, ModelAnswer)
        _exact(resolver, ActualEvidenceResolver)
        _timestamp(checked_at)
        _exact(incomplete, bool)
        _strings(coverage_gaps, 20, 200)
    except Exception:
        invalid_schema = True
    if invalid_schema:
        return ValidationResult(None, (ValidationIssue("invalid_schema", None, None),))

    issues: list[ValidationIssue] = []
    cache: dict[str, ResolvedEvidence | None] = {}
    cards: dict[str, EvidenceCard] = {}
    facts: dict[str, RelationFact] = {}
    sections = tuple(Section(section.kind, tuple(
        ModelClaim(claim.text, "inference" if claim.basis == "static" else claim.basis,
                   claim.evidence_ids, claim.relation_evidence_id)
        for claim in section.claims)) for section in model.sections)
    # Charge each canonical object once, including the complete model envelope.
    initial = Answer(sections, (), (), incomplete, coverage_gaps, checked_at)
    size = len(_json_bytes(_answer_json(initial)))
    for section_index, section in enumerate(model.sections):
        for claim_index, claim in enumerate(section.claims):
            _location(section_index, claim_index)
            issue: IssueCode | None = None
            for identifier in claim.evidence_ids:
                if identifier not in resolver._provided:
                    if issue is None:
                        issue = "unprovided_id"
                    continue
                if identifier not in cache:
                    try:
                        resolved = resolver.resolve_evidence(identifier)
                        _exact(resolved, ResolvedEvidence)
                        card = _card(identifier, resolved)
                        extra = len(_json_bytes(_card_json(card))) + (1 if cards else 0)
                        if len(cards) >= 480 or size + extra > _MAX_BYTES:
                            _invalid()
                        cache[identifier] = resolved
                        cards[identifier] = card
                        size += extra
                    except Exception:
                        cache[identifier] = None
                if cache[identifier] is None and issue is None:
                    issue = "invalid_evidence"
            if issue is None and claim.basis == "static":
                if not claim.evidence_ids:
                    issue = "static_requires_ids"
                elif section.kind == "relations" and claim.relation_evidence_id is None:
                    issue = "relation_required"
            relation_id = claim.relation_evidence_id
            if relation_id is not None and cache.get(relation_id) is not None:
                relation_resolved = cache[relation_id]
                assert relation_resolved is not None
                try:
                    fact = _fact(relation_id, relation_resolved)
                except Exception:
                    if issue is None:
                        issue = ("invalid_evidence" if type(relation_resolved.proof.target) is RelationKey
                                 else "invalid_relation")
                else:
                    if relation_id not in facts:
                        extra = len(_json_bytes(_fact_json(fact))) + (1 if facts else 0)
                        if len(facts) >= 480 or size + extra > _MAX_BYTES:
                            if issue is None:
                                issue = "invalid_evidence"
                        else:
                            facts[relation_id] = fact
                            size += extra
            if issue is None and claim.basis == "unknown":
                marker = _UNKNOWN_MARKER.search(claim.text)
                _diagnostic_fact("unknownMarkerPresent", marker is not None)
                if marker is None:
                    issue = "invalid_schema"
            if issue is not None:
                issues.append(ValidationIssue(issue, section_index, claim_index))
    if issues:
        return ValidationResult(None, tuple(issues))
    try:
        answer = Answer(sections, tuple(cards.values()), tuple(facts.values()),
                        incomplete, coverage_gaps, checked_at)
        if len(_json_bytes(_answer_json(answer))) > _MAX_BYTES:
            _invalid()
        return ValidationResult(answer, ())
    except Exception:
        pass
    return ValidationResult(None, (ValidationIssue("invalid_evidence", None, None),))
