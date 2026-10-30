"""The assistant public error boundary never includes raw diagnostics."""

import importlib
import importlib.util
import unittest


class AssistantIssueTests(unittest.TestCase):
    def api(self):
        name = "codeviz.assistant.issues"
        self.assertIsNotNone(importlib.util.find_spec(name), "Missing fixed issue boundary")
        return importlib.import_module(name)

    def test_evidence_failure_has_only_fixed_public_fields(self):
        issue = self.api().issue_for_code("AI_EVIDENCE_INVALID")
        self.assertEqual(issue.to_public(), {
            "code": "AI_EVIDENCE_INVALID",
            "message": "Assistant evidence validation failed.",
        })

    def test_protocol_issue_serializes_only_a_closed_diagnostic(self):
        api = self.api()
        issue = api.SafeIssue(
            "AI_PROTOCOL_INCOMPATIBLE",
            api.ProtocolDiagnostic("dispatch", 404),
        )
        self.assertEqual(issue.to_public(), {
            "code": "AI_PROTOCOL_INCOMPATIBLE",
            "message": "The assistant response could not be accepted.",
            "diagnostic": {"boundary": "dispatch", "httpStatus": 404},
        })

        for boundary, status in (
            ("unknown", None),
            ("pre_send", 400),
            ("result_validation", 500),
            ("dispatch", True),
            ("dispatch", 99),
            ("dispatch", 600),
        ):
            with self.subTest(boundary=boundary, status=status), self.assertRaises(ValueError):
                api.ProtocolDiagnostic(boundary, status)

        with self.assertRaises(ValueError):
            api.SafeIssue("AI_AUTH_FAILED", api.ProtocolDiagnostic("pre_send"))

    def test_legacy_service_codes_map_to_the_closed_public_taxonomy(self):
        api = self.api()
        for original, expected in (
            ("AI_STALE", "AI_ANALYSIS_STALE"),
            ("AI_JOB_NOT_FOUND", "AI_RESULT_UNAVAILABLE"),
            ("AI_CONVERSATION_CLEARED", "AI_RESULT_UNAVAILABLE"),
            ("AI_BUSY", "AI_CAPACITY"),
            ("AI_CONVERSATION_CAPACITY", "AI_CAPACITY"),
        ):
            with self.subTest(original=original):
                self.assertEqual(api.issue_for_code(original).code, expected)

    def test_closed_dispatch_check_site_and_finish_reason_round_trip(self):
        api = self.api()
        diagnostic = api.ProtocolDiagnostic(
            "dispatch", check_site="terminal_finish", finish_reason="length")
        self.assertEqual(api.issue_for_code("AI_PROTOCOL_INCOMPATIBLE", diagnostic)
                         .to_public()["diagnostic"], {
                             "boundary": "dispatch", "checkSite": "terminal_finish",
                             "finishReason": "length"})
        for values in ({"check_site": "private provider text"},
                       {"check_site": "usage_shape", "finish_reason": "length"},
                       {"check_site": "terminal_finish", "finish_reason": "private value"},
                       {"finish_reason": "length"},
                       {"http_status": 400, "check_site": "response_headers"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                api.ProtocolDiagnostic("dispatch", **values)
        with self.assertRaises(ValueError):
            api.ProtocolDiagnostic("pre_send", check_site="sdk_decode")

    def test_dispatch_sites_round_trip_without_retaining_unknown_fields(self):
        api = self.api()
        for site in ("response_headers", "sse_syntax", "chunk_shape", "usage_shape", "delta_shape",
                     "terminal_finish", "stream_eof", "sdk_decode", "context_exit", "cleanup"):
            self.assertEqual(api.ProtocolDiagnostic("dispatch", check_site=site).to_public(),
                             {"boundary": "dispatch", "checkSite": site})
        with self.assertRaises(TypeError):
            api.ProtocolDiagnostic("dispatch", payload="private raw value")

    def test_unknown_diagnostics_are_never_coerced_or_retained(self):
        class Diagnostic:
            def __str__(self):
                raise AssertionError("Raw diagnostics must not be formatted")

            def __hash__(self):
                raise AssertionError("Raw diagnostics must not be inspected")

        api = self.api()
        expected = api.issue_for_code("AI_PROTOCOL_INCOMPATIBLE").to_public()
        self.assertEqual(api.issue_for_code(Diagnostic()).to_public(), expected)
        self.assertEqual(api.issue_for_code("untrusted provider details").to_public(), expected)
        with self.assertRaises(ValueError):
            api.SafeIssue("untrusted provider details")


if __name__ == "__main__":
    unittest.main()
