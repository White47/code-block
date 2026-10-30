"""C6 service-owned provider loop and bounded answer repair."""

import json
import os
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.assistant_service_fixtures import scope_fingerprint
from tests.assistant_tool_fixtures import make_source_case

from codeviz.assistant.authority import AuthorityState
from codeviz.assistant.contracts import AgentError, JobStatus, RequestScope
from codeviz.assistant.jobs import JobRequest
from codeviz.assistant.provider import (
    PreparedRequest,
    ProviderDraft,
    ProviderEvent,
    WireOccurrence,
)
from codeviz.assistant.service import AssistantService
from codeviz.assistant.snapshot import AnchorRecord
from codeviz.assistant.tool_contracts import EntityRef
from codeviz.assistant.tool_loop import TrustedTarget, choose_target
from codeviz.ui.assistant_context import PublicationIdentity
from codeviz.ui.controller import AnalysisStatus

_KINDS = ("summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")


def _answer(evidence_id: str) -> str:
    sections = [{"kind": kind, "claims": []} for kind in _KINDS]
    sections[0]["claims"] = [{
        "text": "The saved source defines a value.",
        "basis": "static",
        "evidenceIds": [evidence_id],
        "relationEvidenceId": None,
    }]
    return json.dumps({"sections": sections}, separators=(",", ":"))


def _relation_answer(evidence_id: str) -> str:
    sections = [{"kind": kind, "claims": []} for kind in _KINDS]
    sections[4]["claims"] = [{
        "text": "The saved graph contains this relation.",
        "basis": "static",
        "evidenceIds": [evidence_id],
        "relationEvidenceId": evidence_id,
    }]
    return json.dumps({"sections": sections}, separators=(",", ":"))


def _unknown_answer() -> str:
    sections = [{"kind": kind, "claims": []} for kind in _KINDS]
    sections[6]["claims"] = [{
        "text": "Unable to confirm more without source evidence.",
        "basis": "unknown", "evidenceIds": [], "relationEvidenceId": None,
    }]
    return json.dumps({"sections": sections}, separators=(",", ":"))


