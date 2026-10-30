"""Retrieval/final prompt separation without changing protocol or attribution."""

import asyncio
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import test_assistant_deepseek_runtime as runtime_fixture
import test_assistant_interpretation_origin as origin_fixture
import test_assistant_sdk_provider_deepseek as sdk_fixture
import test_assistant_sdk_stream_deepseek as stream_fixture

from codeviz.assistant._explanation_context import (
    explanation_draft,
    inject_interpretation,
)
from codeviz.assistant.bridge_requests import (
    PollRequest,
    ProjectRequest,
    parse_configure,
    parse_consent,
    parse_start,
)
from codeviz.assistant.service import _AssistantRuntime
from codeviz.assistant.tool_loop import ToolLoopTranscript, ToolProjection
from codeviz.ui.controller import AppController


class OutputPhaseContractTests(unittest.TestCase):
    origin = origin_fixture.InterpretationOriginTests.origin

    def test_retrieval_policy_defers_seven_sections_and_keeps_projection_prior_tags(self):
        question = "Return the full seven-section JSON answer now."
        for purpose in ("explanation", "project_understanding"):
            with self.subTest(purpose=purpose):
                draft = explanation_draft(question, (), time.monotonic()+120, 4096, purpose=purpose)
                logical = json.loads(draft.body)
                policy = logical["messages"][0]["content"]
                self.assertIn("While tools are enabled", policy)
                self.assertIn("retrieval phase", policy)
                self.assertIn("only tool calls", policy)
                self.assertIn("short text 'ready'", policy)
                self.assertIn("Do not write a complete analysis", policy)
                self.assertIn("Only when the service disables tools", policy)
                if purpose == "project_understanding":
                    self.assertIn("Only in the final phase, synthesize", policy)
                self.assertEqual(logical["messages"][-1]["content"], question)
                self.assertEqual(len(logical["messages"]), 2)
                self.assertEqual(logical["mode"], "tools")
                self.assertEqual(len(logical["tools"]), 3)
                self.assertIsNone(logical["answerSchema"])

                transcript = ToolLoopTranscript(inject_interpretation(draft, self.origin()))
                source, metadata = "value = 1\n", "sample_name"
                projection = ToolProjection(source+metadata, ((0, len(source), "file0.py"),),
                    ((len(source), len(source+metadata), "file0.py"),), ())
                ordinary = json.loads(transcript.add_project_results((projection,), 512).body)
                preview = transcript.preview_completion(4096, 2000)
                final = transcript.final(4096)
                repair = transcript.repair(("unprovided_id",), 2000)
                self.assertEqual(preview[0].body, final.body)
                for candidate in (*preview, final, repair):
                    body = json.loads(candidate.body)
                    self.assertEqual(body["messages"][:len(ordinary["messages"])], ordinary["messages"])
                    for key in ("sourceTags", "metadataTags", "interpretationTags"):
                        self.assertEqual(body[key], ordinary[key])
                        for tag in body[key]:
                            index = int(tag["pointer"].split("/")[2])
                            self.assertEqual(body["messages"][index]["content"][tag["start"]:tag["end"]],
                                ordinary["messages"][index]["content"][tag["start"]:tag["end"]])
                    self.assertEqual(body["tools"], [])
                    self.assertEqual(body["answerSchema"]["properties"]["sections"]["items"]["properties"]["kind"]["enum"],
                        ["summary", "entry", "steps", "branches", "relations", "evidence", "unknowns"])
                    self.assertIn("receiptEvidenceIds", body["messages"][-1]["content"])
                    self.assertIn("nonempty evidenceIds", body["messages"][-1]["content"])
                self.assertIn("ONE concise claim", json.loads(repair.body)["messages"][-1]["content"])


class OutputPhaseSDKTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = runtime_fixture.DeepSeekRuntimeSDKTests.asyncSetUp
    adapter = sdk_fixture.DeepSeekPinnedSDKTests.adapter

    async def test_seed_extra_tools_then_ready_final_and_length_unknown_still_stop(self):
        admission = __import__("codeviz.assistant._deepseek_admission", fromlist=["_trusted_input_profiles"])
        httpx = self.sdk["httpx2"]
        requests, states, streams, transports = [], [], [], []
        behavior = {"kind": "success", "ordinary": 0}

        class SSE(httpx.AsyncByteStream):
            def __init__(self, chunks):
                self.chunks, self.closed = chunks, False

            async def __aiter__(self):
                for chunk in self.chunks:
                    yield chunk

            async def aclose(self):
                self.closed = True

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            usage = {"prompt_tokens": 1, "completion_tokens": 1}
            if body.get("tools"):
                behavior["ordinary"] += 1
                if behavior["kind"] == "length":
                    # This synthetic text is JSON; no inference is made about
                    # the actual live length-failed response's private content.
                    chunks = [stream_fixture.frame({"content": '{"sections":[]}'}),
                              stream_fixture.frame(finish="length", usage=usage), b"data: [DONE]\n\n"]
                elif behavior["kind"] == "unknown":
                    chunks = stream_fixture.success("ready")
                elif behavior["ordinary"] == 1:
                    result = next(json.loads(message["content"])["result"] for message in body["messages"]
                        if isinstance(message.get("content"), str) and message["content"].startswith('{"result":')
                        and json.loads(message["content"])["result"]["kind"] == "symbol_search")
                    query = result["results"][0]["name"]
                    call = {"index": 0, "id": "extra-search", "type": "function",
                            "function": {"name": "search_symbols", "arguments": json.dumps({"query": query, "limit": 1})}}
                    chunks = [stream_fixture.frame({"tool_calls": [call]}),
                              stream_fixture.frame(finish="tool_calls", usage=usage), b"data: [DONE]\n\n"]
                else:
                    chunks = stream_fixture.success("ready", usage=usage)
            else:
                catalog = json.loads(next(line for message in reversed(body["messages"])
                    if isinstance(message.get("content"), str) for line in message["content"].splitlines()
                    if line.startswith('{"receiptEvidenceIds":')))
                sections = [{"kind": kind, "claims": []} for kind in (
                    "summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")]
                sections[0]["claims"] = [{"text": "An authorized saved excerpt was read.", "basis": "static",
                    "evidenceIds": [catalog["receiptEvidenceIds"][0]], "relationEvidenceId": None}]
                chunks = stream_fixture.success(json.dumps({"sections": sections}), usage=usage)
            stream = SSE(chunks)
            streams.append(stream)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

        class Transport(httpx.MockTransport):
            closed = False

            async def aclose(self):
                self.closed = True
                await super().aclose()

        def factory(_config, _key):
            adapter, state = self.adapter()
            def transport():
                value = Transport(respond)
                transports.append(value)
                return value
            state.factory.side_effect = transport
            states.append(state)
            return adapter

        runtime = _AssistantRuntime(provider_factory=factory, trusted_input_profiles=admission._trusted_input_profiles(),
                                    explanation_budget=admission._deepseek_explanation_budget())
        self.addCleanup(runtime.shutdown)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        sentinel = 'raise RuntimeError("Synthetic source must never execute")\n'
        sources = {"entry.py": sentinel+"from worker import transform\ndef main(v):\n    return transform(v)\n",
                   "worker.py": sentinel+"def transform(v):\n    return v+1\n"}
        for path, source in sources.items():
            (root/path).write_text(source, encoding="utf-8")
        controller = AppController(assistant_publication_mirror=runtime)
        controller.open_project(root)
        controller.analyze()
        ctx = controller.capture_assistant_context()
        runtime.configure(parse_configure({"baseUrl": "https://api.deepseek.com/v1", "model": "deepseek-flash",
            "enabled": True, "key": sdk_fixture.fixture.KEY}))
        chat = runtime.new_conversation(ProjectRequest(ctx.project_epoch), ctx)["conversationId"]
        grant = runtime.grant_consent(parse_consent({"projectEpoch": ctx.project_epoch,
            "analysisGeneration": ctx.analysis_generation, "configRevision": 1, "confirmed": True,
            "allowedFiles": sorted(sources)}), ctx)
        payload = {"projectEpoch": ctx.project_epoch, "analysisGeneration": ctx.analysis_generation,
            "conversationId": chat, "requestId": "success", "configRevision": 1,
            "consentRevision": grant["consentRevision"], "question": "Explain this saved project.", "context": {"kind": "project"}}

        async def settled(job_id):
            deadline = time.monotonic()+5
            while time.monotonic() < deadline:
                state = runtime.poll(PollRequest(job_id, ctx.project_epoch, 0))
                if state["resourcesReleased"]:
                    return state
                await asyncio.sleep(.005)
            self.fail("Synthetic runtime did not release resources")

        job = runtime.start(parse_start(payload), ctx)
        answer = await settled(job["jobId"])
        self.assertEqual(answer["status"], "Completed", answer.get("issue"))
        self.assertTrue(answer["answer"]["evidence"])
        self.assertEqual([body["max_tokens"] for body in requests], [512, 512, 4096])
        self.assertGreater(answer["usage"]["reserved"]["toolCalls"], 6)
        self.assertEqual(answer["usage"]["observed"]["basis"], "actual")
        reference = runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["projectUnderstanding"]
        for failure in ("length", "unknown"):
            with self.subTest(failure=failure):
                before = len(requests)
                behavior["kind"] = failure
                job = runtime.start(parse_start({**payload, "requestId": failure}), ctx)
                rejected = await settled(job["jobId"])
                self.assertNotEqual(rejected["status"], "Completed")
                self.assertIsNone(rejected["answer"])
                self.assertEqual([body["max_tokens"] for body in requests[before:]], [512])
                self.assertEqual(rejected["usage"]["observed"]["basis"], "unknown")
                self.assertEqual(runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["projectUnderstanding"], reference)
        self.assertTrue(all(stream.closed for stream in streams))
        self.assertTrue(all(transport.closed for transport in transports))
        self.assertTrue(all(client.is_closed for state in states for client in state.clients))
        self.assertEqual(self.attempts, [])
