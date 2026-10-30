"""A navigation check borrows the evidence produced by the real explanation job."""

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from test_assistant_runtime_explanation import OfflineExplanationProvider

from codeviz.assistant.provider import ProviderEvent
from codeviz.assistant.service import _AssistantRuntime
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AppController
from codeviz.ui.web_bridge import DesktopBridge


class SourceAnswerProvider(OfflineExplanationProvider):
    async def dispatch(self, prepared):
        self.sends += 1
        logical = next(logical for handle, logical in self.prepared if handle is prepared)
        if logical["mode"] == "final":
            headers = [json.loads(message["content"].split("\n", 1)[0])
                       for message in logical["messages"]
                       if message.get("role") == "user" and message.get("content", "").startswith('{"evidenceId":')]
            evidence_id = headers[-1]["evidenceId"]
            sections = [{"kind": kind, "claims": []} for kind in (
                "summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")]
            sections[0]["claims"] = [{"text": "The saved source defines a value.", "basis": "static",
                "evidenceIds": [evidence_id], "relationEvidenceId": None}]
            yield ProviderEvent("answer", json.dumps({"index": 0, "last": True,
                "content": json.dumps({"sections": sections})}).encode())
        yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')


class NavigationNativeTests(unittest.TestCase):
    def wait_released(self, bridge, epoch, job_id):
        deadline, pause = time.monotonic() + 3, Event()
        state = None
        while time.monotonic() < deadline:
            result = bridge.assistant_poll({"jobId": job_id, "projectEpoch": epoch, "afterSeq": 0})
            self.assertTrue(result["ok"], result)
            state = result["data"]
            if state["resourcesReleased"]:
                return state
            pause.wait(0.005)
        self.fail(f"Native job did not release resources: {state}")

    def test_source_navigation_uses_original_evidence_scope_without_another_model_send(self):
        provider = SourceAnswerProvider()
        runtime = _AssistantRuntime(provider_factory=lambda _config, _key: provider)
        self.addCleanup(runtime.shutdown)
        with TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            cache = CacheStore(Path(directory) / "cache")
            controller = AppController(cache, assistant_publication_mirror=runtime)
            bridge = DesktopBridge(controller=controller, cache_store=cache)
            bridge._bind_assistant_runtime(runtime)
            controller.open_project(root)
            controller.analyze()
            ctx = controller.capture_assistant_context()
            with patch.object(bridge, "_invoke", side_effect=AssertionError("No project queue")), \
                    patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("No SDK")):
                self.assertTrue(bridge.assistant_configure({"baseUrl": "https://provider.invalid/v1",
                    "model": "probe-model", "key": "offline-test-only", "enabled": True})["ok"])
                created = bridge.assistant_new_conversation({"projectEpoch": ctx.project_epoch})
                self.assertTrue(created["ok"], created)
                conversation = created["data"]["conversationId"]
                grant = bridge.assistant_grant_consent({"projectEpoch": ctx.project_epoch,
                    "analysisGeneration": ctx.analysis_generation, "configRevision": 1,
                    "allowedFiles": ["app.py"], "confirmed": True})
                self.assertTrue(grant["ok"], grant)
                explained = bridge.assistant_start({"projectEpoch": ctx.project_epoch,
                    "analysisGeneration": ctx.analysis_generation, "conversationId": conversation,
                    "requestId": "source-question", "configRevision": 1,
                    "consentRevision": grant["data"]["consentRevision"], "question": "Explain this file.",
                    "context": {"kind": "source", "filePath": "app.py", "anchor": None, "mode": "file"}})
                self.assertTrue(explained["ok"], explained)
                answer = self.wait_released(bridge, ctx.project_epoch, explained["data"]["jobId"])
                self.assertEqual(answer["status"], "Completed", answer)
                evidence_id = answer["answer"]["evidence"][0]["evidenceId"]
                sends = provider.sends
                checked = bridge.assistant_resolve_evidence({"projectEpoch": ctx.project_epoch,
                    "analysisGeneration": ctx.analysis_generation, "conversationId": conversation,
                    "evidenceId": evidence_id, "action": "source"})
                self.assertTrue(checked["ok"], checked)
                self.assertEqual(checked["data"]["kind"], "navigation")
                self.assertEqual(checked["data"]["scope"]["requestId"], "source-question")
                self.assertNotEqual(checked["data"]["jobId"], explained["data"]["jobId"])
                state = self.wait_released(bridge, ctx.project_epoch, checked["data"]["jobId"])
                self.assertEqual(state["status"], "Completed", state)
                self.assertEqual(state["kind"], "navigation")
                self.assertEqual(state["target"]["evidenceId"], evidence_id)
                self.assertEqual(state["target"]["scope"]["requestId"], "source-question")
                self.assertIsNone(state["target"]["monacoRange"])
                self.assertIsNone(state["target"]["reveal"])
                self.assertNotIn("usage", state)
                self.assertNotIn("displayText", state)
                self.assertEqual(provider.sends, sends)