class LoopProvider:
    def __init__(self) -> None:
        self.prepared: list[tuple[PreparedRequest, dict[str, object]]] = []
        self.sends = 0
        self.closed = False

    async def prepare(self, draft: ProviderDraft) -> PreparedRequest:
        logical = json.loads(draft.body)
        wire = json.dumps({"messages": logical["messages"]}, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        source = tuple(WireOccurrence(item["pointer"], item["start"], item["end"], item["file"])
                       for item in logical["sourceTags"])
        metadata = tuple(WireOccurrence(item["pointer"], item["start"], item["end"], item["file"])
                         for item in logical.get("metadataTags", []))
        prepared = PreparedRequest(
            f"prepared-{len(self.prepared)}", "POST",
            "https://provider.invalid:443/v1/chat", wire, draft.max_output_tokens,
            draft.deadline, len(wire) + 1024, source, metadata, True,
        )
        self.prepared.append((prepared, logical))
        return prepared

    def _evidence_id(self) -> str:
        for _prepared, logical in reversed(self.prepared):
            for message in reversed(logical["messages"]):
                if message["role"] == "tool" and message["name"] == "read_source_excerpt":
                    return json.loads(message["content"])["result"]["evidenceId"]
        raise AssertionError("tool result was not returned to the provider")

    async def dispatch(self, prepared: PreparedRequest):
        self.sends += 1
        logical = next(value for handle, value in self.prepared if handle is prepared)
        mode = logical["mode"]
        if mode == "tools" and self.sends == 1:
            yield ProviderEvent("tool", json.dumps({
                "id": "call-1", "name": "read_source_excerpt",
                "arguments": {"file_path": "app.py", "start_line": 1, "end_line": 1},
            }, separators=(",", ":")).encode())
        elif mode == "final":
            evidence_id = "forged-id" if self.sends == 3 else self._evidence_id()
            yield ProviderEvent("answer", json.dumps({
                "index": 0, "last": True, "content": _answer(evidence_id),
            }, separators=(",", ":")).encode())
        yield ProviderEvent(
            "usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')

    async def cancel(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


class InvalidFinalProvider(LoopProvider):
    async def dispatch(self, prepared: PreparedRequest):
        self.sends += 1
        yield ProviderEvent("answer", json.dumps({
            "index": 0, "last": True, "content": _answer("forged-id"),
        }, separators=(",", ":")).encode())
        yield ProviderEvent(
            "usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')


class InvalidAfterEvidenceProvider(LoopProvider):
    async def dispatch(self, prepared: PreparedRequest):
        self.sends += 1
        logical = next(value for handle, value in self.prepared if handle is prepared)
        if logical["mode"] == "tools" and self.sends == 1:
            yield ProviderEvent("tool", json.dumps({
                "id": "proof", "name": "read_source_excerpt",
                "arguments": {"file_path": "app.py", "start_line": 1, "end_line": 1},
            }, separators=(",", ":")).encode())
        elif logical["mode"] == "final":
            yield ProviderEvent("answer", json.dumps({
                "index": 0, "last": True, "content": _answer("forged-id"),
            }, separators=(",", ":")).encode())
        yield ProviderEvent(
            "usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')


class RelationLoopProvider(LoopProvider):
    async def dispatch(self, prepared: PreparedRequest):
        self.sends += 1
        logical = next(value for handle, value in self.prepared if handle is prepared)
        if logical["mode"] == "tools" and self.sends == 1:
            payload = {"id": "relations", "name": "get_local_relations",
                       "arguments": {"seed": {"kind": "code", "id": "seed"}}}
            yield ProviderEvent("tool", json.dumps(payload, separators=(",", ":")).encode())
        elif logical["mode"] == "tools" and self.sends == 2:
            payload = {
                "id": "proof", "name": "read_source_excerpt",
                "arguments": {"file_path": "app.py", "start_line": 1,
                              "start_column": 0, "end_line": 1, "end_column": 5},
            }
            yield ProviderEvent("tool", json.dumps(payload, separators=(",", ":")).encode())
        elif logical["mode"] == "final":
            yield ProviderEvent("answer", json.dumps({
                "index": 0, "last": True,
                "content": _relation_answer(self._evidence_id()),
            }, separators=(",", ":")).encode())
        yield ProviderEvent(
            "usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')


class MetadataOnlyLoopProvider(LoopProvider):
    async def dispatch(self, prepared: PreparedRequest):
        self.sends += 1
        logical = next(value for handle, value in self.prepared if handle is prepared)
        if logical["mode"] == "tools" and self.sends == 1:
            payload = {"id": "relations", "name": "get_local_relations",
                       "arguments": {"seed": {"kind": "code", "id": "seed"}}}
            yield ProviderEvent("tool", json.dumps(payload, separators=(",", ":")).encode())
        elif logical["mode"] == "final":
            yield ProviderEvent("answer", json.dumps({
                "index": 0, "last": True, "content": _unknown_answer(),
            }, separators=(",", ":")).encode())
        yield ProviderEvent(
            "usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')


class AssistantServiceLoopTests(unittest.TestCase):
    @staticmethod
    def _wait_released(service: AssistantService, job_id: str):
        deadline = time.monotonic() + 3
        snapshot = service.get(job_id)
        while not snapshot.resources_released and time.monotonic() < deadline:
            time.sleep(0.005)
            snapshot = service.get(job_id)
        return snapshot

    def test_inner_excerpt_does_not_claim_its_enclosing_function_target(self):
        function = EntityRef("code", "function")
        candidates = (TrustedTarget(
            function, (AnchorRecord("app.py", 1, 0, 10, 1),)),)

        chosen = choose_target(AnchorRecord("app.py", 5, 4, 5, 8), candidates)

        self.assertIsNone(chosen)

    def test_tool_turns_then_one_repair_publish_only_the_repaired_answer(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            case = make_source_case(root, b"value = 1\n")
            case.consent = replace(case.consent, provider_origin="https://provider.invalid:443")
            binding = case.snapshot.binding
            identity = PublicationIdentity(
                1, binding.project_epoch, binding.analysis_generation,
                os.path.normcase(os.path.normpath(str(root))), root, (),
                AnalysisStatus.READY, True,
            )
            fingerprint = scope_fingerprint(case.consent.allowed_files)
            authority = AuthorityState(
                True, 1, 1, identity.project_key, "https://provider.invalid:443",
                fingerprint, (("conversation", 0),), "fake",
                "https://provider.invalid:443/v1/chat",
            )
            deadline = time.monotonic() + 120
            logical = {
                "version": 1,
                "mode": "tools",
                "messages": [{"role": "user", "content": "Explain the saved source."}],
                "tools": [{
                    "name": "read_source_excerpt",
                    "description": "Read a bounded saved-source excerpt.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string"},
                            "start_line": {"type": "integer"},
                            "end_line": {"type": "integer"},
                        },
                        "required": ["file_path", "start_line", "end_line"],
                        "additionalProperties": False,
                    },
                }],
                "answerSchema": None,
                "sourceTags": [],
                "metadataTags": [],
            }
            request = JobRequest(
                RequestScope(binding.project_epoch, binding.analysis_generation, "loop-answer"),
                "conversation", "Explain the saved source.", "context", "fake", 1, 1,
                fingerprint,
                ProviderDraft(json.dumps(logical, separators=(",", ":")).encode(), 512, deadline),
                identity,
            )
            provider = LoopProvider()
            service = AssistantService(lambda: provider)
            service._replace_publication(identity, case.snapshot)
            service._replace_source_consent(case.consent)
            service._replace_authority(authority)
            observed = []

            async def before_dispatch(_prepared):
                with service._lock:
                    observed.append(next(iter(service._jobs.values())).ledger.usage)

            service._before_dispatch = before_dispatch
            try:
                started = service.start(request)
                snapshot = self._wait_released(service, started.job_id)
                self.assertEqual(
                    snapshot.status, JobStatus.COMPLETED,
                    (snapshot.issue, provider.sends,
                     [logical["mode"] for _, logical in provider.prepared]),
                )
                self.assertTrue(snapshot.resources_released)
                self.assertEqual(provider.sends, 4)
                self.assertEqual([logical["mode"] for _, logical in provider.prepared],
                                 ["tools", "tools", "final", "final"])
                self.assertTrue(all(handle.deadline == deadline for handle, _ in provider.prepared))
                self.assertEqual((observed[-1].model_requests, observed[-1].tool_calls), (4, 1))
                self.assertGreaterEqual(observed[-1].source_characters, 3 * len("value = 1"))

                evidence_id = provider._evidence_id()
                record = service._conversations.logical_state()["conversations"][0]
                answer = json.loads(record["results"][started.job_id]["answer"])
                self.assertEqual(answer["evidence"][0]["evidenceId"], evidence_id)
                lease = service._borrow_evidence("conversation", evidence_id)
                try:
                    self.assertEqual(lease.resolve_evidence(evidence_id).proof.evidence_id,
                                     evidence_id)
                finally:
                    lease.release()
            finally:
                service.shutdown()

    def test_failed_repair_is_not_repeated_and_budget_exhaustion_skips_it(self):
        for output_limit, expected_sends, expected_code in (
                (512, 2, "AI_EVIDENCE_INVALID"),
                (2000, 1, "AI_BUDGET_EXCEEDED")):
            with self.subTest(output_limit=output_limit), TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                case = make_source_case(root, b"value = 1\n")
                case.consent = replace(
                    case.consent, provider_origin="https://provider.invalid:443")
                binding = case.snapshot.binding
                identity = PublicationIdentity(
                    1, binding.project_epoch, binding.analysis_generation,
                    os.path.normcase(os.path.normpath(str(root))), root, (),
                    AnalysisStatus.READY, True,
                )
                fingerprint = scope_fingerprint(case.consent.allowed_files)
                authority = AuthorityState(
                    True, 1, 1, identity.project_key, "https://provider.invalid:443",
                    fingerprint, (("conversation", 0),), "fake",
                    "https://provider.invalid:443/v1/chat",
                )
                logical = {
                    "version": 1, "mode": "final",
                    "messages": [{"role": "user", "content": "Explain."}],
                    "tools": [], "answerSchema": {}, "sourceTags": [],
                    "metadataTags": [],
                }
                request = JobRequest(
                    RequestScope(binding.project_epoch, binding.analysis_generation,
                                 f"repair-{output_limit}"),
                    "conversation", "Explain.", "context", "fake", 1, 1, fingerprint,
                    ProviderDraft(json.dumps(logical, separators=(",", ":")).encode(),
                                  output_limit, time.monotonic() + 120),
                    identity,
                )
                provider = InvalidFinalProvider()
                service = AssistantService(lambda provider=provider: provider)
                service._replace_publication(identity, case.snapshot)
                service._replace_source_consent(case.consent)
                service._replace_authority(authority)
                try:
                    started = service.start(request)
                    snapshot = self._wait_released(service, started.job_id)
                    self.assertEqual(snapshot.status, JobStatus.FAILED)
                    self.assertEqual(snapshot.issue.code, expected_code)
                    self.assertEqual(provider.sends, expected_sends)
                    self.assertEqual(
                        [logical["mode"] for _, logical in provider.prepared],
                        ["final"] * expected_sends,
                    )
                    record = service._conversations.logical_state()["conversations"][0]
                    self.assertEqual(record["results"], {})
                finally:
                    service.shutdown()

    def test_failed_and_budget_exhausted_jobs_retain_acquired_metadata_only(self):
        for output_limit, expected_code in (
                (512, "AI_EVIDENCE_INVALID"),
                (2000, "AI_BUDGET_EXCEEDED")):
            with self.subTest(output_limit=output_limit), TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                case = make_source_case(root, b"value = 1\n")
                case.consent = replace(
                    case.consent, provider_origin="https://provider.invalid:443")
                binding = case.snapshot.binding
                identity = PublicationIdentity(
                    1, binding.project_epoch, binding.analysis_generation,
                    os.path.normcase(os.path.normpath(str(root))), root, (),
                    AnalysisStatus.READY, True,
                )
                fingerprint = scope_fingerprint(case.consent.allowed_files)
                authority = AuthorityState(
                    True, 1, 1, identity.project_key, "https://provider.invalid:443",
                    fingerprint, (("conversation", 0),), "fake",
                    "https://provider.invalid:443/v1/chat",
                )
                logical = {
                    "version": 1, "mode": "tools",
                    "messages": [{"role": "user", "content": "Explain."}],
                    "tools": [{
                        "name": "read_source_excerpt", "description": "Read source proof.",
                        "parameters": {"type": "object", "properties": {},
                                       "required": [], "additionalProperties": False},
                    }],
                    "answerSchema": None, "sourceTags": [], "metadataTags": [],
                }
                request = JobRequest(
                    RequestScope(binding.project_epoch, binding.analysis_generation,
                                 f"failed-evidence-{output_limit}"),
                    "conversation", "Explain.", "context", "fake", 1, 1,
                    fingerprint,
                    ProviderDraft(json.dumps(logical, separators=(",", ":")).encode(),
                                  output_limit, time.monotonic() + 120),
                    identity,
                )
                provider = InvalidAfterEvidenceProvider()
                service = AssistantService(lambda provider=provider: provider)
                service._replace_publication(identity, case.snapshot)
                service._replace_source_consent(case.consent)
                service._replace_authority(authority)
                try:
                    started = service.start(request)
                    snapshot = self._wait_released(service, started.job_id)
                    self.assertEqual(snapshot.status, JobStatus.FAILED)
                    self.assertEqual(snapshot.issue.code, expected_code)
                    evidence_id = provider._evidence_id()
                    record = service._conversations.logical_state()["conversations"][0]
                    self.assertEqual(set(record["evidence"]), {evidence_id})
                    self.assertEqual(set(record["bindings"]), {evidence_id})
                    self.assertEqual(record["results"], {})
                    self.assertEqual(record["retained"], {})
                    self.assertNotIn("value = 1", json.dumps(record, ensure_ascii=False))
                    self.assertEqual(service._conversations.evidence_count, 1)
                    with self.assertRaises(AgentError):
                        service._borrow_evidence("conversation", evidence_id)

                    service._set_conversation_revision("conversation", 1)
                    self.assertEqual(service._conversations.evidence_count, 0)
                finally:
                    service.shutdown()

    def test_relation_target_comes_from_trusted_local_result_with_metadata_gate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            case = make_source_case(root, b"value = 1\n")
            case.consent = replace(case.consent, provider_origin="https://provider.invalid:443")
            binding = case.snapshot.binding
            identity = PublicationIdentity(
                1, binding.project_epoch, binding.analysis_generation,
                os.path.normcase(os.path.normpath(str(root))), root, (),
                AnalysisStatus.READY, True,
            )
            fingerprint = scope_fingerprint(case.consent.allowed_files)
            authority = AuthorityState(
                True, 1, 1, identity.project_key, "https://provider.invalid:443",
                fingerprint, (("conversation", 0),), "fake",
                "https://provider.invalid:443/v1/chat",
            )
            tool_schema = {"type": "object", "properties": {},
                           "required": [], "additionalProperties": False}
            logical = {
                "version": 1, "mode": "tools",
                "messages": [{"role": "user", "content": "Explain relations."}],
                "tools": [
                    {"name": "get_local_relations", "description": "Get local relations.",
                     "parameters": tool_schema},
                    {"name": "read_source_excerpt", "description": "Read source proof.",
                     "parameters": tool_schema},
                ],
                "answerSchema": None, "sourceTags": [], "metadataTags": [],
            }
            request = JobRequest(
                RequestScope(binding.project_epoch, binding.analysis_generation,
                             "relation-loop"),
                "conversation", "Explain relations.", "context", "fake", 1, 1,
                fingerprint,
                ProviderDraft(json.dumps(logical, separators=(",", ":")).encode(),
                              512, time.monotonic() + 120),
                identity,
            )
            provider = RelationLoopProvider()
            service = AssistantService(lambda: provider)
            service._replace_publication(identity, case.snapshot)
            service._replace_source_consent(case.consent)
            service._replace_authority(authority)
            try:
                started = service.start(request)
                snapshot = self._wait_released(service, started.job_id)
                self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
                self.assertEqual(provider.sends, 4)
                self.assertEqual({item.file for item in provider.prepared[1][0].metadata_occurrences},
                                 {"app.py"})
                evidence_id = provider._evidence_id()
                lease = service._borrow_evidence("conversation", evidence_id)
                try:
                    resolved = lease.resolve_evidence(evidence_id)
                    self.assertEqual(resolved.proof.target.collection_kind, "code")
                    relation_result = next(
                        json.loads(message["content"])["result"]
                        for message in provider.prepared[1][1]["messages"]
                        if message["role"] == "tool" and message["name"] == "get_local_relations"
                    )
                    self.assertEqual(
                        resolved.proof.target.original_index,
                        relation_result["relations"][0]["key"]["originalIndex"],
                    )
                finally:
                    lease.release()
                record = service._conversations.logical_state()["conversations"][0]
                answer = json.loads(record["results"][started.job_id]["answer"])
                self.assertEqual(answer["relationFacts"][0]["evidenceId"], evidence_id)
                guidance = provider.prepared[-1][1]["messages"][-1]["content"]
                catalog = json.loads(next(line for line in guidance.splitlines()
                                          if line.startswith('{"receiptEvidenceIds":')))
                self.assertEqual(catalog, {
                    "receiptEvidenceIds": [evidence_id], "relationEvidenceIds": [evidence_id]})
            finally:
                service.shutdown()

    def test_metadata_only_turn_is_not_reused_as_source_free_history(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            case = make_source_case(root, b"value = 1\n")
            case.consent = replace(case.consent,
                                   provider_origin="https://provider.invalid:443")
            binding = case.snapshot.binding
            identity = PublicationIdentity(
                1, binding.project_epoch, binding.analysis_generation,
                os.path.normcase(os.path.normpath(str(root))), root, (),
                AnalysisStatus.READY, True,
            )
            fingerprint = scope_fingerprint(case.consent.allowed_files)
            authority = AuthorityState(
                True, 1, 1, identity.project_key, "https://provider.invalid:443",
                fingerprint, (("conversation", 0),), "fake",
                "https://provider.invalid:443/v1/chat",
            )
            schema = {"type": "object", "properties": {},
                      "required": [], "additionalProperties": False}
            logical = {
                "version": 1, "mode": "tools",
                "messages": [{"role": "user", "content": "Explain relations."}],
                "tools": [{"name": "get_local_relations",
                           "description": "Get local relations.", "parameters": schema}],
                "answerSchema": None, "sourceTags": [], "metadataTags": [],
            }
            request = JobRequest(
                RequestScope(binding.project_epoch, binding.analysis_generation,
                             "metadata-only-history"),
                "conversation", "Explain relations.", "context", "fake", 1, 1,
                fingerprint,
                ProviderDraft(json.dumps(logical, separators=(",", ":")).encode(),
                              512, time.monotonic() + 120),
                identity,
            )
            provider = MetadataOnlyLoopProvider()
            service = AssistantService(lambda: provider)
            service._replace_publication(identity, case.snapshot)
            service._replace_source_consent(case.consent)
            service._replace_authority(authority)
            try:
                started = service.start(request)
                snapshot = self._wait_released(service, started.job_id)
                self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
                self.assertTrue(any(handle.metadata_occurrences
                                    for handle, _logical in provider.prepared))
                self.assertTrue(all(not handle.source_occurrences
                                    for handle, _logical in provider.prepared))
                record = service._conversations.logical_state()["conversations"][0]
                self.assertFalse(record["messages"][-1]["sourceFree"])
                self.assertEqual(service._conversations._select_eligible_history(
                    identity.project_key, "conversation", 0,
                    project_epoch=binding.project_epoch,
                    origin="https://provider.invalid:443",
                    generation=binding.analysis_generation,
                    consent_revision=1, config_revision=1,
                    normalized_scope=fingerprint), ())
            finally:
                service.shutdown()


if __name__ == "__main__":
    unittest.main()
