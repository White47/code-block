"""Regression tests for bounded native-harness diagnostics and cleanup."""

import io
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError

from scripts import verify_assistant_native as verifier


class _RunningProcess:
    def poll(self):
        return None


class _StubbornProcess:
    pid = 4321

    def __init__(self):
        self.calls = []
        self._running = True

    def poll(self):
        return None if self._running else 0

    def terminate(self):
        self.calls.append("terminate")

    def kill(self):
        self.calls.append("kill")
        self._running = False

    def wait(self, timeout):
        self.calls.append(("wait", timeout))
        if self._running:
            raise subprocess.TimeoutExpired("owned-host", timeout)
        return 0


class _Page:
    def __init__(self, url):
        self.url = url


class _Context:
    def __init__(self, *pages):
        self.pages = list(pages)


class _Browser:
    def __init__(self, *contexts):
        self.contexts = list(contexts)


class AssistantNativeHarnessDiagnosticTests(unittest.TestCase):
    def test_reports_bounded_cdp_probe_error_before_host_exit(self):
        self.assertIn("observe_error", verifier._wait_for_native_cdp_target.__code__.co_varnames)

        class Process:
            exited = False

            def poll(self):
                return 1 if self.exited else None

        process = Process()
        observed = []

        def refused(_url, timeout):
            self.assertEqual(timeout, 2)
            process.exited = True
            raise URLError(ConnectionRefusedError("synthetic refusal"))

        with self.assertRaisesRegex(RuntimeError, "exited before page target"):
            verifier._wait_for_native_cdp_target(
                43123, process, timeout=1, opener=refused,
                sleep=lambda _seconds: None, observe_error=observed.append)

        self.assertEqual(observed, ["URLError/ConnectionRefusedError"])

    def test_waits_for_owned_loopback_page_target_before_cdp_attach(self):
        self.assertTrue(hasattr(verifier, "_wait_for_native_cdp_target"),
                        "verifier must gate attach on the real page target")
        replies = iter([
            [{"id": "early", "type": "page", "title": "", "url": "about:blank"}],
            [{"id": "owned", "type": "page", "title": "CodeViz",
              "url": "http://127.0.0.1:51234/"}],
        ])
        opened = []
        observed = []

        def open_targets(url, timeout):
            opened.append((url, timeout))
            return io.BytesIO(json.dumps(next(replies)).encode("utf-8"))

        self.assertTrue(verifier._owned_page_url(
            "http://127.0.0.1:51234/", 43123),
            "the owned CDP port and the local asset-server port are independent")
        target = verifier._wait_for_native_cdp_target(
            43123, _RunningProcess(), timeout=1, opener=open_targets,
            sleep=lambda _seconds: None, observe=observed.append)

        self.assertEqual(target["id"], "owned")
        self.assertEqual(len(opened), 2)
        self.assertEqual(observed[0], [
            {"id": "early", "type": "page", "title": "", "url": "about:blank"}
        ])

    def test_selects_owned_page_across_all_contexts_not_first_about_blank(self):
        self.assertTrue(hasattr(verifier, "_wait_for_native_page"),
                        "verifier must select the owned page across CDP contexts")
        early = _Page("about:blank")
        owned = _Page("http://127.0.0.1:51234/")
        browser = _Browser(_Context(early), _Context(owned))

        self.assertTrue(verifier._owned_page_url(owned.url, 43123),
                        "selection must accept the host-owned loopback app port")
        selected = verifier._wait_for_native_page(
            browser, 43123, _RunningProcess(), timeout=1,
            sleep=lambda _seconds: None)

        self.assertIs(selected, owned)

    def test_stage_checkpoint_replaces_a_stale_report_before_risky_attach(self):
        self.assertTrue(hasattr(verifier, "_checkpoint"),
                        "verifier must persist the current run before attach")
        with TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "report.json").write_text(
                json.dumps({"pid": 28640, "stage": "old run"}), encoding="utf-8")
            report = {"status": "failed", "pid": 36748}

            verifier._checkpoint(output, report, "wait native CDP target")

            saved = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["pid"], 36748)
            self.assertEqual(saved["stage"], "wait native CDP target")

    def test_cleanup_escalates_from_terminate_to_kill_with_bounded_waits(self):
        self.assertTrue(hasattr(verifier, "_cleanup_process"),
                        "verifier must bound cleanup of its owned host")
        process = _StubbornProcess()
        report = {}

        verifier._cleanup_process(process, report, terminate_timeout=0.25,
                                  kill_timeout=0.5)

        self.assertEqual(process.calls, [
            "terminate", ("wait", 0.25), "kill", ("wait", 0.5)
        ])
        self.assertTrue(report["forcedCleanup"])
        self.assertTrue(report["cleanupEscalated"])


if __name__ == "__main__":
    unittest.main()
