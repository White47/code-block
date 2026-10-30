"""Pure bounded connection runner; all providers are in-memory fakes."""

import asyncio
import json
import unittest
from collections.abc import AsyncIterator
from dataclasses import replace

from codeviz.assistant.budget import BudgetReservation
from codeviz.assistant.connection import (
    ConnectionProbeResult,
    _ConnectionResources,
    run_connection,
)
from codeviz.assistant.connection_profile import connection_schema
from codeviz.assistant.issues import ProtocolDiagnostic
from codeviz.assistant.poll_public import ObservedUsage
from codeviz.assistant.provider import (
    PreparedRequest,
    ProviderAdapter,
    ProviderDraft,
    ProviderError,
    ProviderEvent,
    WireOccurrence,
)


def _body(stage: str) -> bytes:
    body: dict[str, object] = {
        "model": "probe-model",
        "messages": [{"role": "user", "content": "Call p." if stage == "tools" else "Return {}."}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_completion_tokens": 32 if stage == "tools" else 96,
    }
    if stage == "tools":
        body.update({
            "tools": [{"type": "function", "function": {
                "name": "p", "description": "", "parameters": connection_schema(),
                "strict": True,
            }}],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        })
    else:
        body.update({
            "tool_choice": "none",
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "p", "strict": True, "schema": connection_schema(),
            }},
        })
    return json.dumps(body, separators=(",", ":"), allow_nan=False).encode()


def _usage(input_tokens: int | None, output_tokens: int | None, basis: str = "actual") -> ProviderEvent:
    return ProviderEvent("usage", json.dumps({
        "inputTokens": input_tokens, "outputTokens": output_tokens, "basis": basis,
    }, separators=(",", ":")).encode())


class FakeProvider(ProviderAdapter):
    def __init__(self, events: dict[str, tuple[ProviderEvent, ...]], log: list[str]) -> None:
        self.events = events
        self.log = log
        self.prepared: list[PreparedRequest] = []
        self.cancel_calls = 0
        self.close_calls = 0
        self.fail_close = False

    async def prepare(self, draft: ProviderDraft) -> PreparedRequest:
        logical = json.loads(draft.body)
        stage = logical["mode"]
        self.log.append(f"prepare:{stage}")
        body = _body(stage)
        prepared = PreparedRequest(
            f"prepared-{stage}", "POST", "https://provider.invalid:443/v1/chat/completions",
            body, draft.max_output_tokens, draft.deadline, len(body), (), (), True,
        )
        self.prepared.append(prepared)
        return prepared

    def dispatch(self, prepared: PreparedRequest) -> AsyncIterator[ProviderEvent]:
        stage = "tools" if prepared.max_output_tokens == 32 else "final"
        self.log.append(f"dispatch:{stage}")

        async def iterate() -> AsyncIterator[ProviderEvent]:
            for event in self.events[stage]:
                yield event

        return iterate()

    async def cancel(self) -> None:
        self.cancel_calls += 1

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise RuntimeError("fake close failure")


def _valid_events(*, unknown_final: bool = False) -> dict[str, tuple[ProviderEvent, ...]]:
    tools = (
        ProviderEvent("tool", b'{"id":"call-1","name":"p","arguments":{}}'),
        _usage(7, 3),
    )
    final_usage = _usage(None, None, "unknown") if unknown_final else _usage(11, 2)
    final = (ProviderEvent("answer", b'{"index":0,"last":true,"content":"{}"}'), final_usage)
    return {"tools": tools, "final": final}


