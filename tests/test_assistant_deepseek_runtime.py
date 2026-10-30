"""Private runtime injection with pinned SDK and socket-guarded fake SSE only."""

import asyncio
import inspect
import json
import time
import unittest
from threading import Event
from unittest.mock import patch

import test_assistant_connection_deepseek as connection_fixture
import test_assistant_connection_jobs as jobs_fixture
import test_assistant_connection_runner as runner_fixture
import test_assistant_sdk_provider as sdk_fixture
import test_assistant_sdk_provider_deepseek as wire_fixture

from codeviz.assistant.bridge_requests import (
    CancelRequest,
    ConnectionRequest,
    PollRequest,
    parse_configure,
)
from codeviz.assistant.service import AssistantService, ServiceError, _AssistantRuntime


def configure(runtime, *, model="deepseek-flash", base_url="https://api.deepseek.com/v1"):
    return runtime.configure(parse_configure(dict(baseUrl=base_url, model=model,
        key=sdk_fixture.KEY, enabled=True)))


class DeepSeekRuntimeInjectionTests(unittest.TestCase):
    def test_application_runtime_owns_the_deepseek_explanation_budget(self):
        admission = __import__(
            "codeviz.assistant._deepseek_admission", fromlist=["_create_application_runtime"])
        runtime = admission._create_application_runtime()
        self.addCleanup(runtime.shutdown)

        self.assertEqual(runtime._explanation_budget,
                         admission._deepseek_explanation_budget())
        self.assertEqual(runtime._explanation_budget.final_output_tokens, 4_096)
        generic = _AssistantRuntime()
        self.addCleanup(generic.shutdown)
        self.assertEqual(generic._explanation_budget.final_output_tokens, 2_000)

    def test_constructor_copies_both_profile_sets_before_lazy_engine_creation(self):
        self.assertIn("trusted_input_profiles", inspect.signature(_AssistantRuntime).parameters)
        self.assertIn("trusted_connection_profiles", inspect.signature(_AssistantRuntime).parameters)
        self.assertIn("trusted_connection_profiles", inspect.signature(AssistantService).parameters)
        trusted_input = connection_fixture.input_profile()
        trusted_connection = connection_fixture.profile(self)
        runtime = _AssistantRuntime(trusted_input_profiles=(trusted_input,),
                                    trusted_connection_profiles=(trusted_connection,))
        self.addCleanup(runtime.shutdown)
        self.assertEqual(runtime._trusted_input_profiles, (trusted_input,))
        self.assertEqual(runtime._trusted_connection_profiles, (trusted_connection,))
        self.assertIsNot(runtime._trusted_input_profiles[0], trusted_input)
        self.assertIsNot(runtime._trusted_connection_profiles[0], trusted_connection)
        object.__setattr__(trusted_input, "model", True)
        object.__setattr__(trusted_connection, "model", True)
        self.assertEqual(runtime._trusted_input_profiles[0].model, "deepseek-flash")
        self.assertEqual(runtime._trusted_connection_profiles[0].model, "deepseek-flash")
        self.assertIsNone(runtime._engine)

    def test_service_detaches_app_owned_profile_objects(self):
        trusted_input = connection_fixture.input_profile()
        trusted_connection = connection_fixture.profile(self)
        engine = AssistantService(lambda: self.fail("No provider construction"),
            trusted_input_profiles=(trusted_input,), trusted_connection_profiles=(trusted_connection,))
        self.addCleanup(engine.shutdown)
        self.assertIsNot(engine._trusted_input_profiles[0], trusted_input)
        self.assertIsNot(engine._trusted_connection_profiles[0], trusted_connection)
        object.__setattr__(trusted_input, "endpoint", True)
        object.__setattr__(trusted_connection, "protocol", True)
        self.assertEqual(engine._trusted_input_profiles[0], connection_fixture.input_profile())
        self.assertEqual(engine._trusted_connection_profiles[0], connection_fixture.profile(self))

    def test_invalid_profile_collections_refuse_before_any_worker_starts(self):
        trusted_input = connection_fixture.input_profile()
        trusted_connection = connection_fixture.profile(self)
        invalid = (
            dict(trusted_input_profiles=[trusted_input]),
            dict(trusted_input_profiles=(trusted_input, trusted_input)),
            dict(trusted_input_profiles=(trusted_connection,)),
            dict(trusted_connection_profiles=[trusted_connection]),
            dict(trusted_connection_profiles=(trusted_connection, trusted_connection)),
            dict(trusted_connection_profiles=(trusted_input,)),
        )
        constructors = (_AssistantRuntime, lambda **options: AssistantService(lambda: None, **options))
        with patch("codeviz.assistant.service.Thread", side_effect=AssertionError("No worker")):
            for index, options in enumerate(invalid):
                for construct in constructors:
                    with self.subTest(case=index, owner=construct), self.assertRaises(ServiceError) as caught:
                        construct(**options)
                    self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")

    def test_mutated_primitives_refuse_constructor_instead_of_becoming_trusted(self):
        constructors = (_AssistantRuntime, lambda **options: AssistantService(lambda: None, **options))
        for field in ("profile_id", "endpoint", "model", "protocol"):
            trusted = connection_fixture.profile(self)
            object.__setattr__(trusted, field, True)
            for construct in constructors:
                with self.subTest(field=field, owner=construct), self.assertRaises(ServiceError):
                    construct(trusted_connection_profiles=(trusted,))
        for field in ("profile_id", "endpoint", "model", "method"):
            trusted = connection_fixture.input_profile()
            object.__setattr__(trusted, field, True)
            for construct in constructors:
                with self.subTest(field=field, owner=construct), self.assertRaises(ServiceError):
                    construct(trusted_input_profiles=(trusted,))

    def test_default_runtime_remains_off_even_with_both_allow_lists(self):
        with sdk_fixture.offline_guard() as attempts, \
                patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("No SDK")), \
                patch("codeviz.assistant.service.AssistantService", side_effect=AssertionError("No worker")):
            runtime = _AssistantRuntime(trusted_input_profiles=(connection_fixture.input_profile(),),
                                        trusted_connection_profiles=(connection_fixture.profile(self),))
            self.addCleanup(runtime.shutdown)
            configure(runtime)
            with self.assertRaises(ServiceError) as caught:
                runtime.test_connection(ConnectionRequest(1, "default-off"))
            self.assertEqual(caught.exception.code, "AI_DEPENDENCY_MISSING")
            self.assertIsNone(runtime._engine)
            self.assertEqual(attempts, [])

    def test_nonmatching_job_config_uses_unchanged_generic_strict_profile(self):
        provider = runner_fixture.FakeProvider(runner_fixture._valid_events(), [])
        runtime = _AssistantRuntime(provider_factory=lambda _config, _key: provider,
            trusted_input_profiles=(connection_fixture.input_profile(),),
            trusted_connection_profiles=(connection_fixture.profile(self),))
        self.addCleanup(runtime.shutdown)
        configure(runtime, model="probe-model", base_url="https://provider.invalid/v1")
        job_id = runtime.test_connection(ConnectionRequest(1, "strict-mismatch"))["jobId"]
        state = jobs_fixture.ConnectionJobsTests.wait_poll(self, runtime, job_id, lambda p: p["resourcesReleased"])
        self.assertEqual(state["status"], "Completed")
        self.assertEqual(state["connectionResult"], dict(toolSupported=True, structuredOutputSupported=True))
        self.assertEqual([prepared.max_output_tokens for prepared in provider.prepared], [32, 96])


class DeepSeekRuntimeSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Windows creates its local wakeup socketpair in new_event_loop().
        # Construct only that loop before the existing blanket network guard;
        # never exempt socket calls during SDK import, construction or dispatch.
        self.worker_loop = asyncio.new_event_loop()
        self.addCleanup(self.worker_loop.close)
        await wire_fixture.DeepSeekPinnedSDKTests.asyncSetUp(self)
        loop_factory = patch("codeviz.assistant.service.asyncio.new_event_loop", return_value=self.worker_loop)
        loop_factory.start()
        self.addCleanup(loop_factory.stop)

    def configured(self, *, input_profiles=None, connection_profiles=None, before_dispatch=None, **stream_options):
        self.states = []

        def factory(config, key):
            adapter, state, _unused_resources = connection_fixture.DeepSeekConnectionSDKTests.fixture(
                self, **stream_options)
            self.addCleanup(runtime.shutdown)
            self.assertEqual(adapter._config, config)
            self.assertEqual(adapter._key.value, key.value)
            self.states.append(state)
            return adapter

        options = dict(provider_factory=factory,
            trusted_input_profiles=(connection_fixture.input_profile(),) if input_profiles is None else input_profiles,
            trusted_connection_profiles=(connection_fixture.profile(self),)
            if connection_profiles is None else connection_profiles)
        if before_dispatch is not None:
            options["before_dispatch"] = before_dispatch
        runtime = _AssistantRuntime(**options)
        self.addCleanup(runtime.shutdown)
        configure(runtime)
        return runtime

    async def wait_released(self, runtime, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = runtime.poll(PollRequest(job_id, None, 0))
            if state["resourcesReleased"]:
                return state
            await asyncio.sleep(0.005)
        self.fail(f"Runtime connection did not release resources: {state}")

    async def test_runtime_real_sdk_connection_completes_and_retires_local_capability(self):
        runtime = self.configured()
        self.assertIsNone(runtime._engine)
        request = ConnectionRequest(1, "offline-runtime")
        job_id = runtime.test_connection(request)["jobId"]
        state = await self.wait_released(runtime, job_id)
        expected = dict(toolSupported=True, structuredOutputSupported=False,
                        jsonObjectSupported=True, outputValidation="local-json-schema")
        self.assertEqual(state["status"], "Completed")
        self.assertEqual(state["connectionResult"], expected)
        self.assertEqual(state["usage"]["reserved"]["outputTokens"], 128)
        self.assertIsNone(state["projectEpoch"])
        self.assertIsNone(state["consentRevision"])
        self.assertEqual(len(self.states), 1)
        proof = self.states[0]
        self.assertEqual(len(proof.sent), 2)
        for sent, captured in zip(proof.sent, proof.captured, strict=True):
            self.assertIs(sent, captured[0])
        self.assertEqual([json.loads(sent.content)["max_tokens"] for sent in proof.sent], [96, 32])
        self.assertFalse(any(event.kind == "text" for event in proof.events))
        self.assertTrue(all(client.is_closed for client in proof.clients))
        self.assertTrue(all(stream.closed for stream in proof.streams))
        self.assertTrue(all(transport.closed for transport in proof.transports))
        self.assertFalse(runtime._engine._connection_jobs)
        self.assertFalse(runtime._engine._connection_slots)
        self.assertIn(job_id, runtime._engine._connection_completed)
        self.assertIsNot(runtime._engine._trusted_input_profiles[0], runtime._trusted_input_profiles[0])
        self.assertIsNot(runtime._engine._trusted_connection_profiles[0], runtime._trusted_connection_profiles[0])
        self.assertEqual(runtime.test_connection(request)["jobId"], job_id)
        self.assertEqual(runtime.poll(PollRequest(job_id, None, 0))["connectionResult"], expected)
        self.assertEqual(len(proof.sent), 2)

    async def test_estimate_only_injection_never_grants_connection_capability(self):
        runtime = self.configured(connection_profiles=())
        job_id = runtime.test_connection(ConnectionRequest(1, "estimate-only"))["jobId"]
        state = await self.wait_released(runtime, job_id)
        self.assertEqual(state["status"], "Failed")
        self.assertIsNone(state["connectionResult"])
        self.assertEqual(len(self.states), 1)
        self.assertEqual(self.states[0].captured, [])
        self.assertEqual(self.states[0].sent, [])
        self.states[0].factory.assert_not_called()

    async def test_connection_binding_does_not_authorize_input_accounting(self):
        runtime = self.configured(input_profiles=())
        job_id = runtime.test_connection(ConnectionRequest(1, "binding-only"))["jobId"]
        state = await self.wait_released(runtime, job_id)
        self.assertEqual(state["status"], "Failed")
        self.assertEqual(state["issue"]["code"], "AI_PROTOCOL_INCOMPATIBLE")
        self.assertIsNone(state["connectionResult"])
        self.assertEqual(len(self.states[0].captured), 1)
        self.assertEqual(self.states[0].sent, [])
        self.states[0].factory.assert_not_called()

    async def test_mutation_of_callers_profiles_cannot_change_lazy_engine_injection(self):
        trusted_input = connection_fixture.input_profile()
        trusted_connection = connection_fixture.profile(self)
        runtime = self.configured(input_profiles=(trusted_input,), connection_profiles=(trusted_connection,))
        object.__setattr__(trusted_input, "model", True)
        object.__setattr__(trusted_connection, "endpoint", True)
        job_id = runtime.test_connection(ConnectionRequest(1, "detached"))["jobId"]
        state = await self.wait_released(runtime, job_id)
        self.assertEqual(state["status"], "Completed")
        self.assertTrue(state["connectionResult"]["jsonObjectSupported"])
        self.assertEqual(len(self.states[0].sent), 2)

    async def test_mutated_engine_allow_list_refuses_before_provider_factory(self):
        runtime = self.configured()
        engine = runtime._ensure_engine(runtime._config, runtime._key)
        object.__setattr__(engine._trusted_connection_profiles[0], "model", True)
        job_id = runtime.test_connection(ConnectionRequest(1, "mutated-profile"))["jobId"]
        state = await self.wait_released(runtime, job_id)
        self.assertEqual(state["status"], "Failed")
        self.assertEqual(state["issue"]["code"], "AI_PROTOCOL_INCOMPATIBLE")
        self.assertIsNone(state["connectionResult"])
        self.assertEqual(self.states, [])

    async def test_mutated_job_config_refuses_before_transport_entry(self):
        async def before(_prepared):
            object.__setattr__(runtime._config, "model", True)

        runtime = self.configured(before_dispatch=before)
        job_id = runtime.test_connection(ConnectionRequest(1, "mutated-config"))["jobId"]
        state = await self.wait_released(runtime, job_id)
        self.assertEqual(state["status"], "Failed")
        self.assertEqual(state["issue"]["code"], "AI_PROTOCOL_INCOMPATIBLE")
        self.assertIsNone(state["connectionResult"])
        self.assertEqual(len(self.states[0].captured), 1)
        self.assertEqual(self.states[0].sent, [])
        self.states[0].factory.assert_not_called()

    async def test_unknown_tools_usage_retires_failure_and_keeps_sticky_warning(self):
        runtime = self.configured(tools_usage=False)
        job_id = runtime.test_connection(ConnectionRequest(1, "unknown-tools"))["jobId"]
        state = await self.wait_released(runtime, job_id)
        self.assertEqual(state["status"], "Failed")
        self.assertEqual(state["issue"]["code"], "AI_BUDGET_EXCEEDED")
        self.assertIsNone(state["connectionResult"])
        self.assertEqual(state["usage"]["inputAccounting"]["blockedReason"], "usage_unknown")
        self.assertEqual(state["usage"]["reserved"]["outputTokens"], 96)
        self.assertEqual((len(self.states[0].captured), len(self.states[0].sent)), (1, 1))

    async def test_unknown_final_usage_retires_validated_capability_with_warning(self):
        runtime = self.configured(final_usage=False)
        job_id = runtime.test_connection(ConnectionRequest(1, "unknown-final"))["jobId"]
        state = await self.wait_released(runtime, job_id)
        self.assertEqual(state["status"], "Completed")
        self.assertTrue(state["connectionResult"]["jsonObjectSupported"])
        self.assertFalse(state["connectionResult"]["structuredOutputSupported"])
        self.assertEqual(state["usage"]["inputAccounting"]["blockedReason"], "usage_unknown")
        self.assertEqual(state["usage"]["inputAccounting"]["knownActualInputTokens"], 7)
        self.assertEqual(state["usage"]["observed"]["basis"], "unknown")
        self.assertEqual(state["usage"]["reserved"]["outputTokens"], 128)
        self.assertEqual(len(self.states[0].sent), 2)

    async def test_cancel_after_capture_never_enters_transport_and_retires(self):
        entered = Event()

        async def before(_prepared):
            entered.set()
            await asyncio.Event().wait()

        runtime = self.configured(before_dispatch=before)
        job_id = runtime.test_connection(ConnectionRequest(1, "cancel-sdk"))["jobId"]
        self.assertTrue(await asyncio.to_thread(entered.wait, 3))
        runtime.cancel(CancelRequest(job_id, None))
        state = await self.wait_released(runtime, job_id)
        self.assertEqual(state["status"], "Cancelled")
        self.assertTrue(state["cancelAccepted"])
        self.assertIsNone(state["connectionResult"])
        self.assertEqual(state["usage"]["reserved"]["outputTokens"], 96)
        self.assertEqual(len(self.states[0].captured), 1)
        self.assertEqual(self.states[0].sent, [])
        self.states[0].factory.assert_not_called()
        self.assertTrue(all(client.is_closed for client in self.states[0].clients))
        self.assertFalse(runtime._engine._connection_jobs)
        self.assertFalse(runtime._engine._connection_slots)


if __name__ == "__main__":
    unittest.main()
