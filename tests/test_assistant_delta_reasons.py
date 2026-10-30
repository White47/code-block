"""Private, closed transport reasons do not change public error contracts."""

import importlib
import json
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import patch


class DeltaReasonTests(unittest.TestCase):
    def api(self):
        return importlib.import_module("codeviz.assistant._delta_reasons")

    def test_exact_immutable_value_rejects_foreign_or_mutated_provenance(self):
        api = self.api()
        value = api.DeltaRejection("delta_role")
        self.assertIs(api.validated_rejection(value), value)
        with self.assertRaises(FrozenInstanceError):
            value.reason = "delta_fields"
        class Foreign:
            @property
            def reason(self):
                raise AssertionError("Foreign property must not be read")
        class Label(str):
            pass
        self.assertIsNone(api.validated_rejection(Foreign()))
        for label in (Label("delta_role"), "SECRET", None, True):
            with self.subTest(label_type=type(label).__name__), self.assertRaises(ValueError):
                api.DeltaRejection(label)
        object.__setattr__(value, "check_site", "cleanup")
        self.assertIsNone(api.validated_rejection(value))

    def test_transport_uses_shared_two_slots_and_first_reason_only(self):
        api = self.api()
        diagnostics = importlib.import_module("codeviz.assistant._validation_diagnostics")
        capture = diagnostics.Capture()
        capture.begin("final").finish("evidence", (("static_requires_ids", 0, 0),))
        capture.transport("repair", api.DeltaRejection("arguments_json"))
        capture.transport("ordinary", api.DeltaRejection("delta_role"))
        records = json.loads(capture.encode())
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1], {"phase": "repair", "stage": "transport", "reason": "arguments_json"})
        self.assertEqual(records[0]["issueCounts"]["static_requires_ids"], 1)
        self.assertLessEqual(len(capture.encode()), 4096)
        for bad in ({**records[1], "raw": "SECRET"}, {**records[1], "reason": "SECRET"},
                    {**records[1], "phase": "SECRET"}, {**records[1], "reason": True}):
            self.assertIsNone(diagnostics.encode_records([bad]))
        full = diagnostics.Capture()
        full.begin("final")
        full.begin("repair")
        full.transport("ordinary", api.DeltaRejection("delta_role"))
        self.assertEqual([r["stage"] for r in json.loads(full.encode())], ["parse", "parse"])

    def test_sdk_sanitization_and_service_recognize_only_exact_private_values(self):
        api = self.api()
        sdk = importlib.import_module("codeviz.assistant.sdk_provider")
        service = importlib.import_module("codeviz.assistant.service")
        value = api.DeltaRejection("terminal_unknown_name")
        error = sdk.SDKAdapterError("AI_PROTOCOL_INCOMPATIBLE", rejection=value)
        mapped = sdk._error(error, "dispatch", "delta_shape")
        self.assertIs(mapped._delta_rejection, value)
        self.assertEqual(mapped.diagnostic.to_public(), {"boundary": "dispatch", "checkSite": "delta_shape"})
        self.assertIs(service._sdk_delta_rejection(mapped), value)
        class Subclass(sdk.SDKAdapterError):
            pass
        self.assertIsNone(service._sdk_delta_rejection(Subclass("AI_PROTOCOL_INCOMPATIBLE", rejection=value)))
        mapped._delta_rejection = "SECRET"
        self.assertIsNone(service._sdk_delta_rejection(mapped))

    def test_first_failed_frame_guard_is_closed_and_public_site_is_unchanged(self):
        sdk = importlib.import_module("codeviz.assistant.sdk_provider")
        cases = (
            ({"role": None}, "delta_role"),
            ({"SECRET": True, "role": None}, "delta_fields"),
            ({"refusal": "SECRET", "role": None}, "delta_refusal"),
            ({"content": False}, "content_type"),
            ({"tool_calls": [{"index": 0, "type": None}]}, "tool_type"),
            ({"tool_calls": [{"index": True, "type": None}]}, "tool_index"),
        )
        for delta, reason in cases:
            with self.subTest(reason=reason):
                send = SimpleNamespace(binding=(None, "synthetic", (sdk._CAP, 100)),
                    data={"mode": "tools", "tools": []}, draft=SimpleNamespace(max_output_tokens=512),
                    delta_rejection=None)
                owner = SimpleNamespace(_modules={"httpx2": SimpleNamespace(AsyncByteStream=object)},
                                        _remaining=lambda _send: 10)
                stream = sdk._bounded_stream(owner, send, None)
                raw = json.dumps({"choices": [{"index": 0, "delta": delta}]}).encode()
                with self.assertRaises(sdk.SDKAdapterError) as caught:
                    stream.frame(raw)
                self.assertIsNotNone(caught.exception._delta_rejection)
                self.assertEqual(caught.exception._delta_rejection.reason, reason)
                self.assertEqual(stream.check_site, "delta_shape")
                self.assertNotIn("SECRET", str(caught.exception))

    def test_all_delta_guard_families_keep_order_and_boundary_acceptance(self):
        sdk = importlib.import_module("codeviz.assistant.sdk_provider")
        api = self.api()
        seen = set()

        def make_stream(mode="tools"):
            send = SimpleNamespace(binding=(None, "synthetic", (sdk._CAP, 100)),
                data={"mode": mode, "tools": [{"name": "search_symbols"}]},
                draft=SimpleNamespace(max_output_tokens=512), delta_rejection=None)
            owner = SimpleNamespace(_modules={"httpx2": SimpleNamespace(AsyncByteStream=object)},
                                    _remaining=lambda _send: 10)
            return sdk._bounded_stream(owner, send, None), send

        def run(stream, delta, reason, *, finish=None):
            value = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            original = sdk._remote_json

            def parsed(raw, cap=sdk._CAP):
                # Isolate inner defensive guards unreachable beyond outer JSON caps.
                return value if raw == b"synthetic-root" else original(raw, cap)

            with patch.object(sdk, "_remote_json", side_effect=parsed):
                if reason is None:
                    stream.frame(b"synthetic-root")
                    return
                with self.assertRaises(sdk.SDKAdapterError) as caught:
                    stream.frame(b"synthetic-root")
            self.assertEqual(caught.exception._delta_rejection.reason, reason)
            seen.add(reason)

        cases = (
            (None, "delta_object"), ({"SECRET": 1}, "delta_fields"),
            ({"refusal": True, "role": None}, "delta_refusal"),
            ({"role": None}, "delta_role"), ({"reasoning_content": "SECRET"}, "delta_reasoning"),
            ({"content": False}, "content_type"),
            ({"content": "x" * (sdk._CAP + 1)}, "content_string_size"),
            ({"content": "\ud800"}, "content_encoding"),
            ({"tool_calls": {}}, "tool_list"), ({"tool_calls": [None] * 9}, "tool_count"),
            ({"tool_calls": [None]}, "tool_item_object"),
            ({"tool_calls": [{"SECRET": 1}]}, "tool_item_fields"),
            ({"tool_calls": [{"index": False, "type": None}]}, "tool_index"),
            ({"tool_calls": [{"index": 0, "type": None}]}, "tool_type"),
            ({"tool_calls": [{"index": 0, "function": None}]}, "tool_function_object"),
            ({"tool_calls": [{"index": 0, "function": {"SECRET": 1}}]}, "tool_function_fields"),
            ({"tool_calls": [{"index": 0, "id": False}]}, "fragment_type"),
            ({"tool_calls": [{"index": 0, "id": "x" * (sdk._CAP + 1)}]}, "fragment_string_size"),
            ({"tool_calls": [{"index": 0, "id": "\ud800"}]}, "fragment_encoding"),
            ({"tool_calls": [{"index": 0, "id": "x" * 513}]}, "fragment_joined_size"),
        )
        for delta, reason in cases:
            with self.subTest(reason=reason):
                stream, _ = make_stream()
                run(stream, delta, reason)
        stream, _ = make_stream("final")
        run(stream, {"tool_calls": {}}, "tool_mode")
        stream, _ = make_stream()
        stream.text_count = 65536
        run(stream, {"content": "x"}, "content_total_size")
        terminals = (
            ({0: {"id": "", "name": "SECRET", "arguments": "{}"}}, "terminal_missing_id"),
            ({0: {"id": "c", "name": "search_symbols", "arguments": "{}"},
              1: {"id": "c", "name": "SECRET", "arguments": "{}"}}, "terminal_duplicate_id"),
            ({0: {"id": "c", "name": "SECRET", "arguments": "{}"}}, "terminal_unknown_name"),
            ({0: {"id": "c", "name": "search_symbols", "arguments": "{"}}, "arguments_json"),
            ({0: {"id": "c", "name": "search_symbols", "arguments": "[]"}}, "arguments_object"),
            ({0: {"id": "c" * 512, "name": "search_symbols", "arguments": json.dumps({"x": "a" * 7700})}}, "tool_payload_size"),
        )
        for calls, reason in terminals:
            with self.subTest(reason=reason):
                stream, _ = make_stream()
                stream.calls = calls
                run(stream, {}, reason, finish="tool_calls")
        self.assertEqual(seen, set(api.REASONS))
        for key, limit in (("id", 512), ("arguments", 8192)):
            for size in (limit, limit + 1):
                stream, _ = make_stream()
                run(stream, {"tool_calls": [{"index": 0, "function": {key: "x" * size}}]}
                    if key == "arguments" else {"tool_calls": [{"index": 0, key: "x" * size}]},
                    None if size == limit else "fragment_joined_size")
        empty_payload = {"id": "c" * 512, "name": "search_symbols", "arguments": {"x": ""}}
        remaining = 8192 - len(sdk._encode(empty_payload))
        for size in (remaining, remaining + 1):
            stream, _ = make_stream()
            stream.calls = {0: {"id": empty_payload["id"], "name": empty_payload["name"],
                                "arguments": json.dumps({"x": "x" * size})}}
            run(stream, {}, None if size == remaining else "tool_payload_size", finish="tool_calls")
        for delta in ({}, {"content": None, "reasoning_content": None},
                      {"tool_calls": [{"index": 0}]}, {"tool_calls": [{"index": 0, "id": None}]}):
            stream, send = make_stream()
            run(stream, delta, None)
            self.assertIsNone(send.delta_rejection)
        stream, send = make_stream()
        with patch.object(sdk, "_remote_json", side_effect=RuntimeError("SECRET")):
            with self.assertRaises(RuntimeError):
                stream.frame(b"synthetic-root")
        self.assertIsNone(send.delta_rejection)


if __name__ == "__main__":
    unittest.main()
