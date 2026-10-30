"""Private composition keeps the base desktop gate closed and settings inert."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.assistant.service import _AssistantRuntime
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AppController
from codeviz.ui.web_bridge import DesktopBridge


class RuntimeBridgeTests(unittest.TestCase):
    def test_paired_settings_composition_bypasses_project_queue_and_optional_sdk(self):
        self.assertTrue(hasattr(DesktopBridge, "_bind_assistant_runtime"), "Missing private runtime binding")
        with TemporaryDirectory() as directory:
            cache = CacheStore(Path(directory))
            runtime = _AssistantRuntime()
            controller = AppController(cache, assistant_publication_mirror=runtime)
            bridge = DesktopBridge(controller=controller, cache_store=cache)
            bridge._bind_assistant_runtime(runtime)
            with patch.object(bridge, "_invoke", side_effect=AssertionError("No project queue")), \
                    patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("No SDK")), \
                    patch.object(Path, "open", side_effect=AssertionError("No file reads")), \
                    patch("codeviz.ui.web_bridge.logger.exception") as logger:
                result = bridge.assistant_configure({"baseUrl": "https://example.invalid/v1",
                    "model": "demo", "key": "test-only", "enabled": True})
                self.assertTrue(result["ok"])
                self.assertTrue(bridge.assistant_get_settings()["data"]["keySet"])
                self.assertNotIn("test-only", repr(result))
                failed = bridge.assistant_configure({"baseUrl": "https://other.invalid",
                    "model": "demo", "key": None, "enabled": True})
                self.assertEqual(failed["error"]["code"], "AI_INVALID_INPUT")
                self.assertEqual(bridge.assistant_get_settings()["data"]["configRevision"], 1)
                # Remaining jobs are not exposed before their integration gate.
                self.assertEqual(bridge.assistant_test_connection({"configRevision": 1,
                    "requestId": "test"})["error"]["code"], "AI_DEPENDENCY_MISSING")
                bridge._prepare_assistant_close()
                self.assertFalse(bridge.assistant_get_settings()["data"]["keySet"])
                bridge._finish_assistant_close()
                bridge._finish_assistant_close()
                logger.assert_not_called()

    def test_runtime_cannot_be_bound_to_an_unpaired_controller(self):
        self.assertTrue(hasattr(DesktopBridge, "_bind_assistant_runtime"), "Missing private runtime binding")
        with TemporaryDirectory() as directory:
            bridge = DesktopBridge(cache_store=CacheStore(Path(directory)))
            with self.assertRaises(ValueError):
                bridge._bind_assistant_runtime(_AssistantRuntime())
            self.assertFalse(bridge.assistant_get_settings()["data"]["available"])
