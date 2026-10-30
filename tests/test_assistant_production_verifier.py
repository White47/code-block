"""The production probe uses the real assembly and SDK, never a live API."""

import importlib.util
import json
import os
import runpy
import socket
import subprocess
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "verify_assistant_production.py"


class ProductionVerifierTests(unittest.TestCase):
    def test_fixed_fixture_answer_fits_the_real_wire_allocation(self):
        probe = runpy.run_path(str(_SCRIPT))
        kinds = ("summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")
        fixture = probe["FixtureHTTP"](None, kinds)
        wire = {"max_tokens": 512, "messages": [{"role": "user", "content":
            json.dumps({"evidenceId": "e-" + "a" * 64}) + "\nowned source"}]}
        answer = fixture._answer(wire)
        self.assertLessEqual(len(answer.encode()), wire["max_tokens"])
        value = json.loads(answer)
        self.assertEqual(len(value["sections"]), 7)
        self.assertIn("unable to confirm", value["sections"][-1]["claims"][0]["text"].lower())

    def test_failure_receipt_uses_public_issue_code_without_dependency_details(self):
        probe = runpy.run_path(str(_SCRIPT))
        with self.assertRaises(probe["ProbeFailure"]) as caught:
            probe["_completed"]({"status": "Failed", "issue": {
                "code": "AI_PROTOCOL_INCOMPATIBLE", "message": "private dependency detail"}}, "source failed")
        self.assertEqual(str(caught.exception), "source failed: AI_PROTOCOL_INCOMPATIBLE")

    def test_offline_guard_allows_only_owned_socketpair_plumbing(self):
        helper = runpy.run_path(str(_ROOT / "scripts" / "verify_assistant_dependencies.py"))
        with helper["offline_guard"]() as attempts:
            left, right = socket.socketpair()
            try:
                left.send(b"x")
                self.assertEqual(right.recv(1), b"x")
            finally:
                left.close()
                right.close()
            self.assertEqual(attempts, [])
            with socket.socket() as outsider, self.assertRaises(AssertionError):
                outsider.connect(("127.0.0.1", 9))
            self.assertEqual(attempts, ["blocked"])

    def test_probe_requires_fake_only_and_accepts_no_provider_or_project_arguments(self):
        self.assertTrue(_SCRIPT.is_file(), "Missing offline production-path verifier")
        for args in ([], ["--fake-only", "--endpoint", "https://untrusted.invalid"],
                     ["--fake-only", "--project", str(_ROOT)]):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, "-I", "-B", str(_SCRIPT), *args],
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage:", result.stderr)
                self.assertEqual(result.stdout, "")

    @unittest.skipUnless(importlib.util.find_spec("pydantic_ai"), "Requires the pinned optional SDK")
    def test_actual_production_path_returns_local_evidence_and_releases_cancelled_stream(self):
        self.assertTrue(_SCRIPT.is_file(), "Missing offline production-path verifier")
        environment = {**os.environ, "PYTHONPATH": str(_ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, "-B", str(_SCRIPT), "--fake-only"],
                                env=environment, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "passed")
        self.assertIs(report["fakeOnly"], True)
        self.assertEqual(report["profile"], "deepseek-flash-sdk-wire-v1")
        self.assertEqual(report["requests"], 5)
        self.assertEqual(report["externalAttempts"], 0)
        self.assertIs(report["originalRequests"], True)
        self.assertIs(report["released"], True)
        self.assertEqual(report["capabilities"], dict(toolSupported=True,
            structuredOutputSupported=False, jsonObjectSupported=True, outputValidation="local-json-schema"))
        self.assertEqual(report["sections"], 7)
        self.assertIs(report["sourceEvidence"], True)
        self.assertIs(report["cancelled"], True)
        self.assertRegex(report["assemblySha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(report["assemblyFiles"], ["_deepseek_admission.py", "sdk_provider.py",
            "connection.py", "connection_profile.py", "service.py", "response_session.py"])
        for forbidden in ("Bearer", "offline-production-only", "Static fixture must never execute"):
            self.assertNotIn(forbidden, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
