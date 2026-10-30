"""Private native harness wiring, not a native-GUI acceptance substitute."""

import importlib
import importlib.util
import runpy
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch


class AssistantNativeHarnessTests(unittest.TestCase):
    def test_only_frozen_exact_private_flag_routes_to_fixed_smoke_host(self):
        entry = Path(__file__).resolve().parents[1] / "scripts" / "desktop_entry.py"
        smoke = Mock()
        for frozen, flag, should_smoke in ((False, "--_codeviz-assistant-native-smoke", False),
                                           (True, "--_codeviz-assistant-native-smoke", True),
                                           (True, "--other-mode", False)):
            with self.subTest(frozen=frozen, flag=flag), \
                    patch.object(sys, "frozen", frozen, create=True), \
                    patch.object(sys, "argv", ["CodeViz.exe", flag, "--port", "9239", "--output", "owned"]), \
                    patch.dict(sys.modules, {"assistant_smoke_host": SimpleNamespace(main=smoke)}), \
                    patch("codeviz.ui.desktop.main") as normal:
                smoke.reset_mock()
                runpy.run_path(str(entry), run_name="__main__")
                self.assertEqual(smoke.call_count, int(should_smoke))
                self.assertEqual(normal.call_count, int(not should_smoke))
                if should_smoke:
                    smoke.assert_called_once_with(["--port", "9239", "--output", "owned"])

    def test_verifier_requires_fake_only_and_exactly_one_artifact(self):
        spec = importlib.util.find_spec("scripts.verify_assistant_native")
        self.assertIsNotNone(spec, "Task10 needs artifact-specific native verification")
        verifier = importlib.import_module("scripts.verify_assistant_native")
        for options in (["--source"], ["--fake-only"],
                        ["--source", "--exe", "CodeViz.exe", "--fake-only"]):
            with self.subTest(options=options), self.assertRaises(SystemExit) as error:
                verifier._parse_args(options)
            self.assertEqual(error.exception.code, 2)
        for options in (["--source", "--fake-only"],
                        ["--wheel-dir", "build/wheels", "--fake-only"],
                        ["--exe", "dist/CodeViz/CodeViz.exe", "--fake-only"]):
            self.assertTrue(verifier._parse_args(options).fake_only)

    def test_private_host_stays_inert_until_explicit_source_request(self):
        spec = importlib.util.find_spec("scripts.assistant_smoke_host")
        self.assertIsNotNone(spec, "Task10 needs the private fake-provider host")
        host = importlib.import_module("scripts.assistant_smoke_host")
        with TemporaryDirectory() as directory, patch(
            "socket.create_connection", side_effect=AssertionError("No provider network")
        ):
            bridge, provider = host._create_smoke_bridge(Path(directory))
            self.addCleanup(bridge._finish_assistant_close)
            settings = bridge.assistant_get_settings()["data"]
            self.assertFalse(settings["enabled"])
            self.assertFalse(settings["keySet"])
            self.assertEqual(provider.sends, 0)
            configured = bridge.assistant_configure({
                "baseUrl": "https://provider.invalid/v1", "model": "probe-model",
                "key": "synthetic-native-only", "enabled": True,
            })
            self.assertTrue(configured["ok"], configured)
            ctx = bridge._controller.capture_assistant_context()
            chat = bridge.assistant_new_conversation({"projectEpoch": ctx.project_epoch})
            grant = bridge.assistant_grant_consent({"projectEpoch": ctx.project_epoch,
                "analysisGeneration": ctx.analysis_generation, "configRevision": 1,
                "allowedFiles": ["sample.py"], "confirmed": True})
            self.assertTrue(grant["ok"], grant)
            self.assertEqual(provider.sends, 0)
            request = {"projectEpoch": ctx.project_epoch, "analysisGeneration": ctx.analysis_generation,
                "conversationId": chat["data"]["conversationId"], "requestId": "native-harness",
                "configRevision": 1, "consentRevision": grant["data"]["consentRevision"],
                "question": "Explain the saved function.", "context": {"kind": "source",
                    "filePath": "sample.py", "anchor": None, "mode": "file"}}
            started = bridge.assistant_start(request)
            self.assertTrue(started["ok"], started)
            deadline = time.monotonic() + 5
            result = None
            while time.monotonic() < deadline:
                poll = bridge.assistant_poll({"projectEpoch": ctx.project_epoch,
                    "jobId": started["data"]["jobId"], "afterSeq": 0})
                self.assertTrue(poll["ok"], poll)
                result = poll["data"]
                if result["resourcesReleased"]:
                    break
                Event().wait(.01)
            self.assertIsNotNone(result)
            self.assertTrue(result["resourcesReleased"], result)
            self.assertEqual(result["status"], "Completed", result)
            self.assertEqual(len(result["answer"]["sections"]), 7)
            self.assertTrue(result["answer"]["evidence"], result)
            self.assertGreater(provider.sends, 0)
            bridge._finish_assistant_close()
            self.assertTrue(provider.closed)


if __name__ == "__main__":
    unittest.main()
