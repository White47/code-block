"""Closed C7 projection of receipt-validated assistant answers."""

import importlib
import importlib.util
import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from assistant_tool_fixtures import make_source_case

from codeviz.assistant.response import (
    ActualEvidenceResolver,
    Answer,
    EvidenceCard,
    ModelAnswer,
    ModelClaim,
    ResponseError,
    Section,
    validate_model_answer,
)
from codeviz.assistant.tool_contracts import RelationKey
from codeviz.assistant.tools import create_tool_session
from codeviz.core import Confidence, EdgeType

KINDS = ("summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")
CHECKED_AT = "2026-10-18T00:00:00Z"


class AssistantAnswerPublicTests(unittest.TestCase):
    def answer_api(self):
        module_name = "codeviz.assistant.answer_public"
        self.assertIsNotNone(importlib.util.find_spec(module_name), "Missing C7 answer projection")
        return importlib.import_module(module_name)

    def local_answer(self, claim=None, evidence=()):
        return Answer(tuple(
            Section(kind, (claim,) if claim is not None and kind == "summary" else ())
            for kind in KINDS
        ), evidence, (), False, (), CHECKED_AT)

    def assert_fixed_error(self, action, marker=None):
        with self.assertRaises(ResponseError) as caught:
            action()
        error = caught.exception
        self.assertEqual(error.code, "AI_EVIDENCE_INVALID")
        self.assertEqual(str(error), "Assistant evidence validation failed.")
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        if marker is not None:
            self.assertNotIn(marker, str(error))

    def test_real_receipt_validated_answer_projects_canonical_public_shape(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        case = make_source_case(Path(temporary.name), b"value = 1\n")
        session = create_tool_session(
            case.snapshot,
            scope=case.scope,
            consent=case.consent,
            ledger=case.ledger,
            registry=case.registry,
            current_state=case.state,
        )
        self.addCleanup(session.close)
        excerpt = session.dispatch(
            "read_source_excerpt",
            {"file_path": "app.py", "start_line": 1, "end_line": 1},
        )
        target = RelationKey("code", 0)
        proof = session.bind_evidence(excerpt.evidence_id, target)
        resolver = ActualEvidenceResolver(
            session,
            case.registry,
            case.snapshot,
            case.scope,
            {excerpt.evidence_id: target},
        )
        relation_claim = ModelClaim(
            "seed calls neighbor",
            "static",
            (excerpt.evidence_id,),
            excerpt.evidence_id,
        )
        model = ModelAnswer(tuple(
            Section(kind, (relation_claim,) if kind == "relations" else ())
            for kind in KINDS
        ))
        result = validate_model_answer(model, resolver=resolver, checked_at=CHECKED_AT)
        self.assertEqual(result.issues, ())
        self.assertIsNotNone(result.answer)

        api = self.answer_api()
        public = api.answer_to_public(result.answer)

        self.assertEqual(public, {
            "sections": [
                {"kind": kind, "claims": ([{
                    "text": "seed calls neighbor",
                    "basis": "inference",
                    "evidenceIds": [excerpt.evidence_id],
                    "relationEvidenceId": excerpt.evidence_id,
                }] if kind == "relations" else [])}
                for kind in KINDS
            ],
            "evidence": [{
                "evidenceId": excerpt.evidence_id,
                "label": "app.py:1-1",
                "confidence": proof.confidence.value,
                "truncated": False,
                "sourceAvailable": True,
                "graphAvailable": True,
                "unavailableReason": "navigation_check_required",
            }],
            "relationFacts": [{
                "evidenceId": excerpt.evidence_id,
                "sourceId": "seed",
                "targetId": "neighbor",
                "type": EdgeType.CALLS.value,
                "confidence": Confidence.PROBABLE.value,
                "candidateTargets": [],
                "category": "call",
            }],
            "incomplete": False,
            "coverageGaps": [],
            "checkedAt": CHECKED_AT,
        })
        self.assertLessEqual(len(json.dumps(
            public, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")), 65536)

    def test_rejects_static_prose_that_bypasses_the_validator(self):
        api = self.answer_api()
        bypass = self.local_answer(ModelClaim("internal static claim", "static", (), None))

        self.assert_fixed_error(lambda: api.answer_to_public(bypass), "internal static claim")

    def test_rejects_non_exact_and_recursively_forged_answers_without_internal_echo(self):
        api = self.answer_api()
        valid = self.local_answer()
        marker = "PRIVATE_INTERNAL_FIELD"

        class AnswerSubclass(Answer):
            __slots__ = ("internal_marker",)

        extra = AnswerSubclass(
            valid.sections,
            valid.evidence,
            valid.relation_facts,
            valid.incomplete,
            valid.coverage_gaps,
            valid.checked_at,
        )
        object.__setattr__(extra, "internal_marker", marker)

        forged = object.__new__(Answer)
        object.__setattr__(forged, "sections", list(valid.sections))
        for field_name in ("evidence", "relation_facts", "incomplete", "coverage_gaps", "checked_at"):
            object.__setattr__(forged, field_name, getattr(valid, field_name))

        for candidate in (extra, forged):
            with self.subTest(candidate=type(candidate).__name__):
                self.assert_fixed_error(lambda candidate=candidate: api.answer_to_public(candidate), marker)

    def test_rejects_complete_compact_utf8_projection_at_64_kib_plus_one(self):
        api = self.answer_api()
        card = EvidenceCard(
            "evidence-1",
            "x",
            Confidence.EXACT,
            False,
            True,
            False,
            "source_only",
        )
        base = self.local_answer(evidence=(card,))
        base_public = api.answer_to_public(base)
        base_size = len(json.dumps(
            base_public, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8"))
        oversized_label = "x" * (65538 - base_size)
        oversized = replace(base, evidence=(replace(card, label=oversized_label),))
        self.assertEqual(base_size - 1 + len(oversized_label.encode("utf-8")), 65537)

        self.assert_fixed_error(lambda: api.answer_to_public(oversized))


if __name__ == "__main__":
    unittest.main()
