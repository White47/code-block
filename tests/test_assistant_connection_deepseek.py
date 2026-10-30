"""Explicit trusted source-free connection binding, with real SDK/offline SSE."""

import asyncio
import hashlib
import importlib
import json
import time
import unittest
from dataclasses import FrozenInstanceError, replace

import test_assistant_sdk_provider as fixture
import test_assistant_sdk_provider_deepseek as wire_fixture
import test_assistant_sdk_stream_deepseek as stream_fixture

from codeviz.assistant._wire_accounting import TrustedEstimatedProfile
from codeviz.assistant.budget import BudgetReservation
from codeviz.assistant.connection import (
    _ConnectionResources,
    _read_final,
    _validate_prepared,
    run_connection,
)
from codeviz.assistant.connection_profile import connection_draft
from codeviz.assistant.contracts import AgentError
from codeviz.assistant.provider import ProviderDraft, ProviderError
from codeviz.assistant.sdk_provider import SDKAdapterError


def profile(case):
    module = importlib.import_module("codeviz.assistant.connection_profile")
    cls = getattr(module, "TrustedConnectionProfile", None)
    case.assertTrue(callable(cls), "Missing closed TrustedConnectionProfile")
    return cls("deepseek-flash-sdk-wire-v1", "https://api.deepseek.com:443/v1/chat/completions",
               "deepseek-flash", "deepseek-json-object-v1")


def input_profile():
    return TrustedEstimatedProfile("deepseek-flash-sdk-wire-v1",
        "https://api.deepseek.com:443/v1/chat/completions", "deepseek-flash")


