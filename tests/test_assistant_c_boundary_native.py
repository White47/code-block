"""C-boundary recovery through the real bridge with an offline failing provider."""

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from test_assistant_explanation_native import OfflineProvider

from codeviz.assistant.provider import ProviderEvent
from codeviz.assistant.service import _AssistantRuntime
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AppController
from codeviz.ui.web_bridge import DesktopBridge


class InterruptedDisplayProvider(OfflineProvider):
    display = 'Checking saved facts... <img src="https://invalid.invalid/a">'

    async def dispatch(self, prepared):
        self.sends += 1
        yield ProviderEvent("text", self.display.encode("utf-8"))
        raise RuntimeError("Synthetic offline interruption")


class BoundaryRecoveryNativeTests(unittest.TestCase):
    def test_reopen_restores_failed_provisional_after_resources_are_released(self):
        provider = InterruptedDisplayProvider()
        runtime = _AssistantRuntime(provider_factory=lambda _config, _key: provider)
        self.addCleanup(runtime.shutdown)
        with TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            cache = CacheStore(Path(directory) / "cache")
            controller = AppController(cache, assistant_publication_mirror=runtime)
            bridge = DesktopBridge(controller=controller, cache_store=cache)
            bridge._bind_assistant_runtime(runtime)
            controller.open_project(root)
            controller.analyze()
            context = controller.capture_assistant_context()
            configured = bridge.assistant_configure({"baseUrl": "https://provider.invalid/v1",
                "model": "probe-model", "key": "offline-test-only", "enabled": True})
            self.assertTrue(configured["ok"], configured)
            created = bridge.assistant_new_conversation({"projectEpoch": context.project_epoch})
            self.assertTrue(created["ok"], created)
            conversation = created["data"]["conversationId"]
            grant = bridge.assistant_grant_consent({"projectEpoch": context.project_epoch,
                "analysisGeneration": context.analysis_generation, "configRevision": 1,
                "allowedFiles": [], "confirmed": True})
            self.assertTrue(grant["ok"], grant)
            started = bridge.assistant_start({"projectEpoch": context.project_epoch,
                "analysisGeneration": context.analysis_generation, "conversationId": conversation,
                "requestId": "interrupted-display", "configRevision": 1,
                "consentRevision": grant["data"]["consentRevision"],
                "question": "What can you confirm?", "context": {"kind": "question"}})
            self.assertTrue(started["ok"], started)
            job_id = started["data"]["jobId"]
            deadline, pause = time.monotonic() + 3, Event()
            state = None
            while time.monotonic() < deadline:
                polled = bridge.assistant_poll({"jobId": job_id,
                    "projectEpoch": context.project_epoch, "afterSeq": 0})
                self.assertTrue(polled["ok"], polled)
                state = polled["data"]
                if state["resourcesReleased"]:
                    break
                pause.wait(0.005)
            self.assertIsNotNone(state)
            self.assertTrue(state["resourcesReleased"])
            self.assertEqual(state["status"], "Failed")
            self.assertEqual(state["displayText"], provider.display)
            self.assertIsNone(state["answer"])
            restored = bridge.assistant_get_state({"projectEpoch": context.project_epoch})
            self.assertTrue(restored["ok"], restored)
            messages = restored["data"]["conversations"][0]["messages"]
            self.assertEqual([message["type"] for message in messages], ["question", "provisional"])
            self.assertEqual(messages[1]["text"], provider.display)
            self.assertTrue(all(message["jobId"] == job_id and message["seq"] == state["seq"]
                                for message in messages))
            self.assertEqual(restored["data"]["activeJobs"], [])
            self.assertEqual(provider.sends, 1, "Restoring display must never resend a request")
