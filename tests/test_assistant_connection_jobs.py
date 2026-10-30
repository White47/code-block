"""Connection lifecycle integration uses only trusted, offline fake providers."""

import asyncio
import time
import unittest
from dataclasses import replace
from threading import Event
from unittest.mock import patch

from test_assistant_connection_runner import FakeProvider, _valid_events

from codeviz.assistant.bridge_requests import (
    CancelRequest,
    ConnectionRequest,
    PollRequest,
    parse_configure,
)
from codeviz.assistant.issues import ProtocolDiagnostic
from codeviz.assistant.sdk_provider import SDKAdapterError
from codeviz.assistant.service import ServiceError, _AssistantRuntime


class ConnectionJobsTests(unittest.TestCase):
    def configured(self, provider=None, **kwargs):
        provider = provider or FakeProvider(_valid_events(), [])
        runtime = _AssistantRuntime(provider_factory=lambda config, key: provider, **kwargs)
        runtime.configure(parse_configure({"baseUrl": "https://provider.invalid/v1",
            "model": "probe-model", "key": "offline-test-only", "enabled": True}))
        self.addCleanup(runtime.shutdown)
        return runtime, provider

    def wait_poll(self, runtime, job_id, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = runtime.poll(PollRequest(job_id, None, 0))
            if predicate(state):
                return state
            time.sleep(0.005)
        self.fail(f"Connection lifecycle did not settle: {state}")

    def test_default_gate_is_lazy_and_preserves_dependency_missing(self):
        runtime = _AssistantRuntime()
        self.addCleanup(runtime.shutdown)
        with patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("SDK")), \
                patch("codeviz.assistant.service.AssistantService", side_effect=AssertionError("thread")):
            runtime.configure(parse_configure({"baseUrl": "https://provider.invalid/v1",
                "model": "probe-model", "key": "offline-test-only", "enabled": True}))
            self.assertTrue(runtime.get_settings()["keySet"])
            with self.assertRaises(ServiceError) as caught:
                runtime.test_connection(ConnectionRequest(1, "probe"))
            self.assertEqual(caught.exception.code, "AI_DEPENDENCY_MISSING")
            self.assertIsNone(runtime._engine)

    def test_success_replay_and_c7_projection_drop_live_resources(self):
        runtime, provider = self.configured()
        started = runtime.test_connection(ConnectionRequest(1, "probe"))
        self.assertEqual(started, {"jobId": started["jobId"], "status": "Queued",
            "scope": None, "conversationId": None, "kind": "connection"})
        state = self.wait_poll(runtime, started["jobId"], lambda p: p["resourcesReleased"])
        self.assertEqual(state["status"], "Completed")
        self.assertEqual(state["connectionResult"], {
            "toolSupported": True, "structuredOutputSupported": True})
        self.assertIsNone(state["projectEpoch"])
        self.assertIsNone(state["consentRevision"])
        self.assertNotIn("text", state)
        self.assertNotIn("answer", state)
        self.assertEqual(state["usage"]["reserved"]["modelRequests"], 2)
        self.assertEqual(runtime.test_connection(ConnectionRequest(1, "probe"))["jobId"], started["jobId"])
        self.assertEqual(provider.log.count("dispatch:tools"), 1)
        self.assertEqual(provider.log.count("dispatch:final"), 1)
        engine = runtime._engine
        self.assertFalse(engine._connection_jobs)
        self.assertFalse(engine._connection_slots)
        self.assertTrue(all(event["kind"] == "connection" for event in state["events"]))
        self.assertNotIn("confirmation", {event["type"] for event in state["events"]})
        for request in (PollRequest(started["jobId"], "foreign", 0),
                        PollRequest(started["jobId"], None, state["seq"] + 1),
                        PollRequest(started["jobId"], None, True)):
            with self.assertRaises(ServiceError):
                runtime.poll(request)

    def test_cancel_and_config_replacement_block_prepared_dispatch(self):
        entered = Event()

        async def before_dispatch(prepared):
            entered.set()
            await asyncio.Event().wait()

        runtime, provider = self.configured(before_dispatch=before_dispatch)
        job_id = runtime.test_connection(ConnectionRequest(1, "cancel"))["jobId"]
        self.assertTrue(entered.wait(1))
        reserved = runtime.poll(PollRequest(job_id, None, 0))["usage"]["reserved"]
        self.assertEqual((reserved["modelRequests"], reserved["outputTokens"]), (1, 32))
        receipt = runtime.cancel(CancelRequest(job_id, None))
        self.assertTrue(receipt["cancelRequested"])
        self.assertFalse(receipt["cancelAccepted"])
        state = self.wait_poll(runtime, job_id, lambda p: p["cancelAccepted"])
        self.assertEqual(state["status"], "Cancelled")
        self.assertTrue(state["resourcesReleased"])
        self.assertFalse(any(item.startswith("dispatch:") for item in provider.log))
        self.assertEqual(runtime.test_connection(ConnectionRequest(1, "cancel"))["jobId"], job_id)
        entered.clear()
        second = runtime.test_connection(ConnectionRequest(1, "replace"))["jobId"]
        self.assertTrue(entered.wait(1))
        runtime.configure(parse_configure({"baseUrl": "https://provider.invalid/v1",
            "model": "probe-model", "key": "replacement-test-only", "enabled": True}))
        self.wait_poll(runtime, second, lambda p: p["cancelAccepted"])
        with self.assertRaises(ServiceError):
            runtime.test_connection(ConnectionRequest(1, "replace"))
        self.assertFalse(any(item.startswith("dispatch:") for item in provider.log))

    def test_terminal_cleanup_failure_holds_slot_and_cancel_retries_close(self):
        provider = FakeProvider(_valid_events(), [])
        provider.fail_close = True
        runtime, _ = self.configured(provider)
        job_id = runtime.test_connection(ConnectionRequest(1, "cleanup"))["jobId"]
        terminal = self.wait_poll(runtime, job_id, lambda p: p["status"] == "Completed")
        self.assertFalse(terminal["resourcesReleased"])
        self.assertFalse(terminal["cancelAccepted"])
        with self.assertRaises(ServiceError):
            runtime._engine._release_connection_slot(job_id)
        with self.assertRaises(ServiceError) as caught:
            runtime.test_connection(ConnectionRequest(1, "another"))
        self.assertEqual(caught.exception.code, "AI_CAPACITY")
        provider.fail_close = False
        runtime.cancel(CancelRequest(job_id, None))
        released = self.wait_poll(runtime, job_id, lambda p: p["resourcesReleased"])
        self.assertEqual(released["status"], "Completed")
        self.assertGreater(released["seq"], terminal["seq"])

    def test_cancel_confirmation_waits_for_verified_resource_cleanup(self):
        entered = Event()

        async def before_dispatch(prepared):
            entered.set()
            await asyncio.Event().wait()

        provider = FakeProvider(_valid_events(), [])
        provider.fail_close = True
        runtime, _ = self.configured(provider, before_dispatch=before_dispatch)
        job_id = runtime.test_connection(ConnectionRequest(1, "pending"))["jobId"]
        self.assertTrue(entered.wait(1))
        runtime.cancel(CancelRequest(job_id, None))
        state = self.wait_poll(runtime, job_id, lambda p: p["dispatchClosed"])
        self.assertFalse(state["cancelAccepted"])
        self.assertFalse(state["resourcesReleased"])
        self.assertEqual(state["status"], "Running")
        provider.fail_close = False
        runtime.cancel(CancelRequest(job_id, None))
        self.wait_poll(runtime, job_id, lambda p: p["cancelAccepted"])

    def test_retired_history_eviction_never_reissues_an_old_request(self):
        runtime, provider = self.configured()
        first = None
        for index in range(129):
            job_id = runtime.test_connection(ConnectionRequest(1, f"history-{index}"))["jobId"]
            first = first or job_id
            self.wait_poll(runtime, job_id, lambda p: p["resourcesReleased"])
        self.assertEqual(len(runtime._engine._connection_completed), 128)
        before = len(provider.log)
        with self.assertRaises(ServiceError) as caught:
            runtime.test_connection(ConnectionRequest(1, "history-0"))
        self.assertEqual(caught.exception.code, "AI_RESULT_UNAVAILABLE")
        with self.assertRaises(ServiceError):
            runtime.cancel(CancelRequest(first, None))
        self.assertEqual(len(provider.log), before)

    def test_factory_captures_validated_config_key_on_worker_outside_locks(self):
        provider = FakeProvider(_valid_events(), [])
        captured = []
        entered = Event()

        def factory(config, key):
            self.assertFalse(runtime._lock.locked())
            self.assertFalse(runtime._engine._lock.locked())
            captured.append((config.revision, config.model, key.value))
            entered.set()
            return provider

        runtime = _AssistantRuntime(provider_factory=factory)
        self.addCleanup(runtime.shutdown)
        runtime.configure(parse_configure({"baseUrl": "https://provider.invalid/v1",
            "model": "probe-model", "key": "offline-test-only", "enabled": True}))
        self.assertEqual(captured, [])
        job_id = runtime.test_connection(ConnectionRequest(1, "frozen"))["jobId"]
        self.assertTrue(entered.wait(1))
        self.wait_poll(runtime, job_id, lambda p: p["resourcesReleased"])
        self.assertEqual(captured, [(1, "probe-model", "offline-test-only")])

    def test_missing_wire_proof_never_dispatches(self):
        class Unproven(FakeProvider):
            async def prepare(self, draft):
                return replace(await super().prepare(draft),
                               input_token_upper_bound=None, attribution_complete=False)

        runtime, provider = self.configured(Unproven(_valid_events(), []))
        job_id = runtime.test_connection(ConnectionRequest(1, "unproven"))["jobId"]
        state = self.wait_poll(runtime, job_id, lambda p: p["resourcesReleased"])
        self.assertEqual(state["status"], "Failed")
        self.assertEqual(state["issue"]["code"], "AI_PROTOCOL_INCOMPATIBLE")
        self.assertEqual(state["issue"]["diagnostic"], {"boundary": "pre_send"})
        self.assertFalse(any(item.startswith("dispatch:") for item in provider.log))

    def test_only_exact_connection_exceptions_can_supply_diagnostics(self):
        class Failing(FakeProvider):
            def __init__(self, error):
                super().__init__(_valid_events(), [])
                self.error = error

            def dispatch(self, prepared):
                async def iterate():
                    raise self.error
                    yield  # pragma: no cover

                return iterate()

        class Forged(RuntimeError):
            def __init__(self):
                self.code = "AI_AUTH_FAILED"
                self.diagnostic = ProtocolDiagnostic("dispatch", 418)
                super().__init__("untrusted provider detail")

        cases = (
            (SDKAdapterError(
                "AI_PROTOCOL_INCOMPATIBLE",
                ProtocolDiagnostic("dispatch", 404),
            ), {"boundary": "dispatch", "httpStatus": 404}),
            (Forged(), None),
        )
        for index, (error, diagnostic) in enumerate(cases):
            with self.subTest(error=type(error).__name__):
                runtime, _ = self.configured(Failing(error))
                job_id = runtime.test_connection(ConnectionRequest(1, f"diagnostic-{index}"))["jobId"]
                state = self.wait_poll(runtime, job_id, lambda p: p["resourcesReleased"])
                self.assertEqual(state["issue"]["code"], "AI_PROTOCOL_INCOMPATIBLE")
                self.assertEqual(state["issue"].get("diagnostic"), diagnostic)

    def test_global_slots_tombstones_and_history_are_bounded(self):
        runtime, _ = self.configured()
        first = runtime.test_connection(ConnectionRequest(1, "first"))["jobId"]
        self.wait_poll(runtime, first, lambda p: p["resourcesReleased"])
        engine = runtime._engine
        with engine._lock:
            engine._active_job_ids.update(("project-1", "project-2"))
        try:
            with self.assertRaises(ServiceError) as caught:
                runtime.test_connection(ConnectionRequest(1, "full"))
            self.assertEqual(caught.exception.code, "AI_CAPACITY")
        finally:
            with engine._lock:
                engine._active_job_ids.clear()
        with engine._lock:
            for index in range(1023):
                engine._connection_requests[(1, f"old-{index}")] = f"retired-{index}"
        with self.assertRaises(ServiceError) as caught:
            runtime.test_connection(ConnectionRequest(1, "new"))
        self.assertEqual(caught.exception.code, "AI_REQUEST_LIMIT")
        with self.assertRaises(ServiceError) as caught:
            runtime.test_connection(ConnectionRequest(1, "old-0"))
        self.assertEqual(caught.exception.code, "AI_RESULT_UNAVAILABLE")
        self.assertEqual(runtime.test_connection(ConnectionRequest(1, "first"))["jobId"], first)

    def test_prepare_close_is_reusable_after_failed_native_destroy(self):
        runtime, _ = self.configured()
        job_id = runtime.test_connection(ConnectionRequest(1, "first"))["jobId"]
        runtime.prepare_close()
        self.assertFalse(runtime.get_settings()["keySet"])
        state = runtime.poll(PollRequest(job_id, None, 0))
        self.assertTrue(state["resourcesReleased"])
        runtime.configure(parse_configure({"baseUrl": "https://provider.invalid/v1",
            "model": "probe-model", "key": "replacement-test-only", "enabled": True}))
        second = runtime.test_connection(ConnectionRequest(2, "second"))["jobId"]
        state = self.wait_poll(runtime, second, lambda p: p["resourcesReleased"])
        self.assertEqual(state["status"], "Completed")
