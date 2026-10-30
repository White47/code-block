"""Private host process receipts; no real window is started here."""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import assistant_smoke_host
from scripts import verify_assistant_native as verifier


class AssistantNativeHostReceiptTests(unittest.TestCase):
    def test_owned_asset_readiness_requires_completed_loopback_http_response(self):
        wait_for_asset = getattr(assistant_smoke_host, "_wait_for_owned_asset_http", None)
        self.assertTrue(callable(wait_for_asset),
                        "The native host must wait for HTTP readiness, not socket bind only")
        attempts = []
        body_reads = []

        class Response:
            status = 200

            def read(self, amount):
                body_reads.append(amount)
                return b"<"

        class Connection:
            def __init__(self, host, port, timeout):
                self.index = len(attempts)
                self.closed = False
                attempts.append(self)
                self.host, self.port, self.timeout = host, port, timeout

            def request(self, method, target, *, headers):
                self.request_values = method, target, headers
                if self.index == 0:
                    raise ConnectionRefusedError("synthetic startup race")

            def getresponse(self):
                return Response()

            def close(self):
                self.closed = True

        wait_for_asset(51234, connection_factory=Connection,
                       timeout=1, sleep=lambda _seconds: None)

        self.assertEqual([(item.host, item.port) for item in attempts], [
            ("127.0.0.1", 51234), ("127.0.0.1", 51234),
        ])
        self.assertTrue(all(0 < item.timeout <= 0.5 for item in attempts))
        self.assertEqual(attempts[1].request_values,
                         ("GET", "/", {"Connection": "close"}))
        self.assertEqual(body_reads, [1])
        self.assertTrue(all(item.closed for item in attempts))

    def test_host_records_fixed_native_lifecycle_stages_without_provider_content(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            module = SimpleNamespace(settings={}, start=Mock())
            with patch.dict("sys.modules", {"webview": module}), patch(
                    "codeviz.ui.desktop.create_window", return_value=SimpleNamespace(destroy=Mock())):
                assistant_smoke_host.main(["--port", "9239", "--output", str(output)])
            receipt = output / "host-stages.json"
            self.assertTrue(receipt.is_file(), "Missing bounded native-stage diagnostics")
            stages = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertIn("create-window", stages)
            self.assertEqual(stages[-1], "native-loop-returned")
            self.assertTrue(all(type(stage) is str and len(stage) < 40 for stage in stages))

    def test_verifier_accepts_only_this_launchers_host_receipt(self):
        self.assertTrue(callable(getattr(verifier, "_native_pid", None)))
        with TemporaryDirectory() as directory:
            output = Path(directory)
            receipt = output / "host-start.json"
            receipt.write_text(json.dumps({"pid": 4321, "parentPid": 1234, "port": 9239}), encoding="utf-8")
            process = SimpleNamespace(pid=1234)
            self.assertEqual(verifier._native_pid(output, process, 9239), 4321)
            with self.assertRaises(ValueError):
                verifier._native_pid(output, process, 9240)
            receipt.write_text(json.dumps({"pid": 4321, "parentPid": 9999, "port": 9239}), encoding="utf-8")
            with self.assertRaises(ValueError):
                verifier._native_pid(output, process, 9239)

    def test_host_records_its_own_pid_and_parent_before_creating_window(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)

            def create_window(_webview, *, bridge):
                self.assertFalse(bridge.assistant_get_settings()["data"]["keySet"])
                receipt = output / "host-start.json"
                self.assertTrue(receipt.is_file(), "Need actual host PID, not a venv launcher PID")
                saved = json.loads(receipt.read_text(encoding="utf-8"))
                self.assertEqual(saved, {"pid": os.getpid(), "parentPid": os.getppid(), "port": 9239})
                return SimpleNamespace(destroy=Mock())

            module = SimpleNamespace(settings={}, start=Mock())
            with patch.dict("sys.modules", {"webview": module}), \
                    patch("codeviz.ui.desktop.create_window", side_effect=create_window), \
                    patch("socket.create_connection", side_effect=AssertionError("No provider network")):
                assistant_smoke_host.main(["--port", "9239", "--output", str(output)])
            exit_report = json.loads((output / "host-exit.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_report["sends"], 0)
            self.assertTrue(exit_report["runtimeShutdown"])


if __name__ == "__main__":
    unittest.main()