class ConnectionRunnerTests(unittest.IsolatedAsyncioTestCase):
    def resources(self) -> _ConnectionResources:
        return _ConnectionResources(admitted_at=10.0, clock=lambda: 10.0, deadline=25.0)

    async def test_two_fixed_sends_are_guarded_reserved_and_aggregated(self) -> None:
        log: list[str] = []
        provider = FakeProvider(_valid_events(), log)
        resources = self.resources()

        async def before_dispatch(prepared: PreparedRequest) -> None:
            stage = "tools" if prepared.max_output_tokens == 32 else "final"
            log.append(f"before:{stage}")
            await asyncio.sleep(0)

        def guard(prepared: PreparedRequest) -> None:
            stage = "tools" if prepared.max_output_tokens == 32 else "final"
            log.append(f"guard:{stage}")

        result = await run_connection(resources, lambda: provider, before_dispatch, guard)

        self.assertIsInstance(result, ConnectionProbeResult)
        self.assertEqual(result.usage, resources.ledger.usage)
        self.assertEqual((result.usage.model_requests, result.usage.tool_calls,
                          result.usage.output_tokens, result.usage.source_characters,
                          result.usage.files), (2, 1, 128, 0, ()))
        self.assertEqual(result.usage.input_tokens, sum(len(item.body) for item in provider.prepared))
        self.assertEqual(result.observed, ObservedUsage(18, 5, "actual"))
        self.assertEqual(log, [
            "prepare:tools", "guard:tools", "before:tools", "guard:tools", "dispatch:tools",
            "prepare:final", "guard:final", "before:final", "guard:final", "dispatch:final",
        ])
        self.assertIs(resources.provider, provider)
        self.assertIsNone(resources.prepared)
        self.assertFalse(resources.resources_released)

    async def test_unknown_stage_usage_makes_aggregate_unknown(self) -> None:
        provider = FakeProvider(_valid_events(unknown_final=True), [])
        result = await run_connection(self.resources(), lambda: provider,
                                      lambda _prepared: asyncio.sleep(0), lambda _prepared: None)
        self.assertEqual(result.observed, ObservedUsage())

    async def test_preparation_and_callback_mutation_fail_closed_without_dispatch(self) -> None:
        mutations = {
            "missing-input-bound": {"input_token_upper_bound": None},
            "short-input-bound": {"input_token_upper_bound": 1},
            "unattributed": {"attribution_complete": False},
            "source": {"source_occurrences": (WireOccurrence("/messages/0/content", 0, 1, "a.py"),)},
            "metadata": {"metadata_occurrences": (WireOccurrence("/messages/0/content", 0, 1, "a.py"),)},
            "wrong-limit": {"max_output_tokens": 31},
            "source-bearing-body": {"body": _body("tools").replace(b"Call p.", b"secret!")},
        }
        for name, changes in mutations.items():
            with self.subTest(name=name):
                log: list[str] = []
                provider = FakeProvider(_valid_events(), log)
                original_prepare = provider.prepare

                async def prepare(draft: ProviderDraft, *, _changes=changes,
                                  _prepare=original_prepare) -> PreparedRequest:
                    return replace(await _prepare(draft), **_changes)

                provider.prepare = prepare  # type: ignore[method-assign]
                with self.assertRaises(ProviderError) as caught:
                    await run_connection(self.resources(), lambda _provider=provider: _provider,
                                         lambda _prepared: asyncio.sleep(0), lambda _prepared: None)
                self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
                self.assertFalse(any(item.startswith("dispatch:") for item in log))

        provider = FakeProvider(_valid_events(), [])

        async def mutate(prepared: PreparedRequest) -> None:
            object.__setattr__(prepared, "body", prepared.body + b" ")

        resources = self.resources()
        with self.assertRaises(ProviderError):
            await run_connection(resources, lambda: provider, mutate, lambda _prepared: None)
        self.assertEqual(resources.ledger.usage.model_requests, 1)
        self.assertEqual(resources.ledger.usage.output_tokens, 32)

    async def test_local_prepared_proof_failure_is_pre_send(self) -> None:
        provider = FakeProvider(_valid_events(), [])
        original_prepare = provider.prepare

        async def prepare(draft: ProviderDraft) -> PreparedRequest:
            return replace(await original_prepare(draft), attribution_complete=False)

        provider.prepare = prepare  # type: ignore[method-assign]
        with self.assertRaises(ProviderError) as caught:
            await run_connection(self.resources(), lambda: provider,
                                 lambda _prepared: asyncio.sleep(0), lambda _prepared: None)
        self.assertEqual(caught.exception.diagnostic, ProtocolDiagnostic("pre_send"))

    async def test_unknown_provider_factory_failure_is_pre_send(self) -> None:
        def fail_factory():
            raise RuntimeError("untrusted factory detail")

        with self.assertRaises(ProviderError) as caught:
            await run_connection(self.resources(), fail_factory,
                                 lambda _prepared: asyncio.sleep(0), lambda _prepared: None)
        self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
        self.assertEqual(caught.exception.diagnostic, ProtocolDiagnostic("pre_send"))

    async def test_completed_stream_content_failure_is_result_validation(self) -> None:
        events = {
            "tools": (ProviderEvent("tool", b'{"id":"x","name":"p","arguments":{}}'),),
            "final": _valid_events()["final"],
        }
        with self.assertRaises(ProviderError) as caught:
            await run_connection(self.resources(), lambda: FakeProvider(events, []),
                                 lambda _prepared: asyncio.sleep(0), lambda _prepared: None)
        self.assertEqual(
            caught.exception.diagnostic,
            ProtocolDiagnostic("result_validation"),
        )

    async def test_final_usage_order_errors_are_result_validation(self) -> None:
        answer = ProviderEvent("answer", b'{"index":0,"last":true,"content":"{}"}')
        cases = {
            "early": (_usage(1, 1),),
            "duplicate": (answer, _usage(1, 1), _usage(1, 1)),
        }
        for name, final_events in cases.items():
            with self.subTest(name=name):
                events = {"tools": _valid_events()["tools"], "final": final_events}
                with self.assertRaises(ProviderError) as caught:
                    await run_connection(
                        self.resources(),
                        lambda _events=events: FakeProvider(_events, []),
                        lambda _prepared: asyncio.sleep(0),
                        lambda _prepared: None,
                    )
                self.assertEqual(
                    caught.exception.diagnostic,
                    ProtocolDiagnostic("result_validation"),
                )

    async def test_response_shape_json_and_usage_caps_are_strict(self) -> None:
        too_deep = (b'{"id":"x","name":"p","arguments":'
                    + b'{"x":' * 17 + b'{}' + b'}' * 17 + b'}')
        bad_tools = (
            (ProviderEvent("tool", b'{"id":"x","name":"p","arguments":{},"arguments":{}}'), _usage(1, 1)),
            (ProviderEvent("tool", too_deep), _usage(1, 1)),
            (ProviderEvent("text", b"not allowed"), _usage(1, 1)),
            (ProviderEvent("tool", b'{"id":"x","name":"p","arguments":{}}'),),
            (ProviderEvent("tool", b'{"id":"x","name":"p","arguments":{}}'), _usage(1, 33)),
        )
        for events in bad_tools:
            with self.subTest(events=events):
                provider = FakeProvider({"tools": events, "final": _valid_events()["final"]}, [])
                with self.assertRaises(ProviderError) as caught:
                    await run_connection(self.resources(), lambda _provider=provider: _provider,
                                         lambda _prepared: asyncio.sleep(0), lambda _prepared: None)
                self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")

        bad_finals = (
            (ProviderEvent("answer", b'{"index":0,"last":true,"content":"{\\"x\\":1}"}'), _usage(1, 1)),
            (ProviderEvent("answer", b'{"index":0,"last":true,"content":"{\\"x\\":1,\\"x\\":2}"}'), _usage(1, 1)),
            (ProviderEvent("tool", b'{"id":"x","name":"p","arguments":{}}'), _usage(1, 1)),
        )
        for events in bad_finals:
            with self.subTest(events=events):
                provider = FakeProvider({"tools": _valid_events()["tools"], "final": events}, [])
                with self.assertRaises(ProviderError):
                    await run_connection(self.resources(), lambda _provider=provider: _provider,
                                         lambda _prepared: asyncio.sleep(0), lambda _prepared: None)

    async def test_cleanup_retries_close_failure_and_is_idempotent(self) -> None:
        provider = FakeProvider(_valid_events(), [])
        resources = self.resources()
        await run_connection(resources, lambda: provider,
                             lambda _prepared: asyncio.sleep(0), lambda _prepared: None)
        provider.fail_close = True
        with self.assertRaises(RuntimeError):
            await resources.cancel_and_close()
        self.assertFalse(resources.resources_released)
        self.assertIs(resources.provider, provider)
        provider.fail_close = False
        await resources.cancel_and_close()
        await resources.cancel_and_close()
        self.assertTrue(resources.resources_released)
        self.assertIsNone(resources.provider)
        self.assertIsNone(resources.prepared)
        self.assertEqual((provider.cancel_calls, provider.close_calls), (1, 2))

    def test_connection_budget_is_fixed_and_starts_at_admission(self) -> None:
        now = [10.0]
        resources = _ConnectionResources(admitted_at=10.0, clock=lambda: now[0], deadline=25.0)
        resources.ledger.reserve(BudgetReservation(model_requests=2, tool_calls=1,
                                                   input_tokens=1024, output_tokens=128))
        with self.assertRaises(ValueError):
            resources.ledger.reserve(BudgetReservation(model_requests=1))
        with self.assertRaises(ValueError):
            resources.ledger.reserve(BudgetReservation(source_fragments=(("a.py", "x"),)))
        now[0] = 25.0
        with self.assertRaises(ValueError):
            resources.ledger.reserve(BudgetReservation())


if __name__ == "__main__":
    unittest.main()
