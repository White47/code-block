"""C7 snapshot projection never serializes internal jobs or retrieved source."""

import importlib
import importlib.util
import json
import unittest
from dataclasses import replace

from codeviz.assistant.budget import BudgetUsage
from codeviz.assistant.contracts import JobStatus
from codeviz.assistant.jobs import JobSnapshot
from codeviz.assistant.response import Answer, ModelClaim, Section


class AssistantPublicPollTests(unittest.TestCase):
    def test_delivery_is_bounded_and_cancel_receipt_needs_no_answer(self):
        api = self.api()
        self.assertTrue(callable(getattr(api, "poll_to_public", None)), "Missing C7 delivery projection")
        state = replace(self.state(), seq=40)
        events = tuple(api.DisplayEvent(i, "display_text", {"text": "x" * 7000}) for i in range(1, 41))
        poll = api.poll_to_public(state, after_seq=0, events=events)
        self.assertLessEqual(len(poll["events"]), 32)
        self.assertLessEqual(len(json.dumps(poll["events"], separators=(",", ":")).encode()), 64 * 1024)
        self.assertLessEqual(len(json.dumps(poll, separators=(",", ":")).encode()), 160 * 1024)
        self.assertEqual(poll["events"][0]["projectEpoch"], "epoch")
        reset = api.poll_to_public(state, after_seq=0, reset=True)
        self.assertEqual(reset["events"], [])
        self.assertTrue(reset["reset"])
        completed_without_answer = self.state(status=JobStatus.COMPLETED)
        receipt = api.cancel_receipt_to_public(completed_without_answer)
        self.assertNotIn("answer", receipt)
        self.assertNotIn("usage", receipt)
        self.assertEqual(receipt["seq"], 2)

    def test_delivery_rejects_tools_extra_payload_and_invalid_cursors(self):
        api = self.api()
        self.assertTrue(callable(getattr(api, "poll_to_public", None)), "Missing C7 delivery projection")
        state = self.state()
        for after, events in (
            (True, ()), (3, ()),
            (0, (api.DisplayEvent(1, "tool", {"sourceText": "forbidden"}),)),
            (0, (api.DisplayEvent(1, "status", {"status": "Running", "sourceText": "forbidden"}),)),
            (0, (api.DisplayEvent(2, "status", {"status": "Running"}),
                 api.DisplayEvent(1, "status", {"status": "Running"}))),
        ):
            with self.subTest(after=after), self.assertRaises(api.PollProjectionError):
                api.poll_to_public(state, after_seq=after, events=events)
        navigation = replace(self.state("navigation"), evidence_id="evidence", action="source")
        with self.assertRaises(api.PollProjectionError):
            api.poll_to_public(navigation, after_seq=0,
                               events=(api.DisplayEvent(1, "display_text", {"text": "forbidden"}),))

    def api(self):
        name = "codeviz.assistant.poll_public"
        self.assertIsNotNone(importlib.util.find_spec(name), "Missing C7 snapshot projection")
        return importlib.import_module(name)

    def state(self, kind="explanation", status=JobStatus.RUNNING):
        api = self.api()
        identity = api.PollIdentity(kind, "job", None if kind == "connection" else "epoch",
                                   None if kind == "connection" else 1,
                                   None if kind == "connection" else "chat",
                                   None if kind == "connection" else 0)
        snapshot = JobSnapshot("job", status, False, False,
                               status not in (JobStatus.QUEUED, JobStatus.RUNNING), False)
        return api.PollState(identity, snapshot, 1, None if kind == "connection" else 1, 2)

    def test_explanation_is_closed_and_only_completed_answer_is_actionable(self):
        api = self.api()
        claim = ModelClaim("Unable to confirm this behavior.", "unknown", (), None)
        answer = Answer(tuple(Section(kind, (claim,) if kind == "summary" else ())
                              for kind in ("summary", "entry", "steps", "branches", "relations",
                                           "evidence", "unknowns")), (), (), False, (), "2026-10-18T00:00:00Z")
        state = replace(self.state(status=JobStatus.COMPLETED), answer=answer,
                        usage=BudgetUsage(files=("private/app.py",), source_characters=8))
        public = api.snapshot_to_public(state)
        self.assertEqual(set(public), {
            "kind", "jobId", "projectEpoch", "analysisGeneration", "conversationId", "conversationRevision",
            "status", "configRevision", "consentRevision", "cancelRequested", "cancelAccepted", "cancellation",
            "dispatchClosed", "resources", "resourcesReleased", "issue", "displayText", "usage", "answer",
        })
        self.assertEqual(public["resources"], "closing")
        self.assertEqual(public["answer"]["sections"][0]["claims"][0]["basis"], "unknown")
        self.assertNotIn("private/app.py", json.dumps(public))
        stale = replace(state, lifecycle=replace(state.lifecycle, status=JobStatus.STALE))
        self.assertIsNone(api.snapshot_to_public(stale)["answer"])
        self.assertEqual(api.snapshot_to_public(state)["usage"]["observed"]["basis"], "unknown")

    def test_connection_and_navigation_never_emit_explanation_fields(self):
        api = self.api()
        connection = api.snapshot_to_public(replace(self.state("connection", JobStatus.COMPLETED),
                                                    connection_supported=True))
        self.assertEqual(connection["connectionResult"], {
            "toolSupported": True, "structuredOutputSupported": True})
        self.assertIsNone(connection["projectEpoch"])
        self.assertNotIn("displayText", connection)
        self.assertNotIn("answer", connection)
        navigation = api.snapshot_to_public(replace(self.state("navigation"),
                                                   evidence_id="evidence", action="source"))
        self.assertIsNone(navigation["target"])
        self.assertNotIn("usage", navigation)

    def test_inconsistent_lifecycle_cross_kind_fields_and_missing_result_fail_closed(self):
        api = self.api()
        state = self.state()
        for malformed in (
            replace(state, config_revision=True),
            replace(state, lifecycle=replace(state.lifecycle, cancel_accepted=True)),
            self.state(status=JobStatus.COMPLETED),
            replace(self.state("connection"), display_text="source must not pass here"),
            replace(self.state("navigation"), evidence_id="evidence", action="source", display_text="forbidden"),
            replace(state, display_text="\ud800"),
        ):
            with self.subTest(kind=malformed.identity.kind), self.assertRaises(api.PollProjectionError) as caught:
                api.snapshot_to_public(malformed)
            self.assertIsNone(caught.exception.__context__)
            self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")

