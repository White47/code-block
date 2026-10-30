"""A source-free explicit question traverses the actual private native runtime."""

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from assistant_service_fixtures import final_answer_event

from codeviz.assistant.provider import PreparedRequest, ProviderEvent
from codeviz.assistant.service import _AssistantRuntime
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AppController
from codeviz.ui.web_bridge import DesktopBridge


class OfflineProvider:
    def __init__(self):
        self.sends = 0
        self.modes = {}

    async def prepare(self, draft):
        logical = json.loads(draft.body)
        body = json.dumps({"model": "probe-model", "messages": logical["messages"]},
                          ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        preparation_id = f"offline-prepared-{len(self.modes)}"
        self.modes[preparation_id] = logical["mode"]
        return PreparedRequest(preparation_id, "POST",
            "https://provider.invalid:443/v1/chat/completions", body,
            draft.max_output_tokens, draft.deadline, len(body), (), (), True)

    async def dispatch(self, prepared):
        self.sends += 1
        if self.modes[prepared.preparation_id] in ("final", "repair"):
            yield final_answer_event()
        yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')

    async def cancel(self):
        pass

    async def aclose(self):
        pass


class ExplanationNativeTests(unittest.TestCase):
    def test_explicit_question_poll_restore_and_replay_use_the_same_job(self):
        provider = OfflineProvider()
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
            with patch.object(bridge, "_invoke", side_effect=AssertionError("No project queue")), \
                    patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("No SDK")), \
                    patch("codeviz.ui.web_bridge.logger.exception") as logger:
                self.assertTrue(bridge.assistant_configure({"baseUrl": "https://provider.invalid/v1",
                    "model": "probe-model", "key": "offline-test-only", "enabled": True})["ok"])
                created = bridge.assistant_new_conversation({"projectEpoch": context.project_epoch})
                self.assertTrue(created["ok"], created)
                conversation = created["data"]["conversationId"]
                grant = bridge.assistant_grant_consent({"projectEpoch": context.project_epoch,
                    "analysisGeneration": context.analysis_generation, "configRevision": 1,
                    "allowedFiles": [], "confirmed": True})
                self.assertTrue(grant["ok"], grant)
                self.assertEqual(provider.sends, 0)
                request = {"projectEpoch": context.project_epoch,
                    "analysisGeneration": context.analysis_generation, "conversationId": conversation,
                    "requestId": "explicit-question", "configRevision": 1,
                    "consentRevision": grant["data"]["consentRevision"],
                    "question": "What can you confirm?", "context": {"kind": "question"}}
                started = bridge.assistant_start(request)
                self.assertTrue(started["ok"], started)
                job_id = started["data"]["jobId"]
                deadline, pause = time.monotonic() + 3, Event()
                state = None
                while time.monotonic() < deadline:
                    result = bridge.assistant_poll({"jobId": job_id,
                        "projectEpoch": context.project_epoch, "afterSeq": 0})
                    self.assertTrue(result["ok"], result)
                    state = result["data"]
                    if state["resourcesReleased"]:
                        break
                    pause.wait(0.005)
                self.assertIsNotNone(state)
                self.assertTrue(state["resourcesReleased"])
                self.assertEqual(state["kind"], "explanation")
                self.assertEqual(state["status"], "Completed", state)
                self.assertIsNotNone(state["answer"])
                restored = bridge.assistant_get_state({"projectEpoch": context.project_epoch})
                self.assertTrue(restored["ok"], restored)
                messages = restored["data"]["conversations"][0]["messages"]
                self.assertEqual([message["type"] for message in messages], ["question", "validated_answer"])
                self.assertTrue(all(message["jobId"] == job_id and message["seq"] == state["seq"]
                                    for message in messages))
                self.assertEqual(messages[1]["answer"], state["answer"])
                replay = bridge.assistant_start(request)
                self.assertTrue(replay["ok"], replay)
                self.assertEqual(replay["data"]["jobId"], job_id)
                self.assertEqual(provider.sends, 2)
                logger.assert_not_called()
