"""Estimated-input accounting for the bounded source-free connection probe."""

import asyncio
import hashlib
import json
import time
import unittest
from collections.abc import AsyncIterator
from functools import partial

from codeviz.assistant._usage_values import ObservedUsage
from codeviz.assistant._wire_accounting import TrustedEstimatedProfile
from codeviz.assistant.bridge_requests import ConnectionRequest
from codeviz.assistant.config import ProviderConfig
from codeviz.assistant.connection import _ConnectionResources, run_connection
from codeviz.assistant.connection_profile import connection_schema
from codeviz.assistant.contracts import AgentError, JobStatus
from codeviz.assistant.provider import (
    EstimatedInputAccounting,
    PreparedRequest,
    ProviderDraft,
    ProviderError,
    ProviderEvent,
)
from codeviz.assistant.service import AssistantService

PROFILE = TrustedEstimatedProfile(
    "connection-estimated-v1",
    "https://estimated.invalid:443/v1/chat/completions",
    "connection-estimated",
)


def _body(stage: str) -> bytes:
    body: dict[str, object] = {
        "model": PROFILE.model,
        "messages": [{
            "role": "user",
            "content": "Call p." if stage == "tools" else "Return {}.",
        }],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_completion_tokens": 32 if stage == "tools" else 96,
    }
    if stage == "tools":
        body.update({
            "tools": [{"type": "function", "function": {
                "name": "p",
                "description": "",
                "parameters": connection_schema(),
                "strict": True,
            }}],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        })
    else:
        body.update({
            "tool_choice": "none",
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "p",
                "strict": True,
                "schema": connection_schema(),
            }},
        })
    return json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _usage(usage: ObservedUsage) -> ProviderEvent:
    return ProviderEvent("usage", json.dumps({
        "inputTokens": usage.input_tokens,
        "outputTokens": usage.output_tokens,
        "basis": usage.basis,
    }, separators=(",", ":")).encode("utf-8"))