class DeepSeekConnectionProfileTests(unittest.TestCase):
    def test_explicit_binding_selects_one_exact_allocation_without_changing_logical_bytes(self):
        from codeviz.assistant.sdk_provider import _draft, _SDKWireProfile

        trusted = profile(self)
        private = _SDKWireProfile(trusted.profile_id, trusted.endpoint, trusted.model, "POST")
        for stage, generic_limit, deepseek_limit in (("tools", 32, 96), ("final", 96, 32)):
            with self.subTest(stage=stage):
                generic = connection_draft(stage, deadline=10)
                deepseek = connection_draft(stage, deadline=10, connection_profile=trusted)
                self.assertEqual(generic.body, deepseek.body)
                self.assertEqual((generic.max_output_tokens, deepseek.max_output_tokens),
                                 (generic_limit, deepseek_limit))
                _draft(generic, 0)
                _draft(deepseek, 0, _wire_profile=private)
                with self.assertRaises(SDKAdapterError):
                    _draft(generic, 0, _wire_profile=private)
                with self.assertRaises(SDKAdapterError):
                    _draft(deepseek, 0)
                with self.assertRaises(SDKAdapterError):
                    _draft(deepseek, 0, _wire_profile=input_profile())
        self.assertEqual(sum(connection_draft(stage, deadline=10, connection_profile=trusted).max_output_tokens
                             for stage in ("tools", "final")), 128)

    def test_closed_profile_is_copied_and_selected_only_by_config(self):
        module = importlib.import_module("codeviz.assistant.connection_profile")
        trusted = profile(self)
        copied = module._copy_connection_profiles((trusted,))
        self.assertEqual(copied, (trusted,))
        self.assertIsNot(copied[0], trusted)
        self.assertEqual(module._select_connection_profile(wire_fixture.config(), copied), trusted)
        with self.assertRaises(ValueError):
            module._copy_connection_profiles((trusted, trusted))

    def test_profile_requires_exact_closed_primitives_and_frozen_tuple_injection(self):
        module = importlib.import_module("codeviz.assistant.connection_profile")
        trusted = profile(self)
        with self.assertRaises(FrozenInstanceError):
            trusted.model = "other"
        for field, value in (("profile_id", "foreign"), ("endpoint", "https://api.deepseek.com/beta"),
                             ("model", True), ("protocol", "remote-strict")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(trusted, **{field: value})
        with self.assertRaises(ValueError):
            module._copy_connection_profiles([trusted])
        with self.assertRaises(ValueError):
            module._copy_connection_profiles((input_profile(),))
        self.assertIsNone(module._select_connection_profile(wire_fixture.config(), ()))
        self.assertIsNone(module._select_connection_profile(replace(wire_fixture.config(), model="other"),
                                                            (trusted,)))


class DeepSeekConnectionSDKTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = wire_fixture.DeepSeekPinnedSDKTests.asyncSetUp

    def fixture(self, *, tools_usage=True, final_usage=True, answer="{}", tool_arguments="{}", tool_id="c",
                tools_input=7, final_input=11):
        adapter, state = wire_fixture.DeepSeekPinnedSDKTests.adapter(self)
        httpx = self.sdk["httpx2"]
        state.streams, state.transports, state.events = [], [], []

        class SSE(httpx.AsyncByteStream):
            def __init__(self, chunks):
                self.chunks, self.closed = chunks, False

            async def __aiter__(self):
                for chunk in self.chunks:
                    yield chunk

            async def aclose(self):
                self.closed = True

        def respond(request):
            tools = bool(json.loads(request.content).get("tools"))
            usage = dict(prompt_tokens=tools_input if tools else final_input, completion_tokens=3 if tools else 2)
            if tools:
                chunks = [stream_fixture.frame(dict(tool_calls=[dict(index=0, id=tool_id, type="function",
                    function=dict(name="p", arguments=tool_arguments))])),
                    stream_fixture.frame(finish="tool_calls", usage=usage if tools_usage else None),
                    b"data: [DONE]\n\n"]
            else:
                chunks = stream_fixture.success(answer, usage=usage if final_usage else None)
            stream = SSE(chunks)
            state.streams.append(stream)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

        class Transport(httpx.MockTransport):
            closed = False

            async def handle_async_request(self, request):
                state.sent.append(request)
                return await super().handle_async_request(request)

            async def aclose(self):
                self.closed = True
                await super().aclose()

        def create_transport():
            transport = Transport(respond)
            state.transports.append(transport)
            return transport

        state.factory.side_effect = create_transport
        original_dispatch = adapter.dispatch

        async def record(prepared):
            async for event in original_dispatch(prepared):
                state.events.append(event)
                yield event

        adapter.dispatch = record
        admitted = time.monotonic()
        resources = _ConnectionResources(admitted_at=admitted, clock=time.monotonic,
                                         deadline=admitted + 15)
        self.addAsyncCleanup(resources.cancel_and_close)
        return adapter, state, resources

    async def test_actual_sdk_two_fixed_probes_publish_only_locally_validated_capability(self):
        trusted = profile(self)
        adapter, state, resources = self.fixture()
        result = await run_connection(resources, lambda: adapter, lambda _p: asyncio.sleep(0), lambda _p: None,
                                      trusted_profiles=(input_profile(),), connection_profile=trusted)
        self.assertEqual(len(state.sent), 2)
        for sent, captured in zip(state.sent, state.captured, strict=True):
            self.assertIs(sent, captured[0])
        self.assertEqual((result.connection_capabilities.structured_output_supported,
                          result.connection_capabilities.json_object_supported,
                          result.connection_capabilities.output_validation), (False, True, "local-json-schema"))
        self.assertFalse(any(event.kind == "text" for event in state.events))
        self.assertEqual(result.observed.input_tokens, 18)
        self.assertEqual((result.usage.model_requests, result.usage.tool_calls, result.usage.output_tokens,
                          result.usage.source_characters, result.usage.files), (2, 1, 128, 0, ()))
        self.assertEqual([json.loads(request.content)["max_tokens"] for request in state.sent], [96, 32])
        await resources.cancel_and_close()
        self.assertTrue(resources.resources_released)
        self.assertTrue(all(stream.closed for stream in state.streams))
        self.assertTrue(all(transport.closed for transport in state.transports))

    async def test_estimated_profile_alone_cannot_authorize_json_object_connection(self):
        adapter, state, resources = self.fixture()
        with self.assertRaises(SDKAdapterError):
            await run_connection(resources, lambda: adapter, lambda _p: asyncio.sleep(0), lambda _p: None,
                                 trusted_profiles=(input_profile(),))
        self.assertEqual(state.sent, [])
        self.assertEqual(state.captured, [])
        await resources.cancel_and_close()

    async def test_actual_sdk_refuses_generic_probe_allocations_before_capture(self):
        for stage in ("tools", "final"):
            with self.subTest(stage=stage):
                adapter, state, resources = self.fixture()
                with self.assertRaises(SDKAdapterError) as caught:
                    await adapter.prepare(connection_draft(stage, deadline=resources._deadline))
                self.assertEqual(caught.exception.code, "AI_INVALID_INPUT")
                self.assertEqual(state.captured, [])
                self.assertEqual(state.sent, [])
                state.factory.assert_not_called()

    async def test_connection_binding_does_not_self_register_input_trust(self):
        adapter, state, resources = self.fixture()
        with self.assertRaises(ProviderError):
            await run_connection(resources, lambda: adapter, lambda _p: asyncio.sleep(0), lambda _p: None,
                                 connection_profile=profile(self))
        self.assertEqual(state.sent, [])
        await resources.cancel_and_close()

    async def test_actual_sdk_wire_matches_only_the_exact_source_free_profile(self):
        total = 0
        trusted = profile(self)
        for stage in ("tools", "final"):
            with self.subTest(stage=stage):
                adapter, state, resources = self.fixture()
                prepared = await adapter.prepare(connection_draft(
                    stage, deadline=resources._deadline, connection_profile=trusted))
                _validate_prepared(prepared, stage, resources._deadline, connection_profile=trusted)
                with self.assertRaises(ProviderError):
                    _validate_prepared(replace(prepared, max_output_tokens=32 if stage == "tools" else 96),
                                       stage, resources._deadline, connection_profile=trusted)
                total += len(prepared.body)
                wire = json.loads(prepared.body)
                for key, value in (("model", "foreign"), ("thinking", {"type": "enabled"}),
                                   ("max_tokens", 32 if stage == "tools" else 96),
                                   ("untrusted-extra", True)):
                    changed = fixture.encoded(wire | {key: value})
                    accounting = replace(prepared.estimated_input_accounting,
                        estimated_input_tokens=len(changed), body_sha256=hashlib.sha256(changed).hexdigest())
                    with self.subTest(key=key), self.assertRaises(ProviderError):
                        _validate_prepared(replace(prepared, body=changed, estimated_input_accounting=accounting),
                                           stage, resources._deadline, connection_profile=trusted)
                with self.assertRaises(ProviderError):
                    _validate_prepared(replace(prepared, endpoint="https://other.invalid/v1/chat/completions"),
                                       stage, resources._deadline, connection_profile=trusted)
                state.factory.assert_not_called()
                await adapter.aclose()
        self.assertLessEqual(total, 1024)

    async def test_normalized_tool_framing_accepts_96_bytes_and_refuses_97(self):
        for length in (61, 62):
            with self.subTest(id_bytes=length):
                adapter, state, resources = self.fixture(tool_id="c" * length)
                payload = fixture.encoded(dict(id="c" * length, name="p", arguments={}))
                self.assertEqual(len(payload), 35 + length)
                run = run_connection(resources, lambda adapter=adapter: adapter, lambda _p: asyncio.sleep(0), lambda _p: None,
                                     trusted_profiles=(input_profile(),), connection_profile=profile(self))
                if length == 61:
                    result = await run
                    self.assertEqual(result.usage.output_tokens, 128)
                    self.assertEqual(len(state.sent), 2)
                    self.assertEqual(next(event.payload for event in state.events if event.kind == "tool"), payload)
                else:
                    with self.assertRaises(SDKAdapterError) as caught:
                        await run
                    self.assertEqual(caught.exception.code, "AI_BUDGET_EXCEEDED")
                    self.assertEqual(len(state.sent), 1)
                    self.assertFalse(any(event.kind in ("tool", "answer", "usage") for event in state.events))
                    self.assertEqual(resources.ledger.usage.output_tokens, 96)
                await resources.cancel_and_close()
                self.assertTrue(resources.resources_released)
                self.assertTrue(all(stream.closed for stream in state.streams))

    async def test_fixed_final_accepts_32_generated_bytes_and_refuses_33(self):
        for count in (32, 33):
            with self.subTest(generated_bytes=count):
                adapter, state, resources = self.fixture(answer="{}" + " " * (count - 2))
                run = run_connection(resources, lambda adapter=adapter: adapter, lambda _p: asyncio.sleep(0), lambda _p: None,
                                     trusted_profiles=(input_profile(),), connection_profile=profile(self))
                if count == 32:
                    result = await run
                    self.assertTrue(result.connection_capabilities.json_object_supported)
                else:
                    with self.assertRaises(SDKAdapterError) as caught:
                        await run
                    self.assertEqual(caught.exception.code, "AI_BUDGET_EXCEEDED")
                    self.assertFalse(any(event.kind in ("text", "answer") for event in state.events))
                self.assertEqual(resources.ledger.usage.output_tokens, 128)
                self.assertEqual(len(state.sent), 2)
                await resources.cancel_and_close()
                self.assertTrue(resources.resources_released)
                self.assertTrue(all(stream.closed for stream in state.streams))

    async def test_unknown_tools_usage_is_sticky_and_blocks_final_prepare(self):
        adapter, state, resources = self.fixture(tools_usage=False)
        with self.assertRaises(AgentError) as caught:
            await run_connection(resources, lambda: adapter, lambda _p: asyncio.sleep(0), lambda _p: None,
                                 trusted_profiles=(input_profile(),), connection_profile=profile(self))
        self.assertEqual(caught.exception.code, "budget_exceeded")
        self.assertEqual((len(state.captured), len(state.sent)), (1, 1))
        self.assertEqual(resources.input_accounting.blocked_reason, "usage_unknown")
        self.assertEqual(resources.input_accounting.known_actual_input_tokens, 0)
        self.assertEqual(resources.observed.basis, "unknown")
        self.assertEqual(resources.ledger.usage.output_tokens, 96)
        await resources.cancel_and_close()
        self.assertEqual(resources.input_accounting.blocked_reason, "usage_unknown")

    async def test_unknown_final_usage_keeps_validated_capability_and_warning(self):
        adapter, state, resources = self.fixture(final_usage=False)
        result = await run_connection(resources, lambda: adapter, lambda _p: asyncio.sleep(0), lambda _p: None,
                                      trusted_profiles=(input_profile(),), connection_profile=profile(self))
        self.assertTrue(result.connection_capabilities.json_object_supported)
        self.assertEqual(result.connection_capabilities.output_validation, "local-json-schema")
        self.assertEqual(result.observed.basis, "unknown")
        self.assertIsNone(result.observed.input_tokens)
        self.assertEqual(result.input_accounting.blocked_reason, "usage_unknown")
        self.assertEqual(result.input_accounting.known_actual_input_tokens, 7)
        self.assertEqual(result.usage.output_tokens, 128)
        self.assertEqual(len(state.sent), 2)

    async def test_actual_input_overrun_blocks_final_without_refunding_output(self):
        adapter, state, resources = self.fixture(tools_input=1025)
        with self.assertRaises(AgentError) as caught:
            await run_connection(resources, lambda: adapter, lambda _p: asyncio.sleep(0), lambda _p: None,
                                 trusted_profiles=(input_profile(),), connection_profile=profile(self))
        self.assertEqual(caught.exception.code, "budget_exceeded")
        self.assertEqual(resources.input_accounting.blocked_reason, "observed_overrun")
        self.assertEqual((len(state.captured), len(state.sent)), (1, 1))
        self.assertEqual(resources.ledger.usage.output_tokens, 96)

    async def test_nonempty_tool_arguments_cannot_reach_final_probe(self):
        adapter, state, resources = self.fixture(tool_arguments='{"extra":1}')
        with self.assertRaises(ProviderError):
            await run_connection(resources, lambda: adapter, lambda _p: asyncio.sleep(0), lambda _p: None,
                                 trusted_profiles=(input_profile(),), connection_profile=profile(self))
        self.assertEqual((len(state.captured), len(state.sent)), (1, 1))
        self.assertEqual(resources.ledger.usage.output_tokens, 96)

    async def test_profile_is_frozen_before_factory_and_rechecked_after_callbacks(self):
        for timing in ("factory", "before_dispatch", "config_guard"):
            for field, changed in (("profile_id", "foreign"), ("endpoint", "https://other.invalid/v1"),
                                   ("model", True), ("protocol", "remote-strict")):
                with self.subTest(timing=timing, field=field):
                    trusted = profile(self)
                    adapter, state, resources = self.fixture()

                    def mutate(trusted=trusted, field=field, changed=changed):
                        object.__setattr__(trusted, field, changed)

                    def create(timing=timing, mutate=mutate, adapter=adapter):
                        if timing == "factory":
                            mutate()
                        return adapter

                    async def before(_prepared, timing=timing, mutate=mutate):
                        if timing == "before_dispatch":
                            mutate()
                        await asyncio.sleep(0)

                    def guard(_prepared, timing=timing, mutate=mutate):
                        if timing == "config_guard":
                            mutate()

                    with self.assertRaises(ProviderError):
                        await run_connection(resources, create, before, guard,
                            trusted_profiles=(input_profile(),), connection_profile=trusted)
                    self.assertEqual(state.sent, [])
                    state.factory.assert_not_called()
                    if timing == "factory":
                        self.assertEqual(state.captured, [])
                    await resources.cancel_and_close()
                    self.assertTrue(resources.resources_released)

    async def test_only_fixed_deepseek_final_probe_suppresses_provisional_text(self):
        for fixed in (True, False):
            with self.subTest(fixed=fixed):
                adapter, state, resources = self.fixture()
                request = (connection_draft("final", deadline=resources._deadline, connection_profile=profile(self))
                           if fixed else ProviderDraft(
                    fixture.encoded(fixture.envelope(mode="final", answerSchema=fixture.final_schema())),
                    512, resources._deadline))
                prepared = await adapter.prepare(request)
                events = [event async for event in adapter.dispatch(prepared)]
                self.assertEqual(any(event.kind == "text" for event in events), not fixed)
                self.assertTrue(any(event.kind == "answer" for event in events))
                self.assertEqual(len(state.sent), 1)

    async def test_fixed_final_probe_still_requires_strict_local_empty_object(self):
        for answer in ("{}", "[]", '{"extra":true}', '{"extra":0,"extra":0}'):
            with self.subTest(answer=answer):
                adapter, state, resources = self.fixture(answer=answer)
                prepared = await adapter.prepare(connection_draft(
                    "final", deadline=resources._deadline, connection_profile=profile(self)))
                observe = []
                reader = _read_final(adapter.dispatch(prepared), BudgetReservation(output_tokens=32), observe.append)
                if answer == "{}":
                    await reader
                else:
                    with self.assertRaises(ProviderError):
                        await reader
                await adapter.aclose()
                self.assertEqual(len(state.sent), 1)
                self.assertTrue(all(client.is_closed for client in state.clients))

    async def test_cancel_in_before_dispatch_releases_sdk_without_transport_entry(self):
        trusted = profile(self)
        adapter, state, resources = self.fixture()
        entered = asyncio.Event()

        async def before(_prepared):
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(run_connection(resources, lambda: adapter, before, lambda _p: None,
            trusted_profiles=(input_profile(),), connection_profile=trusted))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await resources.cancel_and_close()
        self.assertEqual(state.sent, [])
        self.assertTrue(resources.resources_released)
        self.assertTrue(all(client.is_closed for client in state.clients))

    async def test_cancel_before_final_dispatch_keeps_cumulative_128_and_sends_only_tools(self):
        adapter, state, resources = self.fixture()
        entered = asyncio.Event()

        async def before(prepared):
            if not json.loads(prepared.body).get("tools"):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(run_connection(resources, lambda: adapter, before, lambda _p: None,
            trusted_profiles=(input_profile(),), connection_profile=profile(self)))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await resources.cancel_and_close()
        self.assertEqual((len(state.captured), len(state.sent)), (2, 1))
        self.assertEqual(resources.ledger.usage.output_tokens, 128)
        self.assertTrue(resources.resources_released)
        self.assertTrue(all(client.is_closed for client in state.clients))


if __name__ == "__main__":
    unittest.main()
