"""Pinned-SDK DeepSeek SSE proof using only socket-guarded in-memory streams."""

import asyncio
import importlib
import json
import time
import unittest

import test_assistant_sdk_provider as fixture
import test_assistant_sdk_provider_deepseek as wire_fixture

from codeviz.assistant.provider import ProviderDraft

_ABSENT = object()
_DONE = b"data: [DONE]\n\n"


def frame(delta=None, finish=None, *, usage=_ABSENT, choices=_ABSENT):
    value = dict(id="synthetic-deepseek", object="chat.completion.chunk", created=0,
                 model="deepseek-flash", choices=[dict(index=0, delta=delta or {}, finish_reason=finish)]
                 if choices is _ABSENT else choices)
    if usage is not _ABSENT:
        value["usage"] = usage
    return b"data: " + fixture.encoded(value) + b"\n\n"


def success(text="ok", *, usage=_ABSENT):
    return [frame(dict(role="assistant", content=text)), frame(finish="stop", usage=usage), _DONE]


class DeepSeekStreamTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = wire_fixture.DeepSeekPinnedSDKTests.asyncSetUp

    def adapter(self, chunks, *, barrier=None):
        adapter, state = wire_fixture.DeepSeekPinnedSDKTests.adapter(self)
        httpx = self.sdk["httpx2"]
        state.blocked = asyncio.Event()
        state.reads = 0

        class SyntheticSSE(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                for chunk in chunks:
                    state.reads += 1
                    yield chunk
                if barrier is not None:
                    state.blocked.set()
                    await barrier.wait()

            async def aclose(self):
                self.closed = True

        state.stream = SyntheticSSE()
        return adapter, state

    async def run_stream(self, chunks, *, value=None, allocation=512, code=None, diagnostic=None,
                         reason=None, close_failure=False, absent_reason=False):
        module = importlib.import_module(fixture.MODULE)
        adapter, state = self.adapter(chunks)
        if close_failure:
            original_close = state.stream.aclose
            close_calls = 0

            async def fail_once():
                nonlocal close_calls
                close_calls += 1
                if close_calls == 1:
                    raise RuntimeError("SYNTHETIC-CLOSE-SENTINEL")
                await original_close()

            state.stream.aclose = fail_once
        value = fixture.envelope() if value is None else value
        prepared = await adapter.prepare(ProviderDraft(fixture.encoded(value), allocation,
                                                       time.monotonic() + 15))
        self.assertEqual(state.sent, [])
        state.factory.assert_not_called()
        events = []

        async def consume():
            async for event in adapter.dispatch(prepared):
                events.append(event)

        if code is None:
            await asyncio.wait_for(consume(), 5)
        else:
            with self.assertRaises(module.SDKAdapterError) as caught:
                await asyncio.wait_for(consume(), 5)
            self.assertEqual(caught.exception.code, code)
            if reason is not None:
                self.assertEqual(caught.exception._delta_rejection.reason, reason)
            if absent_reason:
                self.assertIsNone(caught.exception._delta_rejection)
            if diagnostic is not None:
                self.assertIsNotNone(caught.exception.diagnostic)
                self.assertEqual(caught.exception.diagnostic.to_public(), diagnostic)
            self.assertIsNone(caught.exception.__context__)
            self.assertFalse(any(event.kind in ("tool", "answer", "usage") for event in events))
        self.assertEqual(len(state.sent), 1)
        self.assertIs(state.sent[0], state.captured[0][0])
        self.assertTrue(state.stream.closed)
        self.assertTrue(state.transport.closed)
        self.assertTrue(all(client.is_closed for client in state.clients))
        self.assertIsNone(adapter._send)
        return events

    async def test_private_first_delta_reason_survives_actual_sdk_and_cleanup(self):
        usage = dict(prompt_tokens=9, completion_tokens=2)
        for delta, finish, reason in (
            ({"content": False}, None, "content_type"),
            ({"tool_calls": [{"index": 0, "id": "c", "type": "function",
                              "function": {"name": "search_symbols", "arguments": "{"}}]},
             "tool_calls", "arguments_json"),
        ):
            chunks = [frame(delta), frame(finish=finish, usage=usage), _DONE]
            value = None
            if finish == "tool_calls":
                value = fixture.envelope(mode="tools", tools=[{
                    "name": "search_symbols", "description": "Search symbols.",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                                   "required": ["query"], "additionalProperties": False},
                }])
            await self.run_stream(chunks, code="AI_PROTOCOL_INCOMPATIBLE", reason=reason,
                                  value=value,
                                  diagnostic={"boundary": "dispatch", "checkSite": "delta_shape"})
        await self.run_stream([frame({"content": False}), _DONE],
            code="AI_PROTOCOL_INCOMPATIBLE", reason="content_type", close_failure=True,
            diagnostic={"boundary": "dispatch", "checkSite": "cleanup"})
        await self.run_stream(success(usage=usage), code="AI_PROTOCOL_INCOMPATIBLE",
            close_failure=True, absent_reason=True,
            diagnostic={"boundary": "dispatch", "checkSite": "cleanup"})

    async def test_deadline_preserves_an_earlier_private_reason_without_changing_timeout(self):
        module = importlib.import_module(fixture.MODULE)
        adapter, state = self.adapter([frame({"content": "ok"}), frame({"content": False}), _DONE])
        self.addAsyncCleanup(adapter.aclose)
        prepared = await adapter.prepare(ProviderDraft(fixture.encoded(fixture.envelope()), 512,
                                                       time.monotonic() + 15))
        original_remaining = adapter._remaining
        observed = []

        def timeout_after_guard(send):
            if send.delta_rejection is not None:
                observed.append(send.delta_rejection.reason)
                raise TimeoutError()
            return original_remaining(send)

        adapter._remaining = timeout_after_guard
        with self.assertRaises(module.SDKAdapterError) as caught:
            async for _event in adapter.dispatch(prepared):
                pass
        self.assertTrue(observed)
        self.assertEqual(caught.exception.code, "AI_NETWORK_TIMEOUT")
        self.assertIsNone(caught.exception.diagnostic)
        self.assertIsNotNone(caught.exception._delta_rejection)
        self.assertEqual(caught.exception._delta_rejection.reason, "content_type")
        self.assertTrue(state.stream.closed)
        self.assertTrue(state.transport.closed)
        self.assertTrue(all(client.is_closed for client in state.clients))
        self.assertIsNone(adapter._send)

        adapter, state = self.adapter(success(usage=dict(prompt_tokens=9, completion_tokens=2)))
        self.addAsyncCleanup(adapter.aclose)
        prepared = await adapter.prepare(ProviderDraft(fixture.encoded(fixture.envelope()), 512,
                                                       time.monotonic() + 15))
        original_remaining = adapter._remaining

        def pure_timeout(send):
            if send.transport_entered and send.stream is not None:
                raise TimeoutError()
            return original_remaining(send)

        adapter._remaining = pure_timeout
        with self.assertRaises(module.SDKAdapterError) as caught:
            async for _event in adapter.dispatch(prepared):
                pass
        self.assertEqual(caught.exception.code, "AI_NETWORK_TIMEOUT")
        self.assertIsNone(caught.exception.diagnostic)
        self.assertIsNone(caught.exception._delta_rejection)
        self.assertTrue(state.stream.closed)
        self.assertTrue(state.transport.closed)
        self.assertTrue(all(client.is_closed for client in state.clients))

    async def test_unsuccessful_finish_has_closed_reason_without_completion_or_private_value(self):
        for finish in ("length", "content_filter", "insufficient_system_resource", "aborted",
                       "private-provider-value"):
            diagnostic = {"boundary": "dispatch", "checkSite": "terminal_finish"}
            if finish != "private-provider-value":
                diagnostic["finishReason"] = finish
            await self.run_stream(
                [frame(dict(content="incomplete")), frame(finish=finish, usage=dict(
                    prompt_tokens=9, completion_tokens=2)), _DONE],
                code="AI_PROTOCOL_INCOMPATIBLE", diagnostic=diagnostic)

    async def test_nonterminal_usage_cannot_yield_successful_completion(self):
        chunks = [frame(dict(role="assistant", content="ok"),
                        usage=dict(prompt_tokens=9, completion_tokens=2)),
                  frame(finish="stop"), _DONE]
        await self.run_stream(chunks, code="AI_PROTOCOL_INCOMPATIBLE")

    async def test_wire_rejection_sites_preserve_classification_through_actual_sdk(self):
        usage = dict(prompt_tokens=9, completion_tokens=2)
        cases = (
            ("sse_syntax", [b'data: {"private":"sentinel",\n\n']),
            ("chunk_shape", [frame(choices=[]), _DONE]),
            ("usage_shape", [frame(dict(content="ok")), frame(finish="stop", usage={}), _DONE]),
            ("delta_shape", [frame(dict(content=False)), _DONE]),
            ("stream_eof", [frame(dict(content="ok")), frame(finish="stop", usage=usage)]),
        )
        for site, chunks in cases:
            with self.subTest(site=site):
                await self.run_stream(chunks, code="AI_PROTOCOL_INCOMPATIBLE",
                                      diagnostic={"boundary": "dispatch", "checkSite": site})

    async def test_terminal_usage_reaches_actual_sdk_without_counting_cache_twice(self):
        usage = dict(prompt_tokens=19, completion_tokens=2, total_tokens=21,
                     prompt_cache_hit_tokens=13, prompt_cache_miss_tokens=6,
                     prompt_tokens_details={"cached_tokens": 13})
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                chunks = [frame(dict(role="assistant", content="中文"), usage=None),
                          frame(dict(content="😀"), usage=None), frame(finish="stop", usage=usage), _DONE]
                raw = b"".join(chunks).replace(b"\n", newline)
                events = await self.run_stream([raw[index:index + 1] for index in range(len(raw))])
                self.assertEqual(b"".join(event.payload for event in events if event.kind == "text"),
                                 "中文😀".encode())
                self.assertEqual(json.loads(events[-1].payload), dict(
                    inputTokens=19, outputTokens=2, basis="actual"))

    async def test_final_answer_requires_terminal_actual_usage(self):
        value = fixture.envelope(mode="final", answerSchema=fixture.final_schema())
        for usage in (_ABSENT, None):
            with self.subTest(usage=usage):
                await self.run_stream(success('{"sections":[]}', usage=usage), value=value,
                                      code="AI_PROTOCOL_INCOMPATIBLE")
        events = await self.run_stream(success('{"sections":[]}', usage=dict(
            prompt_tokens=0, completion_tokens=0)), value=value)
        self.assertTrue(any(event.kind == "answer" for event in events))
        self.assertEqual(json.loads(events[-1].payload), dict(
            inputTokens=0, outputTokens=0, basis="actual"))

    async def test_tool_calls_require_terminal_actual_usage(self):
        value = fixture.envelope(mode="tools", tools=[dict(name="search_symbols", description="Search.",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}})])
        for usage in (dict(prompt_tokens=9, completion_tokens=20), None):
            with self.subTest(usage=usage):
                chunks = [frame(dict(tool_calls=[dict(index=0, id="c", type="function",
                    function=dict(name="search_symbols", arguments='{"query":'))])),
                    frame(dict(tool_calls=[dict(index=0, function=dict(arguments='"x"}'))])),
                    frame(finish="tool_calls", usage=usage), _DONE]
                events = await self.run_stream(
                    chunks, value=value,
                    code=None if usage is not None else "AI_PROTOCOL_INCOMPATIBLE")
                if usage is None:
                    continue
                tools = [json.loads(event.payload) for event in events if event.kind == "tool"]
                self.assertEqual(tools, [dict(id="c", name="search_symbols", arguments={"query": "x"})])
                self.assertEqual(json.loads(events[-1].payload), dict(
                    inputTokens=9, outputTokens=20, basis="actual"))

    async def test_empty_choice_and_post_terminal_json_never_complete(self):
        usage = dict(prompt_tokens=9, completion_tokens=2)
        cases = {
            "empty-choices": [frame(dict(content="ok")), frame(finish="stop"),
                              frame(choices=[], usage=usage), _DONE],
            "duplicate-usage": [frame(dict(content="ok")), frame(finish="stop", usage=usage),
                                frame(finish="stop", usage=usage), _DONE],
            "extra-json": [*success(usage=usage)[:-1], frame(dict(content="more")), _DONE],
            "index-one": [frame(choices=[dict(index=1, delta={}, finish_reason="stop")]), _DONE],
            "two-choices": [frame(choices=[dict(index=i, delta={}, finish_reason="stop")
                                           for i in range(2)]), _DONE],
            "length": [frame(dict(content="ok")), frame(finish="length", usage=usage), _DONE],
        }
        for name, chunks in cases.items():
            with self.subTest(sequence=name):
                await self.run_stream(chunks, code="AI_PROTOCOL_INCOMPATIBLE")

    async def test_only_comments_and_one_done_follow_the_terminal_choice(self):
        terminal = success(usage=dict(prompt_tokens=9, completion_tokens=2))[:-1]
        events = await self.run_stream([*terminal, b": keepalive\n\n", _DONE, b": closed\n\n"])
        self.assertEqual(json.loads(events[-1].payload)["basis"], "actual")
        for trailing in (_DONE, frame(dict(content="extra")), b"data:\n\n"):
            with self.subTest(trailing=trailing):
                await self.run_stream([*terminal, _DONE, trailing], code="AI_PROTOCOL_INCOMPATIBLE")

    async def test_incomplete_terminal_sequences_never_publish_completion(self):
        for chunks in (success()[:-1], [*success()[:-1], b"data: [DO"],
                       [frame(dict(content="ok")), _DONE], [*success(), b": unfinished"]):
            with self.subTest(chunks=chunks):
                await self.run_stream(chunks, code="AI_PROTOCOL_INCOMPATIBLE")

    async def test_disabled_thinking_accepts_only_empty_or_null_reasoning_content(self):
        for reasoning in (None, ""):
            with self.subTest(reasoning=reasoning):
                chunks = [frame(dict(content="ok", reasoning_content=reasoning)),
                          frame(finish="stop", usage=dict(prompt_tokens=9, completion_tokens=2)), _DONE]
                events = await self.run_stream(chunks)
                self.assertEqual(json.loads(events[-1].payload)["basis"], "actual")
        for delta in (dict(content="ok", reasoning_content="private reasoning"),
                      dict(content="ok", reasoning_content=False),
                      dict(content="ok", reasoning="private reasoning")):
            with self.subTest(delta=delta):
                await self.run_stream([frame(delta), frame(finish="stop"), _DONE],
                                      code="AI_PROTOCOL_INCOMPATIBLE")

    async def test_malformed_usage_is_not_coerced_or_replaced_by_an_estimate(self):
        for changes in (dict(prompt_tokens=True), dict(completion_tokens=-1),
                        dict(prompt_tokens=2**63), dict(completion_tokens=513),
                        dict(total_tokens=-1), dict(prompt_cache_hit_tokens=True),
                        dict(completion_tokens_details={"reasoning_tokens": 1})):
            with self.subTest(changes=changes):
                usage = dict(prompt_tokens=9, completion_tokens=2) | changes
                await self.run_stream(success(usage=usage), code="AI_PROTOCOL_INCOMPATIBLE")

    async def test_deepseek_usage_counts_use_safe_integers_without_expanding_output_allocation(self):
        maximum = 2**53 - 1
        usage = dict(prompt_tokens=maximum, completion_tokens=512, total_tokens=maximum,
                     prompt_cache_hit_tokens=maximum, prompt_cache_miss_tokens=maximum,
                     prompt_tokens_details={"cached_tokens": maximum},
                     completion_tokens_details={"accepted_prediction_tokens": maximum})
        events = await self.run_stream(success(usage=usage))
        self.assertEqual(json.loads(events[-1].payload), dict(
            inputTokens=maximum, outputTokens=512, basis="actual"))
        changes = (
            dict(prompt_tokens=2**53), dict(completion_tokens=2**53),
            dict(total_tokens=2**53), dict(prompt_cache_hit_tokens=2**53),
            dict(prompt_cache_miss_tokens=2**53),
            dict(prompt_tokens_details={"cached_tokens": 2**53}),
            dict(completion_tokens_details={"accepted_prediction_tokens": 2**53}),
            dict(completion_tokens=513),
        )
        for change in changes:
            with self.subTest(change=change):
                await self.run_stream(success(usage=usage | change), code="AI_PROTOCOL_INCOMPATIBLE")

    async def test_bare_empty_frames_are_refused_at_every_deepseek_stream_phase(self):
        chunks = success(usage=dict(prompt_tokens=9, completion_tokens=2))
        for newline in (b"\n", b"\r\n"):
            comment = b": keepalive" + newline * 2
            events = await self.run_stream([comment, *chunks[:-1], comment, chunks[-1], comment])
            self.assertEqual(json.loads(events[-1].payload)["basis"], "actual")
            for position in range(len(chunks) + 1):
                with self.subTest(newline=newline, position=position):
                    await self.run_stream([*chunks[:position], newline * 2, *chunks[position:]],
                                          code="AI_PROTOCOL_INCOMPATIBLE")

    async def test_deepseek_uses_actual_tokens_while_resource_bytes_keep_independent_caps(self):
        text = "中" * 200
        events = await self.run_stream(success(text, usage=dict(
            prompt_tokens=9, completion_tokens=200)), allocation=512)
        self.assertEqual(b"".join(event.payload for event in events if event.kind == "text"),
                         text.encode())
        events = await self.run_stream(success("x" * 65536, usage=dict(
            prompt_tokens=9, completion_tokens=512)), allocation=512)
        self.assertEqual(sum(len(event.payload) for event in events if event.kind == "text"), 65536)
        await self.run_stream(success("x" * 65537, usage=dict(
            prompt_tokens=9, completion_tokens=512)), allocation=512,
            code="AI_PROTOCOL_INCOMPATIBLE")
        comments = b":" + b"x" * 262144 + b"\n\n"
        await self.run_stream([comments, *success(usage=dict(
            prompt_tokens=9, completion_tokens=2))], code="AI_BUDGET_EXCEEDED")

    async def test_many_frame_final_accepts_normal_metadata_and_coalesced_transport(self):
        # This wire-equivalence case uses the production loop mode. Per-handle
        # debug traceback/stat work can exhaust its unchanged five-second bound.
        loop = asyncio.get_running_loop()
        original_debug = loop.get_debug()
        loop.set_debug(False)
        try:
            await self._many_frame_final_transports()
        finally:
            loop.set_debug(original_debug)

    async def _many_frame_final_transports(self):
        kinds = ("summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")
        sections = [dict(kind=kind, claims=[]) for kind in kinds]
        sections[0]["claims"] = [dict(text="中" * 1800, basis="unknown", evidenceIds=[],
                                      relationEvidenceId=None)]
        answer = fixture.encoded(dict(sections=sections)).decode()
        def enriched(delta=None, finish=None, *, usage=_ABSENT):
            value = json.loads(frame(delta, finish, usage=usage)[6:])
            value.update(id="chatcmpl-" + "a" * 48, created=1_790_000_000,
                         system_fingerprint="fp_" + "b" * 48)
            return b"data: " + fixture.encoded(value) + b"\n\n"

        chunks = []
        for index, char in enumerate(answer):
            chunks.append(enriched(dict(content=char, **({"role": "assistant"} if index == 0 else {})), usage=None))
        chunks.extend((enriched(finish="stop", usage=dict(prompt_tokens=100, completion_tokens=1800)), _DONE))
        raw = b"".join(chunks)
        self.assertGreater(len(raw), 512 * 1024)
        self.assertLess(len(raw), 1024 * 1024)
        self.assertLess(len(chunks), 4096)
        final = fixture.envelope(mode="final", answerSchema=fixture.final_schema())
        for transport in (chunks, [raw]):
            with self.subTest(coalesced=len(transport) == 1):
                events = await self.run_stream(transport, value=final, allocation=2000)
                actual = "".join(json.loads(event.payload)["content"] for event in events if event.kind == "answer")
                self.assertEqual(actual, answer)
                self.assertEqual(json.loads(events[-1].payload), dict(inputTokens=100, outputTokens=1800, basis="actual"))
        missing = [*chunks[:-2], enriched(finish="stop"), _DONE]
        duplicate = [*chunks[:-1], enriched(finish="stop", usage=dict(prompt_tokens=100, completion_tokens=1800)), _DONE]
        for invalid in (missing, duplicate):
            await self.run_stream(invalid, value=final, allocation=2000, code="AI_PROTOCOL_INCOMPATIBLE")

    async def test_deepseek_raw_total_exact_and_one_over_keep_individual_frames_bounded(self):
        trailer = success(usage=dict(prompt_tokens=9, completion_tokens=2))
        for extra in (0, 1):
            padding = 1024 * 1024 + extra - sum(map(len, trailer))
            sizes = [padding // 4 + (index < padding % 4) for index in range(4)]
            comments = [b":" + b"x" * (size - 3) + b"\n\n" for size in sizes]
            self.assertTrue(all(len(chunk) - 2 <= 262144 for chunk in comments))
            await self.run_stream([*comments, *trailer], code=None if extra == 0 else "AI_BUDGET_EXCEEDED")

    async def test_deepseek_logical_frame_count_includes_comments_and_done(self):
        trailer = success(usage=dict(prompt_tokens=9, completion_tokens=2))
        for extra in (0, 1):
            chunks = [b": keepalive\n\n"] * (4096 + extra - len(trailer)) + trailer
            for transport in (chunks, [b"".join(chunks)]):
                with self.subTest(extra=extra, coalesced=len(transport) == 1):
                    await self.run_stream(transport, code=None if extra == 0 else "AI_BUDGET_EXCEEDED")

    async def test_4096_final_has_frozen_two_mib_and_8192_frame_bounds(self):
        loop = asyncio.get_running_loop()
        original_debug = loop.get_debug()
        loop.set_debug(False)
        try:
            final = fixture.envelope(
                mode="final", tools=[], answerSchema=fixture.final_schema())
            trailer = success('{"sections":[]}', usage=dict(
                prompt_tokens=100, completion_tokens=3_500))
            many_frames = [b":" + b"x" * 237 + b"\n\n"] * 5_000 + trailer
            self.assertGreater(len(b"".join(many_frames)), 1024 * 1024)
            self.assertGreater(len(many_frames), 4_096)
            for transport in (many_frames, [b"".join(many_frames)]):
                await self.run_stream(
                    transport, value=final, allocation=4_096)
            for extra in (0, 1):
                padding = 2 * 1024 * 1024 + extra - sum(map(len, trailer))
                sizes = [padding // 8 + (index < padding % 8) for index in range(8)]
                comments = [b":" + b"x" * (size - 3) + b"\n\n" for size in sizes]
                self.assertTrue(all(len(chunk) - 2 <= 262144 for chunk in comments))
                await self.run_stream(
                    [*comments, *trailer], value=final, allocation=4_096,
                    code=None if extra == 0 else "AI_BUDGET_EXCEEDED")

            for extra in (0, 1):
                chunks = [b": keepalive\n\n"] * (8_192 + extra - len(trailer)) + trailer
                for transport in (chunks, [b"".join(chunks)]):
                    with self.subTest(extra=extra, coalesced=len(transport) == 1):
                        await self.run_stream(
                            transport, value=final, allocation=4_096,
                            code=None if extra == 0 else "AI_BUDGET_EXCEEDED")
        finally:
            loop.set_debug(original_debug)

    async def test_deepseek_single_frame_and_unterminated_tail_remain_bounded(self):
        trailer = success(usage=dict(prompt_tokens=9, completion_tokens=2))
        at_limit = b":" + b"x" * (262144 - 1)
        await self.run_stream([at_limit, b"\n\n", *trailer])
        for delimiter in (b"\n\n", b"\r\n\r\n"):
            with self.subTest(split_delimiter=delimiter):
                await self.run_stream([at_limit, *[bytes([byte]) for byte in delimiter], *trailer])
        await self.run_stream([at_limit, b"\r\n\r", b"x"], code="AI_BUDGET_EXCEEDED")
        await self.run_stream([at_limit + b"x\n\n", *trailer], code="AI_BUDGET_EXCEEDED")
        await self.run_stream([at_limit + b"x"], code="AI_BUDGET_EXCEEDED")

    async def test_tool_fragment_limits_do_not_expand_for_the_profile(self):
        value = fixture.envelope(mode="tools", tools=[dict(name="search_symbols", description="Search.",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}})])
        cases = ((512, '{"query":"x"}', None),
                 (513, '{"query":"x"}', "AI_PROTOCOL_INCOMPATIBLE"),
                 (1, '{"query":"' + "中" * 1000 + '"}', None),
                 (1, '{"query":"' + "x" * 8192 + '"}', "AI_PROTOCOL_INCOMPATIBLE"))
        for identifier_size, arguments, code in cases:
            with self.subTest(identifier_size=identifier_size, argument_size=len(arguments)):
                chunks = [frame(dict(tool_calls=[dict(index=0, id="c" * identifier_size, type="function",
                    function=dict(name="search_symbols", arguments=arguments))])),
                    frame(finish="tool_calls", usage=dict(prompt_tokens=9, completion_tokens=200)), _DONE]
                events = await self.run_stream(chunks, value=value, allocation=512, code=code)
                if code is None:
                    self.assertEqual(len(json.loads(next(event.payload for event in events
                                                        if event.kind == "tool"))["id"]),
                                     identifier_size)

    async def test_cancellation_before_eof_joins_sdk_and_releases_owned_resources(self):
        barrier = asyncio.Event()
        adapter, state = self.adapter(success(usage=dict(prompt_tokens=9, completion_tokens=2)),
                                      barrier=barrier)
        prepared = await adapter.prepare(ProviderDraft(fixture.encoded(fixture.envelope()), 512,
                                                       time.monotonic() + 15))
        sdk_task = adapter._send.sdk_task
        events = []

        async def consume():
            async for event in adapter.dispatch(prepared):
                events.append(event)

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(state.blocked.wait(), 5)
            consumer.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await consumer
        finally:
            if not consumer.done():
                consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        self.assertFalse(any(event.kind in ("tool", "answer", "usage") for event in events))
        self.assertTrue(sdk_task.done())
        self.assertTrue(state.stream.closed)
        self.assertTrue(state.transport.closed)
        self.assertTrue(all(client.is_closed for client in state.clients))
        self.assertIsNone(adapter._send)
        self.assertEqual(len(state.sent), 1)


if __name__ == "__main__":
    unittest.main()
