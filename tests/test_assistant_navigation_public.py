"""Closed public projection for locally verified assistant navigation targets."""

import importlib
import importlib.util
import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypeVar

from assistant_tool_fixtures import make_session, source_case_with

from codeviz.assistant.contracts import RequestScope
from codeviz.assistant.navigation import build_navigation_target
from codeviz.assistant.response import ActualEvidenceResolver
from codeviz.assistant.snapshot import AnchorRecord, SnapshotBinding
from codeviz.assistant.source_excerpts import ExcerptRequest
from codeviz.assistant.source_text import MonacoRange
from codeviz.assistant.tool_contracts import EntityRef
from codeviz.core import SourceAnchor, ViewKind
from codeviz.navigation import RevealTarget

_T = TypeVar("_T")
_MAX_SAFE_INTEGER = 2**53 - 1


def _unchecked(cls: type[_T], **fields: object) -> _T:
    value = object.__new__(cls)
    for name, field in fields.items():
        object.__setattr__(value, name, field)
    return value


class NavigationPublicTests(unittest.TestCase):
    def api(self):
        name = "codeviz.assistant.navigation_public"
        self.assertIsNotNone(importlib.util.find_spec(name), "Missing C7 API: " + name)
        module = importlib.import_module(name)
        self.assertTrue(hasattr(module, "navigation_target_to_public"))
        return module

    def target(self, chosen: EntityRef | None = None, *, action: str = "source"):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        case = source_case_with(Path(temporary.name), "#😀中z\n".encode())
        session = make_session(case)
        self.addCleanup(session.close)
        excerpt = session.read_source_excerpt(
            ExcerptRequest(AnchorRecord(
                "app.py", 1, 0 if chosen is not None else 1, 1, 8)))
        session.bind_evidence(excerpt.evidence_id, chosen)
        resolved = ActualEvidenceResolver(
            session, case.registry, case.snapshot, case.scope,
            {excerpt.evidence_id: chosen},
        ).resolve_evidence(excerpt.evidence_id)
        reveal = (RevealTarget(ViewKind.SYMBOL, chosen.id, chosen.id)
                  if action == "graph" and chosen is not None else None)
        target = build_navigation_target(
            resolved, case.snapshot, case.scope, action, reveal=reveal,
            checked_at="2026-10-18T00:00:00Z",
            conversation_id="conversation-1", conversation_revision=3,
            config_revision=4, consent_revision=5,
        )
        return case, resolved, target

    def assertUnavailable(self, target: object) -> None:
        api = self.api()
        with self.assertRaises(api.NavigationError) as caught:
            api.navigation_target_to_public(target)
        self.assertEqual(str(caught.exception), "Assistant navigation is unavailable.")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_source_target_is_an_exact_whitelisted_public_projection(self):
        api = self.api()
        case, resolved, target = self.target()

        public = api.navigation_target_to_public(target)

        self.assertEqual(public, {
            "evidenceId": resolved.proof.evidence_id,
            "scope": {
                "projectEpoch": case.scope.project_epoch,
                "analysisGeneration": case.scope.analysis_generation,
                "requestId": "request-b5",
            },
            "binding": {
                "projectEpoch": case.snapshot.binding.project_epoch,
                "analysisGeneration": case.snapshot.binding.analysis_generation,
            },
            "action": "source",
            "astAnchor": {
                "filePath": "app.py", "startLine": 1, "startColumn": 1,
                "endLine": 1, "endColumn": 8,
            },
            "monacoRange": None,
            "reveal": None,
            "checkedAt": "2026-10-18T00:00:00Z",
            "conversationId": "conversation-1",
            "conversationRevision": 3,
            "configRevision": 4,
            "consentRevision": 5,
        })
        self.assertNotIn("projectRoot", public["binding"])
        self.assertNotIn("ignorePaths", public["binding"])
        encoded = json.dumps(
            public, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        self.assertLessEqual(len(encoded), 8192)

    def test_source_preserves_only_an_already_verified_monaco_range(self):
        api = self.api()
        _, _, target = self.target()
        target = replace(target, monaco_range=MonacoRange(1, 2, 1, 5))

        public = api.navigation_target_to_public(target)

        self.assertEqual(public["astAnchor"], {
            "filePath": "app.py", "startLine": 1, "startColumn": 1,
            "endLine": 1, "endColumn": 8,
        })
        self.assertEqual(public["monacoRange"], {
            "startLineNumber": 1, "startColumn": 2,
            "endLineNumber": 1, "endColumn": 5,
        })

    def test_missing_ast_column_clears_both_columns_and_monaco_range(self):
        api = self.api()
        _, _, target = self.target()
        target = replace(
            target,
            ast_anchor=SourceAnchor("app.py", 1, None, 1, 8),
            monaco_range=MonacoRange(1, 1, 1, 5),
        )

        public = api.navigation_target_to_public(target)

        self.assertEqual(public["astAnchor"], {
            "filePath": "app.py", "startLine": 1, "startColumn": None,
            "endLine": 1, "endColumn": None,
        })
        self.assertIsNone(public["monacoRange"])

    def test_graph_projects_only_a_usable_existing_reveal(self):
        api = self.api()
        _, _, target = self.target(EntityRef("code", "seed"), action="graph")

        public = api.navigation_target_to_public(target)

        self.assertEqual(public["action"], "graph")
        self.assertIsNone(public["monacoRange"])
        self.assertEqual(public["reveal"], {
            "viewKind": "Symbol", "rootId": "seed",
            "selectedId": "seed", "reason": None,
        })

    def test_rejects_scope_binding_and_revision_values_outside_public_contract(self):
        _, _, target = self.target()
        large_scope = RequestScope("epoch-b5", 2**53, "request-b5")
        large_binding = replace(target.binding, analysis_generation=2**53)
        bad_hidden_binding = _unchecked(
            SnapshotBinding, project_root=3, ignore_paths=(),
            project_epoch="epoch-b5", analysis_generation=1,
        )
        cases = (
            replace(target, scope=large_scope, binding=large_binding),
            replace(target, binding=replace(target.binding, project_epoch="other")),
            replace(target, binding=bad_hidden_binding),
            replace(target, conversation_revision=True),
            replace(target, config_revision=-1),
            replace(target, consent_revision=_MAX_SAFE_INTEGER + 1),
        )
        for case in cases:
            with self.subTest(case=case):
                self.assertUnavailable(case)

    def test_rejects_non_public_ids_and_invalid_utc_timestamps(self):
        _, _, target = self.target()
        for case in (
            replace(target, evidence_id=""),
            replace(target, evidence_id="e" * 513),
            replace(target, evidence_id="bad\nvalue"),
            replace(target, conversation_id="c" * 129),
            replace(target, conversation_id="bad\ud800"),
            replace(target, checked_at="2026-02-30T00:00:00Z"),
            replace(target, checked_at="2026-10-18T00:00:00+08:00"),
            replace(target, checked_at="2026-10-18Z"),
        ):
            with self.subTest(case=case):
                self.assertUnavailable(case)

    def test_rejects_noncanonical_paths_and_invalid_ast_coordinates(self):
        _, _, target = self.target()
        paths = (
            "app\\module.py", "/app.py", "app//module.py", "app/./module.py",
            "app/../module.py", "C:app.py", "app/\x01.py", "a" * 513,
        )
        for path in paths:
            anchor = _unchecked(
                SourceAnchor, file_path=path, start_line=1, start_column=0,
                end_line=1, end_column=1,
            )
            with self.subTest(path=path):
                self.assertUnavailable(replace(target, ast_anchor=anchor))
        for anchor in (
            _unchecked(SourceAnchor, file_path="app.py", start_line=0, start_column=0,
                       end_line=1, end_column=1),
            SourceAnchor("app.py", 1, 0, 1, _MAX_SAFE_INTEGER + 1),
            _unchecked(SourceAnchor, file_path="app.py", start_line=1, start_column=2,
                       end_line=1, end_column=1),
        ):
            with self.subTest(anchor=anchor):
                self.assertUnavailable(replace(target, ast_anchor=anchor))

    def test_rejects_unverified_source_ranges_and_action_specific_fields(self):
        _, _, source = self.target()
        _, _, graph = self.target(EntityRef("code", "seed"), action="graph")
        outside = replace(source, monaco_range=MonacoRange(1, 1, 2, 1))
        reversed_range = _unchecked(
            MonacoRange, start_line=1, start_column=3, end_line=1, end_column=2,
        )
        cases = (
            outside,
            replace(source, monaco_range=reversed_range),
            replace(source, reveal=RevealTarget(ViewKind.SYMBOL, None, "seed")),
            replace(graph, monaco_range=MonacoRange(1, 1, 1, 2)),
            replace(graph, reveal=None),
            replace(graph, reveal=RevealTarget(ViewKind.SYMBOL, None, "")),
            replace(graph, reveal=RevealTarget(ViewKind.SYMBOL, None, "seed", "failed")),
        )
        for case in cases:
            with self.subTest(case=case):
                self.assertUnavailable(case)

    def test_rejects_a_target_whose_compact_utf8_projection_exceeds_8192_bytes(self):
        _, _, target = self.target(EntityRef("code", "seed"), action="graph")
        huge = "😀" * 512
        target = replace(
            target, evidence_id=huge, conversation_id="😀" * 128,
            ast_anchor=SourceAnchor(huge, 1, 0, 1, 1),
            reveal=RevealTarget(ViewKind.SYMBOL, huge, huge),
        )

        self.assertUnavailable(target)


if __name__ == "__main__":
    unittest.main()
