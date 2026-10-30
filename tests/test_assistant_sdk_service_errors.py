"""Typed SDK failures keep fixed public meaning through the real worker."""

import json
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.assistant.bridge_requests import (
    PollRequest,
    ProjectRequest,
    parse_configure,
    parse_consent,
    parse_start,
)
from codeviz.assistant.issues import ProtocolDiagnostic
from codeviz.assistant.provider import PreparedRequest, ProviderEvent, WireOccurrence
from codeviz.assistant.sdk_provider import SDKAdapterError
from codeviz.assistant.service import ServiceError, _AssistantRuntime
from codeviz.assistant.tool_loop import ToolLoopTranscript
from codeviz.ui.controller import AppController


class _FailingSDKProvider:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def prepare(self, draft):
        logical = json.loads(draft.body)
        body = json.dumps({
            "model": "probe-model",
            "messages": logical["messages"],
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        def occurrences(name):
            return tuple(WireOccurrence(
                item["pointer"], item["start"], item["end"], item["file"]
            ) for item in logical.get(name, []))

        return PreparedRequest(
            "sdk-error-prepared",
            "POST",
            "https://provider.invalid:443/v1/chat/completions",
            body,
            draft.max_output_tokens,
            draft.deadline,
            len(body) + 1024,
            occurrences("sourceTags"),
            occurrences("metadataTags"),
            True,
        )

    async def dispatch(self, prepared):
        raise self._error
        yield None

    async def cancel(self) -> None:
        pass

    async def aclose(self) -> None:
        pass


class _ForgedCodeError(RuntimeError):
    def __init__(self) -> None:
        self.code = "AI_AUTH_FAILED"
        super().__init__("untrusted provider detail")


class _ToolProvider(_FailingSDKProvider):
    def __init__(self, arguments: dict[str, object]) -> None:
        super().__init__(RuntimeError("unused"))
        self._arguments = arguments

    async def dispatch(self, prepared):
        yield ProviderEvent("tool", json.dumps({
            "id": "call", "name": "search_symbols", "arguments": self._arguments,
        }, separators=(",", ":")).encode())
        yield ProviderEvent(
            "usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')


class _InvalidAnswerProvider(_FailingSDKProvider):
    async def dispatch(self, prepared):
        yield ProviderEvent("answer", b'{"index":0,"last":true,"content":"{}"}')
        yield ProviderEvent(
            "usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')


class _RepairSendTranscript:
    mode = "final"

    def __init__(self, draft) -> None:
        self._transcript = ToolLoopTranscript(draft)
        self._final_catalog = None

    def preview_final(self, output_limit, catalog=()):
        return self._transcript.preview_final(output_limit, catalog)

    def preview_completion(self, final_limit, repair_limit, catalog=()):
        return self._transcript.preview_completion(final_limit, repair_limit, catalog)

    def final(self, output_limit, catalog=()):
        self._final_catalog = catalog
        return self._transcript.final(output_limit, catalog)

    def _check_catalog(self, catalog):
        assert self._final_catalog is not None
        assert catalog == self._final_catalog, "Repair must retain the final receipt catalog"

    def repair(self, codes, output_limit, catalog=()):
        self._check_catalog(catalog)
        return self._transcript.repair(codes, output_limit, catalog)


class _RepairFailureTranscript(_RepairSendTranscript):
    def __init__(self, draft) -> None:
        super().__init__(draft)
        self.repair_calls = 0

    def repair(self, codes, output_limit, catalog=()):
        self.repair_calls += 1
        self._check_catalog(catalog)
        raise ValueError("SENSITIVE-REPAIR")


class _DiagnosticRepairProvider(_FailingSDKProvider):
    def __init__(self, repair_error: Exception | None = None) -> None:
        super().__init__(RuntimeError("unused"))
        self._repair_error = repair_error or ServiceError(
            "AI_PROTOCOL_INCOMPATIBLE", ProtocolDiagnostic("dispatch"))
        self._prepares = 0
        self._sends = 0

    async def prepare(self, draft):
        self._prepares += 1
        prepared = await super().prepare(draft)
        return replace(prepared, preparation_id=f"sdk-repair-{self._prepares}")

    async def dispatch(self, prepared):
        self._sends += 1
        if self._sends == 1:
            yield ProviderEvent("answer", b'{"index":0,"last":true,"content":"{}"}')
            yield ProviderEvent(
                "usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')
            return
        raise self._repair_error


class AssistantSDKServiceErrorTests(unittest.TestCase):
    def _run_provider(self, provider) -> dict[str, object]:
        with TemporaryDirectory() as directory:
            runtime = _AssistantRuntime(
                provider_factory=lambda config, key: provider
            )
            try:
                controller = AppController(assistant_publication_mirror=runtime)
                controller.open_project(Path(directory).resolve())
                controller.analyze()
                context = controller.capture_assistant_context()
                runtime.configure(parse_configure({
                    "baseUrl": "https://provider.invalid/v1",
                    "model": "probe-model",
                    "key": "offline-test-only",
                    "enabled": True,
                }))
                conversation = runtime.new_conversation(
                    ProjectRequest(context.project_epoch), context
                )["conversationId"]
                grant = runtime.grant_consent(parse_consent({
                    "projectEpoch": context.project_epoch,
                    "analysisGeneration": context.analysis_generation,
                    "configRevision": 1,
                    "allowedFiles": [],
                    "confirmed": True,
                }), context)
                started = runtime.start(parse_start({
                    "projectEpoch": context.project_epoch,
                    "analysisGeneration": context.analysis_generation,
                    "conversationId": conversation,
                    "requestId": "sdk-error",
                    "configRevision": 1,
                    "consentRevision": grant["consentRevision"],
                    "question": "What can you confirm?",
                    "context": {"kind": "question"},
                }), context)
                deadline = time.monotonic() + 3
                state = runtime.poll(PollRequest(
                    started["jobId"], context.project_epoch, 0
                ))
                while not state["resourcesReleased"] and time.monotonic() < deadline:
                    time.sleep(0.005)
                    state = runtime.poll(PollRequest(
                        started["jobId"], context.project_epoch, 0
                    ))
                self.assertTrue(state["resourcesReleased"])
                self.assertEqual(state["status"], "Failed")
                return state
            finally:
                runtime.shutdown()

    def _run_failure(self, error: Exception) -> dict[str, object]:
        return self._run_provider(_FailingSDKProvider(error))

    def test_fixed_sdk_adapter_failures_retain_their_public_codes(self):
        optional_before = {
            name for name in sys.modules
            if name == "pydantic_ai" or name.startswith("pydantic_ai.")
        }
        for code in (
            "AI_BUDGET_EXCEEDED",
            "AI_AUTH_FAILED",
            "AI_RATE_LIMITED",
            "AI_NETWORK_TIMEOUT",
        ):
            with self.subTest(code=code):
                state = self._run_failure(SDKAdapterError(code))
                self.assertEqual(state["issue"]["code"], code)
        optional_after = {
            name for name in sys.modules
            if name == "pydantic_ai" or name.startswith("pydantic_ai.")
        }
        self.assertEqual(optional_after, optional_before)

    def test_arbitrary_exception_code_remains_a_protocol_failure(self):
        state = self._run_failure(_ForgedCodeError())
        self.assertEqual(state["issue"]["code"], "AI_PROTOCOL_INCOMPATIBLE")

    def test_sdk_dispatch_site_and_closed_finish_survive_public_poll(self):
        diagnostic = ProtocolDiagnostic("dispatch", check_site="terminal_finish", finish_reason="length")
        state = self._run_failure(SDKAdapterError("AI_PROTOCOL_INCOMPATIBLE", diagnostic))
        self.assertEqual(state["issue"]["diagnostic"], {
            "boundary": "dispatch", "checkSite": "terminal_finish", "finishReason": "length"})
        self.assertNotIn("traceback", json.dumps(state))

    def test_model_tool_dto_failure_is_result_validation(self):
        state = self._run_provider(_ToolProvider({"query": True}))
        self.assertEqual(state["issue"], {
            "code": "AI_PROTOCOL_INCOMPATIBLE",
            "message": "The assistant response could not be accepted.",
            "diagnostic": {"boundary": "result_validation"},
        })

    def test_tool_projection_failure_is_pre_send_without_raw_detail(self):
        with patch("codeviz.assistant.service.project_tool_result",
                   side_effect=ValueError("SENSITIVE-PROJECTION")):
            state = self._run_provider(_ToolProvider({"query": ""}))
        self.assertEqual(state["issue"], {
            "code": "AI_PROTOCOL_INCOMPATIBLE",
            "message": "The assistant response could not be accepted.",
            "diagnostic": {"boundary": "pre_send"},
        })
        self.assertNotIn("SENSITIVE", json.dumps(state))

    def test_repair_failure_retains_pre_send_diagnostic(self):
        transcripts = []

        def create_transcript(draft):
            transcript = _RepairFailureTranscript(draft)
            transcripts.append(transcript)
            return transcript

        with patch("codeviz.assistant.service.ToolLoopTranscript.maybe_create",
                   side_effect=create_transcript):
            state = self._run_provider(_InvalidAnswerProvider(RuntimeError("unused")))
        self.assertEqual(len(transcripts), 1)
        self.assertEqual(transcripts[0].repair_calls, 1)
        self.assertEqual(state["issue"], {
            "code": "AI_PROTOCOL_INCOMPATIBLE",
            "message": "The assistant response could not be accepted.",
            "diagnostic": {"boundary": "pre_send"},
        })
        self.assertNotIn("SENSITIVE", json.dumps(state))

    def test_repair_send_retains_closed_protocol_diagnostic(self):
        with patch("codeviz.assistant.service.ToolLoopTranscript.maybe_create",
                   side_effect=_RepairSendTranscript):
            state = self._run_provider(_DiagnosticRepairProvider())
        self.assertEqual(state["issue"], {
            "code": "AI_PROTOCOL_INCOMPATIBLE",
            "message": "The assistant response could not be accepted.",
            "diagnostic": {"boundary": "dispatch"},
        })

    def test_repair_send_preserves_the_actual_sdk_error_type_and_diagnostic(self):
        error = SDKAdapterError("AI_PROTOCOL_INCOMPATIBLE", ProtocolDiagnostic(
            "dispatch", check_site="terminal_finish", finish_reason="length"))
        with patch("codeviz.assistant.service.ToolLoopTranscript.maybe_create",
                   side_effect=_RepairSendTranscript):
            state = self._run_provider(_DiagnosticRepairProvider(error))
        self.assertEqual(state["issue"], {
            "code": "AI_PROTOCOL_INCOMPATIBLE",
            "message": "The assistant response could not be accepted.",
            "diagnostic": {"boundary": "dispatch", "checkSite": "terminal_finish",
                           "finishReason": "length"},
        })


if __name__ == "__main__":
    unittest.main()