class EstimatedConnectionProvider:
    def __init__(
        self,
        tools_usage: ObservedUsage,
        final_usage: ObservedUsage,
        *,
        after_tools_usage: str | None = None,
    ) -> None:
        self.tools_usage = tools_usage
        self.final_usage = final_usage
        self.after_tools_usage = after_tools_usage
        self.prepares: list[str] = []
        self.dispatches: list[str] = []

    async def prepare(self, draft: ProviderDraft) -> PreparedRequest:
        logical = json.loads(draft.body)
        stage = logical["mode"]
        if stage not in ("tools", "final"):
            raise AssertionError("unexpected connection stage")
        self.prepares.append(stage)
        body = _body(stage)
        accounting = EstimatedInputAccounting(
            PROFILE.profile_id,
            PROFILE.method,
            len(body),
            hashlib.sha256(body).hexdigest(),
        )
        return PreparedRequest(
            f"estimated-{stage}",
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

    def dispatch(self, prepared: PreparedRequest) -> AsyncIterator[ProviderEvent]:
        stage = "tools" if prepared.max_output_tokens == 32 else "final"
        self.dispatches.append(stage)

        async def iterate() -> AsyncIterator[ProviderEvent]:
            if stage == "tools":
                yield ProviderEvent(
                    "tool", b'{"id":"call-1","name":"p","arguments":{}}')
                yield _usage(self.tools_usage)
                if self.after_tools_usage == "invalid":
                    yield ProviderEvent("text", b"not allowed")
                elif self.after_tools_usage == "error":
                    raise RuntimeError("synthetic provider failure")
            else:
                yield ProviderEvent(
                    "answer", b'{"index":0,"last":true,"content":"{}"}')
                yield _usage(self.final_usage)

        return iterate()

    async def cancel(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


def _provider(provider: EstimatedConnectionProvider) -> EstimatedConnectionProvider:
    return provider


class EstimatedConnectionAccountingTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _resources() -> _ConnectionResources:
        return _ConnectionResources(admitted_at=10.0, clock=lambda: 10.0, deadline=25.0)

    async def test_tools_unknown_or_overrun_stops_before_final_prepare(self) -> None:
        for usage, reason in (
            (ObservedUsage(), "usage_unknown"),
            (ObservedUsage(1025, 1, "actual"), "observed_overrun"),
        ):
            with self.subTest(reason=reason):
                provider = EstimatedConnectionProvider(
                    usage,
                    ObservedUsage(1, 1, "actual"),
                )
                resources = self._resources()

                with self.assertRaises(AgentError) as caught:
                    await run_connection(
                        resources,
                        partial(_provider, provider),
                        lambda _prepared: _ready(),
                        lambda _prepared: None,
                        trusted_profiles=(PROFILE,),
                    )

                self.assertEqual(caught.exception.code, "budget_exceeded")
                self.assertEqual(provider.prepares, ["tools"])
                self.assertEqual(provider.dispatches, ["tools"])
                self.assertEqual(resources.observed, ObservedUsage() if reason == "usage_unknown"
                                 else usage)
                self.assertIsNotNone(resources.input_accounting)
                assert resources.input_accounting is not None
                self.assertEqual(resources.input_accounting.limit_tokens, 1024)
                self.assertEqual(resources.input_accounting.blocked_reason, reason)

    async def test_complete_final_keeps_unknown_or_overrun_warning(self) -> None:
        cases = (
            (ObservedUsage(), ObservedUsage(), "usage_unknown", 7),
            (ObservedUsage(1020, 2, "actual"),
             ObservedUsage(1027, 5, "actual"), "observed_overrun", 1027),
        )
        for final_usage, observed, reason, known in cases:
            with self.subTest(reason=reason):
                provider = EstimatedConnectionProvider(
                    ObservedUsage(7, 3, "actual"),
                    final_usage,
                )
                resources = self._resources()

                result = await run_connection(
                    resources,
                    partial(_provider, provider),
                    lambda _prepared: _ready(),
                    lambda _prepared: None,
                    trusted_profiles=(PROFILE,),
                )

                self.assertEqual(provider.prepares, ["tools", "final"])
                self.assertEqual(provider.dispatches, ["tools", "final"])
                self.assertEqual(result.observed, observed)
                self.assertEqual(
                    (result.usage.model_requests, result.usage.tool_calls,
                     result.usage.output_tokens, result.usage.source_characters,
                     result.usage.files),
                    (2, 1, 128, 0, ()),
                )
                self.assertIsNotNone(result.input_accounting)
                assert result.input_accounting is not None
                self.assertEqual(result.input_accounting.limit_tokens, 1024)
                self.assertEqual(result.input_accounting.known_actual_input_tokens, known)
                self.assertEqual(result.input_accounting.blocked_reason, reason)

    async def test_trusted_profiles_are_copied_before_dispatch(self) -> None:
        trusted = TrustedEstimatedProfile(
            PROFILE.profile_id,
            PROFILE.endpoint,
            PROFILE.model,
        )
        provider = EstimatedConnectionProvider(
            ObservedUsage(7, 3, "actual"),
            ObservedUsage(11, 2, "actual"),
        )

        async def mutate_profile(prepared: PreparedRequest) -> None:
            if prepared.max_output_tokens == 32:
                object.__setattr__(trusted, "model", "changed-after-admission")

        result = await run_connection(
            self._resources(),
            lambda: provider,
            mutate_profile,
            lambda _prepared: None,
            trusted_profiles=(trusted,),
        )

        self.assertEqual(provider.dispatches, ["tools", "final"])
        self.assertEqual(result.observed, ObservedUsage(18, 5, "actual"))

    async def test_frozen_snapshot_copies_all_estimated_metadata_primitives(self) -> None:
        mutations = (
            ("profile_id", "changed-profile"),
            ("method", "changed-method"),
            ("estimated_input_tokens", 1),
            ("body_sha256", "0" * 64),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                provider = EstimatedConnectionProvider(
                    ObservedUsage(7, 3, "actual"),
                    ObservedUsage(11, 2, "actual"),
                )
                resources = self._resources()

                async def mutate(
                    prepared: PreparedRequest,
                    *,
                    field: str = field,
                    value: object = value,
                ) -> None:
                    accounting = prepared.estimated_input_accounting
                    if accounting is None:
                        raise AssertionError("estimated accounting missing")
                    object.__setattr__(accounting, field, value)

                with self.assertRaises(ProviderError) as caught:
                    await run_connection(
                        resources,
                        partial(_provider, provider),
                        mutate,
                        lambda _prepared: None,
                        trusted_profiles=(PROFILE,),
                    )

                self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
                self.assertEqual(provider.dispatches, [])
                self.assertIsNotNone(resources.input_accounting)
                assert resources.input_accounting is not None
                self.assertIsNone(resources.input_accounting.blocked_reason)

    async def test_service_failure_retires_resource_accounting_snapshot(self) -> None:
        provider = EstimatedConnectionProvider(
            ObservedUsage(),
            ObservedUsage(1, 1, "actual"),
        )
        service = AssistantService(
            lambda: provider,
            trusted_input_profiles=(PROFILE,),
        )
        service._replace_connection_config(ProviderConfig.from_base_url(
            "https://estimated.invalid/v1",
            PROFILE.model,
            1,
        ))
        try:
            started = service._start_connection(
                ConnectionRequest(1, "estimated-failure"),
                lambda: provider,
            )
            job_id = started["jobId"]
            if type(job_id) is not str:
                raise AssertionError("connection job id missing")
            deadline = time.monotonic() + 3
            retired = None
            while retired is None and time.monotonic() < deadline:
                with service._lock:
                    retired = service._connection_completed.get(job_id)
                if retired is None:
                    await _sleep()

            self.assertIsNotNone(retired)
            assert retired is not None
            self.assertEqual(retired.state.lifecycle.status, JobStatus.FAILED)
            self.assertIsNotNone(retired.state.lifecycle.issue)
            assert retired.state.lifecycle.issue is not None
            self.assertEqual(retired.state.lifecycle.issue.code, "AI_BUDGET_EXCEEDED")
            self.assertEqual(provider.prepares, ["tools"])
            self.assertEqual(provider.dispatches, ["tools"])
            self.assertIsNotNone(retired.input_accounting)
            assert retired.input_accounting is not None
            self.assertEqual(retired.input_accounting.blocked_reason, "usage_unknown")
        finally:
            service.shutdown()

    async def test_valid_usage_is_retained_before_later_stream_failure(self) -> None:
        for trailing, error_type in (
            ("invalid", ProviderError),
            ("error", RuntimeError),
        ):
            with self.subTest(trailing=trailing):
                provider = EstimatedConnectionProvider(
                    ObservedUsage(7, 3, "actual"),
                    ObservedUsage(1, 1, "actual"),
                    after_tools_usage=trailing,
                )
                resources = self._resources()

                with self.assertRaises(error_type):
                    await run_connection(
                        resources,
                        partial(_provider, provider),
                        lambda _prepared: _ready(),
                        lambda _prepared: None,
                        trusted_profiles=(PROFILE,),
                    )

                self.assertEqual(provider.prepares, ["tools"])
                self.assertEqual(provider.dispatches, ["tools"])
                self.assertEqual(resources.observed, ObservedUsage(7, 3, "actual"))
                self.assertIsNotNone(resources.input_accounting)
                assert resources.input_accounting is not None
                self.assertEqual(
                    resources.input_accounting.known_actual_input_tokens,
                    7,
                )
                self.assertIsNone(resources.input_accounting.blocked_reason)


async def _ready() -> None:
    return None


async def _sleep() -> None:
    await asyncio.sleep(0.005)


if __name__ == "__main__":
    unittest.main()
