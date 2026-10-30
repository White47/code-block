"""Exercise the real private runtime through the native bridge without network."""

import inspect
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from test_assistant_connection_runner import FakeProvider, _valid_events

from codeviz.assistant.service import _AssistantRuntime
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AppController
from codeviz.ui.web_bridge import DesktopBridge


class ConnectionNativeTests(unittest.TestCase):
    def test_explicit_probe_completes_through_native_bridge_without_project_work(self):
        self.assertIn("provider_factory", inspect.signature(_AssistantRuntime).parameters,
                      "Missing trusted runtime composition")
        provider = FakeProvider(_valid_events(), [])
        runtime = _AssistantRuntime(provider_factory=lambda _config, _key: provider)
        self.addCleanup(runtime.shutdown)
        with TemporaryDirectory() as directory:
            cache = CacheStore(Path(directory))
            controller = AppController(cache, assistant_publication_mirror=runtime)
            bridge = DesktopBridge(controller=controller, cache_store=cache)
            bridge._bind_assistant_runtime(runtime)
            with patch.object(bridge, "_invoke", side_effect=AssertionError("No project queue")), \
                    patch.object(Path, "open", side_effect=AssertionError("No source read")), \
                    patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("No SDK")), \
                    patch("codeviz.ui.web_bridge.logger.exception") as logger:
                settings = bridge.assistant_configure({"baseUrl": "https://provider.invalid/v1",
                    "model": "probe-model", "key": "offline-test-only", "enabled": True})
                self.assertTrue(settings["ok"])
                self.assertEqual(provider.log, [])
                receipt = bridge.assistant_test_connection({"configRevision": 1, "requestId": "native-probe"})
                self.assertTrue(receipt["ok"], receipt)
                admitted = receipt["data"]
                self.assertEqual(set(admitted), {"jobId", "status", "kind", "scope", "conversationId"})
                self.assertEqual(admitted["kind"], "connection")
                self.assertIsNone(admitted["scope"])
                self.assertIsNone(admitted["conversationId"])
                deadline = time.monotonic() + 3
                pause = Event()
                state = None
                while time.monotonic() < deadline:
                    response = bridge.assistant_poll({"jobId": admitted["jobId"],
                                                      "projectEpoch": None, "afterSeq": 0})
                    self.assertTrue(response["ok"], response)
                    state = response["data"]
                    if state["resourcesReleased"]:
                        break
                    pause.wait(0.005)
                self.assertIsNotNone(state)
                self.assertTrue(state["resourcesReleased"])
                self.assertEqual(state["status"], "Completed")
                self.assertEqual(state["connectionResult"], {
                    "toolSupported": True, "structuredOutputSupported": True})
                self.assertNotIn("answer", state)
                self.assertIsNone(state["projectEpoch"])
                replay = bridge.assistant_test_connection({"configRevision": 1, "requestId": "native-probe"})
                self.assertTrue(replay["ok"])
                self.assertEqual(replay["data"]["jobId"], admitted["jobId"])
                self.assertEqual(provider.log.count("dispatch:tools"), 1)
                self.assertEqual(provider.log.count("dispatch:final"), 1)
                self.assertNotIn("offline-test-only", repr(state))
                logger.assert_not_called()
