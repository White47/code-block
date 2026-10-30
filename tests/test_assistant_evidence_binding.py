"""B5 R10--R12: native range support, authoritative provenance and private ownership."""

import unittest
from dataclasses import fields, is_dataclass, replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assistant_tool_fixtures import make_session, require_api, source_case_with

from codeviz.assistant.contracts import AgentError
from codeviz.assistant.evidence import EvidenceRecord
from codeviz.assistant.snapshot import AnchorRecord
from codeviz.assistant.source_excerpts import (
    ExcerptRequest,
    SourceExcerptReader,
)
from codeviz.assistant.source_policy import SourceExcerptError
from codeviz.assistant.source_text import ExcerptLimits
from codeviz.core import Confidence, SourceAnchor
from codeviz.semantic.components import SemanticAnalysis


class EvidenceBindingTests(unittest.TestCase):
    def setUp(self):
        self.dto = require_api(self, "tool_contracts", "EntityRef", "RelationKey", "EvidenceBinding")
        api = require_api(self, "tools", "ToolSession")
        for method in ("read_source_excerpt", "bind_evidence", "lookup_binding", "close"):
            self.assertTrue(hasattr(api.ToolSession, method), "Missing B5 API: ToolSession." + method)

    def source(self, raw=b"x = 1\r\n", **kwargs):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return source_case_with(Path(temporary.name), raw, **kwargs)

    def rejected(self, action, code="evidence_unavailable"):
        with self.assertRaises((AgentError, SourceExcerptError)) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), str(type(caught.exception)(code)))

    def read(self, case, anchor=None):
        session = make_session(case)
        excerpt = session.read_source_excerpt(ExcerptRequest(anchor or case.anchor))
        return session, excerpt

    def test_r10_actual_containment_overlap_disjoint_zero_and_whole_line(self):
        for start, end, supported in ((0, 5, True), (0, 4, False), (1, 5, False),
                                       (1, 4, False), (5, 5, False)):
            with self.subTest(start=start, end=end):
                case = self.source()
                session, excerpt = self.read(case, AnchorRecord("app.py", 1, start, 1, end))
                target = self.dto.EntityRef("symbol", "s0000")
                if supported:
                    binding = session.bind_evidence(excerpt.evidence_id, target)
                    self.assertEqual(binding.provenance, case.snapshot.symbol_inventory[0])
                else:
                    self.rejected(lambda session=session, excerpt=excerpt, target=target:
                                  session.bind_evidence(excerpt.evidence_id, target))
                source = session.bind_evidence(excerpt.evidence_id, None)
                self.assertEqual((source.status, source.target, source.provenance), ("source_only", None, None))
        def whole_line(analysis):
            for sid, entry in tuple(analysis.symbol_index.entries.items()):
                analysis.symbol_index.entries[sid] = replace(entry, source_anchor=SourceAnchor("app.py", 1))
            node = analysis.flow.graph.nodes["seed"]
            analysis.flow.graph.nodes["seed"] = replace(
                node, source_anchors=(SourceAnchor("app.py", 1),), confidence=Confidence.PROBABLE)
        for start, end in ((1, None), (None, 2), (None, None)):
            with self.subTest(start_column=start, end_column=end):
                raw = b"x = 1\n"
                case = self.source(raw, configure=whole_line)
                requested = replace(case.anchor, start_column=start, end_column=end)
                session, excerpt = self.read(case, requested)
                self.assertEqual(excerpt.requested_anchor, requested)
                self.assertEqual(excerpt.returned_anchor, AnchorRecord("app.py", 1, 0, 1, 5))
                self.assertEqual(excerpt.text, "x = 1")
                self.assertEqual(excerpt.content_hash, sha256(raw).hexdigest())
                self.assertEqual(excerpt.scope, case.scope)
                self.assertFalse(excerpt.truncated)
                failures = []
                for target, provenance, confidence in (
                    (self.dto.EntityRef("symbol", "s0000"), case.snapshot.symbol_inventory[0], Confidence.EXACT),
                    (self.dto.EntityRef("code", "seed"),
                     next(node for node in case.snapshot.code_node_inventory if node.id == "seed"),
                     Confidence.PROBABLE),
                ):
                    try:
                        binding = session.bind_evidence(excerpt.evidence_id, target)
                    except (AgentError, SourceExcerptError) as error:
                        failures.append(target.kind + ": " + error.code)
                        continue
                    self.assertEqual(binding.status, "bound")
                    self.assertEqual(binding.target, target)
                    self.assertEqual(binding.provenance, provenance)
                    self.assertEqual(binding.confidence, confidence)
                    self.assertEqual(binding.returned_anchor, excerpt.returned_anchor)
                    self.assertEqual(binding.content_hash, excerpt.content_hash)
                    self.assertFalse(binding.truncated)
                    self.assertEqual(session.lookup_binding(excerpt.evidence_id, target), binding)
                if failures:
                    self.fail("Whole-line B4 proof rejected for missing-column targets: " + ", ".join(failures))
        case = self.source(configure=whole_line)
        session, excerpt = self.read(case)
        self.assertEqual(session.bind_evidence(excerpt.evidence_id, self.dto.EntityRef("symbol", "s0000")).status,
                         "bound")
        session, excerpt = self.read(case, AnchorRecord("app.py", 1, 0, 1, 5))
        self.rejected(lambda: session.bind_evidence(excerpt.evidence_id, self.dto.EntityRef("symbol", "s0000")))

    def test_r10_truncation_and_unicode_use_native_returned_bytes(self):
        def ranges(analysis):
            entries = analysis.symbol_index.entries
            entries["s0001"] = replace(entries["s0001"], source_anchor=SourceAnchor("app.py", 1, 0, 1, 2))
            entries["s0002"] = replace(entries["s0002"], source_anchor=SourceAnchor("app.py", 1))
        case = self.source(b"abcde\n", limits=ExcerptLimits(max_characters=2), configure=ranges)
        session, excerpt = self.read(case)
        self.assertEqual((excerpt.text, excerpt.returned_anchor.end_column, excerpt.truncated), ("ab", 2, True))
        for sid in ("s0000", "s0002"):
            with self.subTest(target=sid):
                self.rejected(lambda sid=sid: session.bind_evidence(excerpt.evidence_id,
                                                                    self.dto.EntityRef("symbol", sid)))
        binding = session.bind_evidence(excerpt.evidence_id, self.dto.EntityRef("symbol", "s0001"))
        self.assertTrue(binding.truncated)
        def unicode_range(analysis):
            for sid, entry in tuple(analysis.symbol_index.entries.items()):
                analysis.symbol_index.entries[sid] = replace(entry, source_anchor=SourceAnchor("app.py", 1, 1, 1, 8))
        case = self.source("#😀中z\n".encode(), configure=unicode_range)
        session, excerpt = self.read(case, AnchorRecord("app.py", 1, 1, 1, 8))
        self.assertEqual((excerpt.text, excerpt.monaco_range.start_column, excerpt.monaco_range.end_column),
                         ("😀中", 2, 5))
        self.assertEqual(session.bind_evidence(excerpt.evidence_id, self.dto.EntityRef("symbol", "s0000")).status,
                         "bound")
        self.rejected(lambda: session.read_source_excerpt(ExcerptRequest(AnchorRecord("app.py", 1, 2, 1, 8))),
                      "invalid_range")

    def test_r11_original_ordinals_namespaces_confidence_and_edge_not_endpoint(self):
        def duplicate(analysis):
            analysis.flow.graph.edges.append(analysis.flow.graph.edges[0])
        case = self.source(configure=duplicate)
        session, excerpt = self.read(case)
        for collection, ordinal, confidence in (("code", 0, Confidence.PROBABLE),
                                                ("code", 1, Confidence.UNRESOLVED),
                                                ("code", 3, Confidence.PROBABLE),
                                                ("semantic", 0, Confidence.PROBABLE)):
            with self.subTest(collection=collection, ordinal=ordinal):
                target = self.dto.RelationKey(collection, ordinal)
                binding = session.bind_evidence(excerpt.evidence_id, target)
                inventory = case.snapshot.code_edge_inventory if collection == "code" else case.snapshot.relation_inventory
                self.assertEqual(binding.provenance, inventory[ordinal])
                self.assertEqual(binding.confidence, confidence)
                self.assertEqual(binding.target, target)
                self.assertEqual(binding.scope, case.scope)
                self.assertEqual(binding.snapshot_binding, case.snapshot.binding)
                self.assertEqual(binding.content_hash, excerpt.content_hash)
                self.assertEqual(binding.returned_anchor, excerpt.returned_anchor)
                with patch.object(SourceExcerptReader, "read_excerpt", side_effect=AssertionError("proof read")):
                    self.assertEqual(session.lookup_binding(excerpt.evidence_id, target), binding)
                    self.assertEqual(session.bind_evidence(excerpt.evidence_id, target), binding)
                self.assertFalse(hasattr(binding, "__dict__"))
                with self.assertRaises((AttributeError, TypeError)):
                    binding.confidence = Confidence.EXACT
        record = case.registry.lookup(excerpt.evidence_id, scope=case.scope, expected_file_hash=excerpt.content_hash)
        self.assertIsNone(record.code_edge_id)
        self.assertEqual(record.confidence, Confidence.EXACT)
        def elsewhere(analysis):
            graph = analysis.flow.graph
            graph.edges[0] = replace(graph.edges[0], source_anchor=SourceAnchor("app.py", 2, 0, 2, 5))
            semantic = analysis.semantic.graph
            return replace(analysis, semantic=SemanticAnalysis(replace(semantic, relations=tuple(
                replace(r, source_anchors=(SourceAnchor("app.py", 2, 0, 2, 5),)) for r in semantic.relations))))
        case = self.source(b"x = 1\ny = 2\n", configure=elsewhere)
        session, excerpt = self.read(case)
        for target in (self.dto.RelationKey("code", 0), self.dto.RelationKey("semantic", 0),
                       self.dto.RelationKey("code", 999), self.dto.EntityRef("code", "missing")):
            with self.subTest(target=target):
                self.rejected(lambda target=target: session.bind_evidence(excerpt.evidence_id, target))

    def test_r12_session_receipts_scope_revocation_close_and_no_retained_source(self):
        case = self.source()
        session, excerpt = self.read(case)
        target = self.dto.EntityRef("code", "seed")
        binding = session.bind_evidence(excerpt.evidence_id, target)
        forged = case.registry.register(EvidenceRecord(case.scope, SourceAnchor("app.py", 1, 0, 1, 5),
                                                      excerpt.content_hash, Confidence.EXACT))
        other = make_session(case)
        for owner, evidence in ((session, forged), (other, excerpt.evidence_id)):
            with self.subTest(owner=owner, evidence=evidence):
                self.rejected(lambda owner=owner, evidence=evidence: owner.bind_evidence(evidence, target))
                self.rejected(lambda owner=owner, evidence=evidence: owner.lookup_binding(evidence, target))
        # Inspect only session-owned mappings, never traverse trusted reader/snapshot ports.
        retained_fields = vars(session).values() if hasattr(session, "__dict__") else (
            getattr(session, name) for name in type(session).__slots__)
        for value in retained_fields:
            if isinstance(value, dict):
                for retained in value.values():
                    self.assertNotIsInstance(retained, (bytes, bytearray))
                    if is_dataclass(retained):
                        self.assertFalse({"text", "raw_bytes", "source"} & {f.name for f in fields(retained)})
        self.assertEqual(binding.confidence, Confidence.EXACT)
        for mode in ("epoch", "generation", "request", "consent", "cancel"):
            with self.subTest(mode=mode):
                scoped_case = self.source()
                scoped_session, scoped_excerpt = self.read(scoped_case)
                scoped_session.bind_evidence(scoped_excerpt.evidence_id, target)
                original = scoped_case.state.value
                updates = {"epoch": {"project_epoch": "other"}, "generation": {"analysis_generation": 2},
                           "request": {"request_id": "other"}}
                scoped_case.state.value = (replace(original, scope=replace(scoped_case.scope, **updates[mode]))
                                    if mode in updates else replace(original, consent=None) if mode == "consent"
                                    else replace(original, cancelled=True))
                expected = "scope_mismatch" if mode in updates else "consent_required" if mode == "consent" else "cancelled"
                self.rejected(lambda scoped_session=scoped_session, scoped_excerpt=scoped_excerpt:
                              scoped_session.lookup_binding(scoped_excerpt.evidence_id, target), expected)
        with patch.object(SourceExcerptReader, "read_excerpt", side_effect=AssertionError("proof read")):
            session.close()
            session.close()
            self.rejected(lambda: session.lookup_binding(excerpt.evidence_id, target))
        self.assertEqual((case.registry.entry_count, case.registry.byte_count), (0, 0))
        self.assertEqual(case.ledger.usage.tool_calls, 0)
        closed_case = self.source()
        closed_session, closed_excerpt = self.read(closed_case)
        closed_session.bind_evidence(closed_excerpt.evidence_id, target)
        closed_case.registry.clear()
        self.rejected(lambda: closed_session.lookup_binding(closed_excerpt.evidence_id, target))

    def test_r12_combined_entry_capacity_and_provenance_byte_capacity(self):
        case = self.source()
        session, excerpt = self.read(case)
        record = case.registry.lookup(excerpt.evidence_id, scope=case.scope, expected_file_hash=excerpt.content_hash)
        # 999 registry entries plus one retained receipt reaches the combined 1,000 cap.
        while case.registry.entry_count < 999:
            case.registry.register(record)
        self.rejected(lambda: session.bind_evidence(excerpt.evidence_id, None), "registry_full")
        self.rejected(lambda: session.lookup_binding(excerpt.evidence_id, None))
        def large_provenance(analysis):
            graph = analysis.semantic.graph
            return replace(analysis, semantic=SemanticAnalysis(replace(graph, components=(
                replace(graph.components[0], evidence=("p" * (2 * 1024 * 1024),)), graph.components[1]))))
        case = self.source(configure=large_provenance)
        session, excerpt = self.read(case)
        target = self.dto.EntityRef("semantic", "component-a")
        self.rejected(lambda: session.bind_evidence(excerpt.evidence_id, target), "registry_full")
        self.rejected(lambda: session.lookup_binding(excerpt.evidence_id, target))


if __name__ == "__main__":
    unittest.main()
