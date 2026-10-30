"""Estimated-input accounting at the explanation service dispatch boundary."""

import asyncio
import hashlib
import json
import os
import time
import unittest
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from assistant_estimated_fixtures import PROFILE

from codeviz.analysis.project import ProjectAnalysis
from codeviz.analysis.symbols import SymbolIndex
from codeviz.assistant._usage_values import InputAccountingSnapshot, ObservedUsage
from codeviz.assistant.authority import AuthorityState
from codeviz.assistant.contracts import JobStatus, RequestScope
from codeviz.assistant.jobs import JobRequest, JobSnapshot
from codeviz.assistant.provider import (
    EstimatedInputAccounting,
    PreparedRequest,
    ProviderDraft,
    ProviderEvent,
)
from codeviz.assistant.service import AssistantService, ServiceError
from codeviz.assistant.snapshot import SnapshotBinding, build_snapshot
from codeviz.assistant.source_policy import SourceConsent
from codeviz.ui.assistant_context import PublicationIdentity
from codeviz.ui.controller import AnalysisStatus

EMPTY_SCOPE_FINGERPRINT = hashlib.sha256(b"[]").hexdigest()


def _final_answer_event() -> ProviderEvent:
    sections: list[dict[str, object]] = [
        {"kind": kind, "claims": []}
        for kind in (
            "summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")
    ]
    sections[0]["claims"] = [{
        "text": "Unable to confirm the function without evidence.",
        "basis": "unknown",
        "evidenceIds": [],
        "relationEvidenceId": None,
    }]
    content = json.dumps({"sections": sections}, separators=(",", ":"))
    return ProviderEvent("answer", json.dumps({
        "index": 0,
        "last": True,
        "content": content,
    }, separators=(",", ":")).encode("utf-8"))


def _logical(mode: str = "final") -> dict[str, object]:
    return {
        "version": 1,
        "mode": mode,
        "messages": [{"role": "user", "content": "Explain."}],
        "tools": [],
        "answerSchema": {} if mode == "final" else None,
        "sourceTags": [],
        "metadataTags": [],
    }


