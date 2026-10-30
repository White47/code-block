"""Owned synthetic project requests through the real B4/B5 and wire gates."""

import json
import time
import unittest
from dataclasses import replace
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_assistant_runtime_explanation import OfflineExplanationProvider

from codeviz.assistant.bridge_requests import (
    BridgeRequestError,
    PollRequest,
    ProjectRequest,
    parse_configure,
    parse_consent,
    parse_start,
)
from codeviz.assistant.budget import EXPLANATION_BUDGET, BudgetUsage
from codeviz.assistant.jobs import JobRequest
from codeviz.assistant.provider import ProviderDraft, ProviderEvent
from codeviz.assistant.retrieval_budget import InputBudgetPlanner, SourceHeadroomPlanner
from codeviz.assistant.service import _AssistantRuntime
from codeviz.assistant.tool_contracts import (
    EntityRef,
    LocalRelationsResult,
    SymbolSearchResult,
)
from codeviz.assistant.tool_loop import ToolLoopTranscript, project_tool_result
from codeviz.assistant.tools import ToolSession
from codeviz.ui.controller import AppController


class ProjectProvider(OfflineExplanationProvider):
    def __init__(self, *, reject_bound=False, fail_dispatch=False, ask_many_tools=False):
        super().__init__()
        self.sent = []
        self.reject_bound = reject_bound
        self.fail_dispatch = fail_dispatch
        self.ask_many_tools = ask_many_tools
        self.before_prepare = None
        self.closed = False
        self.wire_cross_file_calls = set()

    async def prepare(self, draft):
        if self.before_prepare is not None:
            self.before_prepare()
        prepared = await super().prepare(draft)
        if self.reject_bound:
            prepared = replace(prepared, input_token_upper_bound=100_001)
            self.prepared[-1] = (prepared, self.prepared[-1][1])
        return prepared

    async def dispatch(self, prepared):
        self.sent.append(prepared)
        self.sends += 1
        if self.fail_dispatch:
            raise RuntimeError("Owned fake transport failure")
        logical = next(logical for handle, logical in self.prepared if handle is prepared)
        if logical["mode"] == "tools" and self.ask_many_tools:
            for index in range(8):
                yield ProviderEvent("tool", json.dumps({"id": f"extra-{index}",
                    "name": "search_symbols", "arguments": {"query": "", "limit": 1}}).encode())
        if logical["mode"] == "final":
            messages = json.loads(prepared.body)["messages"]
            catalog = json.loads(next(line for line in messages[-1]["content"].splitlines()
                                      if line.startswith('{"receiptEvidenceIds":')))
            results = [json.loads(message["content"])["result"] for message in messages
                       if isinstance(message.get("content"), str)
                       and message["content"].startswith('{"result":')]
            calls = []
            for result in results:
                if result["kind"] != "local_relations":
                    continue
                nodes = {(node["ref"]["kind"], node["ref"]["id"]): node for node in result["nodes"]}
                for edge in result["relations"]:
                    source = nodes.get((edge["source"]["kind"], edge["source"]["id"]))
                    target = nodes.get((edge["target"]["kind"], edge["target"]["id"]))
                    if target is None or edge["type"] != "CALLS":
                        continue
                    # A bounded relation page may omit the caller node. Its actual
                    # CALLS source anchor still identifies the disclosed caller file;
                    # final assertions independently resolve both snapshot endpoints.
                    source_files = {anchor["filePath"] for anchor in (
                        edge["anchors"] if source is None else source["anchors"])}
                    target_files = {anchor["filePath"] for anchor in target["anchors"]}
                    if source_files and target_files and source_files.isdisjoint(target_files):
                        calls.append(edge)
                        self.wire_cross_file_calls.add((edge["source"]["id"], edge["target"]["id"]))

            def contains(outer, inner):
                return (outer["filePath"] == inner["filePath"]
                        and all(anchor[key] is not None for anchor in (outer, inner)
                                for key in ("startColumn", "endColumn"))
                        and (outer["startLine"], outer["startColumn"]) <= (inner["startLine"], inner["startColumn"])
                        and (inner["endLine"], inner["endColumn"]) <= (outer["endLine"], outer["endColumn"]))

            relation_id = next((result["evidenceId"] for result in results
                if result["kind"] == "source_excerpt"
                and result["evidenceId"] in catalog["relationEvidenceIds"]
                and any(contains(result["returnedAnchor"], anchor)
                        for edge in calls for anchor in edge["anchors"])), None)
            sections = [{"kind": kind, "claims": []} for kind in (
                "summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")]
            if relation_id is not None:
                sections[4]["claims"] = [{"text": "The indexed call crosses files.", "basis": "static",
                    "evidenceIds": [relation_id], "relationEvidenceId": relation_id}]
            else:
                sections[6]["claims"] = [{"text": "Unable to confirm a dependency.", "basis": "unknown",
                    "evidenceIds": [], "relationEvidenceId": None}]
            yield ProviderEvent("answer", json.dumps({"index": 0, "last": True,
                "content": json.dumps({"sections": sections})}).encode())
        yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')

    async def aclose(self):
        self.closed = True


