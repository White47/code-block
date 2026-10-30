"""Local conversation controls use the paired runtime, not the project queue."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.assistant.service import _AssistantRuntime
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AppController
from codeviz.ui.web_bridge import DesktopBridge


class ProjectNativeTests(unittest.TestCase):
    def test_explicit_grant_and_revoke_use_saved_analysis_without_source_reads_or_sdk(self):
        runtime = _AssistantRuntime()
        self.addCleanup(runtime.shutdown)
        with TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            cache = CacheStore(Path(directory) / "cache")
            controller = AppController(cache, assistant_publication_mirror=runtime)
            bridge = DesktopBridge(controller=controller, cache_store=cache)
            bridge._bind_assistant_runtime(runtime)
            controller.open_project(root)
            controller.analyze()
            ctx = controller.capture_assistant_context()
            with patch.object(bridge, "_invoke", side_effect=AssertionError("No project queue")), \
                    patch.object(Path, "open", side_effect=AssertionError("No source reads")), \
                    patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("No SDK")):
                configured = bridge.assistant_configure({"baseUrl": "https://provider.invalid/v1",
                    "model": "probe-model", "key": "offline-test-only", "enabled": True})
                self.assertTrue(configured["ok"])
                granted = bridge.assistant_grant_consent({"projectEpoch": ctx.project_epoch,
                    "analysisGeneration": ctx.analysis_generation, "configRevision": 1,
                    "allowedFiles": ["app.py"], "confirmed": True})
                self.assertTrue(granted["ok"], granted)
                state = bridge.assistant_get_state({"projectEpoch": ctx.project_epoch})
                self.assertTrue(state["ok"], state)
                self.assertEqual(state["data"]["grant"], granted["data"])
                revoked = bridge.assistant_revoke_consent({"projectEpoch": ctx.project_epoch})
                self.assertTrue(revoked["ok"], revoked)
                state = bridge.assistant_get_state({"projectEpoch": ctx.project_epoch})
                self.assertTrue(state["ok"], state)
                self.assertIsNone(state["data"]["grant"])
                self.assertIsNone(runtime._engine)

    def test_local_new_clear_and_restore_work_without_configuration_or_sdk(self):
        runtime = _AssistantRuntime()
        self.addCleanup(runtime.shutdown)
        with TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            cache = CacheStore(Path(directory) / "cache")
            controller = AppController(cache, assistant_publication_mirror=runtime)
            bridge = DesktopBridge(controller=controller, cache_store=cache)
            bridge._bind_assistant_runtime(runtime)
            controller.open_project(root)
            epoch = controller.capture_assistant_context().project_epoch
            with patch.object(bridge, "_invoke", side_effect=AssertionError("No project queue")), \
                    patch.object(Path, "open", side_effect=AssertionError("No source reads")), \
                    patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("No SDK")), \
                    patch("codeviz.ui.web_bridge.logger.exception") as logger:
                created = bridge.assistant_new_conversation({"projectEpoch": epoch})
                self.assertTrue(created["ok"], created)
                conversation = created["data"]["conversationId"]
                cleared = bridge.assistant_clear_conversation({"projectEpoch": epoch,
                                                               "conversationId": conversation})
                self.assertTrue(cleared["ok"], cleared)
                self.assertEqual(cleared["data"], {"cleared": True, "conversationRevision": 1})
                restored = bridge.assistant_get_state({"projectEpoch": epoch})
                self.assertTrue(restored["ok"], restored)
                state = restored["data"]
                self.assertEqual(set(state), {"projectEpoch", "analysisGeneration", "configRevision",
                                              "conversations", "grant", "activeJobs"})
                self.assertEqual(state["projectEpoch"], epoch)
                self.assertTrue(state["analysisGeneration"] is None or state["analysisGeneration"] >= 1)
                self.assertEqual(state["configRevision"], 0)
                self.assertEqual(state["conversations"], [{"conversationId": conversation,
                                                         "conversationRevision": 1, "messages": []}])
                self.assertIsNone(state["grant"])
                self.assertEqual(state["activeJobs"], [])
                self.assertIsNone(runtime._engine, "Local controls must not launch workers")
                foreign = bridge.assistant_get_state({"projectEpoch": "foreign-project"})
                self.assertFalse(foreign["ok"])
                rejected = bridge.assistant_get_state({"projectEpoch": epoch, "sourceText": "not permitted"})
                self.assertEqual(rejected["error"]["code"], "AI_INVALID_INPUT")
                logger.assert_not_called()