class EstimatedProvider:
    def __init__(self, *, usage: ObservedUsage, valid_answer: bool = True,
                 omit_usage: bool = False, wait_for_cancel: bool = False) -> None:
        self.usage = usage
        self.valid_answer = valid_answer
        self.omit_usage = omit_usage
        self.wait_for_cancel = wait_for_cancel
        self.prepares = 0
        self.sends = 0
        self.closed = False
        self.dispatched = Event()
        self._modes: dict[str, object] = {}

    async def prepare(self, draft: ProviderDraft) -> PreparedRequest:
        self.prepares += 1
        logical = json.loads(draft.body)
        body = json.dumps({
            "model": PROFILE.model,
            "messages": logical.get(
                "messages", [{"role": "user", "content": "Explain."}]),
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        accounting = EstimatedInputAccounting(
            PROFILE.profile_id,
            PROFILE.method,
            len(body),
            hashlib.sha256(body).hexdigest(),
        )
        preparation_id = f"estimated-{self.prepares}"
        self._modes[preparation_id] = logical.get("mode")
        return PreparedRequest(
            preparation_id,
            "POST",
            PROFILE.endpoint,
            body,
            draft.max_output_tokens,
            draft.deadline,
            None,
            (),
            (),
            True,
            accounting,
        )

    async def dispatch(self, prepared: PreparedRequest) -> AsyncIterator[ProviderEvent]:
        self.sends += 1
        self.dispatched.set()
        if self.wait_for_cancel:
            await asyncio.Event().wait()
        if self._modes[prepared.preparation_id] != "tools":
            if self.valid_answer:
                yield _final_answer_event()
            else:
                yield ProviderEvent(
                    "answer", b'{"index":0,"last":true,"content":"{}"}')
        if not self.omit_usage:
            payload = {
                "inputTokens": self.usage.input_tokens,
                "outputTokens": self.usage.output_tokens,
                "basis": self.usage.basis,
            }
            yield ProviderEvent(
                "usage",
                json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            )

    async def cancel(self) -> None:
        return None

    async def aclose(self) -> None:
        self.closed = True


class EstimatedInputServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def _identity_request(
        self,
        request_id: str,
        *,
        mode: str = "final",
    ) -> tuple[PublicationIdentity, JobRequest]:
        identity = PublicationIdentity(
            1,
            "epoch",
            1,
            os.path.normcase(os.path.normpath(str(self.root))),
            self.root,
            (),
            AnalysisStatus.READY,
            True,
        )
        draft = ProviderDraft(
            json.dumps(_logical(mode), separators=(",", ":")).encode("utf-8"),
            32,
            time.monotonic() + 120,
        )
        request = JobRequest(
            RequestScope("epoch", 1, request_id),
            "conversation",
            "Explain.",
            "context-digest",
            PROFILE.model,
            1,
            1,
            EMPTY_SCOPE_FINGERPRINT,
            draft,
            identity,
        )
        return identity, request

    @staticmethod
    def _install(service: AssistantService, identity: PublicationIdentity) -> None:
        project_epoch = identity.project_epoch
        analysis_generation = identity.analysis_generation
        project_key = identity.project_key
        if (project_epoch is None or analysis_generation is None
                or project_key is None):
            raise AssertionError("eligible publication identity is incomplete")
        snapshot = build_snapshot(
            ProjectAnalysis((), SymbolIndex(), ()),
            binding=SnapshotBinding(
                str(identity.root),
                identity.ignore_paths,
                project_epoch,
                analysis_generation,
            ),
        )
        service._replace_publication(identity, snapshot)
        service._replace_source_consent(SourceConsent(
            snapshot.binding,
            "estimated-consent",
            "https://estimated.invalid:443",
            (),
        ))
        service._replace_authority(AuthorityState(
            True,
            1,
            1,
            project_key,
            "https://estimated.invalid:443",
            EMPTY_SCOPE_FINGERPRINT,
            (("conversation", 0),),
            PROFILE.model,
            PROFILE.endpoint,
        ))

    @staticmethod
    def _wait_released(service: AssistantService, job_id: str) -> JobSnapshot:
        deadline = time.monotonic() + 3
        snapshot = service.get(job_id)
        while not snapshot.resources_released and time.monotonic() < deadline:
            time.sleep(0.005)
            snapshot = service.get(job_id)
        return snapshot

    @staticmethod
    def _accounting(service: AssistantService, job_id: str) -> InputAccountingSnapshot:
        with service._lock:
            retired = service._completed[job_id]
            accounting = retired.input_accounting
        if accounting is None:
            raise AssertionError("estimated accounting snapshot was not retained")
        return accounting

    def _service(
        self,
        provider: EstimatedProvider,
        *,
        before_dispatch: Callable[[PreparedRequest], Awaitable[None]] | None = None,
    ) -> AssistantService:
        if before_dispatch is None:
            return AssistantService(
                lambda: provider,
                trusted_input_profiles=(PROFILE,),
            )
        return AssistantService(
            lambda: provider,
            before_dispatch=before_dispatch,
            trusted_input_profiles=(PROFILE,),
        )

    def test_complete_final_keeps_unknown_or_overrun_warning(self) -> None:
        cases = (
            (ObservedUsage(), "usage_unknown", 0),
            (ObservedUsage(20_001, 1, "actual"), "observed_overrun", 20_001),
        )
        for index, (usage, reason, known) in enumerate(cases):
            with self.subTest(reason=reason):
                identity, request = self._identity_request(f"valid-warning-{index}")
                provider = EstimatedProvider(usage=usage)
                service = self._service(provider)
                try:
                    self._install(service, identity)
                    started = service.start(request)
                    snapshot = self._wait_released(service, started.job_id)

                    self.assertEqual(snapshot.status, JobStatus.COMPLETED)
                    self.assertEqual((provider.prepares, provider.sends), (1, 1))
                    accounting = self._accounting(service, started.job_id)
                    self.assertEqual(accounting.blocked_reason, reason)
                    self.assertEqual(accounting.known_actual_input_tokens, known)
                finally:
                    service.shutdown()

    def test_tools_overrun_stops_before_another_prepare_or_dispatch(self) -> None:
        identity, request = self._identity_request("tools-overrun", mode="tools")
        provider = EstimatedProvider(usage=ObservedUsage(20_001, 1, "actual"))
        service = self._service(provider)
        try:
            self._install(service, identity)
            started = service.start(request)
            snapshot = self._wait_released(service, started.job_id)

            self.assertEqual(snapshot.status, JobStatus.FAILED)
            self.assertIsNotNone(snapshot.issue)
            assert snapshot.issue is not None
            self.assertEqual(snapshot.issue.code, "AI_BUDGET_EXCEEDED")
            self.assertEqual((provider.prepares, provider.sends), (1, 1))
            self.assertEqual(
                self._accounting(service, started.job_id).blocked_reason,
                "observed_overrun",
            )
        finally:
            service.shutdown()

    def test_blocked_invalid_final_never_prepares_repair(self) -> None:
        for index, usage in enumerate((
            ObservedUsage(),
            ObservedUsage(20_001, 1, "actual"),
        )):
            with self.subTest(basis=usage.basis):
                identity, request = self._identity_request(f"blocked-repair-{index}")
                provider = EstimatedProvider(usage=usage, valid_answer=False)
                service = self._service(provider)
                try:
                    self._install(service, identity)
                    started = service.start(request)
                    snapshot = self._wait_released(service, started.job_id)

                    self.assertEqual(snapshot.status, JobStatus.FAILED)
                    self.assertIsNotNone(snapshot.issue)
                    assert snapshot.issue is not None
                    self.assertEqual(snapshot.issue.code, "AI_BUDGET_EXCEEDED")
                    self.assertEqual((provider.prepares, provider.sends), (1, 1))
                    self.assertIn(
                        self._accounting(service, started.job_id).blocked_reason,
                        ("usage_unknown", "observed_overrun"),
                    )
                finally:
                    service.shutdown()

    def test_missing_usage_and_cancel_retain_unknown_snapshot(self) -> None:
        identity, request = self._identity_request("missing-usage")
        provider = EstimatedProvider(usage=ObservedUsage(), omit_usage=True)
        service = self._service(provider)
        try:
            self._install(service, identity)
            started = service.start(request)
            snapshot = self._wait_released(service, started.job_id)
            self.assertEqual(snapshot.status, JobStatus.FAILED)
            self.assertEqual(
                self._accounting(service, started.job_id).blocked_reason,
                "usage_unknown",
            )
        finally:
            service.shutdown()

        identity, request = self._identity_request("cancelled-usage")
        provider = EstimatedProvider(usage=ObservedUsage(), wait_for_cancel=True)
        service = self._service(provider)
        try:
            self._install(service, identity)
            started = service.start(request)
            self.assertTrue(provider.dispatched.wait(2), "dispatch did not start")
            service.cancel(started.job_id)
            snapshot = self._wait_released(service, started.job_id)
            self.assertEqual(snapshot.status, JobStatus.CANCELLED)
            self.assertEqual(
                self._accounting(service, started.job_id).blocked_reason,
                "usage_unknown",
            )
        finally:
            service.shutdown()

    def test_frozen_comparison_copies_all_estimated_metadata_primitives(self) -> None:
        mutations = (
            ("profile_id", "changed-profile"),
            ("method", "changed-method"),
            ("estimated_input_tokens", 1),
            ("body_sha256", "0" * 64),
        )
        for index, (field, value) in enumerate(mutations):
            with self.subTest(field=field):
                identity, request = self._identity_request(f"frozen-{index}")
                provider = EstimatedProvider(usage=ObservedUsage(1, 1, "actual"))

                async def mutate(
                    prepared: PreparedRequest,
                    *,
                    field: str = field,
                    value: object = value,
                ) -> None:
                    accounting = prepared.estimated_input_accounting
                    if accounting is None:
                        raise AssertionError("estimated metadata missing")
                    object.__setattr__(accounting, field, value)

                service = self._service(provider, before_dispatch=mutate)
                try:
                    self._install(service, identity)
                    started = service.start(request)
                    snapshot = self._wait_released(service, started.job_id)

                    self.assertEqual(snapshot.status, JobStatus.FAILED)
                    self.assertIsNotNone(snapshot.issue)
                    assert snapshot.issue is not None
                    self.assertEqual(snapshot.issue.code, "AI_PROTOCOL_INCOMPATIBLE")
                    self.assertEqual(provider.sends, 0)
                    self.assertIsNone(
                        self._accounting(service, started.job_id).blocked_reason,
                    )
                finally:
                    service.shutdown()

    def test_constructor_rejects_non_tuple_trusted_profiles(self) -> None:
        with self.assertRaises(ServiceError) as caught:
            AssistantService(lambda: EstimatedProvider(usage=ObservedUsage()),
                             trusted_input_profiles=[PROFILE])  # type: ignore[arg-type]
        self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")


if __name__ == "__main__":
    unittest.main()
