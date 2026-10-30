"""Offline pinned-SDK wire proof; no DeepSeek API or terminal-SSE acceptance."""

import asyncio
import hashlib
import importlib
import importlib.util
import json
import sys
import time
import unittest
from copy import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_assistant_sdk_provider as fixture

from codeviz.assistant.config import ProviderConfig, ProviderError
from codeviz.assistant.provider import MemoryKey, ProviderDraft

ADMISSION = "codeviz.assistant._deepseek_admission"


def config():
    return ProviderConfig.from_base_url("https://api.deepseek.com/v1", "deepseek-flash", 1)


def draft(value, allocation=512):
    return ProviderDraft(fixture.encoded(value), allocation, time.monotonic() + 15)


def tagged_history(mode):
    source, metadata = "保留_😀", "符号_é"
    arguments = fixture.encoded({"query": source, "meta": metadata}).decode()
    messages = [dict(role="system", content="Static " + source),
                dict(role="user", content=source + "/" + metadata + '\n"\\'),
                dict(role="assistant", content="Recall " + source, toolCalls=[dict(
                    id="synthetic-call", name="search_symbols", arguments=arguments)]),
                dict(role="tool", name="search_symbols", toolCallId="synthetic-call",
                     content=source + " | " + source + " " + metadata),
                dict(role="user", content="Explain these saved values.")]

    def tag(pointer, start, text):
        return dict(pointer=pointer, start=start, end=start + len(text), file="src/示例.py")

    sources = [tag("/messages/0/content", 7, source), tag("/messages/1/content", 0, source),
               tag("/messages/2/content", 7, source),
               tag("/messages/2/toolCalls/0/arguments", len('{"query":"'), source),
               tag("/messages/3/content", 0, source),
               tag("/messages/3/content", len(source) + 3, source)]
    meta = [tag("/messages/1/content", len(source) + 1, metadata),
            tag("/messages/2/toolCalls/0/arguments", len('{"query":"' + source + '","meta":"'), metadata),
            tag("/messages/3/content", 2 * len(source) + 4, metadata)]
    tools = [dict(name="search_symbols", description="Search locally.", parameters={
        "type": "object", "properties": {"query": {"type": "string"}, "meta": {"type": "string"}}})]
    return fixture.envelope(mode=mode, messages=messages, tools=tools if mode == "tools" else [],
                            answerSchema=fixture.final_schema() if mode == "final" else None,
                            sourceTags=sources, metadataTags=meta)


class DeepSeekFactoryTests(unittest.TestCase):
    def test_invalid_destination_or_model_refuses_before_optional_loading(self):
        self.assertIsNotNone(importlib.util.find_spec(ADMISSION), "Missing private admission module")
        admission = importlib.import_module(ADMISSION)
        loader = Mock(side_effect=AssertionError("Optional SDK must stay lazy"))
        transport = Mock(side_effect=AssertionError("No dispatch transport"))
        for base, model in (("https://other.invalid/v1", "deepseek-flash"),
                            ("https://api.deepseek.com", "deepseek-flash"),
                            ("https://api.deepseek.com/v1", "deepseek-reasoner")):
            with self.subTest(base=base, model=model), fixture.offline_guard():
                with self.assertRaises(ProviderError):
                    admission._create_deepseek_provider(
                        ProviderConfig.from_base_url(base, model, 1), MemoryKey(fixture.KEY),
                        sdk_loader=loader, transport_factory=transport)
        loader.assert_not_called()
        transport.assert_not_called()

    def test_malformed_beta_config_is_revalidated_before_loader(self):
        admission = importlib.import_module(ADMISSION)
        invalid = config()
        object.__setattr__(invalid, "base_url", "https://api.deepseek.com:443/beta")
        object.__setattr__(invalid, "endpoint", "https://api.deepseek.com:443/beta/chat/completions")
        loader, transport = Mock(), Mock()
        with fixture.offline_guard(), self.assertRaises(ProviderError):
            admission._create_deepseek_provider(invalid, MemoryKey(fixture.KEY),
                                               sdk_loader=loader, transport_factory=transport)
        loader.assert_not_called()
        transport.assert_not_called()


class DeepSeekPinnedSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if Path(sys.prefix).resolve() != fixture.SDK_ENV.resolve():
            self.skipTest("Actual SDK proof requires build/assistant-sdk-probe-20261012/Scripts/python.exe")
        self.guard = fixture.offline_guard()
        self.attempts = self.guard.__enter__()
        self.addCleanup(self.guard.__exit__, None, None, None)
        self.addCleanup(lambda: self.assertEqual(self.attempts, []))
        self.sdk = {key: importlib.import_module(name) for key, name in fixture.SDK_MODULES.items()}
        from importlib.metadata import version
        for name, pin in (("pydantic-ai-slim", "2.55.0"), ("openai", "3.29.0"),
                          ("httpx2", "2.13.1"), ("pydantic", "2.14.1")):
            self.assertEqual(version(name), pin)

    def adapter(self, *, model_class=None):
        self.assertIsNotNone(importlib.util.find_spec(ADMISSION), "Missing private admission module")
        admission = importlib.import_module(ADMISSION)
        httpx = self.sdk["httpx2"]
        state = SimpleNamespace(captured=[], sent=[], clients=[])

        class SyntheticSSE(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                for delta, finish in (({"role": "assistant", "content": "ok"}, None), ({}, "stop")):
                    value = dict(id="offline", object="chat.completion.chunk", created=0,
                                 model="deepseek-flash", choices=[dict(index=0, delta=delta,
                                                                      finish_reason=finish)])
                    if finish is not None:
                        value["usage"] = {"prompt_tokens": 9, "completion_tokens": 2}
                    yield b"data: " + fixture.encoded(value) + b"\n\n"
                yield b"data: [DONE]\n\n"

            async def aclose(self):
                self.closed = True

        state.stream = SyntheticSSE()

        class RecordingTransport(httpx.MockTransport):
            closed = False

            async def handle_async_request(self, request):
                state.sent.append(request)
                return await super().handle_async_request(request)

            async def aclose(self):
                self.closed = True
                await super().aclose()

        state.transport = RecordingTransport(lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=state.stream))
        state.factory = Mock(return_value=state.transport)
        modules = dict(self.sdk)
        http_module = SimpleNamespace(**{name: getattr(httpx, name) for name in dir(httpx)
                                         if not name.startswith("__")})

        def client_factory(**options):
            original = options["transport"].handle_async_request

            async def capture(request):
                state.captured.append((request, request.content, tuple(request.headers.raw)))
                return await original(request)

            options["transport"].handle_async_request = capture
            client = httpx.AsyncClient(**options)
            state.clients.append(client)
            return client

        http_module.AsyncClient = client_factory
        modules["httpx2"] = http_module
        if model_class is not None:
            modules["sdk"] = SimpleNamespace(OpenAIChatModel=model_class)
        adapter = admission._create_deepseek_provider(
            config(), MemoryKey(fixture.KEY), sdk_loader=lambda: modules,
            transport_factory=state.factory)
        self.addAsyncCleanup(adapter.aclose)
        return adapter, state

    async def test_nonprobe_history_allows_sixteen_but_preserves_turn_and_generic_limits(self):
        def history(count, batch_size=8):
            value = fixture.envelope(mode="final", answerSchema=fixture.final_schema())
            messages = [dict(role="user", content="Return a compact JSON answer.")]
            for start in range(0, count, batch_size):
                calls = [dict(id=f"history-{index}", name="search_symbols", arguments='{"query":"x"}')
                         for index in range(start, min(start + batch_size, count))]
                messages.append(dict(role="assistant", content=None, toolCalls=calls))
                messages.extend(dict(role="tool", toolCallId=call["id"], name=call["name"],
                                     content='{"status":"empty"}') for call in calls)
            messages.append(dict(role="user", content="Answer now."))
            value["messages"] = messages
            return value

        adapter, state = self.adapter()
        prepared = await adapter.prepare(draft(history(16)))
        wire = json.loads(prepared.body)
        self.assertEqual(sum(len(message.get("tool_calls", [])) for message in wire["messages"]), 16)
        state.factory.assert_not_called()
        await adapter.aclose()
        duplicate = history(2)
        duplicate["messages"][1]["toolCalls"][1]["id"] = "history-0"
        for invalid in (history(17), history(9, batch_size=9), duplicate):
            adapter, state = self.adapter()
            with self.assertRaises(importlib.import_module(fixture.MODULE).SDKAdapterError):
                await adapter.prepare(draft(invalid))
            state.factory.assert_not_called()
        # Generic structural parsing retains its existing cumulative-eight cap.
        with self.assertRaises(importlib.import_module(fixture.MODULE).SDKAdapterError):
            importlib.import_module(fixture.MODULE)._draft(draft(history(9)), time.monotonic())

    async def test_real_sdk_tools_and_final_capture_the_closed_protocol_without_dispatch(self):
        tools = [dict(name="search_symbols", description="Search local symbols.",
                      parameters={"type": "object", "properties": {}})]
        for mode in ("tools", "final"):
            with self.subTest(mode=mode):
                adapter, state = self.adapter()
                value = fixture.envelope(mode=mode, tools=tools if mode == "tools" else [],
                                         answerSchema=fixture.final_schema() if mode == "final" else None)
                request_draft = draft(value)
                prepared = await adapter.prepare(request_draft)
                wire = json.loads(prepared.body)
                self.assertEqual(prepared.endpoint, "https://api.deepseek.com:443/v1/chat/completions")
                self.assertEqual(wire["model"], "deepseek-flash")
                self.assertEqual(wire["max_tokens"], request_draft.max_output_tokens)
                self.assertEqual(wire["thinking"], {"type": "disabled"})
                self.assertIs(wire["stream"], True)
                self.assertNotIn("max_completion_tokens", wire)
                if mode == "final":
                    self.assertEqual(wire["response_format"], {"type": "json_object"})
                    scaffolding = wire["messages"][0]
                    self.assertEqual(scaffolding["role"], "system")
                    self.assertIn(json.dumps(fixture.final_schema()), scaffolding["content"])
                    self.assertIn('{"sections":[]}', scaffolding["content"])
                else:
                    self.assertNotIn("strict", wire["tools"][0]["function"])
                self.assertIs(prepared.body, state.captured[0][1])
                state.factory.assert_not_called()
                self.assertEqual(state.sent, [])
                await adapter.aclose()

    async def test_only_nonprobe_final_accepts_the_structural_4096_ceiling(self):
        module = importlib.import_module(fixture.MODULE)
        final = fixture.envelope(
            mode="final", tools=[], answerSchema=fixture.final_schema())
        adapter, state = self.adapter()
        prepared = await adapter.prepare(draft(final, 4_096))
        self.assertEqual(prepared.max_output_tokens, 4_096)
        self.assertEqual(json.loads(prepared.body)["max_tokens"], 4_096)
        self.assertEqual(adapter._send.resource_limits, (2 * 1024 * 1024, 8_192))
        state.factory.assert_not_called()
        await adapter.aclose()

        adapter, state = self.adapter()
        prepared = await adapter.prepare(draft(final, 4_096))
        object.__setattr__(prepared, "max_output_tokens", 2_000)
        with self.assertRaises(module.SDKAdapterError):
            await anext(adapter.dispatch(prepared))
        self.assertEqual(state.sent, [])
        state.factory.assert_not_called()

        for value, allocation in (
            (fixture.envelope(), 4_096),
            (final, 4_097),
        ):
            with self.subTest(mode=value["mode"], allocation=allocation):
                adapter, state = self.adapter()
                with self.assertRaises(module.SDKAdapterError):
                    await adapter.prepare(draft(value, allocation))
                self.assertEqual(state.sent, [])
                state.factory.assert_not_called()

        adapter, _state = self.adapter()
        await adapter.prepare(draft(final, 2_000))
        self.assertEqual(adapter._send.resource_limits, (1024 * 1024, 4_096))

    async def test_sdk_settings_cannot_override_closed_protocol(self):
        module = importlib.import_module(fixture.MODULE)
        for extra in ({"model": "deepseek-reasoner"}, {"max_tokens": 513},
                      {"thinking": {"type": "enabled"}},
                      {"response_format": {"type": "json_schema"}}):
            with self.subTest(extra=extra):
                class WrongSettings(self.sdk["sdk"].OpenAIChatModel):
                    async def _completions_create(self, messages, stream, model_settings, parameters, *, _extra=extra):
                        changed = {**model_settings, "extra_body": {
                            **model_settings.get("extra_body", {}), **_extra}}
                        return await super()._completions_create(messages, stream, changed, parameters)

                adapter, state = self.adapter(model_class=WrongSettings)
                with self.assertRaises(module.SDKAdapterError) as caught:
                    await adapter.prepare(draft(fixture.envelope()))
                self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
                self.assertIsNone(caught.exception.__context__)
                state.factory.assert_not_called()
                self.assertEqual(state.sent, [])
                self.assertTrue(all(client.is_closed for client in state.clients))

    async def test_draft_cannot_select_profile_or_override_settings(self):
        module = importlib.import_module(fixture.MODULE)
        adapter, state = self.adapter()
        value = fixture.envelope(thinking={"type": "enabled"}, profile="deepseek-flash-sdk-wire-v1")
        with self.assertRaises(module.SDKAdapterError) as caught:
            await adapter.prepare(draft(value))
        self.assertEqual(caught.exception.code, "AI_INVALID_INPUT")
        self.assertEqual(state.captured, [])
        state.factory.assert_not_called()

    async def test_final_source_and_metadata_keep_logical_offsets_after_sdk_scaffolding(self):
        source = fixture.SOURCE + "\\tail\n"
        metadata = "元数据/é.py"
        content = source + metadata
        value = fixture.envelope(
            mode="final", answerSchema=fixture.final_schema(),
            messages=[dict(role="system", content="Explain the supplied saved excerpt."),
                      dict(role="user", content=content)],
            sourceTags=[dict(pointer="/messages/1/content", start=0, end=len(source), file="src/示例.py")],
            metadataTags=[dict(pointer="/messages/1/content", start=len(source),
                               end=len(content), file="src/示例.py")])
        adapter, state = self.adapter()
        prepared = await adapter.prepare(draft(value))
        self.assertIsNone(prepared.input_token_upper_bound)
        self.assertIs(prepared.attribution_complete, True)
        record = prepared.estimated_input_accounting
        self.assertIsNotNone(record)
        self.assertEqual(record.profile_id, "deepseek-flash-sdk-wire-v1")
        self.assertEqual(record.method, "wire-bytes-v1")
        self.assertEqual(record.estimated_input_tokens, len(prepared.body))
        self.assertEqual(record.body_sha256, hashlib.sha256(prepared.body).hexdigest())
        wire_content = json.loads(prepared.body)["messages"][2]["content"]
        for occurrence, expected in ((prepared.source_occurrences[0], source),
                                     (prepared.metadata_occurrences[0], metadata)):
            self.assertEqual(occurrence.pointer, "/messages/2/content")
            self.assertEqual(wire_content[occurrence.start:occurrence.end], expected)
        state.factory.assert_not_called()

    async def test_undeclared_metadata_copy_is_rejected(self):
        module = importlib.import_module(fixture.MODULE)
        marker = "synthetic_symbol_元"
        value = fixture.envelope(
            messages=[dict(role="user", content=marker), dict(role="user", content=marker)],
            metadataTags=[dict(pointer="/messages/0/content", start=0, end=len(marker), file="sample.py")])
        adapter, state = self.adapter()
        with self.assertRaises(module.SDKAdapterError) as caught:
            await adapter.prepare(draft(value))
        self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
        state.factory.assert_not_called()

    async def test_nested_estimate_primitives_cannot_change_before_handoff(self):
        module = importlib.import_module(fixture.MODULE)
        for field, changed in (("profile_id", "foreign-profile"), ("method", "foreign-method"),
                               ("estimated_input_tokens", 1), ("body_sha256", "0" * 64)):
            with self.subTest(field=field):
                adapter, state = self.adapter()
                prepared = await adapter.prepare(draft(fixture.envelope()))
                self.assertIsNotNone(prepared.estimated_input_accounting)
                object.__setattr__(prepared.estimated_input_accounting, field, changed)
                with self.assertRaises(module.SDKAdapterError):
                    await anext(adapter.dispatch(prepared))
                state.factory.assert_not_called()
                self.assertEqual(state.sent, [])

    async def test_nested_occurrence_primitives_cannot_change_before_handoff(self):
        module = importlib.import_module(fixture.MODULE)
        for group in ("source", "metadata"):
            for field, changed in (("pointer", "/messages/9/content"), ("start", 1),
                                   ("end", 1), ("file", "other.py")):
                with self.subTest(group=group, field=field):
                    marker = "declared_片段"
                    value = fixture.envelope(
                        messages=[dict(role="user", content=marker)],
                        **{group + "Tags": [dict(pointer="/messages/0/content", start=0,
                                                 end=len(marker), file="sample.py")]})
                    adapter, state = self.adapter()
                    prepared = await adapter.prepare(draft(value))
                    occurrence = getattr(prepared, group + "_occurrences")[0]
                    object.__setattr__(occurrence, field, changed)
                    with self.assertRaises(module.SDKAdapterError):
                        await anext(adapter.dispatch(prepared))
                    state.factory.assert_not_called()
                    self.assertEqual(state.sent, [])

    async def test_equivalent_request_object_is_not_the_captured_request(self):
        module = importlib.import_module(fixture.MODULE)
        adapter, state = self.adapter()
        prepared = await adapter.prepare(draft(fixture.envelope()))
        original = state.captured[0][0]
        replacement = copy(original)
        self.assertIsNot(replacement, original)
        self.assertIs(replacement.content, prepared.body)
        adapter._send.request = replacement
        with self.assertRaises(module.SDKAdapterError):
            await anext(adapter.dispatch(prepared))
        state.factory.assert_not_called()
        self.assertEqual(state.sent, [])

    async def test_repeated_history_and_tool_arguments_keep_declared_unicode_origins(self):
        for mode in ("tools", "final"):
            with self.subTest(mode=mode):
                adapter, state = self.adapter()
                value = tagged_history(mode)
                prepared = await adapter.prepare(draft(value))
                wire = json.loads(prepared.body)
                self.assertEqual(len(prepared.source_occurrences), len(value["sourceTags"]))
                self.assertEqual(len(prepared.metadata_occurrences), len(value["metadataTags"]))
                for group in ("source", "metadata"):
                    for logical, occurrence in zip(value[group + "Tags"],
                                                   getattr(prepared, group + "_occurrences"), strict=True):
                        _, _, index, *tail = logical["pointer"].split("/")
                        index = int(index)
                        wire_index = index + int(mode == "final" and index >= 1)
                        suffix = ("content" if tail == ["content"]
                                  else "tool_calls/0/function/arguments")
                        self.assertEqual(occurrence.pointer, f"/messages/{wire_index}/{suffix}")
                        original = (value["messages"][index]["content"] if tail == ["content"]
                                    else value["messages"][index]["toolCalls"][0]["arguments"])
                        encoded = (wire["messages"][wire_index]["content"] if tail == ["content"]
                                   else wire["messages"][wire_index]["tool_calls"][0]["function"]["arguments"])
                        self.assertEqual(encoded[occurrence.start:occurrence.end],
                                         original[logical["start"]:logical["end"]])
                state.factory.assert_not_called()
                await adapter.aclose()

    async def test_sdk_copies_or_transforms_do_not_grant_an_origin(self):
        module = importlib.import_module(fixture.MODULE)
        for change in ("copy", "transform"):
            with self.subTest(change=change):
                class DriftedMessages(self.sdk["sdk"].OpenAIChatModel):
                    async def _map_messages(self, *args, _change=change, **kwargs):
                        messages = await super()._map_messages(*args, **kwargs)
                        if _change == "copy":
                            messages.append(dict(messages[-1]))
                        else:
                            messages[-1]["content"] = messages[-1]["content"].replace("é", "e\u0301")
                        return messages

                value = fixture.envelope(
                    messages=[dict(role="user", content="café_示例")],
                    sourceTags=[dict(pointer="/messages/0/content", start=0, end=7, file="sample.py")])
                adapter, state = self.adapter(model_class=DriftedMessages)
                with self.assertRaises(module.SDKAdapterError) as caught:
                    await adapter.prepare(draft(value))
                self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
                state.factory.assert_not_called()

    async def test_escaped_schema_keys_cannot_acquire_source_or_metadata_origin(self):
        module = importlib.import_module(fixture.MODULE)
        marker = 'key~/中文"'
        for group in ("source", "metadata"):
            with self.subTest(group=group):
                value = fixture.envelope(mode="tools", messages=[dict(role="user", content=marker)],
                    tools=[dict(name="search_symbols", description="Search.", parameters={
                        "type": "object", "properties": {marker: {"type": "string"}}})],
                    **{group + "Tags": [dict(pointer="/messages/0/content", start=0,
                                             end=len(marker), file="sample.py")]})
                adapter, state = self.adapter()
                with self.assertRaises(module.SDKAdapterError) as caught:
                    await adapter.prepare(draft(value))
                self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
                state.factory.assert_not_called()

    async def test_json_scaffolding_is_not_an_implicit_source_copy(self):
        module = importlib.import_module(fixture.MODULE)
        value = fixture.envelope(mode="final", answerSchema=fixture.final_schema(),
            messages=[dict(role="user", content="sections")],
            sourceTags=[dict(pointer="/messages/0/content", start=0, end=8, file="sample.py")])
        adapter, state = self.adapter()
        with self.assertRaises(module.SDKAdapterError) as caught:
            await adapter.prepare(draft(value))
        self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
        state.factory.assert_not_called()

    async def test_cross_kind_overlap_cannot_claim_two_origins(self):
        module = importlib.import_module(fixture.MODULE)
        value = fixture.envelope(messages=[dict(role="user", content="abcdef")],
            sourceTags=[dict(pointer="/messages/0/content", start=0, end=4, file="sample.py")],
            metadataTags=[dict(pointer="/messages/0/content", start=2, end=6, file="sample.py")])
        adapter, state = self.adapter()
        with self.assertRaises(module.SDKAdapterError) as caught:
            await adapter.prepare(draft(value))
        self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
        state.factory.assert_not_called()

    async def test_portable_path_validator_is_reused_for_final_attribution(self):
        module = importlib.import_module(fixture.MODULE)
        value = fixture.envelope(messages=[dict(role="user", content="synthetic")],
            metadataTags=[dict(pointer="/messages/0/content", start=0, end=9, file="NUL.py")])
        adapter, state = self.adapter()
        with self.assertRaises(module.SDKAdapterError) as caught:
            await adapter.prepare(draft(value))
        self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
        state.factory.assert_not_called()

    async def test_cancel_before_dispatch_joins_sdk_and_releases_captured_metadata(self):
        adapter, state = self.adapter()
        prepared = await adapter.prepare(draft(tagged_history("tools")))
        send = adapter._send
        task, response_future = send.sdk_task, send.response_future
        self.assertTrue(send.metadata_attribution)
        await adapter.cancel()
        self.assertTrue(task.done())
        self.assertTrue(response_future.done())
        self.assertTrue(all(client.is_closed for client in state.clients))
        self.assertEqual(send.metadata_attribution, [])
        self.assertIsNone(send.binding)
        self.assertIsNone(send.request)
        self.assertIsNone(send.prepared)
        state.factory.assert_not_called()
        self.assertEqual(state.sent, [])
        with self.assertRaises(ValueError):
            await anext(adapter.dispatch(prepared))

    async def test_factory_time_nested_mutation_is_refused_before_transport_entry(self):
        module = importlib.import_module(fixture.MODULE)
        changes = (("estimate", "body_sha256", "0" * 64),
                   ("config", "endpoint", "https://other.invalid:443/v1/chat/completions"),
                   ("config", "model", "other-model"), ("config", "revision", 2),
                   ("config", "revision", True),
                   ("profile", "endpoint", "https://other.invalid:443/v1/chat/completions"),
                   ("profile", "model", "other-model"), ("profile", "model", True),
                   ("profile", "profile_id", "other-profile"), ("profile", "method", "GET"),
                   ("config", "identity", None), ("profile", "identity", None),
                   ("profile", "selection", None))
        for timing in ("before-factory", "inside-factory"):
            for target, field, changed in changes:
                with self.subTest(timing=timing, target=target, field=field, changed=changed):
                    adapter, state = self.adapter()
                    prepared = await adapter.prepare(draft(fixture.envelope()))
                    original_request = state.captured[0][0]
                    captured_url = str(original_request.url)

                    def mutate(target=target, field=field, changed=changed, adapter=adapter,
                               prepared=prepared):
                        if target == "estimate":
                            value = prepared.estimated_input_accounting
                        else:
                            attribute = "_config" if target == "config" else "_wire_profile"
                            value = getattr(adapter, attribute)
                            if field in ("identity", "selection"):
                                setattr(adapter, attribute, copy(value) if field == "identity" else None)
                                return
                        object.__setattr__(value, field, changed)

                    def create_transport(mutate=mutate, state=state):
                        mutate()
                        return state.transport

                    if timing == "before-factory":
                        mutate()
                    else:
                        state.factory.side_effect = create_transport
                    with self.assertRaises(module.SDKAdapterError):
                        await anext(adapter.dispatch(prepared))
                    self.assertEqual(state.sent, [], "Underlying handle_async_request must not enter")
                    self.assertEqual(str(original_request.url), captured_url)
                    if timing == "before-factory":
                        state.factory.assert_not_called()
                    else:
                        state.factory.assert_called_once_with()
                        self.assertTrue(state.transport.closed)
                    await adapter.aclose()

    async def test_sdk_schema_mutation_cannot_change_the_expected_tool_contract(self):
        module = importlib.import_module(fixture.MODULE)

        class MutatedToolSchema(self.sdk["sdk"].OpenAIChatModel):
            def prepare_request(self, model_settings, parameters):
                parameters.function_tools[0].parameters_json_schema["description"] = "unexpected SDK insertion"
                return super().prepare_request(model_settings, parameters)

        adapter, state = self.adapter(model_class=MutatedToolSchema)
        value = fixture.envelope(mode="tools", tools=[dict(name="search_symbols", description="Search.",
                                parameters={"type": "object", "properties": {}})])
        with self.assertRaises(module.SDKAdapterError) as caught:
            await adapter.prepare(draft(value))
        self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
        state.factory.assert_not_called()

    async def test_raw_sdk_method_url_headers_and_body_are_frozen(self):
        module = importlib.import_module(fixture.MODULE)
        for field in ("method", "url", "headers", "body"):
            with self.subTest(field=field):
                adapter, state = self.adapter()
                prepared = await adapter.prepare(draft(fixture.envelope()))
                request = state.captured[0][0]
                if field == "method":
                    request.method = "PUT"
                elif field == "url":
                    request.url = self.sdk["httpx2"].URL("https://other.invalid/v1/chat/completions")
                elif field == "headers":
                    request.headers["x-stainless-retry-count"] = "1"
                else:
                    request._content = request.content + b" "
                with self.assertRaises(module.SDKAdapterError):
                    await anext(adapter.dispatch(prepared))
                state.factory.assert_not_called()
                self.assertEqual(state.sent, [])

    async def test_original_request_handoff_is_single_use_and_releases_all_resources(self):
        module = importlib.import_module(fixture.MODULE)
        adapter, state = self.adapter()
        prepared = await adapter.prepare(draft(tagged_history("tools")))
        original, body, headers = state.captured[0]
        order = []
        enter = state.transport.handle_async_request

        async def record_entry(request):
            order.append("entry")
            return await enter(request)

        state.transport.handle_async_request = record_entry
        self.assertEqual(state.sent, [])
        loop = asyncio.get_running_loop()
        loop.call_soon(order.append, "queued")
        iterator = adapter.dispatch(prepared)
        self.addAsyncCleanup(iterator.aclose)
        watchdog = loop.call_later(5, asyncio.current_task().cancel)
        try:
            first = await anext(iterator)
            events = [first, *[event async for event in iterator]]
        finally:
            watchdog.cancel()
        self.assertEqual(order, ["entry", "queued"])
        self.assertEqual(b"".join(event.payload for event in events if event.kind == "text"), b"ok")
        self.assertEqual(state.sent, [original])
        self.assertIs(state.sent[0], original)
        self.assertIs(original.content, body)
        self.assertEqual(tuple(original.headers.raw), headers)
        self.assertTrue(state.transport.closed)
        self.assertTrue(state.stream.closed)
        self.assertTrue(all(client.is_closed for client in state.clients))
        self.assertIsNone(adapter._send)
        with self.assertRaises(module.SDKAdapterError):
            await anext(adapter.dispatch(prepared))
        self.assertEqual(len(state.sent), 1)

    async def test_prepare_does_not_reserve_a_job_or_register_dispatch_trust(self):
        from codeviz.assistant._wire_accounting import WireAccountingError, inspect_wire

        adapter, state = self.adapter()
        with patch("codeviz.assistant.budget.BudgetLedger.reserve",
                   side_effect=AssertionError("Prepare cannot reserve a job")) as reserve:
            prepared = await adapter.prepare(draft(fixture.envelope()))
        reserve.assert_not_called()
        with self.assertRaises(WireAccountingError):
            inspect_wire(prepared)
        self.assertIsNone(prepared.input_token_upper_bound)
        state.factory.assert_not_called()

    async def test_sdk_scaffolding_counts_toward_the_existing_message_limit(self):
        module = importlib.import_module(fixture.MODULE)
        for count in (39, 40):
            with self.subTest(logical_messages=count):
                adapter, state = self.adapter()
                value = fixture.envelope(mode="final", answerSchema=fixture.final_schema(),
                    messages=[dict(role="user", content="Saved prompt.") for _ in range(count)])
                if count == 39:
                    prepared = await adapter.prepare(draft(value))
                    self.assertEqual(len(json.loads(prepared.body)["messages"]), 40)
                else:
                    with self.assertRaises(module.SDKAdapterError) as caught:
                        await adapter.prepare(draft(value))
                    self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
                state.factory.assert_not_called()
                await adapter.aclose()


if __name__ == "__main__":
    unittest.main()
