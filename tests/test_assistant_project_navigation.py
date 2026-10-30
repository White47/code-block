"""Main-workspace navigation is constrained by the original project answer."""

import json
import unittest
from dataclasses import replace
from threading import Event
from unittest.mock import patch

import test_assistant_project_retrieval as fixtures
from test_assistant_runtime_conversations import publish

from codeviz.assistant import service as service_module
from codeviz.assistant.bridge_requests import (
    BridgeRequestError,
    CancelRequest,
    ClearRequest,
    PollRequest,
    ProjectRequest,
    parse_resolve,
    parse_start,
)
from codeviz.assistant.provider import ProviderEvent
from codeviz.assistant.service import ServiceError


class ProjectNavigationProvider(fixtures.ProjectProvider):
    async def dispatch(self, prepared):
        async for event in super().dispatch(prepared):
            if event.kind == "answer":
                envelope = json.loads(event.payload)
                answer = json.loads(envelope["content"])
                relation_id = answer["sections"][4]["claims"][0]["relationEvidenceId"]
                messages = json.loads(prepared.body)["messages"]
                catalog = json.loads(next(line for line in messages[-1]["content"].splitlines()
                                         if line.startswith('{"receiptEvidenceIds":')))
                others = [value for value in catalog["receiptEvidenceIds"] if value != relation_id]
                self.uncited = others[-1]
                answer["sections"][0]["claims"] = [{"text": "An authorized representative was read.",
                    "basis": "static", "evidenceIds": [others[0]], "relationEvidenceId": None}]
                envelope["content"] = json.dumps(answer)
                event = ProviderEvent("answer", json.dumps(envelope).encode())
            yield event


