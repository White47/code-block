"""C4 bounded trusted-local executor and cancellation barriers."""

import asyncio
import os
import time
import unittest
from concurrent.futures import Future
from hashlib import sha256
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

from codeviz.analysis.project import ProjectAnalysis
from codeviz.analysis.python import ParsedFile
from codeviz.analysis.symbols import SymbolIndex
from codeviz.assistant.authority import AuthorityState
from codeviz.assistant.budget import BudgetLedger, BudgetReservation
from codeviz.assistant.contracts import AgentError, JobStatus, RequestScope
from codeviz.assistant.events import EventRing
from codeviz.assistant.jobs import JobRequest, _Job
from codeviz.assistant.provider import PreparedRequest, ProviderDraft, WireOccurrence
from codeviz.assistant.service import (
    AssistantService,
    ServiceError,
    _SendPolicy,
    _verify_wire_files,
    _wire_reservation,
)
from codeviz.assistant.snapshot import SnapshotBinding, build_snapshot
from codeviz.assistant.source_policy import SourceConsent
from codeviz.ui.assistant_context import PublicationIdentity
from codeviz.ui.controller import AnalysisStatus


class ExecutorFoundationTests(unittest.TestCase):
    def setUp(self):
        self.service = AssistantService(lambda: None)
        self.root = Path.cwd()
        self.identity = PublicationIdentity(
            1, "epoch", 1, os.path.normcase(os.path.normpath(str(self.root))),
            self.root, (), AnalysisStatus.READY, True)
        self.service.replace_current(self.identity)
        self.service._replace_authority(AuthorityState(
            True, 1, 1, self.identity.project_key, "https://provider.invalid:443",
            "scope", (("conversation", 0),), "fake",
            "https://provider.invalid:443/v1/chat"))

    def tearDown(self):
        if self.service._thread.is_alive():
            self.service.shutdown()

    def job(self, number):
        request = JobRequest(
            RequestScope("epoch", 1, str(number)), "conversation", "question",
            "digest", "fake", 1, 1, "scope",
            ProviderDraft(b"{}", 4, time.monotonic() + 90), self.identity)
        job_id = str(number)
        job = _Job(job_id, request, request.digest(), EventRing(job_id, kind="explanation"))
        job.conversation_revision = 0
        job.authority_origin = "https://provider.invalid:443"
        job.authority_endpoint = "https://provider.invalid:443/v1/chat"
        job.status = JobStatus.RUNNING
        job.admission_pending = False
        with self.service._lock:
            self.service._jobs[job_id] = job
            self.service._active_job_ids.add(job_id)
            self.service._requests[(request.scope.project_epoch, request.scope.request_id)] = (
                job.digest, job_id)
        return job

    def run_b(self, job, operation):
        return asyncio.run_coroutine_threadsafe(
            self.service._run_b(job, operation), self.service._loop)

    def wait_until(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(predicate())

    def test_two_global_leases_and_one_per_job_reject_third(self):
        release = Event()
        entered = [Event(), Event()]
        jobs = [self.job(i) for i in range(3)]
        futures = [self.run_b(jobs[i], lambda i=i: (entered[i].set(), release.wait(3)))
                   for i in range(2)]
        try:
            self.assertTrue(all(event.wait(2) for event in entered))
            with self.assertRaises(ServiceError) as third:
                self.run_b(jobs[2], lambda: None).result(2)
            self.assertEqual(third.exception.code, "AI_BUSY")
            with self.assertRaises(ServiceError) as duplicate:
                self.run_b(jobs[0], lambda: None).result(2)
            self.assertEqual(duplicate.exception.code, "AI_BUSY")
            self.assertEqual(self.service._b_outstanding, 2)
        finally:
            release.set()
            for future in futures:
                future.result(3)

    def test_running_cancel_waits_for_os_future_before_confirmation(self):
        release = Event()
        entered = Event()
        job = self.job(1)
        future = self.run_b(job, lambda: (entered.set(), release.wait(5)))
        try:
            self.assertTrue(entered.wait(2))
            self.wait_until(lambda: job.b_future is not None)
            raw = job.b_future
            self.assertIsInstance(raw, Future)
            self.assertFalse(self.service.cancel(job.job_id).cancel_accepted)
            self.wait_until(lambda: self.service.get(job.job_id).dispatch_closed)
            self.assertFalse(self.service.get(job.job_id).resources_released)
            self.assertFalse(raw.done())
            with self.assertRaises(ServiceError) as busy:
                self.run_b(job, lambda: None).result(2)
            self.assertEqual(busy.exception.code, "AI_CANCELLED")
        finally:
            release.set()
            try:
                future.result(3)
            except Exception:
                pass
        self.wait_until(lambda: self.service.get(job.job_id).cancel_accepted)
        self.assertTrue(raw.done())
        self.assertTrue(self.service.get(job.job_id).resources_released)

    def test_queued_cancel_prevents_operation_entry(self):
        job = self.job(1)
        raw = Future()
        called = Event()
        with patch.object(self.service._b_executor, "submit", return_value=raw):
            queued = self.run_b(job, called.set)
            self.wait_until(lambda: job.b_future is raw)
            self.assertFalse(self.service.cancel(job.job_id).cancel_accepted)
            self.wait_until(lambda: self.service.get(job.job_id).cancel_accepted)
            self.assertTrue(raw.cancelled())
            self.assertFalse(called.is_set())
            self.assertTrue(queued.done())

    def test_submit_cancel_gap_cannot_confirm_or_run_after_gate_closure(self):
        job = self.job(1)
        entered = Event()
        release = Event()
        called = Event()
        original_submit = self.service._b_executor.submit

        def delayed_submit(operation):
            entered.set()
            release.wait(5)
            return original_submit(operation)

        with patch.object(self.service._b_executor, "submit", side_effect=delayed_submit):
            future = self.run_b(job, called.set)
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(self.service.cancel(job.job_id).cancel_accepted)
                self.assertFalse(self.service.get(job.job_id).resources_released)
                self.assertEqual(self.service._b_outstanding, 1)
            finally:
                release.set()
            try:
                future.result(3)
            except Exception:
                pass
        self.wait_until(lambda: self.service.get(job.job_id).cancel_accepted)
        self.assertFalse(called.is_set())

    def test_submit_and_completion_callbacks_do_not_hold_service_lock(self):
        job = self.job(1)

        def probe():
            self.assertTrue(self.service._lock.acquire(blocking=False))
            self.service._lock.release()

        original_submit = self.service._b_executor.submit

        def submit(operation):
            probe()
            return original_submit(operation)

        with patch.object(self.service._b_executor, "submit", side_effect=submit):
            self.run_b(job, probe).result(2)
        self.assertEqual(self.service._b_outstanding, 0)

        completed = Future()
        completed.set_result(7)
        original_add = completed.add_done_callback

        def add_callback(callback):
            probe()
            original_add(callback)

        with patch.object(self.service._b_executor, "submit", return_value=completed):
            with patch.object(completed, "add_done_callback", side_effect=add_callback):
                self.assertEqual(self.run_b(job, probe).result(2), 7)
        self.assertEqual(self.service._b_outstanding, 0)

    def test_repeated_cancel_and_shutdown_keep_running_slot(self):
        job = self.job(1)
        entered = Event()
        release = Event()
        future = self.run_b(job, lambda: (entered.set(), release.wait(5)))
        self.assertTrue(entered.wait(2))
        try:
            self.service.cancel(job.job_id)
            self.service.cancel(job.job_id)
            errors = []
            shutdown = Thread(target=lambda: self._shutdown_capture(errors))
            shutdown.start()
            shutdown.join(4)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].code, "AI_CLEANUP_INCOMPLETE")
            self.assertFalse(self.service.get(job.job_id).resources_released)
            self.assertEqual(self.service._b_outstanding, 1)
        finally:
            release.set()
            try:
                future.result(3)
            except Exception:
                pass
        self.service.shutdown()
        self.assertEqual(self.service._b_outstanding, 0)

    def _shutdown_capture(self, errors):
        try:
            self.service.shutdown()
        except ServiceError as error:
            errors.append(error)


