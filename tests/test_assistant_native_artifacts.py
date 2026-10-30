"""Artifact routing checks, not source/wheel/frozen native acceptance."""

import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


class AssistantNativeArtifactTests(unittest.TestCase):
    def test_missing_artifact_preflight_needs_neither_windows_nor_playwright(self):
        verifier = importlib.import_module("scripts.verify_assistant_native")
        with TemporaryDirectory() as directory, \
                patch.object(verifier, "os", SimpleNamespace(name="posix", replace=os.replace)):
            output = Path(directory)
            result = verifier.main(["--exe", str(output / "missing.exe"), "--fake-only",
                                    "--output", str(output)])
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(result, 2)
            self.assertIn("missing", report["reason"].lower())
            self.assertNotIn("pid", report)

    def test_main_reports_missing_frozen_artifact_without_source_fallback(self):
        verifier = importlib.import_module("scripts.verify_assistant_native")
        with TemporaryDirectory() as directory:
            output = Path(directory)
            result = verifier.main(["--exe", str(output / "missing.exe"), "--fake-only",
                                    "--output", str(output)])
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(result, 2)
            self.assertEqual(report["status"], "skipped")
            self.assertIn("missing", report["reason"].lower())
            self.assertNotIn("pid", report)

    def test_artifact_command_keeps_fake_injection_private_and_wheel_isolated(self):
        verifier = importlib.import_module("scripts.verify_assistant_native")
        self.assertTrue(callable(getattr(verifier, "_host_command", None)),
                        "Each requested artifact needs its own native launch command")
        with TemporaryDirectory() as directory:
            output = Path(directory)
            source = verifier._host_command(SimpleNamespace(source=True, wheel_dir=None, exe=None),
                                            port=9239, output=output)
            self.assertEqual(source[:3], [sys.executable, "-I", "-B"])
            self.assertEqual(Path(source[3]).name, "assistant_smoke_host.py")
            executable = output / "CodeViz.exe"
            executable.touch()
            frozen = verifier._host_command(SimpleNamespace(source=False, wheel_dir=None, exe=executable),
                                            port=9239, output=output)
            self.assertEqual(frozen[:2], [str(executable.resolve()), "--_codeviz-assistant-native-smoke"])
            for command in (source, frozen):
                self.assertEqual(command[-4:], ["--port", "9239", "--output", str(output)])
                self.assertNotIn("--provider", command)
                self.assertNotIn("--project", command)

    def test_missing_executable_is_not_replaced_with_source_success(self):
        verifier = importlib.import_module("scripts.verify_assistant_native")
        self.assertTrue(callable(getattr(verifier, "_host_command", None)),
                        "Missing artifacts must fail closed")
        with TemporaryDirectory() as directory:
            output = Path(directory)
            with self.assertRaises(FileNotFoundError):
                verifier._host_command(SimpleNamespace(source=False, wheel_dir=None,
                    exe=output / "missing.exe"), port=9239, output=output)


if __name__ == "__main__":
    unittest.main()