class ProjectNavigationTests(unittest.TestCase):
    ready = fixtures.ProjectRetrievalTests.ready
    settled = fixtures.ProjectRetrievalTests.settled

    def project(self):
        _root, runtime, ctx, payload, provider = self.ready(provider=ProjectNavigationProvider())
        started = runtime.start(parse_start(payload), ctx)
        result = self.settled(runtime, ctx, started["jobId"])
        self.assertEqual(result["status"], "Completed", result.get("issue"))
        reference = runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["projectUnderstanding"]
        evidence = result["answer"]["sections"][4]["claims"][0]["relationEvidenceId"]
        resolve = {"projectEpoch": ctx.project_epoch, "analysisGeneration": ctx.analysis_generation,
            "conversationId": reference["conversationId"], "evidenceId": evidence, "action": "source",
            "understanding": {"artifactId": reference["artifactId"], "revision": reference["revision"],
                              "itemId": "relations:0"}}
        return runtime, ctx, payload, provider, resolve, result["answer"]

    def test_original_owner_works_after_chat_switch_and_checks_exact_item_membership(self):
        runtime, ctx, payload, provider, body, answer = self.project()
        other_chat = runtime.new_conversation(ProjectRequest(ctx.project_epoch), ctx)["conversationId"]
        sends = provider.sends
        try:
            request = parse_resolve(body)
        except BridgeRequestError:
            self.fail("Frozen understanding extension is not accepted")
        started = runtime.resolve_evidence(request, ctx)
        completed = self.settled(runtime, ctx, started["jobId"])
        self.assertEqual(completed["status"], "Completed", completed.get("issue"))
        self.assertEqual(completed["target"]["conversationId"], payload["conversationId"])
        self.assertEqual(completed["target"]["evidenceId"], body["evidenceId"])
        self.assertNotIn("understanding", completed)
        grouped = runtime.resolve_evidence(replace(request, action="graph"), ctx)
        refused_graph = self.settled(runtime, ctx, grouped["jobId"])
        self.assertEqual(refused_graph["status"], "Failed")
        self.assertIsNone(refused_graph["target"])
        root_body = {**body, "understanding": {**body["understanding"], "itemId": "project"}}
        for card in answer["evidence"]:
            started = runtime.resolve_evidence(parse_resolve({**root_body, "evidenceId": card["evidenceId"]}), ctx)
            self.assertEqual(self.settled(runtime, ctx, started["jobId"])["status"], "Completed")
        for invalid in (
            {**body, "conversationId": other_chat},
            {**body, "understanding": {**body["understanding"], "artifactId": "foreign"}},
            {**body, "understanding": {**body["understanding"], "revision": body["understanding"]["revision"] + 1}},
            {**body, "understanding": {**body["understanding"], "itemId": "summary:0"}},
            {**body, "understanding": {**body["understanding"], "itemId": "entry:0"}},
            {**root_body, "evidenceId": provider.uncited},
        ):
            with self.subTest(invalid=invalid["understanding"]["itemId"]):
                with self.assertRaises(ServiceError) as rejected:
                    runtime.resolve_evidence(parse_resolve(invalid), ctx)
                self.assertEqual(rejected.exception.code, "AI_EVIDENCE_INVALID")
        self.assertEqual(provider.sends, sends)

    def test_extension_is_closed_optional_non_null_and_has_canonical_item_ids(self):
        body = {"projectEpoch": "epoch", "analysisGeneration": 1, "conversationId": "owner",
            "evidenceId": "receipt", "action": "source"}
        self.assertIsNone(parse_resolve(body).understanding)
        binding = {"artifactId": "artifact", "revision": 1, "itemId": "project"}
        self.assertEqual(parse_resolve({**body, "understanding": binding}).understanding.item_id, "project")
        for value in (None, {}, {**binding, "extra": 1}, {**binding, "revision": True},
                      {**binding, "revision": 0}, {**binding, "revision": 2**53},
                      {**binding, "artifactId": "bad/id"}, {**binding, "itemId": "summary:00"},
                      {**binding, "itemId": "invented:0"}, {**binding, "itemId": "summary:+1"}):
            with self.subTest(value=value), self.assertRaises(BridgeRequestError):
                parse_resolve({**body, "understanding": value})

    def test_replay_identity_and_cancel_preserve_original_lease_until_work_releases(self):
        runtime, ctx, _payload, _provider, body, _answer = self.project()
        engine = runtime._engine
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        original = engine._check_navigation

        def blocked(job):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(job)

        with patch.object(engine, "_check_navigation", side_effect=blocked):
            request = parse_resolve(body)
            started = runtime.resolve_evidence(request, ctx)
            self.assertTrue(entered.wait(2))
            self.assertEqual(runtime.resolve_evidence(request, ctx)["jobId"], started["jobId"])
            for other in ({**body, "understanding": {**body["understanding"], "itemId": "project"}},
                          {key: value for key, value in body.items() if key != "understanding"}):
                with self.assertRaises(ServiceError) as busy:
                    runtime.resolve_evidence(parse_resolve(other), ctx)
                self.assertEqual(busy.exception.code, "AI_CAPACITY")
            cancelled = runtime.cancel(CancelRequest(started["jobId"], ctx.project_epoch))
            self.assertTrue(cancelled["cancelRequested"])
            self.assertFalse(cancelled["resourcesReleased"])
            self.assertIsNotNone(engine._navigation_jobs[started["jobId"]].lease)
            release.set()
            result = self.settled(runtime, ctx, started["jobId"])
        self.assertEqual(result["status"], "Cancelled")
        self.assertTrue(result["cancelAccepted"])
        self.assertIsNone(result["target"])

    def test_replacement_after_real_freshness_cannot_build_an_old_target(self):
        runtime, ctx, payload, _provider, body, _answer = self.project()
        entered, release, finished = Event(), Event(), Event()
        self.addCleanup(release.set)
        freshness = service_module.verify_snapshot_freshness
        check = runtime._engine._check_navigation

        def paused_freshness(*args, **kwargs):
            report = freshness(*args, **kwargs)
            if kwargs["scope"].request_id == payload["requestId"]:
                entered.set()
                self.assertTrue(release.wait(3))
            return report

        def checked(job):
            try:
                return check(job)
            finally:
                finished.set()

        with patch.object(service_module, "verify_snapshot_freshness", side_effect=paused_freshness), \
                patch.object(runtime._engine, "_check_navigation", side_effect=checked), \
                patch.object(service_module, "build_navigation_target",
                             wraps=service_module.build_navigation_target) as build:
            started = runtime.resolve_evidence(parse_resolve(body), ctx)
            self.assertTrue(entered.wait(2))
            replacement = runtime.start(parse_start({**payload, "requestId": "replace-during-freshness"}), ctx)
            self.assertEqual(self.settled(runtime, ctx, replacement["jobId"])["status"], "Completed")
            release.set()
            self.assertTrue(finished.wait(2))
            self.assertEqual(build.call_count, 0)
            result = self.settled(runtime, ctx, started["jobId"])
        self.assertNotEqual(result["status"], "Completed")
        self.assertIsNone(result["target"])

    def test_every_completed_poll_rejects_replacement_and_owner_invalidation(self):
        for change in ("replace", "clear", "revoke", "publication", "analysis"):
            with self.subTest(change=change):
                runtime, ctx, payload, provider, body, _answer = self.project()
                started = runtime.resolve_evidence(parse_resolve(body), ctx)
                self.assertEqual(self.settled(runtime, ctx, started["jobId"])["status"], "Completed")
                if change == "replace":
                    newer = runtime.start(parse_start({**payload, "requestId": "new-project-version"}), ctx)
                    self.assertEqual(self.settled(runtime, ctx, newer["jobId"])["status"], "Completed")
                elif change == "clear":
                    runtime.clear_conversation(ClearRequest(ctx.project_epoch, payload["conversationId"]), ctx)
                elif change == "revoke":
                    runtime.revoke_consent(ctx.project_epoch)
                else:
                    ctx = replace(ctx, publication_revision=ctx.publication_revision + 1,
                        analysis_generation=ctx.analysis_generation + (change == "analysis"))
                    publish(runtime, ctx)
                sends = provider.sends
                polled = runtime.poll(PollRequest(started["jobId"], ctx.project_epoch, 0))
                self.assertNotEqual(polled["status"], "Completed")
                self.assertIsNone(polled["target"])
                if change != "publication":
                    with self.assertRaises(ServiceError):
                        runtime.resolve_evidence(parse_resolve(body), ctx)
                else:
                    # The old short navigation is stale, but the artifact remains
                    # usable by a new click with the current publication capture.
                    current = runtime.resolve_evidence(parse_resolve(body), ctx)
                    self.assertEqual(self.settled(runtime, ctx, current["jobId"])["status"], "Completed")
                self.assertEqual(provider.sends, sends)


if __name__ == "__main__":
    unittest.main()
