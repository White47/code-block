"""Real B5 evidence ownership, borrowing, and retirement invariants."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from assistant_service_fixtures import scope_fingerprint
from assistant_tool_fixtures import make_session, make_source_case, require_api

from codeviz.assistant.contracts import AgentError
from codeviz.assistant.service import ConversationStore
from codeviz.assistant.source_policy import SourceExcerptError
from codeviz.assistant.tool_contracts import EntityRef


class RetainedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.api = require_api(
            self, "retained_evidence", "RetainedEvidenceContext",
            "RetainedEvidenceIdentity",
        )
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.case = make_source_case(Path(directory.name), b"value = 1\n")
        self.session = make_session(self.case)
        result = self.session.dispatch("read_source_excerpt", {
            "file_path": "app.py", "start_line": 1, "end_line": 1,
        })
        self.evidence_id = result.evidence_id
        self.target = EntityRef("code", "seed")
        self.session.bind_evidence(self.evidence_id, self.target)
        self.identity = self.api.RetainedEvidenceIdentity(
            self.case.scope.project_epoch, self.case.scope.analysis_generation,
            1, 1, 0, self.case.consent.provider_origin,
            scope_fingerprint(self.case.consent.allowed_files),
            self.case.consent,
        )

    def context(self):
        return self.api.RetainedEvidenceContext.create(
            self.session, self.case.registry, self.case.snapshot, self.case.scope,
            {self.evidence_id: self.target}, self.identity,
        )

    def test_new_borrow_uses_independent_budget_after_original_deadline(self):
        context = self.context()
        self.case.now[0] = 91.0

        with self.assertRaises((AgentError, SourceExcerptError)):
            self.session.lookup_binding(self.evidence_id, self.target)
        self.assertFalse(self.session._closed)

        lease = context.borrow(self.identity)
        try:
            resolved = lease.resolve_evidence(self.evidence_id)
            self.assertEqual(resolved.proof.evidence_id, self.evidence_id)
            self.assertEqual(resolved.proof.target, self.target)
        finally:
            lease.release()
            context.revoke()

    def test_only_frozen_provided_ids_can_be_resolved(self):
        second = self.session.dispatch("read_source_excerpt", {
            "file_path": "app.py", "start_line": 1, "end_line": 1,
        })
        self.session.bind_evidence(second.evidence_id, self.target)
        context = self.context()
        lease = context.borrow(self.identity)
        try:
            with self.assertRaises(AgentError):
                lease.resolve_evidence(second.evidence_id)
            with self.assertRaises(AgentError):
                lease.resolve_evidence("forged-evidence-id")
            for operation in (
                lambda: self.session.dispatch("search_symbols", {"query": ""}),
                lambda: self.session.bind_evidence(self.evidence_id, self.target),
                lambda: self.session.lookup_binding(self.evidence_id, self.target),
            ):
                with self.assertRaises((AgentError, SourceExcerptError)):
                    operation()
                self.assertFalse(self.session._closed)
        finally:
            lease.release()
            context.revoke()

    def test_clear_retires_borrowed_context_until_last_release(self):
        store = ConversationStore()
        store.register("project", "chat", 0)
        closes = []
        original_close = self.session.close

        def close():
            self.assertFalse(store._lock.locked())
            closes.append(True)
            original_close()

        self.session.close = close
        context = self.context()
        store.reserve_append(
            "project", "chat", 0,
            messages=({"role": "assistant", "content": "answer"},),
            results={"job": {"answer": "answer"}},
            retained={"job": context},
        )
        lease = store._borrow_retained("project", "chat", "job", self.identity)
        before = store.logical_bytes

        store.clear("project", "chat", 1)
        self.assertEqual(closes, [])
        self.assertEqual(store.evidence_count, 1)
        self.assertGreater(store.logical_bytes, 0)
        self.assertNotEqual(store.logical_bytes, before)
        with self.assertRaises(AgentError):
            store._borrow_retained("project", "chat", "job", self.identity)
        with self.assertRaises(AgentError):
            lease.resolve_evidence(self.evidence_id)

        lease.release()
        self.assertEqual(closes, [True])
        self.assertEqual(store.evidence_count, 0)


if __name__ == "__main__":
    unittest.main()