class WireGateTests(unittest.TestCase):
    def test_unverified_attribution_fails_closed(self):
        body = b'{"messages":[{"content":"plain"}]}'
        prepared = PreparedRequest("one", "POST", "https://provider.invalid:443/v1/chat",
                                   body, 4, 100.0, len(body), (), (), False)
        with self.assertRaises(ServiceError) as caught:
            _wire_reservation(prepared)
        self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")

    def test_reservation_counts_captured_source_and_metadata_file(self):
        body = b'{"messages":[{"content":"alpha"},{"content":"alpha beta"}]}'
        prepared = PreparedRequest(
            "one", "POST", "https://provider.invalid:443/v1/chat", body, 4, 100.0,
            len(body) + 1024 + 128 * 2,
            (WireOccurrence("/messages/0/content", 0, 5, "a.py"),
             WireOccurrence("/messages/1/content", 0, 5, "a.py")),
            (WireOccurrence("/messages/1/content", 6, 10, "b.py"),), True)
        reservation, files = _wire_reservation(prepared)
        self.assertEqual(reservation.source_fragments, (("a.py", "alpha"),
                                                        ("a.py", "alpha")))
        self.assertEqual((reservation.model_requests, reservation.input_tokens,
                          reservation.output_tokens, files),
                         (1, len(body) + 1024 + 128 * 2, 4, ("a.py", "b.py")))

    def test_metadata_only_file_requires_whole_file_secret_gate(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            raw = b"symbol = 1\nPASSWORD = 'secretvalue123'\n"
            (root / "module.py").write_bytes(raw)
            binding = SnapshotBinding(str(root), (), "epoch", 1)
            file = ParsedFile("module.py", "module", None, 2, parsed_success=True,
                              content_hash=sha256(raw).hexdigest())
            snapshot = build_snapshot(ProjectAnalysis((file,), SymbolIndex(), ()),
                                      binding=binding)
            consent = SourceConsent(binding, "consent", "https://provider.invalid:443",
                                    ("module.py",))
            with self.assertRaises(ServiceError) as caught:
                _verify_wire_files(snapshot, consent, RequestScope("epoch", 1, "request"),
                                   "https://provider.invalid:443", ("module.py",), lambda: None)
            self.assertEqual(caught.exception.code, "AI_CONSENT_REQUIRED")


class CumulativeSendPolicyTests(unittest.TestCase):
    @staticmethod
    def prepared(identifier, limit, text="question", *, source=(), metadata=()):
        body = (f'{{"messages":[{{"content":"{text}"}}]}}').encode()
        return PreparedRequest(
            identifier, "POST", "https://provider.invalid:443/v1/chat",
            body, limit, 100.0, len(body) + 1024,
            tuple(WireOccurrence("/messages/0/content", start, end, path)
                  for start, end, path in source),
            tuple(WireOccurrence("/messages/0/content", start, end, path)
                  for start, end, path in metadata), True)

    def test_ordinary_and_final_allocations_share_2000_token_ledger(self):
        ledger = BudgetLedger()
        policy = _SendPolicy(ledger)
        with self.assertRaises(ServiceError):
            policy.admit(self.prepared("too-large", 513), "ordinary")
        self.assertEqual(ledger.usage.model_requests, 0)
        policy.admit(self.prepared("ordinary", 512), "ordinary")
        policy.admit(self.prepared("final", 1488), "final")
        self.assertEqual((ledger.usage.model_requests, ledger.usage.output_tokens), (2, 2000))
        with self.assertRaises(ServiceError):
            policy.admit(self.prepared("exhausted", 1), "repair")

    def test_retransmitted_source_and_metadata_files_charge_cumulatively(self):
        ledger = BudgetLedger()
        policy = _SendPolicy(ledger)
        first = self.prepared("first", 128, "alpha beta",
                              source=((0, 5, "a.py"),), metadata=((6, 10, "b.py"),))
        policy.admit(first, "ordinary")
        policy.admit(self.prepared("second", 128, "alpha beta",
                                   source=((0, 5, "a.py"),),
                                   metadata=((6, 10, "b.py"),)), "ordinary")
        self.assertEqual((ledger.usage.source_characters, ledger.usage.files),
                         (10, ("a.py",)))
        self.assertEqual(policy.disclosed_files, ("a.py", "b.py"))
        policy.admit(self.prepared("more", 1, " ".join(
            f"code{index:03d}" for index in range(6)), metadata=tuple(
                (index * 8, index * 8 + 7, f"{index}.py")
                for index in range(6))), "ordinary")
        with self.assertRaises(ServiceError):
            policy.admit(self.prepared("ninth", 1, "unique999",
                                       metadata=((0, 9, "ninth.py"),)), "ordinary")
        self.assertEqual(ledger.usage.model_requests, 3)

    def test_same_preparation_cannot_be_admitted_twice(self):
        ledger = BudgetLedger()
        policy = _SendPolicy(ledger)
        prepared = self.prepared("one", 10)
        policy.admit(prepared, "ordinary")
        with self.assertRaises(ServiceError):
            policy.admit(prepared, "ordinary")
        self.assertEqual(ledger.usage.model_requests, 1)

    def test_usage_discrepancy_blocks_another_send_without_refund(self):
        ledger = BudgetLedger()
        policy = _SendPolicy(ledger)
        reservation, _ = policy.admit(self.prepared("one", 100), "ordinary")
        with self.assertRaises(ServiceError):
            policy.report_usage(reservation, {"inputTokens": reservation.input_tokens,
                                              "outputTokens": 101, "basis": "actual"})
        self.assertEqual((ledger.usage.model_requests, ledger.usage.output_tokens), (1, 100))
        with self.assertRaises(ServiceError):
            policy.admit(self.prepared("two", 100), "ordinary")

    def test_tool_attempts_remain_with_existing_ledger(self):
        ledger = BudgetLedger()
        policy = _SendPolicy(ledger)
        for _ in range(8):
            ledger.reserve(BudgetReservation(tool_calls=1))
        policy.admit(self.prepared("one", 10), "ordinary")
        self.assertEqual(ledger.usage.tool_calls, 8)
        with self.assertRaises(AgentError):
            ledger.reserve(BudgetReservation(tool_calls=1))

    def test_six_sends_are_the_request_limit(self):
        ledger = BudgetLedger()
        policy = _SendPolicy(ledger)
        for index in range(6):
            policy.admit(self.prepared(str(index), 1), "ordinary")
        with self.assertRaises(AgentError):
            policy.admit(self.prepared("seventh", 1), "ordinary")
        self.assertEqual(ledger.usage.model_requests, 6)

    def test_only_one_repair_can_spend_remaining_output(self):
        ledger = BudgetLedger()
        policy = _SendPolicy(ledger)
        policy.admit(self.prepared("first", 512), "ordinary")
        policy.admit(self.prepared("final", 1000), "final")
        policy.admit(self.prepared("repair", 488), "repair")
        with self.assertRaises(ServiceError):
            policy.admit(self.prepared("second-repair", 1), "repair")
        self.assertEqual(ledger.usage.output_tokens, 2000)
