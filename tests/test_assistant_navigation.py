"""C6 navigation DTOs use B5 evidence and immutable snapshot facts."""

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from assistant_tool_fixtures import make_session, source_case_with

from codeviz.assistant.contracts import RequestScope
from codeviz.assistant.response import ActualEvidenceResolver, ResolvedEvidence
from codeviz.assistant.snapshot import AnchorRecord
from codeviz.assistant.source_excerpts import ExcerptRequest
from codeviz.assistant.tool_contracts import EntityRef, RelationKey
from codeviz.core import SourceAnchor, ViewKind
from codeviz.navigation import RevealTarget


class NavigationTests(unittest.TestCase):
    def source(self, chosen=None):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        case = source_case_with(Path(temporary.name), "#😀中z\n".encode())
        session = make_session(case)
        self.addCleanup(session.close)
        excerpt = session.read_source_excerpt(
            ExcerptRequest(AnchorRecord("app.py", 1, 0 if chosen is not None else 1, 1, 8)))
        session.bind_evidence(excerpt.evidence_id, chosen)
        resolver = ActualEvidenceResolver(
            session, case.registry, case.snapshot, case.scope,
            {excerpt.evidence_id: chosen})
        return case, resolver.resolve_evidence(excerpt.evidence_id)

    def build(self, case, resolved, action="source", **kwargs):
        from codeviz.assistant.navigation import build_navigation_target

        return build_navigation_target(
            resolved, case.snapshot, case.scope, action,
            checked_at="2026-10-16T00:00:00Z", conversation_id="local-conversation",
            conversation_revision=3, config_revision=4, consent_revision=5, **kwargs)

    def test_target_binds_trusted_local_revisions(self):
        case, resolved = self.source()
        target = self.build(case, resolved)
        self.assertEqual((target.conversation_id, target.conversation_revision,
                          target.config_revision, target.consent_revision),
                         ("local-conversation", 3, 4, 5))

    def test_invalid_local_admission_values_fail_closed(self):
        from codeviz.assistant.navigation import (
            NavigationError,
            build_navigation_target,
        )

        case, resolved = self.source()
        valid = dict(conversation_id="local", conversation_revision=0,
                     config_revision=0, consent_revision=0)
        for field, value in (("conversation_id", ""), ("conversation_revision", -1),
                             ("config_revision", True), ("consent_revision", -1)):
            with self.subTest(field=field):
                with self.assertRaises(NavigationError):
                    build_navigation_target(
                        resolved, case.snapshot, case.scope, "source",
                        checked_at="2026-10-16T00:00:00Z", **(valid | {field: value}))

    def test_graph_preserves_existing_reveal_for_bound_entities(self):
        for kind, selected in (("symbol", "s0000"), ("code", "seed"),
                               ("semantic", "component-a")):
            with self.subTest(kind=kind):
                case, resolved = self.source(EntityRef(kind, selected))
                reveal = RevealTarget(ViewKind.SYMBOL, selected, selected)
                target = self.build(case, resolved, "graph", reveal=reveal)
                self.assertEqual(target.action, "graph")
                self.assertIs(target.reveal, reveal)

    def test_graph_rejects_provenance_from_another_entity(self):
        from codeviz.assistant.navigation import NavigationError

        case, resolved = self.source(EntityRef("code", "seed"))
        forged = replace(resolved, proof=replace(
            resolved.proof, target=EntityRef("code", "neighbor")))
        with self.assertRaises(NavigationError):
            self.build(case, forged, "graph",
                       reveal=RevealTarget(ViewKind.SYMBOL, "neighbor", "neighbor"))

    def test_graph_rejects_unproven_selection_and_relation_mapping(self):
        from codeviz.assistant.navigation import NavigationError

        for chosen, reveal in (
            (None, RevealTarget(ViewKind.SYMBOL, "seed", "seed")),
            (EntityRef("code", "seed"), None),
            (EntityRef("code", "seed"), RevealTarget(ViewKind.SYMBOL, "seed", "neighbor")),
            (EntityRef("code", "seed"), RevealTarget(None, None, "seed")),
            (EntityRef("code", "seed"), RevealTarget(ViewKind.SYMBOL, "seed", "seed", "failed")),
            (RelationKey("code", 0), RevealTarget(ViewKind.SYMBOL, "seed", "seed")),
            (RelationKey("semantic", 0),
             RevealTarget(ViewKind.EXECUTION, "component-a", "component-a")),
        ):
            with self.subTest(chosen=chosen, reveal=reveal):
                case, resolved = self.source(chosen)
                with self.assertRaises(NavigationError):
                    self.build(case, resolved, "graph", reveal=reveal)

    def test_source_uses_a_anchor_and_does_not_guess_utf16_columns(self):
        from codeviz.assistant.navigation import build_navigation_target

        case, resolved = self.source()
        target = build_navigation_target(
            resolved, case.snapshot, case.scope, "source",
            conversation_id="local-conversation", conversation_revision=3,
            config_revision=4, consent_revision=5,
            checked_at="2026-10-16T00:00:00Z")
        self.assertEqual(target.ast_anchor, SourceAnchor("app.py", 1, 1, 1, 8))
        self.assertIsNone(target.monaco_range)
        self.assertIsNone(target.reveal)
        self.assertEqual(target.binding, case.snapshot.binding)
        self.assertEqual(target.scope, case.scope)
        self.assertEqual(target.evidence_id, resolved.proof.evidence_id)
        self.assertEqual(target.checked_at, "2026-10-16T00:00:00Z")

    def test_stale_scope_or_proof_and_graph_without_local_reveal_fail_closed(self):
        from codeviz.assistant.navigation import (
            NavigationError,
            build_navigation_target,
        )

        case, resolved = self.source()
        stale = RequestScope("other-epoch", case.scope.analysis_generation,
                             case.scope.request_id)
        forged = ResolvedEvidence(resolved.record, replace(resolved.proof,
                                                           content_hash="0" * 64))
        for evidence, scope, action in (
            (resolved, stale, "source"),
            (forged, case.scope, "source"),
            (resolved, case.scope, "graph"),
        ):
            with self.subTest(scope=scope, action=action, forged=evidence is forged):
                with self.assertRaises(NavigationError):
                    build_navigation_target(
                        evidence, case.snapshot, scope, action,
                        conversation_id="local-conversation", conversation_revision=3,
                        config_revision=4, consent_revision=5,
                        checked_at="2026-10-16T00:00:00Z")
