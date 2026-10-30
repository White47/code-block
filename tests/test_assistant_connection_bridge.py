"""Native connection wiring is explicit, queue-free, and errors remain fixed."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.assistant.bridge_requests import (
    CancelRequest,
    ConnectionRequest,
    PollRequest,
)
from codeviz.assistant.contracts import JobStatus
from codeviz.assistant.jobs import JobSnapshot
from codeviz.assistant.poll_public import (
    PollIdentity,
    PollState,
    cancel_receipt_to_public,
    poll_to_public,
)
from codeviz.assistant.service import ServiceError, _AssistantRuntime
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AppController
from codeviz.ui.web_bridge import DesktopBridge


class BridgeRuntime(_AssistantRuntime):
    def test_connection(self, request):
        assert type(request) is ConnectionRequest
        return {"jobId": "connection", "status": "Queued", "kind": "connection",
                "scope": None, "conversationId": None}

    @staticmethod
    def state():
        return PollState(PollIdentity("connection", "connection", None, None, None, None),
                         JobSnapshot("connection", JobStatus.RUNNING, False, False, False, False),
                         1, None, 1)

    def poll(self, request):
        assert type(request) is PollRequest
        return poll_to_public(self.state(), after_seq=request.after_seq)

    def cancel(self, request):
        assert type(request) is CancelRequest
        if request.job_id == "missing":
            raise ServiceError("AI_JOB_NOT_FOUND")
        return cancel_receipt_to_public(self.state())


class ConnectionBridgeTests(unittest.TestCase):
    def test_bound_runtime_receives_only_parsed_connection_requests_without_project_queue(self):
        with TemporaryDirectory() as directory:
            cache = CacheStore(Path(directory))
            runtime = BridgeRuntime()
            controller = AppController(cache, assistant_publication_mirror=runtime)
            bridge = DesktopBridge(controller=controller, cache_store=cache)
            bridge._bind_assistant_runtime(runtime)
            with patch.object(bridge, "_invoke", side_effect=AssertionError("No project queue")), \
                    patch.object(Path, "open", side_effect=AssertionError("No file work")), \
                    patch("codeviz.ui.web_bridge.logger.exception") as logger:
                result = bridge.assistant_test_connection({"configRevision": 1, "requestId": "probe"})
                self.assertTrue(result["ok"])
                self.assertIsNone(result["data"]["scope"])
                self.assertTrue(bridge.assistant_poll({"jobId": "connection", "projectEpoch": None,
                                                     "afterSeq": 0})["ok"])
                self.assertTrue(bridge.assistant_cancel({"jobId": "connection", "projectEpoch": None})["ok"])
                rejected = bridge.assistant_test_connection({"configRevision": 1, "requestId": "probe",
                                                             "sourceText": "not permitted"})
                self.assertEqual(rejected["error"]["code"], "AI_INVALID_INPUT")
                missing = bridge.assistant_cancel({"jobId": "missing", "projectEpoch": None})
                self.assertEqual(missing["error"]["code"], "AI_RESULT_UNAVAILABLE")
                logger.assert_not_called()
