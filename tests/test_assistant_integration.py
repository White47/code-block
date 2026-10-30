"""Deterministic saved-project acceptance; no SDK, network or fixture execution.

Exercises the real controller, snapshot, tools, worker and public bridge.
This is in-process acceptance, not native WebView/packaged-PC verification.
"""

import json
import shutil
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from test_assistant_runtime_explanation import OfflineExplanationProvider

from codeviz.analysis.project import analyze_project
from codeviz.assistant.budget import EXPLANATION_BUDGET
from codeviz.assistant.provider import ProviderEvent, WireOccurrence
from codeviz.assistant.service import _AssistantRuntime
from codeviz.core import EdgeType, EntityRole, NodeKind, ViewKind
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AppController
from codeviz.ui.web_bridge import DesktopBridge

PROJECT = Path(__file__).parent / "fixtures" / "assistant_project"
KINDS = ("summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")


def event(kind, value):
    return ProviderEvent(kind, json.dumps(value, ensure_ascii=False).encode("utf-8"))


class PathProvider(OfflineExplanationProvider):
    """Only follows identifiers/anchors returned by real saved-analysis tools."""

    def __init__(self):
        super().__init__()
        self.seed = None
        self.relation = None
        self.excerpt = None
        self.snapshot = None

    async def prepare(self, draft):
        prepared = await super().prepare(draft)
        messages = json.loads(prepared.body)["messages"]
        occurrences = list(prepared.source_occurrences)
        # The call excerpt also occurs inside the full function excerpt. Account
        # for every copy without changing the wire body or its input bound.
        for tag in prepared.source_occurrences:
            content = messages[int(tag.pointer.split("/")[2])]["content"]
            text = content[tag.start:tag.end]
            for index, message in enumerate(messages):
                content = message.get("content")
                if not isinstance(content, str):
                    continue
                offset = 0
                while (start := content.find(text, offset)) >= 0:
                    occurrence = WireOccurrence(
                        f"/messages/{index}/content", start, start + len(text), tag.file)
                    if occurrence not in occurrences:
                        occurrences.append(occurrence)
                    offset = start + 1
        prepared = replace(prepared, source_occurrences=tuple(occurrences))
        self.prepared[-1] = (prepared, self.prepared[-1][1])
        return prepared

    async def dispatch(self, prepared):
        self.sends += 1
        logical = next(value for handle, value in self.prepared if handle is prepared)
        results = [json.loads(message["content"])["result"] for message in logical["messages"]
                   if message.get("role") == "tool"]
        searches = [value for value in results if value["kind"] == "symbol_search"]
        relations = [value for value in results if value["kind"] == "local_relations"]
        excerpts = [value for value in results if value["kind"] == "source_excerpt"]
        if logical["mode"] == "tools" and not relations:
            self.seed = next(hit for hit in searches[-1]["results"]
                             if hit["qualifiedName"] == "app.process_record")
            outer = self.seed["anchor"]
            # The real snapshot separates definitions, statements and call sites.
            # Select an invocation inside the returned symbol, never invent an ID.
            anchor, invocation = min(
                ((anchor, node) for node in self.snapshot.code_node_inventory
                 if node.kind == NodeKind.CALL_SITE for anchor in node.source_anchors
                 if anchor.file_path == outer["filePath"]
                 and (outer["startLine"], outer["startColumn"])
                 <= (anchor.start_line, anchor.start_column)
                 and (anchor.end_line, anchor.end_column)
                 <= (outer["endLine"], outer["endColumn"])),
                key=lambda item: (item[0].start_line, item[0].start_column, item[1].id))
            yield event("tool", {"id": "relations", "name": "get_local_relations", "arguments": {
                "seed": {"kind": "code", "id": invocation.id}, "hops": 1,
                "max_nodes": 3, "max_edges": 1}})
            # Tools execute in order: the relation projection precedes binding
            # its exact call span. The enclosing symbol gets a separate proof
            # because graph navigation requires an entity, not a relation key.
            yield event("tool", {"id": "call-source", "name": "read_source_excerpt", "arguments": {
                "file_path": anchor.file_path, "start_line": anchor.start_line,
                "end_line": anchor.end_line, "start_column": anchor.start_column,
                "end_column": anchor.end_column}})
            yield event("tool", {"id": "symbol-source", "name": "read_source_excerpt", "arguments": {
                "file_path": outer["filePath"], "start_line": outer["startLine"],
                "end_line": outer["endLine"], "start_column": outer["startColumn"],
                "end_column": outer["endColumn"]}})
        elif logical["mode"] == "tools":
            self.relation = next(edge for edge in relations[-1]["relations"]
                                 if edge["type"] == "CALLS" and edge["anchors"])
        elif logical["mode"] == "final":
            self.excerpt = next(value for value in excerpts
                                if value["returnedAnchor"] == self.seed["anchor"])
            evidence = self.excerpt["evidenceId"]
            relation_evidence = next(value["evidenceId"] for value in excerpts
                                     if value["returnedAnchor"] == self.relation["anchors"][0])
            sections = [{"kind": kind, "claims": []} for kind in KINDS]
            sections[0]["claims"] = [{"text": "Normalize, validate, handle conflicts, write, return.",
                "basis": "inference", "evidenceIds": [evidence], "relationEvidenceId": None}]
            sections[4]["claims"] = [{"text": "The saved call graph has this call, not an observed execution.",
                "basis": "static", "evidenceIds": [relation_evidence],
                "relationEvidenceId": relation_evidence}]
            sections[6]["claims"] = [{"text": "Unable to confirm dynamic dispatch or actual file-write success.",
                "basis": "unknown", "evidenceIds": [], "relationEvidenceId": None}]
            yield event("answer", {"index": 0, "last": True, "content": json.dumps({"sections": sections})})
        yield event("usage", {"inputTokens": 1, "outputTokens": 1, "basis": "actual"})