class ProjectRetrievalTests(unittest.TestCase):
    def ready(self, *, provider=None, allowed=None, secret=False, large=False, fixture_sources=None):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        sentinel = 'raise RuntimeError("Owned static fixture must never execute")\n'
        noise = "".join(f"def noise_{i:02d}():\n    return {i}\n" for i in range(25))
        sources = {
            "a_entry.py": sentinel + "from b_worker import transform\n" + noise
                          + "def main(value):\n    return transform(value)\n",
            "b_worker.py": sentinel + "from c_storage import persist\n"
                           + "def transform(value):\n    return persist(value)\n",
            "c_storage.py": sentinel + "def persist(value):\n    return value + 1\n",
        }
        if fixture_sources is not None:
            sources = dict(fixture_sources)
        if secret:
            sources["b_worker.py"] += 'api_key = "' + "sk-" + "S" * 40 + '"\n'
        if large:
            literal = repr("中" * 5_000)
            sources = {path: text.replace("(value)\n", f"(value, {literal})\n")
                       .replace("return value + 1", f"return {literal}")
                       for path, text in sources.items()}
        for path, text in sources.items():
            (root / path).write_text(text, encoding="utf-8")
        provider = ProjectProvider() if provider is None else provider
        runtime = _AssistantRuntime(provider_factory=lambda _config, _key: provider)
        self.addCleanup(runtime.shutdown)
        controller = AppController(assistant_publication_mirror=runtime)
        controller.open_project(root)
        controller.analyze()
        ctx = controller.capture_assistant_context()
        runtime.configure(parse_configure({"baseUrl": "https://provider.invalid/v1", "model": "probe-model",
            "key": "synthetic-only", "enabled": True}))
        conversation = runtime.new_conversation(ProjectRequest(ctx.project_epoch), ctx)["conversationId"]
        grant = runtime.grant_consent(parse_consent({"projectEpoch": ctx.project_epoch,
            "analysisGeneration": ctx.analysis_generation, "configRevision": 1,
            "allowedFiles": sorted(sources) if allowed is None else allowed, "confirmed": True}), ctx)
        payload = {"projectEpoch": ctx.project_epoch, "analysisGeneration": ctx.analysis_generation,
            "conversationId": conversation, "requestId": "project-intent", "configRevision": 1,
            "consentRevision": grant["consentRevision"], "question": "请跨文件深入理解整个项目及入口依赖。",
            "context": {"kind": "project"}}
        return root, runtime, ctx, payload, provider

    def settled(self, runtime, ctx, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = runtime.poll(PollRequest(job_id, ctx.project_epoch, 0))
            if state["resourcesReleased"]:
                return state
            time.sleep(.005)
        self.fail("Owned project task did not release within deadline")

    def test_project_context_is_closed_and_purpose_is_part_of_job_identity(self):
        _root, runtime, ctx, payload, _provider = self.ready()
        request = parse_start(payload)
        self.assertEqual(request.context.kind, "project")
        for context in ({"kind": "project", "query": "untrusted"}, {"kind": "project", "rootId": "x"}):
            with self.assertRaises(BridgeRequestError):
                parse_start({**payload, "context": context})
        ordinary = parse_start({**payload, "context": {"kind": "question"}})
        self.assertEqual(ordinary.context.kind, "question")
        base = JobRequest(request.scope, request.conversation_id, request.question, "context", "fake",
            1, 1, "scope", ProviderDraft(b"{}", 2_000, time.monotonic() + 120),
            runtime._context_identity(ctx))
        project = replace(base, purpose="project_understanding")
        self.assertNotEqual(base.digest(), project.digest())
        with self.assertRaises(ValueError):
            replace(project, seed_search=True)

    def test_project_task_sends_diverse_real_sources_and_cross_file_proof(self):
        _root, runtime, ctx, payload, provider = self.ready()
        snapshot = ctx.snapshot
        before_nodes = snapshot.code_node_inventory
        before_edges = snapshot.code_edge_inventory
        started = runtime.start(parse_start(payload), ctx)
        state = self.settled(runtime, ctx, started["jobId"])
        self.assertEqual(state["status"], "Completed", state.get("issue"))
        metadata = {item.file for request in provider.sent for item in request.metadata_occurrences}
        sources = {item.file for request in provider.sent for item in request.source_occurrences}
        self.assertGreaterEqual(len(metadata), 2)
        self.assertGreaterEqual(len(sources), 2)
        self.assertTrue(state["answer"]["relationFacts"])
        for fact in state["answer"]["relationFacts"]:
            self.assertEqual(fact["type"], "CALLS")
            source = snapshot.get_code_node(fact["sourceId"])
            target = snapshot.get_code_node(fact["targetId"])
            self.assertIsNotNone(source)
            self.assertIsNotNone(target)
            self.assertNotEqual(source.file_path, target.file_path)
            self.assertIn((source.id, target.id), provider.wire_cross_file_calls)
        coverage = runtime._engine._project_coverage(started["jobId"])
        self.assertEqual(set(coverage.metadata_files), metadata)
        self.assertEqual(set(coverage.source_files), sources)
        self.assertEqual((coverage.indexed_file_count, coverage.authorized_file_count), (3, 3))
        self.assertTrue(state["answer"]["incomplete"])
        self.assertLessEqual(state["usage"]["reserved"]["toolCalls"], 16)
        self.assertLessEqual(state["usage"]["reserved"]["sourceCharacters"], 12_000)
        self.assertEqual((snapshot.code_node_inventory, snapshot.code_edge_inventory),
                         (before_nodes, before_edges))
        self.assertTrue(provider.closed)

    def test_project_arguments_stay_local_and_ordinary_search_keeps_its_query(self):
        original = ToolSession.dispatch
        queries = []

        def dispatch(session, name, payload):
            if name == "search_symbols":
                queries.append(payload["query"])
            return original(session, name, payload)

        _root, runtime, ctx, payload, provider = self.ready()
        with patch.object(ToolSession, "dispatch", new=dispatch):
            started = runtime.start(parse_start(payload), ctx)
            self.settled(runtime, ctx, started["jobId"])
        self.assertTrue(queries)
        self.assertNotIn(payload["question"], queries)
        self.assertFalse(any(message.get("toolCalls") for _request, logical in provider.prepared
                             for message in logical["messages"]))
        queries.clear()
        ordinary = {**payload, "requestId": "ordinary-query", "context": {"kind": "question"}}
        with patch.object(ToolSession, "dispatch", new=dispatch):
            started = runtime.start(parse_start(ordinary), ctx)
            state = self.settled(runtime, ctx, started["jobId"])
        self.assertEqual(state["status"], "Completed", state.get("issue"))
        self.assertEqual(queries, [payload["question"]])
        self.assertIsNone(runtime._engine._project_coverage(started["jobId"]))

    def test_exact_project_seed_preserves_broad_count_and_only_real_pagination(self):
        warning = "Symbol search returned a bounded page; additional matches may exist."
        original = ToolSession.dispatch
        for truncated in (False, True):
            with self.subTest(truncated=truncated):
                _root, runtime, ctx, payload, provider = self.ready()
                exact_ids = []

                def dispatch(session, name, arguments, *, snapshot=ctx.snapshot,
                             expected=exact_ids, paged=truncated):
                    result = original(session, name, arguments)
                    if name != "search_symbols":
                        return result
                    exact = next(hit for hit in result.results
                                 if hit.qualified_name == arguments["query"])
                    other = next(symbol for symbol in snapshot.symbol_inventory
                                 if symbol.definition and symbol.id != exact.ref.id)
                    non_exact = replace(exact, ref=EntityRef("symbol", other.id),
                        name=other.name, qualified_name=other.qualified_name, kind=other.kind.value,
                        scope=other.scope, anchor=other.source_anchor)
                    expected.append(exact.ref.id)
                    return SymbolSearchResult((exact, non_exact), 3 if paged else 2, paged)

                with patch.object(ToolSession, "dispatch", new=dispatch):
                    started = runtime.start(parse_start(payload), ctx)
                    state = self.settled(runtime, ctx, started["jobId"])
                self.assertEqual(state["status"], "Completed", state.get("issue"))
                messages = json.loads(provider.sent[-1].body)["messages"]
                results = [json.loads(message["content"])["result"] for message in messages
                           if message["content"].startswith('{"result":')]
                seeds = [result for result in results if result["kind"] == "symbol_search"]
                self.assertEqual([result["results"][0]["ref"]["id"] for result in seeds], exact_ids)
                for result in seeds:
                    self.assertEqual(len(result["results"]), 1)
                    self.assertEqual(result["totalCount"], 3 if truncated else 2)
                    self.assertIs(result["truncated"], truncated)
                self.assertEqual(warning in state["answer"]["coverageGaps"], truncated)
                if truncated:
                    coverage = runtime._engine._project_coverage(started["jobId"])
                    self.assertIn("retrieval_gap", coverage.gap_codes)

    def test_project_relations_use_full_candidates_when_final_and_repair_fit(self):
        _root, runtime, ctx, payload, provider = self.ready(fixture_sources={
            "a_entry.py": 'from b_worker import transform\ndef main(value):\n'
                          + '    transform(value)\n' * 12 + '    return transform(value)\n',
            "b_worker.py": 'from c_storage import persist\ndef transform(value):\n    return persist(value)\n',
            "c_storage.py": 'def persist(value):\n    return value + 1\n',
        })
        full_candidates = {}
        project_base = []
        full_previews = []
        add_results = ToolLoopTranscript.add_project_results
        preview_completion = ToolLoopTranscript.preview_completion

        def observe_projection(result, *, snapshot, maximum_bytes=None):
            if id(result) not in full_candidates:
                full_candidates[id(result)] = project_tool_result(result, snapshot=snapshot)
            return project_tool_result(result, snapshot=snapshot, maximum_bytes=maximum_bytes)

        def observe_add(transcript, results, output_limit):
            if not project_base:
                project_base.append(transcript.current(output_limit))
            return add_results(transcript, results, output_limit)

        def observe_preview(transcript, final_limit, repair_limit, catalog=()):
            if project_base and not full_previews:
                complete = ToolLoopTranscript(project_base[0])
                add_results(complete, tuple(full_candidates.values()), final_limit)
                full_previews.append(preview_completion(complete, final_limit, repair_limit, catalog))
            return preview_completion(transcript, final_limit, repair_limit, catalog)

        with patch("codeviz.assistant.service.project_tool_result", side_effect=observe_projection), \
                patch.object(ToolLoopTranscript, "add_project_results", new=observe_add), \
                patch.object(ToolLoopTranscript, "preview_completion", new=observe_preview):
            started = runtime.start(parse_start(payload), ctx)
            state = self.settled(runtime, ctx, started["jobId"])
        self.assertEqual(state["status"], "Completed", state.get("issue"))
        relations = [projection for projection in full_candidates.values()
                     if json.loads(projection.content)["result"]["kind"] == "local_relations"]
        self.assertTrue(any(len(projection.content.encode("utf-8")) > 3_072
                            for projection in relations))
        self.assertEqual(len(full_previews), 1)
        final, repair = full_previews[0]
        self.assertTrue(InputBudgetPlanner(EXPLANATION_BUDGET.max_input_tokens).fits(
            BudgetUsage(), final, repair))
        self.assertTrue(SourceHeadroomPlanner(EXPLANATION_BUDGET.limits()).fits(
            BudgetUsage(), final, final))

        messages = json.loads(provider.sent[-1].body)["messages"]
        results = [json.loads(message["content"])["result"] for message in messages
                   if message["content"].startswith('{"result":')]
        forwarded = [result for result in results if result["kind"] == "local_relations"]
        self.assertEqual(len(forwarded), len(relations))
        for full_projection, selected in zip(relations, forwarded, strict=True):
            full = json.loads(full_projection.content)["result"]
            self.assertNotIn("relation_node", selected.get("omittedTypes", []))
            self.assertEqual(selected["nodes"], full["nodes"])
            if full["truncated"]:
                self.assertEqual(selected["coverageGap"], full["coverageGap"])
                self.assertIn(full["coverageGap"], state["answer"]["coverageGaps"])
        self.assertTrue(any(json.loads(projection.content)["result"]["truncated"]
                            for projection in relations))
        self.assertEqual([logical["mode"] for _request, logical in provider.prepared], ["tools", "final"])
        self.assertEqual(state["usage"]["reserved"]["toolCalls"], 10)
        self.assertEqual(len(provider.sent), 2)
        self.assertLessEqual(sum(request.input_token_upper_bound for request in provider.sent), 100_000)
        self.assertLessEqual(state["usage"]["reserved"]["sourceCharacters"], 12_000)
        disclosed_files = {item.file for request in provider.sent
                           for item in (*request.source_occurrences, *request.metadata_occurrences)}
        self.assertLessEqual(len(disclosed_files), 8)

    def test_denied_paths_and_foreign_dependency_metadata_never_enter_wire(self):
        allowed = ["a_entry.py", "b_worker.py"]
        _root, runtime, ctx, payload, provider = self.ready(allowed=allowed)
        started = runtime.start(parse_start(payload), ctx)
        state = self.settled(runtime, ctx, started["jobId"])
        self.assertEqual(state["status"], "Completed", state.get("issue"))
        wire = b"".join(request.body for request in provider.sent)
        self.assertNotIn(b"c_storage", wire)
        self.assertTrue(all(item.file in allowed for request in provider.sent
                            for item in (*request.source_occurrences, *request.metadata_occurrences)))
        coverage = runtime._engine._project_coverage(started["jobId"])
        self.assertEqual((coverage.indexed_file_count, coverage.authorized_file_count), (3, 2))

    def test_unsent_failed_and_secret_or_changed_sources_are_not_model_coverage(self):
        for failure in ("budget", "dispatch", "secret", "changed"):
            with self.subTest(failure=failure):
                provider = ProjectProvider(reject_bound=failure == "budget", fail_dispatch=failure == "dispatch")
                root, runtime, ctx, payload, provider = self.ready(
                    provider=provider, secret=failure == "secret")
                if failure == "changed":
                    provider.before_prepare = partial((root / "b_worker.py").write_text,
                        'raise RuntimeError("Changed owned fixture must not execute")\n', encoding="utf-8")
                started = runtime.start(parse_start(payload), ctx)
                state = self.settled(runtime, ctx, started["jobId"])
                self.assertEqual(state["status"], "Stale" if failure == "changed" else "Failed")
                coverage = runtime._engine._project_coverage(started["jobId"])
                self.assertEqual((coverage.source_files, coverage.metadata_files), ((), ()))
                if failure != "dispatch":
                    self.assertEqual(provider.sent, [])

    def test_project_omission_retains_final_headroom_and_counts_only_disclosed_fragments(self):
        _root, runtime, ctx, payload, provider = self.ready(large=True)
        started = runtime.start(parse_start(payload), ctx)
        state = self.settled(runtime, ctx, started["jobId"])
        self.assertEqual(state["status"], "Completed", state.get("issue"))
        coverage = runtime._engine._project_coverage(started["jobId"])
        self.assertIn("retrieval_gap", coverage.gap_codes)
        actual = {item.file for request in provider.sent for item in request.source_occurrences}
        self.assertEqual(set(coverage.source_files), actual)
        self.assertLess(len(actual), coverage.authorized_file_count)
        self.assertTrue(state["answer"]["incomplete"])
        self.assertLessEqual(state["usage"]["reserved"]["sourceCharacters"], 12_000)
        self.assertLessEqual(state["usage"]["reserved"]["toolCalls"], 16)

    def test_project_prefetch_keeps_final_when_model_calls_exceed_remaining_tool_attempts(self):
        _root, runtime, ctx, payload, provider = self.ready(provider=ProjectProvider(ask_many_tools=True))
        started = runtime.start(parse_start(payload), ctx)
        state = self.settled(runtime, ctx, started["jobId"])
        self.assertEqual(state["status"], "Completed", state.get("issue"))
        self.assertEqual([logical["mode"] for _request, logical in provider.prepared], ["tools", "final"])
        self.assertEqual(state["usage"]["reserved"]["toolCalls"], 10)
        self.assertTrue(state["answer"]["incomplete"])

    def test_omitted_project_relation_cannot_restore_a_preferred_receipt_binding(self):
        def bounded(result, *, snapshot, maximum_bytes=None):
            if type(result) is LocalRelationsResult:
                maximum_bytes = min(512, maximum_bytes or 512)
            return project_tool_result(result, snapshot=snapshot, maximum_bytes=maximum_bytes)

        _root, runtime, ctx, payload, provider = self.ready()
        with patch("codeviz.assistant.service.project_tool_result", side_effect=bounded):
            started = runtime.start(parse_start(payload), ctx)
            state = self.settled(runtime, ctx, started["jobId"])
        self.assertEqual(state["status"], "Completed", state.get("issue"))
        self.assertEqual(state["answer"]["relationFacts"], [])
        messages = json.loads(provider.sent[-1].body)["messages"]
        catalog = json.loads(next(line for line in messages[-1]["content"].splitlines()
                                  if line.startswith('{"receiptEvidenceIds":')))
        self.assertTrue(catalog["receiptEvidenceIds"])
        self.assertEqual(catalog["relationEvidenceIds"], [])
        self.assertTrue(state["answer"]["incomplete"])

