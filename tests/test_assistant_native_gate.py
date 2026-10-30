"""Closed host proof and post-cleanup native acceptance, without real GUI."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import verify_assistant_native as verifier


class NativeGateTests(unittest.TestCase):
    def test_duplicate_receipt_fields_cannot_hide_a_false_flag(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "host-exit.json"
            path.write_bytes(b'{"fakeOnly":false,"fakeOnly":true,"sends":2,'
                b'"providerClosed":true,"cancelCalls":0,"runtimeShutdown":true}')
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                verifier._read_host_exit(path, deepseek=False)

    def test_receipt_requires_closed_fields_and_exact_types(self):
        good = {"fakeOnly": True, "sends": 2, "providerClosed": True,
                "cancelCalls": 0, "runtimeShutdown": True}
        validate = getattr(verifier, "_validate_host_receipt", None)
        self.assertTrue(callable(validate), "Missing closed native host proof validation")
        self.assertEqual(validate(good, deepseek=False), good)
        for bad in ({**good, "fakeOnly": False}, {**good, "cancelCalls": False},
                    {**good, "unexpected": True}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate(bad, deepseek=False)
        production = {"fakeOnly": True, "productionSDK": True, "sends": 4,
            "providerClosed": True, "runtimeShutdown": True, "externalAttempts": 0, "originalRequests": True}
        self.assertEqual(validate(production, deepseek=True), production)
        with self.assertRaises(ValueError):
            validate({**production, "externalAttempts": False}, deepseek=True)

    def test_cleanup_escalation_or_error_cannot_return_success(self):
        finish = getattr(verifier, "_final_result", None)
        self.assertTrue(callable(finish), "Missing post-cleanup native result gate")
        for problem in ({"forcedCleanup": True}, {"cleanupErrorType": "OSError"}):
            report = {"status": "pending", **problem}
            self.assertEqual(finish(report, True), 1)
            self.assertEqual(report["status"], "failed")
        report = {"status": "pending"}
        self.assertEqual(finish(report, True), 0)
        self.assertEqual(report["status"], "passed")


if __name__ == "__main__":
    unittest.main()
