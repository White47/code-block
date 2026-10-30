"""Closed local references to an original validated answer, never model authority."""

import uuid
from typing import cast

from .bridge_requests import _id, _natural
from .contracts import RequestScope
from .project_retrieval import ProjectCoverage
from .response import Answer

_FIELDS = frozenset({"schemaVersion", "artifactId", "revision", "projectEpoch",
    "analysisGeneration", "conversationId", "conversationRevision", "jobId",
    "configRevision", "consentRevision", "coverage"})
_COUNTS = ("indexedFileCount", "authorizedFileCount", "metadataFileCount", "sourceFileCount")
_GAPS = frozenset({"representative_sample", "retrieval_gap", "unverified"})


def validate_reference(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != _FIELDS:
        raise ValueError()
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise ValueError()
    for field in ("artifactId", "projectEpoch", "conversationId", "jobId"):
        _id(value[field])
    for field in ("revision", "analysisGeneration", "conversationRevision",
                  "configRevision", "consentRevision"):
        _natural(value[field], 1 if field in ("revision", "analysisGeneration") else 0)
    coverage = value["coverage"]
    if type(coverage) is not dict or set(coverage) != {*_COUNTS, "gapCodes"}:
        raise ValueError()
    indexed, authorized, metadata, source = (_natural(coverage[field]) for field in _COUNTS)
    if not (metadata <= authorized <= indexed and source <= authorized):
        raise ValueError()
    gaps = coverage["gapCodes"]
    if (type(gaps) is not list or len(gaps) > 3
            or any(type(code) is not str or code not in _GAPS for code in gaps)
            or gaps != sorted(set(gaps))):
        raise ValueError()
    return cast(dict[str, object], value)


def create_reference(scope: RequestScope, conversation_id: str, conversation_revision: int,
                     job_id: str, config_revision: int, consent_revision: int,
                     coverage: ProjectCoverage) -> dict[str, object]:
    """Worker-only factory; revision is assigned by the capacity transaction."""
    if type(coverage) is not ProjectCoverage:
        raise ValueError()
    return validate_reference({"schemaVersion": 1, "artifactId": uuid.uuid4().hex, "revision": 1,
        "projectEpoch": scope.project_epoch, "analysisGeneration": scope.analysis_generation,
        "conversationId": conversation_id, "conversationRevision": conversation_revision,
        "jobId": job_id, "configRevision": config_revision, "consentRevision": consent_revision,
        "coverage": {"indexedFileCount": coverage.indexed_file_count,
            "authorizedFileCount": coverage.authorized_file_count,
            "metadataFileCount": len(coverage.metadata_files), "sourceFileCount": len(coverage.source_files),
            "gapCodes": list(coverage.gap_codes)}})


def owns_answer(reference: dict[str, object], record: dict[str, object]) -> bool:
    """Check the same stored answer occurrence; no copied proof or new lease."""
    if (record["id"] != reference["conversationId"]
            or record["revision"] != reference["conversationRevision"]):
        return False
    messages = cast(list[dict[str, object]], record["messages"])
    answers = [message for message in messages if message.get("role") == "assistant"
               and message.get("jobId") == reference["jobId"]]
    if len(answers) != 1 or answers[0].get("provisional") is True:
        return False
    answer = answers[0]
    scope = answer.get("scope")
    result = cast(dict[str, dict[str, object]], record["results"]).get(cast(str, reference["jobId"]))
    return (type(scope) is dict and result is not None and type(result.get("answer")) is str
            and result["answer"] == answer.get("content")
            and all(scope.get(left) == reference[right] for left, right in (
                ("projectEpoch", "projectEpoch"), ("generation", "analysisGeneration"),
                ("configRevision", "configRevision"), ("consentRevision", "consentRevision"))))


def effective_reference(reference: object, conversations: list[dict[str, object]]) -> dict[str, object] | None:
    value = validate_reference(reference)
    owners = [record for record in conversations if record["conversationId"] == value["conversationId"]
              and record["conversationRevision"] == value["conversationRevision"]]
    if len(owners) != 1:
        return None
    answers = [message for message in cast(list[dict[str, object]], owners[0]["messages"])
               if message["type"] == "validated_answer" and message["jobId"] == value["jobId"]]
    if len(answers) != 1 or answers[0]["stale"] is not False:
        return None
    return value if all(answers[0][field] == value[field] for field in (
        "projectEpoch", "analysisGeneration", "conversationRevision", "configRevision", "consentRevision")) else None


def item_ids(answer: Answer) -> tuple[str, ...]:
    """Canonical item membership comes only from the exact retained answer."""
    if type(answer) is not Answer:
        raise ValueError()
    return ("project", *(f"{section.kind}:{index}" for section in answer.sections
                         for index in range(len(section.claims))))


def item_evidence_ids(answer: Answer, item_id: str) -> frozenset[str]:
    """An item only narrows the original validated evidence set, never grants proof."""
    if type(answer) is not Answer or type(item_id) is not str:
        raise ValueError()
    validated = frozenset(card.evidence_id for card in answer.evidence)
    if item_id == "project":
        return validated
    for section in answer.sections:
        for index, claim in enumerate(section.claims):
            if item_id == f"{section.kind}:{index}":
                own = frozenset(claim.evidence_ids)
                if claim.relation_evidence_id is not None:
                    own |= {claim.relation_evidence_id}
                return validated & own
    return frozenset()
