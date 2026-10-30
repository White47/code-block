"""Runtime authority invalidation clears only through the retained store lifecycle."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.assistant_service_fixtures import scope_fingerprint
from tests.assistant_tool_fixtures import make_session, make_source_case

from codeviz.assistant.authority import AuthorityState
from codeviz.assistant.bridge_requests import parse_configure
from codeviz.assistant.contracts import AgentError
from codeviz.assistant.retained_evidence import (
    RetainedEvidenceContext,
    RetainedEvidenceIdentity,
)
from codeviz.assistant.service import AssistantService, _AssistantRuntime
from codeviz.assistant.source_policy import SourceConsent
from codeviz.assistant.tool_contracts import EntityRef
from codeviz.ui.assistant_context import PublicationIdentity
from codeviz.ui.controller import AnalysisStatus


class RuntimeInvalidationTests(unittest.TestCase):
    def test_revoke_clears_history_but_retires_borrowed_evidence_and_keeps_tombstone(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            case = make_source_case(root, b"value = 1\n")
            session = make_session(case)
            excerpt = session.dispatch("read_source_excerpt", {
                "file_path": "app.py", "start_line": 1, "end_line": 1,
            })
            target = EntityRef("code", "seed")
            session.bind_evidence(excerpt.evidence_id, target)

            runtime = _AssistantRuntime()
            runtime.configure(parse_configure({
                "baseUrl": "https://provider.example", "model": "test",
                "key": "test-only", "enabled": True,
            }))
            config = runtime._config
            self.assertIsNotNone(config)
            assert config is not None
            identity = PublicationIdentity(
                1, case.scope.project_epoch, case.scope.analysis_generation,
                os.path.normcase(os.path.normpath(str(root))), root, (),
                AnalysisStatus.READY, True,
            )
            runtime.replace_current(identity)
            engine = AssistantService(lambda: (_ for _ in ()).throw(
                AssertionError("provider must remain lazy")))
            runtime._engine = engine
            engine._replace_publication(identity, case.snapshot)
            grant = runtime._consents.grant(
                project_key=identity.project_key, config=config,
                allowed_files=("app.py",), confirmed=True,
            )
            runtime._grant_partitions.add((identity.project_key, config.origin))
            authority = AuthorityState.from_trusted(config, grant, (("chat", 0),))
            consent = SourceConsent(
                case.consent.binding, "runtime-consent", config.origin, ("app.py",),
            )
            engine._replace_runtime_authority(authority, consent)
            retained_identity = RetainedEvidenceIdentity(
                case.scope.project_epoch, case.scope.analysis_generation,
                config.revision, grant.revision, 0, config.origin,
                scope_fingerprint(consent.allowed_files), consent,
            )
            context = RetainedEvidenceContext.create(
                session, case.registry, case.snapshot, case.scope,
                {excerpt.evidence_id: target}, retained_identity,
            )
            store = engine._conversations
            store.register(identity.project_key, "chat", 0)
            store.reserve_append(
                identity.project_key, "chat", 0,
                messages=({"role": "assistant", "content": "answer"},),
                results={"job": {"answer": "answer"}},
                request_key=(case.scope.project_epoch, "request"), digest="digest",
                retained={"job": context},
            )
            lease = engine._borrow_evidence("chat", excerpt.evidence_id)
            try:
                runtime.revoke_consent(case.scope.project_epoch)
                record = store.logical_state()["conversations"][0]
                self.assertEqual(record["revision"], 1)
                self.assertEqual(record["messages"], [])
                self.assertEqual(record["results"], {})
                self.assertEqual(record["retained"], {})
                self.assertEqual(store.tombstone(
                    (case.scope.project_epoch, "request")), "digest")
                self.assertEqual(store.evidence_count, 1)
                self.assertEqual(len(store.logical_state()["retiredEvidence"]), 1)
                with self.assertRaises(AgentError):
                    lease.resolve_evidence(excerpt.evidence_id)
            finally:
                lease.release()
                runtime.shutdown()
            self.assertEqual(store.evidence_count, 0)


if __name__ == "__main__":
    unittest.main()
