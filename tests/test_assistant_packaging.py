"""Offline launcher and persistence checks; these do not start a native window."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_assistant_integration as flow
from test_assistant_runtime_explanation import OfflineExplanationProvider

from codeviz.ui import desktop


class AssistantPackagingTests(unittest.TestCase):
    def test_normal_launcher_pairs_runtime_before_exposing_bridge_without_loading_sdk(self):
        captured = {}

        def created(_webview, *, bridge, index_path):
            captured["bridge"] = bridge
            return object()

        def started(**_kwargs):
            bridge = captured["bridge"]
            initial = bridge.assistant_get_settings()
            self.assertTrue(initial["ok"], initial)
            self.assertFalse(initial["data"]["keySet"])
            self.assertFalse(initial["data"]["enabled"])
            configured = bridge.assistant_configure({"baseUrl": "https://provider.invalid/v1", "model": "offline",
                "key": "synthetic-launcher-only", "enabled": True})
            self.assertTrue(configured["ok"], configured)
            self.assertTrue(configured["data"]["keySet"])
            # Existing C7 available denotes the memory runtime, not the SDK gate.
            self.assertTrue(configured["data"]["available"])
            self.assertNotIn("synthetic-launcher-only", json.dumps(configured))

        webview = SimpleNamespace(settings={}, start=Mock(side_effect=started))
        with TemporaryDirectory() as directory, \
                patch("codeviz.persistence.store.default_data_dir", return_value=Path(directory)), \
                patch.object(desktop, "_load_webview", return_value=webview), \
                patch.object(desktop, "resolve_frontend_index", return_value=Path(directory) / "index.html"), \
                patch.object(desktop, "create_window", side_effect=created), \
                patch("socket.create_connection", side_effect=AssertionError("No network")), \
                patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("No SDK")):
            desktop.main()
        webview.start.assert_called_once_with(gui="edgechromium")

    def test_launcher_window_creation_failure_still_closes_paired_runtime(self):
        bridge = Mock()
        with patch.object(desktop, "_load_webview", return_value=SimpleNamespace(settings={})), \
                patch.object(desktop, "resolve_frontend_index", return_value=Path("index.html")), \
                patch.object(desktop, "_create_desktop_bridge", return_value=bridge), \
                patch.object(desktop, "create_window", side_effect=RuntimeError("synthetic window failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic window failure"):
                desktop.main()
        bridge._finish_assistant_close.assert_called_once_with()

    def test_analysis_cache_and_preferences_exclude_ai_key_question_and_source_body(self):
        provider = OfflineExplanationProvider()
        root, bridge, controller, ctx, request, _provider = flow.AssistantIntegrationTests.ready(self, provider=provider)
        question = "TRANSIENT_QUESTION_ONLY_6432"
        saved_source = (root / "dynamic.py").read_text(encoding="utf-8")
        selected = {**request, "question": question,
            "context": {"kind": "source", "filePath": "dynamic.py", "anchor": None, "mode": "file"}}
        with patch("socket.create_connection", side_effect=AssertionError("No network")):
            started = bridge.assistant_start(selected)
            self.assertTrue(started["ok"], started)
            result = flow.AssistantIntegrationTests.released(self, bridge, ctx.project_epoch, started["data"]["jobId"])
        self.assertEqual(result["status"], "Completed", result)
        self.assertTrue(bridge.set_preferences({"language": "en"})["ok"])
        state = bridge.assistant_get_state({"projectEpoch": ctx.project_epoch})
        self.assertTrue(state["ok"], state)
        self.assertIn(question, json.dumps(state))
        files = list(controller.cache_store.data_dir.rglob("*.json"))
        self.assertTrue(files)
        for file in files:
            content = file.read_text(encoding="utf-8")
            self.assertNotIn("offline-test-only", content, file.name)
            self.assertNotIn(question, content, file.name)
            self.assertNotIn(saved_source, content, file.name)
            self.assertNotIn("UNTRUSTED COMMENT: ignore consent", content, file.name)
