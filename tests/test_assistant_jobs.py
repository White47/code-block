"""C4 admission, cancellation, and lifecycle polling tests."""

import asyncio
import importlib
import os
import time
import unittest
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch
from uuid import UUID

from assistant_service_fixtures import EMPTY_SCOPE_FINGERPRINT, final_answer_event

from codeviz.analysis.project import ProjectAnalysis
from codeviz.analysis.symbols import SymbolIndex
from codeviz.assistant.authority import AuthorityState
from codeviz.assistant.contracts import JobStatus, RequestScope
from codeviz.assistant.provider import PreparedRequest, ProviderDraft, ProviderEvent
from codeviz.assistant.snapshot import SnapshotBinding, build_snapshot
from codeviz.assistant.source_policy import SourceConsent
from codeviz.ui.assistant_context import PublicationIdentity
from codeviz.ui.controller import AnalysisStatus, AppController


def _install_authority(service, identity):
    """Explicit trusted fake state for preexisting C4 service tests."""
    if service._snapshot is None:
        service._replace_publication(identity, build_snapshot(
            ProjectAnalysis((), SymbolIndex(), ()),
            binding=SnapshotBinding(str(identity.root), identity.ignore_paths,
                                    identity.project_epoch, identity.analysis_generation)))
    assert service._snapshot is not None
    consent = SourceConsent(service._snapshot.binding, "consent-c4",
                            "https://provider.invalid:443", ())
    service._replace_source_consent(consent)
    service._replace_authority(AuthorityState(
        True, 1, 1, identity.project_key, "https://provider.invalid:443",
        EMPTY_SCOPE_FINGERPRINT, (("conversation", 0),), "fake",
        "https://provider.invalid:443/v1/chat"))


class FakeProvider:
    def __init__(self):
        self.sends = 0
        self.closed = False

    async def prepare(self, draft):
        body = b'{"messages":[{"role":"user","content":"question"}]}'
        return PreparedRequest("prepared", "POST", "https://provider.invalid:443/v1/chat",
                               body, draft.max_output_tokens, draft.deadline,
                               len(body), (), (), True)

    async def dispatch(self, prepared):
        self.sends += 1
        yield final_answer_event()
        yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')

    async def cancel(self):
        pass

    async def aclose(self):
        self.closed = True


class FailingProvider(FakeProvider):
    async def dispatch(self, prepared):
        self.sends += 1
        raise RuntimeError("synthetic dispatch failure")
        yield None


class JobsFirstSliceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_digest_excludes_wire_and_publication_revision(self):
        identity, request = self._request("canonical")
        changed = replace(request, draft=ProviderDraft(
            b'{"wire":"changed"}', 8, time.monotonic() + 120),
            expected_identity=replace(identity, publication_revision=2))
        self.assertEqual(request.digest(), changed.digest())

    def test_digest_includes_frozen_explanation_budget(self):
        admission = importlib.import_module("codeviz.assistant._deepseek_admission")
        deepseek = admission._deepseek_explanation_budget()
        identity, request = self._request("budget-digest")
        deep_request = replace(
            request,
            draft=replace(request.draft, max_output_tokens=deepseek.final_output_tokens),
            budget=deepseek,
        )
        generic_request = replace(
            deep_request,
            draft=replace(deep_request.draft, max_output_tokens=2_000),
            budget=importlib.import_module("codeviz.assistant.budget").EXPLANATION_BUDGET,
        )

        self.assertNotEqual(deep_request.digest(), generic_request.digest())
        with self.assertRaises(ValueError):
            replace(deep_request, draft=replace(deep_request.draft, max_output_tokens=2_000))

    def test_private_admission_rejects_a_mismatched_frozen_job_digest(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        _identity, request = self._request("digest-mismatch")
        service = service_module.AssistantService(FakeProvider)
        try:
            with self.assertRaises(service_module.ServiceError) as caught:
                service._admit_job(request, prepared_digest="0" * 64)
            self.assertEqual(caught.exception.code, "AI_INVALID_INPUT")
            self.assertEqual(service._jobs, {})
        finally:
            service.shutdown()

    def _request(self, request_id="deadline"):
        root = self.root
        identity = PublicationIdentity(1, "epoch", 1,
                                       os.path.normcase(os.path.normpath(str(root))), root,
                                       (), AnalysisStatus.READY, True)
        request = importlib.import_module("codeviz.assistant.jobs").JobRequest(
            RequestScope("epoch", 1, request_id), "conversation", "question",
            "context-digest", "fake", 1, 1, EMPTY_SCOPE_FINGERPRINT,
            ProviderDraft(b'{"model":"fake"}', 4, time.monotonic() + 120), identity)
        return identity, request

    def test_final_wire_is_reserved_once_before_dispatch(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("wire-budget")
        provider = FakeProvider()
        observed = []

        async def before_dispatch(prepared):
            with service._lock:
                job = next(iter(service._jobs.values()))
                ledger = job.ledger
            self.assertIsNotNone(ledger)
            observed.append((prepared.body, ledger.usage))

        service = service_module.AssistantService(lambda: provider,
                                                   before_dispatch=before_dispatch)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            receipt = service.start(request)
            self._wait_status(service, receipt.job_id, JobStatus.COMPLETED)
            self.assertEqual(provider.sends, 1)
            self.assertEqual(len(observed), 1)
            body, usage = observed[0]
            self.assertNotEqual(body, request.draft.body)
            self.assertEqual((usage.model_requests, usage.input_tokens,
                              usage.output_tokens, usage.tool_calls),
                             (1, len(body), request.draft.max_output_tokens, 0))
        finally:
            service.shutdown()

    def test_unproven_input_bound_prevents_dispatch(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("unproven-input")

        class UnprovenProvider(FakeProvider):
            async def prepare(self, draft):
                return replace(await super().prepare(draft), input_token_upper_bound=None)

        provider = UnprovenProvider()
        service = service_module.AssistantService(lambda: provider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            receipt = service.start(request)
            self._wait_status(service, receipt.job_id, JobStatus.FAILED)
            self.assertEqual(provider.sends, 0)
        finally:
            service.shutdown()

    def _assert_slow_start_stage_is_unlocked(self, stage):
        service_module = importlib.import_module("codeviz.assistant.service")
        events_module = importlib.import_module("codeviz.assistant.events")
        identity, previous_request = self._request("previous-admission")
        service = service_module.AssistantService(FakeProvider)
        entered = Event()
        release = Event()
        probe_done = Event()
        results = []
        errors = []
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            previous = service.start(previous_request)
            self._wait_status(service, previous.job_id, JobStatus.COMPLETED)
            request = replace(previous_request,
                              scope=RequestScope("epoch", 1, "slow-admission"))

            def slow_uuid():
                entered.set()
                self.assertTrue(service._lock.acquire(blocking=False))
                service._lock.release()
                release.wait(2)
                return UUID(int=1)

            original_encoded = events_module._encoded

            def slow_encoded(value):
                if type(value) is str:
                    entered.set()
                    self.assertTrue(service._lock.acquire(blocking=False))
                    service._lock.release()
                    release.wait(2)
                return original_encoded(value)

            def start():
                try:
                    results.append(service.start(request))
                except Exception as error:
                    errors.append(error)

            def probe():
                try:
                    self.assertEqual(service.get(previous.job_id).status,
                                     JobStatus.COMPLETED)
                    self.assertEqual(service.poll(previous.job_id, 0)["status"],
                                     JobStatus.COMPLETED.value)
                    self.assertEqual(service.cancel(previous.job_id).status,
                                     JobStatus.COMPLETED)
                except Exception as error:
                    errors.append(error)
                finally:
                    probe_done.set()

            target = service_module.uuid if stage == "uuid" else events_module
            name = "uuid4" if stage == "uuid" else "_encoded"
            replacement = slow_uuid if stage == "uuid" else slow_encoded
            with patch.object(target, name, side_effect=replacement):
                starter = Thread(target=start)
                starter.start()
                try:
                    self.assertTrue(entered.wait(2), f"{stage} did not start")
                    prober = Thread(target=probe)
                    prober.start()
                    self.assertTrue(probe_done.wait(0.2),
                                    f"get/cancel/poll blocked on {stage}")
                finally:
                    release.set()
                    starter.join(2)
                    if "prober" in locals():
                        prober.join(2)
            self.assertFalse(errors)
            self.assertEqual(len(results), 1)
            self._wait_status(service, results[0].job_id, JobStatus.COMPLETED)
        finally:
            release.set()
            service.shutdown()

    def test_slow_uuid_generation_does_not_hold_service_lock(self):
        self._assert_slow_start_stage_is_unlocked("uuid")

    def test_slow_event_ring_validation_does_not_hold_service_lock(self):
        self._assert_slow_start_stage_is_unlocked("ring")

    def test_concurrent_same_request_after_uuid_race_admits_once(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("uuid-race")
        service = service_module.AssistantService(FakeProvider)
        entered = Event()
        release = Event()
        receipts = []
        errors = []
        original_uuid = service_module.uuid.uuid4
        calls = 0

        def delayed_uuid():
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                release.wait(2)
            return original_uuid()

        def start():
            try:
                receipts.append(service.start(request))
            except Exception as error:
                errors.append(error)

        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            with patch.object(service_module.uuid, "uuid4", side_effect=delayed_uuid):
                starter = Thread(target=start)
                starter.start()
                try:
                    self.assertTrue(entered.wait(2))
                    second = service.start(request)
                finally:
                    release.set()
                    starter.join(2)
            self.assertFalse(errors)
            self.assertEqual(receipts[0].job_id, second.job_id)
            self.assertEqual(len(service._requests), 1)
            self._wait_status(service, second.job_id, JobStatus.COMPLETED)
        finally:
            release.set()
            service.shutdown()

    def test_publication_change_during_uuid_generation_rejects_admission(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("publication-race")
        service = service_module.AssistantService(FakeProvider)
        entered = Event()
        release = Event()
        errors = []

        def slow_uuid():
            entered.set()
            release.wait(2)
            return UUID(int=2)

        def start():
            try:
                service.start(request)
            except Exception as error:
                errors.append(error)

        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            with patch.object(service_module.uuid, "uuid4", side_effect=slow_uuid):
                starter = Thread(target=start)
                starter.start()
                try:
                    self.assertTrue(entered.wait(2))
                    service.replace_current(replace(identity, publication_revision=2))
                finally:
                    release.set()
                    starter.join(2)
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], service_module.ServiceError)
            self.assertEqual(errors[0].code, "AI_ANALYSIS_REQUIRED")
            self.assertFalse(service._requests)
        finally:
            release.set()
            service.shutdown()

    def test_pending_admission_does_not_requeue_on_worker_loop(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("pending-launch")
        provider = FakeProvider()
        service = service_module.AssistantService(lambda: provider)
        entered = Event()
        release = Event()
        first_launch = Event()
        receipts = []
        launch_count = 0
        original_schedule = service._loop.call_soon_threadsafe
        original_launch = service._launch

        def counted_launch(job_id):
            nonlocal launch_count
            launch_count += 1
            first_launch.set()
            original_launch(job_id)

        def slow_schedule(callback, *args):
            result = original_schedule(callback, *args)
            if callback == service._launch:
                entered.set()
                release.wait(2)
            return result

        def start():
            receipts.append(service.start(request))

        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            with patch.object(service, "_launch", side_effect=counted_launch):
                with patch.object(service._loop, "call_soon_threadsafe",
                                  side_effect=slow_schedule):
                    starter = Thread(target=start)
                    starter.start()
                    try:
                        self.assertTrue(entered.wait(2))
                        self.assertTrue(first_launch.wait(2))
                        asyncio.run_coroutine_threadsafe(
                            asyncio.sleep(0), service._loop).result(timeout=0.5)
                        self.assertEqual(launch_count, 1)
                    finally:
                        release.set()
                        starter.join(2)
            self.assertEqual(len(receipts), 1)
            self._wait_status(service, receipts[0].job_id, JobStatus.COMPLETED)
            self.assertEqual(launch_count, 2)
            self.assertEqual(provider.sends, 1)
        finally:
            release.set()
            service.shutdown()

    def test_ledger_constructor_failure_retires_job_and_preserves_replay(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("ledger-failure")
        service = service_module.AssistantService(FakeProvider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            with patch.object(service_module, "BudgetLedger",
                              side_effect=RuntimeError("ledger unavailable")):
                started = service.start(request)
                self._wait_status(service, started.job_id, JobStatus.FAILED)
            failed = service.get(started.job_id)
            self.assertTrue(failed.dispatch_closed)
            self.assertTrue(failed.resources_released)
            self.assertEqual(service.start(request), failed)
            replacement = service.start(replace(
                request, scope=RequestScope("epoch", 1, "after-ledger-failure")))
            self._wait_status(service, replacement.job_id, JobStatus.COMPLETED)
        finally:
            service.shutdown()

    def test_schedule_failure_retires_job_without_dispatch_and_preserves_replay(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("schedule-failure-start")
        provider = FakeProvider()
        service = service_module.AssistantService(lambda: provider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            schedule = service._loop.call_soon_threadsafe

            def schedule_then_fail(*args):
                schedule(*args)
                raise RuntimeError("loop unavailable")

            with patch.object(service._loop, "call_soon_threadsafe",
                              side_effect=schedule_then_fail):
                with self.assertRaises(service_module.ServiceError) as failure:
                    service.start(request)
            self.assertEqual(failure.exception.code, "AI_SCHEDULE_FAILED")
            asyncio.run_coroutine_threadsafe(asyncio.sleep(0), service._loop).result(timeout=2)
            self.assertEqual(provider.sends, 0)
            replay = service.start(request)
            self.assertEqual(replay.status, JobStatus.FAILED)
            self.assertTrue(replay.dispatch_closed)
            self.assertTrue(replay.resources_released)
            replacement = service.start(replace(
                request, scope=RequestScope("epoch", 1, "after-schedule-failure")))
            self._wait_status(service, replacement.job_id, JobStatus.COMPLETED)
        finally:
            service.shutdown()

    def test_schedule_failure_does_not_overwrite_concurrent_cancellation(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("schedule-cancel-race")
        service = service_module.AssistantService(FakeProvider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            schedule = service._loop.call_soon_threadsafe

            def cancel_then_fail(callback, *args):
                if callback == service._launch:
                    job_id = args[0]
                    service.cancel(job_id)
                    self._wait_status(service, job_id, JobStatus.CANCELLED)
                    raise RuntimeError("loop unavailable")
                return schedule(callback, *args)

            with patch.object(service._loop, "call_soon_threadsafe",
                              side_effect=cancel_then_fail):
                with self.assertRaises(service_module.ServiceError):
                    service.start(request)
            replay = service.start(request)
            self.assertEqual(replay.status, JobStatus.CANCELLED)
            self.assertTrue(replay.cancel_accepted)
        finally:
            service.shutdown()

    def test_schedule_failure_releases_publication_cancelled_admission(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("schedule-publication-race")
        service = service_module.AssistantService(FakeProvider)
        job_ids = []
        try:
            service.replace_current(identity)
            _install_authority(service, identity)

            def replace_then_fail(_callback, job_id):
                job_ids.append(job_id)
                service.replace_current(replace(identity, publication_revision=2))
                raise RuntimeError("loop unavailable")

            with patch.object(service._loop, "call_soon_threadsafe",
                              side_effect=replace_then_fail):
                with self.assertRaises(service_module.ServiceError):
                    service.start(request)
            cancelled = service.get(job_ids[0])
            self.assertEqual(cancelled.status, JobStatus.CANCELLED)
            self.assertTrue(cancelled.cancel_accepted)
            self.assertTrue(cancelled.resources_released)
            self.assertNotIn(job_ids[0], service._active_job_ids)
        finally:
            service.shutdown()

    def test_slow_ledger_constructor_does_not_block_start(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        budget = importlib.import_module("codeviz.assistant.budget")
        identity, request = self._request("slow-ledger")
        entered = Event()
        release = Event()
        returned = Event()
        receipts = []
        service = service_module.AssistantService(FakeProvider)

        def slow_ledger(*, clock):
            entered.set()
            release.wait(2)
            return budget.BudgetLedger(clock=clock)

        def start():
            receipts.append(service.start(request))
            returned.set()

        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            with patch.object(service_module, "BudgetLedger", side_effect=slow_ledger):
                thread = Thread(target=start)
                thread.start()
                try:
                    self.assertTrue(entered.wait(2))
                    self.assertTrue(returned.wait(0.2))
                finally:
                    release.set()
                    thread.join(2)
            self._wait_status(service, receipts[0].job_id, JobStatus.COMPLETED)
        finally:
            release.set()
            service.shutdown()

    def test_ledger_deadline_is_exactly_ninety_seconds_from_admission(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("exact-deadline")
        entered = Event()
        release = Future()
        admitted_at = time.monotonic()

        async def before_dispatch(_prepared):
            entered.set()
            await asyncio.wrap_future(release)

        service = service_module.AssistantService(FakeProvider,
                                                   before_dispatch=before_dispatch)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            with patch.object(service_module, "monotonic",
                              side_effect=lambda: admitted_at):
                started = service.start(request)
                self.assertTrue(entered.wait(2))
                job = service._jobs[started.job_id]
                self.assertEqual(job.deadline, admitted_at + 90.0)
                self.assertIsNotNone(job.ledger)
                self.assertEqual(job.ledger._deadline, job.deadline)
            release.set_result(None)
            self._wait_status(service, started.job_id, JobStatus.COMPLETED)
        finally:
            if not release.done():
                release.set_result(None)
            service.shutdown()

    def test_deadline_includes_suspended_prepare(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request()
        entered = Event()
        release = Future()
        admitted_at = time.monotonic() - 89.95
        clock_calls = 0

        def admission_clock():
            nonlocal clock_calls
            clock_calls += 1
            return admitted_at if clock_calls == 1 else time.monotonic()

        class SlowPrepare(FakeProvider):
            async def prepare(self, draft):
                entered.set()
                await asyncio.wrap_future(release)
                return await super().prepare(draft)

        provider = SlowPrepare()
        service = service_module.AssistantService(lambda: provider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            with patch.object(service_module, "monotonic", side_effect=admission_clock):
                job = service.start(request)
            self.assertTrue(entered.wait(2))
            self._wait_status(service, job.job_id, JobStatus.FAILED)
            self.assertTrue(provider.closed)
            self.assertEqual(provider.sends, 0)
        finally:
            if not release.done():
                release.set_result(None)
            service.shutdown()

    def test_deadline_begins_before_ledger_setup_finishes(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        budget = importlib.import_module("codeviz.assistant.budget")
        identity, request = self._request("admission-clock")
        admitted_at = time.monotonic() - 89.95
        clock_calls = 0

        def admission_clock():
            nonlocal clock_calls
            clock_calls += 1
            return admitted_at if clock_calls == 1 else time.monotonic()

        class CountingProvider(FakeProvider):
            prepares = 0

            async def prepare(self, draft):
                self.prepares += 1
                return await super().prepare(draft)

        provider = CountingProvider()
        service = service_module.AssistantService(lambda: provider)

        def delayed_ledger(*, clock):
            time.sleep(0.08)
            return budget.BudgetLedger(clock=clock)

        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            with patch.object(service_module, "monotonic", side_effect=admission_clock):
                with patch.object(service_module, "BudgetLedger", side_effect=delayed_ledger):
                    job = service.start(request)
                    self._wait_status(service, job.job_id, JobStatus.FAILED)
            self.assertEqual(provider.prepares, 0)
            self.assertEqual(provider.sends, 0)
            self.assertTrue(service.get(job.job_id).resources_released)
        finally:
            service.shutdown()

    def test_expiry_at_pre_dispatch_barrier_prevents_send(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("barrier")
        now = [time.monotonic()]
        entered = Event()
        release = Future()
        provider = FakeProvider()

        async def before_dispatch(_prepared):
            entered.set()
            await asyncio.wrap_future(release)

        service = service_module.AssistantService(lambda: provider,
                                                   before_dispatch=before_dispatch)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            with patch.object(service_module, "monotonic", side_effect=lambda: now[0]):
                job = service.start(request)
                self.assertTrue(entered.wait(2))
                now[0] += 91.0
                release.set_result(None)
                self._wait_status(service, job.job_id, JobStatus.FAILED)
            self.assertEqual(provider.sends, 0)
            self.assertTrue(provider.closed)
        finally:
            if not release.done():
                release.set_result(None)
            service.shutdown()

    def test_cancel_after_dispatch_closes_one_send(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("inflight")
        entered = Event()

        class InFlight(FakeProvider):
            async def dispatch(self, prepared):
                self.sends += 1
                entered.set()
                await asyncio.Event().wait()
                yield None

        provider = InFlight()
        service = service_module.AssistantService(lambda: provider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            job = service.start(request)
            self.assertTrue(entered.wait(2))
            receipt = service.cancel(job.job_id)
            self.assertTrue(receipt.cancel_requested)
            self._wait_status(service, job.job_id, JobStatus.CANCELLED)
            confirmed = service.get(job.job_id)
            self.assertTrue(confirmed.cancel_accepted)
            self.assertTrue(confirmed.resources_released)
            self.assertTrue(provider.closed)
            self.assertEqual(provider.sends, 1)
        finally:
            service.shutdown()

    def test_deadline_during_dispatch_closes_resources(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("stream-deadline")
        now = [time.monotonic()]

        class ExpiringStream(FakeProvider):
            async def dispatch(self, prepared):
                self.sends += 1
                now[0] += 91.0
                yield None

        provider = ExpiringStream()
        service = service_module.AssistantService(lambda: provider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            with patch.object(service_module, "monotonic", side_effect=lambda: now[0]):
                job = service.start(request)
                self._wait_status(service, job.job_id, JobStatus.FAILED)
            self.assertEqual(provider.sends, 1)
            self.assertTrue(provider.closed)
            self.assertTrue(service.get(job.job_id).resources_released)
        finally:
            service.shutdown()

    def test_admission_clock_is_outside_service_lock(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("clock-lock")
        service = service_module.AssistantService(FakeProvider)

        def unlocked_clock():
            self.assertTrue(service._lock.acquire(blocking=False))
            service._lock.release()
            return time.monotonic()

        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            with patch.object(service_module, "monotonic", side_effect=unlocked_clock):
                job = service.start(request)
                self._wait_status(service, job.job_id, JobStatus.COMPLETED)
        finally:
            service.shutdown()

    def test_epoch_rotation_retires_old_request_tombstones(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("old-id")
        service = service_module.AssistantService(FakeProvider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            old = service.start(request)
            self._wait_status(service, old.job_id, JobStatus.COMPLETED)
            service.replace_current(replace(identity, project_epoch="new-epoch"))
            self.assertFalse(service._requests)
            with self.assertRaises(service_module.ServiceError) as stale:
                service.start(request)
            self.assertEqual(stale.exception.code, "AI_ANALYSIS_REQUIRED")
        finally:
            service.shutdown()

    def test_completed_job_retains_only_compact_replay_and_poll_snapshot(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("compact")
        service = service_module.AssistantService(FakeProvider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            started = service.start(request)
            self._wait_status(service, started.job_id, JobStatus.COMPLETED)
            self.assertNotIn(started.job_id, service._jobs)
            self.assertEqual(service.get(started.job_id), service.start(request))
            self.assertEqual(service.start(request).status, JobStatus.COMPLETED)
            with self.assertRaises(service_module.ServiceError) as conflict:
                service.start(replace(request, question="different"))
            self.assertEqual(conflict.exception.code, "AI_REQUEST_CONFLICT")
            self.assertFalse(any(isinstance(value, type(request))
                                 for value in service._requests.values()))
            retired = service._completed[started.job_id]
            self.assertEqual(retired.kind, "explanation")
            self.assertFalse(hasattr(retired, "ring"))
            self.assertFalse(hasattr(retired, "text"))
            self.assertFalse(hasattr(retired, "answer"))
            polled = service.poll(started.job_id, 0)
            self.assertTrue(polled["reset"])
            self.assertEqual(polled["events"], [])
            self.assertEqual(polled["seq"], retired.seq)
            self.assertEqual(polled["status"], JobStatus.COMPLETED.value)
            self.assertTrue(polled["dispatchClosed"])
            self.assertTrue(polled["resourcesReleased"])
            self.assertEqual(polled["text"], "")
            self.assertIsNone(polled["answer"])
            self.assertFalse(service.poll(started.job_id, retired.seq)["reset"])
            for cursor in (True, -1, retired.seq + 1, 1.0):
                with self.subTest(cursor=cursor), self.assertRaises(service_module.ServiceError):
                    service.poll(started.job_id, cursor)
        finally:
            service.shutdown()

    def test_active_ring_tracks_requested_then_confirmed_cancellation(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("ring-cancel")
        entered = Event()
        release = Future()

        async def before_dispatch(_prepared):
            entered.set()
            await asyncio.wrap_future(release)

        provider = FakeProvider()
        service = service_module.AssistantService(lambda: provider,
                                                   before_dispatch=before_dispatch)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            started = service.start(request)
            self.assertEqual(service.poll(started.job_id, 0)["kind"], "explanation")
            self.assertTrue(entered.wait(2))
            running = service.poll(started.job_id, 0)
            self.assertEqual(running["status"], JobStatus.RUNNING.value)
            self.assertGreater(running["seq"], 0)
            for cursor in (True, -1, running["seq"] + 1):
                with self.subTest(cursor=cursor), self.assertRaises(service_module.ServiceError):
                    service.poll(started.job_id, cursor)
            schedule = service._loop.call_soon_threadsafe
            held_closures = []

            def hold_close(callback, *args):
                if callback == service._schedule_close:
                    held_closures.append((callback, args))
                    return None
                return schedule(callback, *args)

            with patch.object(service._loop, "call_soon_threadsafe", side_effect=hold_close):
                receipt = service.cancel(started.job_id)
                self.assertTrue(receipt.cancel_requested)
                requested = service.poll(started.job_id, running["seq"])
                self.assertTrue(requested["cancelRequested"])
                self.assertFalse(requested["cancelAccepted"])
                self.assertIn("confirmation", [event["type"] for event in requested["events"]])
            self.assertEqual(len(held_closures), 1)
            callback, args = held_closures[0]
            schedule(callback, *args)
            self._wait_status(service, started.job_id, JobStatus.CANCELLED)
            confirmed = service.poll(started.job_id, requested["seq"])
            self.assertFalse(confirmed["reset"])
            self.assertEqual(confirmed["events"][-1]["type"], "status")
            self.assertGreater(confirmed["seq"], requested["seq"])
            self.assertTrue(confirmed["cancelAccepted"])
            self.assertTrue(confirmed["dispatchClosed"])
            self.assertTrue(confirmed["resourcesReleased"])
            self.assertEqual(provider.sends, 0)
        finally:
            if not release.done():
                release.set_result(None)
            service.shutdown()

    def test_epoch_rotation_defers_tombstone_retirement_until_drain(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("retired")
        service = service_module.AssistantService(FakeProvider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            started = service.start(request)
            self._wait_status(service, started.job_id, JobStatus.COMPLETED)

            class LockCheckingClear(dict):
                def clear(self):
                    self_outer.assertTrue(service._lock.acquire(blocking=False))
                    service._lock.release()
                    super().clear()

            self_outer = self
            old_requests = LockCheckingClear(service._requests)
            service._requests = old_requests
            service.replace_current(replace(identity, project_epoch="new-epoch"))
            self.assertFalse(service._requests)
            self.assertTrue(old_requests)
            with self.assertRaises(service_module.ServiceError):
                service.get(started.job_id)
            service._drain_pending_closures()
            self.assertFalse(old_requests)
        finally:
            service.shutdown()

    def test_undrained_epoch_rotation_fails_closed_at_one_retired_map(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("bounded-rotation")
        service = service_module.AssistantService(FakeProvider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            started = service.start(request)
            self._wait_status(service, started.job_id, JobStatus.COMPLETED)
            second = replace(identity, project_epoch="second")
            service.replace_current(second)
            self.assertEqual(len(service._retired_maps), 1)
            with self.assertRaises(service_module.ServiceError):
                service.replace_current(replace(second, project_epoch="third"))
            self.assertEqual(service._identity, second)
            self.assertEqual(len(service._retired_maps), 1)
            service._drain_pending_closures()
            self.assertFalse(service._retired_maps)
            service.replace_current(replace(second, project_epoch="third"))
        finally:
            service.shutdown()

    def test_schedule_failure_keeps_pending_closure_for_retry(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("schedule-failure")
        entered = Event()
        release = Future()
        provider = FakeProvider()

        async def before_dispatch(_prepared):
            entered.set()
            await asyncio.shield(asyncio.wrap_future(release))

        service = service_module.AssistantService(lambda: provider,
                                                   before_dispatch=before_dispatch)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            started = service.start(request)
            self.assertTrue(entered.wait(2))
            service.replace_current(replace(identity, publication_revision=2))
            with patch.object(service._loop, "call_soon_threadsafe",
                              side_effect=RuntimeError("loop unavailable")):
                with self.assertRaises(RuntimeError):
                    service._drain_pending_closures()
            self.assertIn(started.job_id, service._pending_closures)
            self.assertTrue(service.get(started.job_id).cancel_requested)
            self.assertFalse(service.get(started.job_id).cancel_accepted)
            requested = service.poll(started.job_id, 0)
            self.assertTrue(requested["cancelRequested"])
            self.assertFalse(requested["cancelAccepted"])
            service._drain_pending_closures()
            deadline = time.monotonic() + 2
            while not service.get(started.job_id).cancel_accepted and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(service.get(started.job_id).resources_released)
            release.set_result(None)
            asyncio.run_coroutine_threadsafe(asyncio.sleep(0), service._loop).result(timeout=2)
            self.assertEqual(provider.sends, 0)
        finally:
            if not release.done():
                release.set_result(None)
            service.shutdown()

    def test_controller_publication_closes_pre_dispatch_without_send(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        entered = Event()
        release = Future()
        provider = FakeProvider()

        async def before_dispatch(_prepared):
            entered.set()
            await asyncio.shield(asyncio.wrap_future(release))

        service = service_module.AssistantService(lambda: provider,
                                                   before_dispatch=before_dispatch)
        try:
            with TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "sample.py").write_text("value = 1\n", encoding="utf-8")
                controller = AppController(assistant_publication_mirror=service)
                controller.open_project(root)
                controller.analyze()
                identity = service._identity
                self.assertIsNotNone(identity)
                assert identity is not None
                _, request = self._request("publication")
                request = replace(request, scope=RequestScope(
                    identity.project_epoch, identity.analysis_generation, "publication"),
                    expected_identity=identity)
                _install_authority(service, identity)
                started = service.start(request)
                self.assertTrue(entered.wait(2))
                schedule = service._loop.call_soon_threadsafe

                def schedule_outside_locks(*args):
                    self.assertFalse(controller._assistant_publication_lock.locked())
                    self.assertFalse(service._lock.locked())
                    return schedule(*args)

                with patch.object(service._loop, "call_soon_threadsafe",
                                  side_effect=schedule_outside_locks):
                    controller._assistant_invalidate(
                        status=AnalysisStatus.OUTDATED, root=root, ignore_paths=())
                deadline = time.monotonic() + 2
                while not service.get(started.job_id).cancel_accepted and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(service.get(started.job_id).resources_released)
                release.set_result(None)
                asyncio.run_coroutine_threadsafe(asyncio.sleep(0), service._loop).result(timeout=2)
                self.assertEqual(provider.sends, 0)
        finally:
            if not release.done():
                release.set_result(None)
            service.shutdown()

    def test_current_epoch_tombstones_stop_at_1024_without_reissue(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("limit-original")
        service = service_module.AssistantService(FakeProvider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            started = service.start(request)
            self._wait_status(service, started.job_id, JobStatus.COMPLETED)
            original = service.get(started.job_id)
            for index in range(1023):
                service._requests[("epoch", f"other-{index}")] = ("digest", "retired-job")
            with self.assertRaises(service_module.ServiceError) as limit:
                service.start(replace(request, scope=RequestScope("epoch", 1, "new-id")))
            self.assertEqual(limit.exception.code, "AI_REQUEST_LIMIT")
            self.assertEqual(service.start(request), original)
            self.assertEqual(len(service._requests), 1024)
        finally:
            service.shutdown()

    def test_failed_cleanup_retains_slot_until_retry_releases(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("failed-close")

        class CloseFailsOnce(FailingProvider):
            def __init__(self):
                super().__init__()
                self.closes = 0

            async def aclose(self):
                self.closes += 1
                if self.closes == 1:
                    raise RuntimeError("close failed")
                await super().aclose()

        provider = CloseFailsOnce()
        service = service_module.AssistantService(lambda: provider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            job = service.start(request)
            self._wait_status(service, job.job_id, JobStatus.FAILED)
            self.assertFalse(service.get(job.job_id).resources_released)
            terminal = service.poll(job.job_id, 0)
            self.assertEqual(terminal["status"], JobStatus.FAILED.value)
            self.assertTrue(terminal["dispatchClosed"])
            self.assertFalse(terminal["resourcesReleased"])
            with self.assertRaises(service_module.ServiceError) as busy:
                service.start(replace(request, scope=RequestScope("epoch", 1, "next")))
            self.assertEqual(busy.exception.code, "AI_BUSY")
            service.cancel(job.job_id)
            deadline = time.monotonic() + 2
            while not service.get(job.job_id).resources_released and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(service.get(job.job_id).resources_released)
            released = service.poll(job.job_id, terminal["seq"])
            self.assertGreater(released["seq"], terminal["seq"])
            self.assertTrue(released["resourcesReleased"])
            self.assertTrue(released["cancelAccepted"])
            self.assertTrue(provider.closed)
        finally:
            service.shutdown()

    def test_shutdown_reports_unreleased_cleanup_and_can_retry(self):
        service_module = importlib.import_module("codeviz.assistant.service")
        identity, request = self._request("shutdown")
        provider_ready = Event()

        async def before_dispatch(_prepared):
            provider_ready.set()
            await asyncio.Event().wait()

        class CannotClose(FakeProvider):
            fail_close = True

            async def aclose(self):
                if self.fail_close:
                    raise RuntimeError("close failed")
                await super().aclose()

        provider = CannotClose()
        service = service_module.AssistantService(lambda: provider, before_dispatch=before_dispatch)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            job = service.start(request)
            # Running can precede provider creation; exercise an owned resource.
            self.assertTrue(provider_ready.wait(2))
            with self.assertRaises(service_module.ServiceError) as failure:
                service.shutdown()
            self.assertEqual(failure.exception.code, "AI_CLEANUP_INCOMPLETE")
            self.assertTrue(service._thread.is_alive())
            self.assertFalse(service.get(job.job_id).resources_released)
            provider.fail_close = False
            service.shutdown()
        finally:
            if service._thread.is_alive():
                provider.fail_close = False
                service.shutdown()

    def _wait_status(self, service, job_id, expected):
        deadline = time.monotonic() + 2
        while service.get(job_id).status is not expected and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(service.get(job_id).status, expected)

    def test_provider_failure_releases_resources_before_freeing_job_slot(self):
        jobs = importlib.import_module("codeviz.assistant.jobs")
        service_module = importlib.import_module("codeviz.assistant.service")
        root = self.root
        identity = PublicationIdentity(1, "epoch", 1,
                                       os.path.normcase(os.path.normpath(str(root))), root,
                                       (), AnalysisStatus.READY, True)
        request = jobs.JobRequest(RequestScope("epoch", 1, "failure"), "conversation",
                                  "question", "context-digest", "fake", 1, 1,
                                  EMPTY_SCOPE_FINGERPRINT, ProviderDraft(
                                      b'{"model":"fake"}', 4, time.monotonic() + 60),
                                  identity)
        provider = FailingProvider()
        service = service_module.AssistantService(lambda: provider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            started = service.start(request)
            deadline = time.monotonic() + 2
            while (service.get(started.job_id).status is not JobStatus.FAILED
                   and time.monotonic() < deadline):
                time.sleep(0.01)
            failed = service.get(started.job_id)
            self.assertEqual(failed.status, JobStatus.FAILED)
            self.assertTrue(failed.dispatch_closed)
            self.assertTrue(failed.resources_released)
            self.assertTrue(provider.closed)
        finally:
            service.shutdown()

    def test_equivalent_captured_publication_value_is_admitted(self):
        jobs = importlib.import_module("codeviz.assistant.jobs")
        service_module = importlib.import_module("codeviz.assistant.service")
        root = self.root
        identity = PublicationIdentity(1, "epoch", 1,
                                       os.path.normcase(os.path.normpath(str(root))), root,
                                       (), AnalysisStatus.READY, True)
        captured = replace(identity)
        self.assertIsNot(captured, identity)
        self.assertEqual(captured, identity)
        request = jobs.JobRequest(RequestScope("epoch", 1, "clone"), "conversation",
                                  "question", "context-digest", "fake", 1, 1,
                                  EMPTY_SCOPE_FINGERPRINT, ProviderDraft(
                                      b'{"model":"fake"}', 4, time.monotonic() + 60),
                                  captured)
        service = service_module.AssistantService(FakeProvider)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            try:
                receipt = service.start(request)
            except service_module.ServiceError as error:
                self.fail(f"equivalent captured publication rejected: {error.code}")
            self.assertEqual(receipt.status, JobStatus.QUEUED)
        finally:
            service.shutdown()

    def test_publication_replacement_closes_prepared_job_after_drain(self):
        jobs = importlib.import_module("codeviz.assistant.jobs")
        service_module = importlib.import_module("codeviz.assistant.service")
        provider = FakeProvider()
        prepared = Event()
        release = Future()

        async def before_dispatch(_captured):
            prepared.set()
            await asyncio.shield(asyncio.wrap_future(release))

        root = self.root
        identity = PublicationIdentity(1, "epoch", 1,
                                       os.path.normcase(os.path.normpath(str(root))), root,
                                       (), AnalysisStatus.READY, True)
        draft = ProviderDraft(b'{"model":"fake"}', 4, time.monotonic() + 60)
        request = jobs.JobRequest(RequestScope("epoch", 1, "request"), "conversation",
                                  "question", "context-digest", "fake", 1, 1,
                                  EMPTY_SCOPE_FINGERPRINT, draft, identity)
        service = service_module.AssistantService(lambda: provider,
                                                   before_dispatch=before_dispatch)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            first = service.start(request)
            self.assertTrue(prepared.wait(2), "provider request did not reach barrier")
            class NoRetainedScan(dict):
                def values(self):
                    raise AssertionError("publication replacement scanned retained jobs")

            retained_jobs = service._jobs
            service._jobs = NoRetainedScan(retained_jobs)
            try:
                service.replace_current(replace(identity, publication_revision=2))
            finally:
                service._jobs = retained_jobs
            with self.assertRaises(service_module.ServiceError) as stale:
                service.start(replace(request, scope=RequestScope(
                    "epoch", 1, "new-request-after-publication")))
            self.assertEqual(stale.exception.code, "AI_ANALYSIS_REQUIRED")
            receipt = service.get(first.job_id)
            self.assertTrue(receipt.cancel_requested)
            self.assertFalse(receipt.cancel_accepted)
            service._drain_pending_closures()
            deadline = time.monotonic() + 2
            while not service.get(first.job_id).cancel_accepted and time.monotonic() < deadline:
                time.sleep(0.01)
            confirmed = service.get(first.job_id)
            self.assertEqual(confirmed.status, JobStatus.CANCELLED)
            self.assertTrue(confirmed.cancel_accepted)
            self.assertTrue(confirmed.dispatch_closed)
            self.assertTrue(confirmed.resources_released)
            self.assertTrue(provider.closed)
            release.set_result(None)
            asyncio.run_coroutine_threadsafe(asyncio.sleep(0), service._loop).result(timeout=2)
            self.assertEqual(provider.sends, 0)
            self.assertEqual(service.get(first.job_id).status, JobStatus.CANCELLED)
        finally:
            if not release.done():
                release.set_result(None)
            service.shutdown()

    def test_cancel_closes_pre_dispatch_gate_and_preserves_replay(self):
        try:
            jobs = importlib.import_module("codeviz.assistant.jobs")
            service_module = importlib.import_module("codeviz.assistant.service")
        except ModuleNotFoundError as error:
            self.fail(f"C4 internal jobs/service implementation missing: {error.name}")

        provider = FakeProvider()
        prepared = Event()
        release = Future()

        async def before_dispatch(_captured):
            prepared.set()
            await asyncio.shield(asyncio.wrap_future(release))

        root = self.root
        identity = PublicationIdentity(1, "epoch", 1,
                                       os.path.normcase(os.path.normpath(str(root))), root,
                                       (), AnalysisStatus.READY, True)
        draft = ProviderDraft(b'{"model":"fake"}', 4, time.monotonic() + 60)
        request = jobs.JobRequest(RequestScope("epoch", 1, "request"), "conversation",
                                  "question", "context-digest", "fake", 1, 1,
                                  EMPTY_SCOPE_FINGERPRINT, draft, identity)
        service = service_module.AssistantService(lambda: provider,
                                                   before_dispatch=before_dispatch)
        try:
            service.replace_current(identity)
            _install_authority(service, identity)
            first = service.start(request)
            self.assertTrue(prepared.wait(2), "provider request did not reach barrier")
            self.assertEqual(service.start(request).job_id, first.job_id)
            with self.assertRaises(service_module.ServiceError) as conflict:
                service.start(replace(request, question="changed"))
            self.assertEqual(conflict.exception.code, "AI_REQUEST_CONFLICT")

            receipt = service.cancel(first.job_id)
            self.assertTrue(receipt.cancel_requested)
            self.assertFalse(receipt.cancel_accepted)
            deadline = time.monotonic() + 2
            while not service.get(first.job_id).cancel_accepted and time.monotonic() < deadline:
                time.sleep(0.01)
            confirmed = service.get(first.job_id)
            self.assertEqual(confirmed.status, JobStatus.CANCELLED)
            self.assertTrue(confirmed.cancel_accepted)
            self.assertTrue(confirmed.dispatch_closed)
            self.assertTrue(confirmed.resources_released)
            self.assertTrue(provider.closed)
            release.set_result(None)
            self.assertEqual(provider.sends, 0)
            self.assertEqual(service.start(request).job_id, first.job_id)
        finally:
            if not release.done():
                release.set_result(None)
            service.shutdown()