class AssistantIntegrationTests(unittest.TestCase):
    def ready(self, provider=None, before_dispatch=None):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "project"
        shutil.copytree(PROJECT, root)
        provider = provider or PathProvider()
        kwargs = {} if before_dispatch is None else {"before_dispatch": before_dispatch}
        runtime = _AssistantRuntime(provider_factory=lambda _config, _key: provider, **kwargs)
        self.addCleanup(runtime.shutdown)
        cache = CacheStore(Path(temporary.name) / "cache")
        controller = AppController(cache, assistant_publication_mirror=runtime)
        bridge = DesktopBridge(controller=controller, cache_store=cache)
        bridge._bind_assistant_runtime(runtime)
        controller.open_project(root)
        controller.analyze()
        context = controller.capture_assistant_context()
        configured = bridge.assistant_configure({"baseUrl": "https://provider.invalid/v1", "model": "probe-model",
            "key": "offline-test-only", "enabled": True})
        self.assertTrue(configured["ok"], configured)
        conversation = bridge.assistant_new_conversation({"projectEpoch": context.project_epoch})
        self.assertTrue(conversation["ok"], conversation)
        consent = bridge.assistant_grant_consent({"projectEpoch": context.project_epoch,
            "analysisGeneration": context.analysis_generation, "configRevision": 1,
            "allowedFiles": sorted(path.name for path in root.glob("*.py")), "confirmed": True})
        self.assertTrue(consent["ok"], consent)
        request = {"projectEpoch": context.project_epoch, "analysisGeneration": context.analysis_generation,
            "conversationId": conversation["data"]["conversationId"], "requestId": "acceptance-1", "configRevision": 1,
            "consentRevision": consent["data"]["consentRevision"], "question": "process_record", "context": {"kind": "question"}}
        return root, bridge, controller, context, request, provider

    def released(self, bridge, epoch, job):
        deadline, pause = time.monotonic() + 5, Event()
        result = None
        while time.monotonic() < deadline:
            envelope = bridge.assistant_poll({"projectEpoch": epoch, "jobId": job, "afterSeq": 0})
            self.assertTrue(envelope["ok"], envelope)
            result = envelope["data"]
            if result["resourcesReleased"]:
                return result
            pause.wait(0.005)
        self.fail(f"No bounded worker release: {result}")

    def test_fixture_spans_ordinary_python_and_model_training_without_imports(self):
        for name in ("app.py", "helpers.py", "dynamic.py", "model.py", "train.py"):
            self.assertTrue((PROJECT / name).is_file(), name)
        analysis = analyze_project(PROJECT)
        definitions = {node.qualified_name: node for node in analysis.flow.graph.nodes.values()
                       if node.role == EntityRole.DEFINITION}
        self.assertTrue({"app.process_record", "helpers.persist_record", "dynamic.descend",
                         "dynamic.invoke", "model.TinyGPT", "train.train_epoch"} <= definitions.keys())
        edges = analysis.flow.graph.edges
        self.assertTrue(any(edge.type == EdgeType.BRANCH_TRUE for edge in edges))
        self.assertTrue(any(edge.type == EdgeType.LOOP_BACK for edge in edges))
        self.assertTrue(any(edge.type == EdgeType.CALLS and edge.target == definitions["dynamic.descend"].id
                            for edge in edges))
        self.assertIsNotNone(analysis.semantic)
        self.assertTrue(analysis.semantic.graph.components)

    def test_saved_snapshot_tool_path_returns_source_and_graph_citations_without_extra_send(self):
        root, bridge, controller, ctx, request, provider = self.ready()
        provider.snapshot = ctx.snapshot
        with patch("socket.create_connection", side_effect=AssertionError("No network")), \
                patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("No SDK")):
            self.assertEqual(provider.sends, 0)
            started = bridge.assistant_start(request)
            self.assertTrue(started["ok"], started)
            result = self.released(bridge, ctx.project_epoch, started["data"]["jobId"])
            self.assertEqual(result["status"], "Completed", result)
            self.assertEqual(provider.sends, 3)
            usage = result["usage"]["reserved"]
            self.assertLessEqual(usage["modelRequests"], EXPLANATION_BUDGET.max_model_requests)
            self.assertLessEqual(usage["inputTokens"], EXPLANATION_BUDGET.max_input_tokens)
            self.assertLessEqual(usage["outputTokens"], EXPLANATION_BUDGET.max_output_tokens)
            self.assertTrue(all(
                handle.max_output_tokens <= EXPLANATION_BUDGET.final_output_tokens
                for handle, _logical in provider.prepared
            ))
            self.assertIn(provider.seed["ref"]["id"], {item.id for item in ctx.snapshot.symbol_inventory})
            answer = result["answer"]
            self.assertEqual([section["kind"] for section in answer["sections"]], list(KINDS))
            self.assertEqual(answer["relationFacts"][0]["type"], "CALLS")
            self.assertEqual(answer["relationFacts"][0]["category"], "call")
            self.assertEqual(answer["sections"][6]["claims"][0]["basis"], "unknown")
            evidence = answer["evidence"][0]
            self.assertTrue(evidence["graphAvailable"])
            sends = provider.sends
            # Graph navigation requires provenance from a delivered real view.
            delivered = bridge.get_view(ViewKind.PROJECT_STRUCTURE.value,
                                        root_id=provider.seed["ref"]["id"])
            self.assertTrue(delivered["ok"], delivered)
            for action in ("source", "graph"):
                checked = bridge.assistant_resolve_evidence({"projectEpoch": ctx.project_epoch,
                    "analysisGeneration": ctx.analysis_generation, "conversationId": request["conversationId"],
                    "evidenceId": evidence["evidenceId"], "action": action})
                self.assertTrue(checked["ok"], checked)
                navigation = self.released(bridge, ctx.project_epoch, checked["data"]["jobId"])
                self.assertEqual(navigation["status"], "Completed", navigation)
                target = navigation["target"]
                self.assertEqual(target["scope"]["requestId"], request["requestId"])
                a = target["astAnchor"]
                self.assertEqual(a, provider.seed["anchor"])
                self.assertTrue((root / a["filePath"]).is_file())
                self.assertGreaterEqual(a["startLine"], 1)
                self.assertIsNone(target["monacoRange"])
                if action == "graph":
                    self.assertIsNotNone(target["reveal"])
                    graph = controller.view(ViewKind(target["reveal"]["viewKind"]), root_id=target["reveal"]["rootId"])
                    self.assertIn(target["reveal"]["selectedId"], {node.id for node in graph.nodes})
            self.assertEqual(provider.sends, sends)
            self.assertFalse((root / "OUTPUT_MUST_NOT_EXIST").exists())
