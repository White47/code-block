"""B2 budget-aware retrieval, projection, and final-answer scheduling."""

import importlib
import importlib.util
import inspect
import json
import os
import time
import unittest
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from tests.assistant_service_fixtures import scope_fingerprint
from tests.assistant_tool_fixtures import (
    make_index_case,
    make_session,
    make_source_case,
)

from codeviz.assistant.authority import AuthorityState
from codeviz.assistant.budget import (
    EXPLANATION_BUDGET,
    BudgetLedger,
    BudgetLimits,
    BudgetUsage,
)
from codeviz.assistant.contracts import AgentError, JobStatus, RequestScope
from codeviz.assistant.jobs import JobRequest, JobSnapshot, RootRetrieval
from codeviz.assistant.provider import (
    PreparedRequest,
    ProviderDraft,
    ProviderEvent,
    WireOccurrence,
)
from codeviz.assistant.service import AssistantService
from codeviz.assistant.snapshot import AnchorRecord
from codeviz.assistant.tool_contracts import (
    EntityRef,
    ExcerptToolResult,
    RelationKey,
    RelationsRequest,
    SearchRequest,
)
from codeviz.assistant.tool_loop import (
    ToolCall,
    ToolLoopTranscript,
    ToolProjection,
    TrustedTarget,
    choose_target,
    project_tool_result,
)
from codeviz.ui.assistant_context import PublicationIdentity
from codeviz.ui.controller import AnalysisStatus


def _draft() -> ProviderDraft:
    logical = {
        "version": 1,
        "mode": "tools",
        "messages": [{"role": "user", "content": "解释这个调用链。"}],
        "tools": [{
            "name": "read_source_excerpt",
            "description": "Read exact source proof.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }],
        "answerSchema": None,
        "sourceTags": [],
        "metadataTags": [],
    }
    return ProviderDraft(
        json.dumps(logical, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        512,
        100.0,
    )


def _tagged_draft(
    text: str,
    path: str = "app.py",
    *,
    metadata_paths: tuple[str, ...] = (),
) -> ProviderDraft:
    logical = {
        "messages": [{"role": "user", "content": text}],
        "sourceTags": [{
            "pointer": "/messages/0/content",
            "start": 0,
            "end": len(text),
            "file": path,
        }],
        "metadataTags": [{
            "pointer": "/messages/0/content",
            "start": 0,
            "end": len(text),
            "file": metadata_path,
        } for metadata_path in metadata_paths],
    }
    return ProviderDraft(
        json.dumps(logical, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        512,
        100.0,
    )


class RetrievalBudgetProjectionTests(unittest.TestCase):
    def test_receipt_catalog_previews_match_actual_guidance_and_metadata(self) -> None:
        loop = importlib.import_module("codeviz.assistant.tool_loop")
        receipt = 'receipt-original-中"\\/not-a-symbol'
        catalog = (
            loop.EvidenceIdEntry(receipt, "app.py", RelationKey("code", 0), True),
            loop.EvidenceIdEntry("receipt-source", "other.py", None, False),
        )
        transcript = ToolLoopTranscript(_draft())
        before = transcript.current(512).body
        empty_inputs = transcript.preview_completion(4_096, 2_000)
        final, repair = transcript.preview_completion(4_096, 2_000, catalog)
        planning = importlib.import_module("codeviz.assistant.retrieval_budget")
        small_limit = sum(len(draft.body) for draft in empty_inputs)
        planner = planning.InputBudgetPlanner(small_limit)
        self.assertTrue(planner.fits(BudgetUsage(), *empty_inputs))
        self.assertFalse(planner.fits(BudgetUsage(), final, repair))
        self.assertEqual(transcript.current(512).body, before)
        self.assertEqual(transcript.final(4_096, catalog).body, final.body)
        codes = ("invalid_schema", "unprovided_id", "invalid_evidence", "relation_required",
                 "invalid_relation", "static_requires_ids")
        self.assertEqual(transcript.repair(codes, 2_000, catalog).body, repair.body)
        for draft in (final, repair):
            logical = json.loads(draft.body)
            message = logical["messages"][-1]["content"]
            self.assertIn("basis=static", message)
            self.assertIn("nonempty evidenceIds", message)
            self.assertIn("receiptEvidenceIds supporting that exact claim", message)
            self.assertIn("inference/unknown or omitted", message)
            self.assertIn("Never invent IDs", message)
            self.assertIn("unable to confirm", message)
            values = json.loads(next(line for line in message.splitlines()
                                     if line.startswith('{"receiptEvidenceIds":')))
            self.assertEqual(values, {
                "receiptEvidenceIds": [receipt, "receipt-source"], "relationEvidenceIds": [receipt]})
            pointer = f'/messages/{len(logical["messages"]) - 1}/content'
            spans = [tag for tag in logical["metadataTags"] if tag["pointer"] == pointer]
            self.assertEqual(len(spans), 3)
            for tag in spans:
                identifier = json.loads(message[tag["start"]:tag["end"]])
                self.assertIn(identifier, (receipt, "receipt-source"))
                self.assertEqual(tag["file"], "app.py" if identifier == receipt else "other.py")
            self.assertNotIn("app.py", message)
        repair_message = json.loads(repair.body)["messages"][-1]["content"]
        self.assertNotIn("at most ONE concise claim", json.loads(final.body)["messages"][-1]["content"])
        self.assertIn("at most ONE concise claim", repair_message)
        self.assertIn("80 UTF-8 bytes", repair_message)

    def test_empty_excerpt_is_a_nonbudget_gap_without_a_citable_receipt(self) -> None:
        case = make_index_case()
        for anchor in (AnchorRecord("app.py", 1, 0, 1, 0),
                       AnchorRecord("app.py", 1, None, 1, None)):
            result = ExcerptToolResult("receipt-empty", "app.py", "", anchor, False)
            for maximum in (None, 512):
                with self.subTest(anchor=anchor, maximum=maximum):
                    projection = project_tool_result(
                        result, snapshot=case.snapshot, maximum_bytes=maximum)
                    payload = json.loads(projection.content)["result"]
                    self.assertEqual(payload["kind"], "retrieval_gap")
                    self.assertIn("no source characters", payload["coverageGap"].lower())
                    self.assertNotIn("budget", payload["coverageGap"].lower())
                    self.assertNotIn("receipt-empty", projection.content)
                    self.assertNotIn("app.py", projection.content)
                    self.assertEqual((projection.source_spans, projection.metadata_spans,
                                      projection.targets), ((), (), ()))
            with self.assertRaises(ValueError):
                project_tool_result(result, snapshot=case.snapshot, maximum_bytes=1)
        whitespace = ExcerptToolResult(
            "receipt-space", "app.py", " ", AnchorRecord("app.py", 1, 0, 1, 1), False)
        retained = project_tool_result(whitespace, snapshot=case.snapshot)
        self.assertTrue(retained.source_spans)
        self.assertIn("receipt-space", retained.content)

    def test_final_preview_is_utf8_exact_and_does_not_mutate_transcript(self) -> None:
        transcript = ToolLoopTranscript(_draft())
        before = transcript.current(512).body

        self.assertTrue(
            hasattr(transcript, "preview_final"),
            "B2 requires a non-mutating final preview",
        )
        preview = transcript.preview_final(512)

        self.assertEqual(transcript.mode, "tools")
        self.assertEqual(transcript.current(512).body, before)
        self.assertEqual(len(preview.body), len(preview.body.decode("utf-8").encode("utf-8")))
        self.assertEqual(json.loads(preview.body)["mode"], "final")

    def test_tool_result_final_preview_is_also_non_mutating(self) -> None:
        transcript = ToolLoopTranscript(_draft())
        before = transcript.current(512).body
        projection = ToolProjection(
            '{"result":{"kind":"retrieval_gap","omittedCount":1}}', (), (), ()
        )
        self.assertTrue(hasattr(transcript, "preview_tool_results_final"))

        preview = transcript.preview_tool_results_final(
            (ToolCall("call-1", "read_source_excerpt", {}),),
            (projection,),
            512,
        )

        self.assertEqual(transcript.current(512).body, before)
        logical = json.loads(preview.body)
        self.assertEqual(logical["mode"], "final")
        self.assertEqual(logical["messages"][-2]["role"], "tool")
        self.assertEqual(logical["messages"][-1]["role"], "user")

    def test_completion_previews_preserve_transcript_and_source_for_one_repair(self) -> None:
        transcript = ToolLoopTranscript(_draft())
        before = transcript.current(512).body
        calls = (ToolCall("preview-only", "read_source_excerpt", {}),)
        projections = (ToolProjection('{"source":"x"}', ((11, 12, "app.py"),), (), ()),)
        final, repair = transcript.preview_tool_results_completion(
            calls, projections, 4_096, 2_000)

        self.assertEqual(transcript.current(512).body, before)
        self.assertEqual((final.max_output_tokens, repair.max_output_tokens), (4_096, 2_000))
        final_value, repair_value = json.loads(final.body), json.loads(repair.body)
        self.assertEqual(final_value["sourceTags"], repair_value["sourceTags"])
        self.assertEqual(repair_value["messages"][:-1], final_value["messages"])
        codes = repair_value["messages"][-1]["content"].split("Validation codes: ")[1][:-1]
        self.assertEqual(len(codes.split(",")), 6)
        self.assertEqual(set(codes.split(",")), {
            "invalid_schema", "unprovided_id", "invalid_evidence", "relation_required",
            "invalid_relation", "static_requires_ids",
        })
        # A preview must not consume the tool exchange or its call ID.
        transcript.add_tool_results(calls, projections, 4_096)
        self.assertEqual(transcript.final(4_096).body, final.body)

    def test_excerpt_that_does_not_fit_is_omitted_as_a_whole(self) -> None:
        case = make_index_case()
        text = "中" * 3_000
        result = ExcerptToolResult(
            "receipt-must-not-leak",
            "app.py",
            text,
            AnchorRecord("app.py", 1, 0, 1, len(text.encode("utf-8"))),
            True,
        )
        self.assertIn("maximum_bytes", inspect.signature(project_tool_result).parameters)

        projection = project_tool_result(
            result,
            snapshot=case.snapshot,
            maximum_bytes=512,
        )

        self.assertLessEqual(len(projection.content.encode("utf-8")), 512)
        self.assertEqual(projection.source_spans, ())
        self.assertEqual(projection.metadata_spans, ())
        self.assertEqual(projection.targets, ())
        self.assertNotIn("receipt-must-not-leak", projection.content)
        self.assertNotIn("app.py", projection.content)
        self.assertNotIn("returnedAnchor", projection.content)
        omitted = json.loads(projection.content)["result"]
        self.assertEqual(omitted["omittedCount"], 1)
        self.assertEqual(omitted["omittedTypes"], ["source_excerpt"])
        self.assertTrue(omitted["coverageGap"])

    def test_search_projection_keeps_only_complete_items_that_fit(self) -> None:
        case = make_index_case(symbol_count=20)
        result = make_session(case).search_symbols(SearchRequest("", 20))
        full = project_tool_result(result, snapshot=case.snapshot)
        maximum = max(512, len(full.content.encode("utf-8")) // 2)

        projection = project_tool_result(
            result,
            snapshot=case.snapshot,
            maximum_bytes=maximum,
        )

        self.assertLessEqual(len(projection.content.encode("utf-8")), maximum)
        payload = json.loads(projection.content)["result"]
        kept = payload["results"]
        self.assertGreater(len(kept), 0)
        self.assertLess(len(kept), len(result.results))
        self.assertEqual(payload["omittedCount"], result.total_count - len(kept))
        kept_ids = {item["ref"]["id"] for item in kept}
        self.assertEqual({
            target.target.id
            for target in projection.targets
            if isinstance(target.target, EntityRef)
        }, kept_ids)
        for item in result.results:
            if item.ref.id not in kept_ids:
                self.assertNotIn(item.ref.id, projection.content)
        self.assertEqual({path for _start, _end, path in projection.metadata_spans}, {"app.py"})

    def test_search_api_pagination_is_not_reported_as_budget_omission(self) -> None:
        case = make_index_case(symbol_count=20)
        result = make_session(case).search_symbols(SearchRequest("", 1))

        payload = json.loads(project_tool_result(
            result,
            snapshot=case.snapshot,
        ).content)["result"]

        self.assertTrue(result.truncated)
        self.assertGreater(result.total_count, len(result.results))
        self.assertTrue(payload["truncated"])
        self.assertNotIn("omittedCount", payload)
        self.assertIn("coverageGap", payload)
        self.assertNotIn("budget", payload["coverageGap"].lower())

    def test_relation_projection_counts_gaps_and_registers_only_kept_targets(self) -> None:
        case = make_index_case(degree=3)
        result = make_session(case).get_local_relations(
            RelationsRequest(EntityRef("code", "seed"), hops=2, max_nodes=50, max_edges=100)
        )
        full = project_tool_result(result, snapshot=case.snapshot)
        maximum = max(512, len(full.content.encode("utf-8")) * 2 // 3)

        projection = project_tool_result(
            result,
            snapshot=case.snapshot,
            maximum_bytes=maximum,
        )

        self.assertLessEqual(len(projection.content.encode("utf-8")), maximum)
        payload = json.loads(projection.content)["result"]
        self.assertEqual(payload["omittedCount"], {
            "nodes": len(result.nodes) - len(payload["nodes"]),
            "relations": len(result.relations) - len(payload["relations"]),
        })
        kept_entities = {
            (item["ref"]["kind"], item["ref"]["id"])
            for item in payload["nodes"]
        }
        kept_relations = {
            (item["key"]["collectionKind"], item["key"]["originalIndex"])
            for item in payload["relations"]
        }
        projected_entities = {
            (target.target.kind, target.target.id)
            for target in projection.targets
            if isinstance(target.target, EntityRef)
        }
        projected_relations = {
            (target.target.collection_kind, target.target.original_index)
            for target in projection.targets
            if not isinstance(target.target, EntityRef)
        }
        self.assertEqual(projected_entities, kept_entities)
        self.assertEqual(projected_relations, kept_relations)


class RetrievalBudgetPlanningTests(unittest.TestCase):
    def test_source_headroom_counts_unicode_occurrences_and_file_union_without_reserving(self) -> None:
        budget = importlib.import_module("codeviz.assistant.retrieval_budget")
        self.assertTrue(hasattr(budget, "SourceHeadroomPlanner"))
        limits = BudgetLimits()
        planner = budget.SourceHeadroomPlanner(limits)
        ledger = BudgetLedger(limits, clock=lambda: 0.0)
        repeated = _tagged_draft("中" * 4_000)
        before = ledger.usage

        self.assertTrue(planner.fits(before, repeated, repeated, repeated))
        self.assertFalse(planner.fits(
            before, repeated, repeated, _tagged_draft("中" * 4_001)))
        eight_files = tuple(_tagged_draft("x", f"file-{index}.py") for index in range(8))
        self.assertTrue(planner.fits(before, *eight_files))
        self.assertFalse(planner.fits(
            before, *eight_files, _tagged_draft("x", "file-8.py")))
        self.assertEqual(ledger.usage, before)

    def test_source_headroom_fails_closed_for_malformed_internal_tags(self) -> None:
        budget = importlib.import_module("codeviz.assistant.retrieval_budget")
        self.assertTrue(hasattr(budget, "SourceHeadroomPlanner"))
        planner = budget.SourceHeadroomPlanner(BudgetLimits())
        usage = BudgetUsage()
        malformed = (
            ProviderDraft(b"{", 512, 100.0),
            ProviderDraft(b'{"messages":[]}', 512, 100.0),
            ProviderDraft(b'{"sourceTags":[],"sourceTags":[]}', 512, 100.0),
            ProviderDraft(json.dumps({"sourceTags": [{
                "pointer": "/messages/0/content", "start": 0, "end": True,
                "file": "app.py",
            }]}).encode(), 512, 100.0),
            ProviderDraft(json.dumps({"sourceTags": [{
                "pointer": "/messages/0/content", "start": 0, "end": 1,
                "file": "../app.py",
            }]}).encode(), 512, 100.0),
            ProviderDraft(json.dumps({
                "messages": [{"content": "x"}],
                "sourceTags": [{"pointer": "/messages/1/content", "start": 0,
                                "end": 1, "file": "app.py"}],
            }).encode(), 512, 100.0),
            ProviderDraft(json.dumps({
                "messages": [{"content": "x"}],
                "sourceTags": [{"pointer": "/messages/0/content", "start": 0,
                                "end": 2, "file": "app.py"}],
            }).encode(), 512, 100.0),
            ProviderDraft(json.dumps({
                "messages": [{"content": "x"}],
                "sourceTags": [{"pointer": "/messages/0/content", "start": 0,
                                "end": 1, "file": "app.py"}],
                "metadataTags": [{"pointer": "/messages/0/content", "start": 0,
                                  "end": 1, "file": "app.py"}],
            }).encode(), 512, 100.0),
            ProviderDraft(json.dumps({
                "messages": [{"content": "xy"}],
                "sourceTags": [{"pointer": "/messages/0/content", "start": 0,
                                "end": 1, "file": "App.py"}],
                "metadataTags": [{"pointer": "/messages/0/content", "start": 1,
                                  "end": 2, "file": "app.py"}],
            }).encode(), 512, 100.0),
        )

        for draft in malformed:
            with self.subTest(body=draft.body):
                self.assertFalse(planner.fits(usage, draft))

        self.assertFalse(planner.fits(
            BudgetUsage(files=("App.py",)), _tagged_draft("x", "app.py")))
        escaped_key = ProviderDraft(json.dumps({
            "messages": [{"a/b": "value"}],
            "sourceTags": [{"pointer": "/messages/0/a~1b#key", "start": 0,
                            "end": 3, "file": "app.py"}],
        }).encode(), 512, 100.0)
        self.assertTrue(planner.fits(usage, escaped_key))

    def test_production_explanation_budget_is_frozen_and_internally_consistent(self) -> None:
        budgets = importlib.import_module("codeviz.assistant.budget")

        self.assertTrue(hasattr(budgets, "EXPLANATION_BUDGET"))
        config = budgets.EXPLANATION_BUDGET
        self.assertEqual(
            (
                config.max_files,
                config.max_source_characters,
                config.max_tool_calls,
                config.max_model_requests,
                config.max_input_tokens,
                config.max_output_tokens,
                config.timeout_seconds,
                config.ordinary_rounds,
                config.ordinary_output_tokens,
                config.final_output_tokens,
                config.repair_output_tokens,
            ),
            (8, 12_000, 16, 10, 100_000, 8_192, 180, 8, 512, 2_000, 2_000),
        )
        self.assertLessEqual(
            config.ordinary_rounds * config.ordinary_output_tokens
            + config.final_output_tokens
            + config.repair_output_tokens,
            config.max_output_tokens,
        )
        with self.assertRaises(AttributeError):
            config.max_input_tokens = 20_000

    def test_deepseek_explanation_budget_trades_ordinary_depth_for_final_headroom(self) -> None:
        admission = importlib.import_module("codeviz.assistant._deepseek_admission")
        deepseek = admission._deepseek_explanation_budget()

        self.assertEqual(
            (deepseek.ordinary_rounds, deepseek.ordinary_output_tokens,
             deepseek.final_output_tokens, deepseek.repair_output_tokens,
             deepseek.max_output_tokens),
            (4, 512, 4_096, 2_000, 8_192),
        )
        self.assertEqual(
            deepseek.ordinary_rounds * deepseek.ordinary_output_tokens
            + deepseek.final_output_tokens + deepseek.repair_output_tokens,
            8_144,
        )
        self.assertEqual(
            EXPLANATION_BUDGET.ordinary_rounds * EXPLANATION_BUDGET.ordinary_output_tokens
            + EXPLANATION_BUDGET.final_output_tokens + EXPLANATION_BUDGET.repair_output_tokens,
            8_096,
        )
        for change in (
            {"final_output_tokens": 4_097},
            {"repair_output_tokens": 2_001},
            {"ordinary_rounds": 5},
            {"max_output_tokens": 8_143},
        ):
            with self.subTest(change=change), self.assertRaises(AgentError):
                replace(deepseek, **change)

    def test_output_planning_always_reserves_512_for_final(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("codeviz.assistant.retrieval_budget"))
        budget = importlib.import_module("codeviz.assistant.retrieval_budget")

        self.assertEqual(budget.output_allocation(BudgetUsage(), "ordinary", 512), 512)
        used = BudgetUsage(output_tokens=1_024)
        self.assertEqual(budget.output_allocation(used, "ordinary", 512), 464)
        used = BudgetUsage(output_tokens=1_488)
        self.assertIsNone(budget.output_allocation(used, "ordinary", 512))
        self.assertEqual(budget.output_allocation(used, "final", 512), 512)

    def test_large_output_planning_reserves_both_final_and_repair(self) -> None:
        budget = importlib.import_module("codeviz.assistant.retrieval_budget")

        self.assertEqual(
            budget.output_allocation(
                BudgetUsage(output_tokens=3_584),
                "ordinary",
                2_000,
                config=EXPLANATION_BUDGET,
            ),
            512,
        )
        self.assertEqual(budget.output_allocation(
            BudgetUsage(output_tokens=4_096),
            "ordinary",
            2_000,
            config=EXPLANATION_BUDGET,
        ), None)
        self.assertEqual(budget.output_allocation(
            BudgetUsage(output_tokens=4_096),
            "final",
            8_192,
            config=EXPLANATION_BUDGET,
        ), 2_000)

    def test_configured_output_stages_are_all_or_none(self) -> None:
        budget = importlib.import_module("codeviz.assistant.retrieval_budget")
        deepseek = importlib.import_module(
            "codeviz.assistant._deepseek_admission")._deepseek_explanation_budget()

        self.assertEqual(budget.output_allocation(
            BudgetUsage(output_tokens=1_536), "ordinary", 4_096, config=deepseek), 512)
        self.assertIsNone(budget.output_allocation(
            BudgetUsage(output_tokens=2_048), "ordinary", 4_096, config=deepseek))
        self.assertEqual(budget.output_allocation(
            BudgetUsage(output_tokens=2_048), "final", 4_096, config=deepseek), 4_096)
        self.assertIsNone(budget.output_allocation(
            BudgetUsage(output_tokens=4_097), "final", 4_096, config=deepseek))
        self.assertEqual(budget.output_allocation(
            BudgetUsage(output_tokens=6_144), "repair", 4_096, config=deepseek), 2_000)
        self.assertIsNone(budget.output_allocation(
            BudgetUsage(output_tokens=6_193), "repair", 4_096, config=deepseek))

    def test_final_and_repair_guidance_is_compact_without_dropping_source_tags(self) -> None:
        draft = _draft()
        logical = json.loads(draft.body)
        logical["sourceTags"] = [{
            "pointer": "/messages/0/content", "start": 0, "end": 8, "file": "app.py"}]
        transcript = ToolLoopTranscript(replace(
            draft, body=json.dumps(logical, separators=(",", ":")).encode("utf-8")))
        final = json.loads(transcript.final(4_096).body)
        final_guidance = final["messages"][-1]["content"]
        self.assertIn("valid JSON", final_guidance)
        self.assertIn("coverage gaps", final_guidance)
        self.assertIn("avoid repeating", final_guidance)
        self.assertIn("unable to confirm", final_guidance)
        self.assertEqual(len(final["sourceTags"]), 1)

        repaired = json.loads(transcript.repair(("invalid_schema",), 2_000).body)
        repair_guidance = repaired["messages"][-1]["content"]
        self.assertIn("valid JSON", repair_guidance)
        self.assertIn("coverage gaps", repair_guidance)
        self.assertIn("unable to confirm", repair_guidance)
        self.assertEqual(len(repaired["sourceTags"]), 1)

    def test_input_planner_uses_observed_wire_overhead_without_calling_provider(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("codeviz.assistant.retrieval_budget"))
        budget = importlib.import_module("codeviz.assistant.retrieval_budget")
        planner = budget.InputBudgetPlanner()
        small = ProviderDraft(b"x" * 1_000, 512, 100.0)
        planner.observe(small, 3_475)
        next_draft = ProviderDraft(b"x" * 14_100, 512, 100.0)

        self.assertEqual(planner.estimated_input(next_draft), 16_575)
        self.assertFalse(planner.fits(BudgetUsage(input_tokens=3_475), next_draft))

    def test_large_input_planner_accepts_work_beyond_the_legacy_ceiling(self) -> None:
        budget = importlib.import_module("codeviz.assistant.retrieval_budget")
        planner = budget.InputBudgetPlanner(EXPLANATION_BUDGET.max_input_tokens)
        draft = ProviderDraft(b"x" * 10_000, 512, 100.0)

        self.assertTrue(planner.fits(BudgetUsage(input_tokens=20_001), draft))
        self.assertFalse(planner.fits(BudgetUsage(input_tokens=90_001), draft))


def _unknown_answer() -> str:
    kinds = ("summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")
    sections: list[dict[str, object]] = [
        {"kind": kind, "claims": []} for kind in kinds
    ]
    sections[-1]["claims"] = [{
        "text": "Unable to confirm additional facts from the retained evidence.",
        "basis": "unknown",
        "evidenceIds": [],
        "relationEvidenceId": None,
    }]
    return json.dumps({"sections": sections}, separators=(",", ":"))


def _guidance_catalog(logical: dict[str, Any]) -> dict[str, list[str]]:
    content = logical["messages"][-1]["content"]
    return json.loads(next(line for line in content.splitlines()
                           if line.startswith('{"receiptEvidenceIds":')))


def _root_answer(logical: dict[str, Any]) -> str:
    evidence_id = next(
        json.loads(message["content"].splitlines()[0])["evidenceId"]
        for message in logical["messages"]
        if message.get("role") == "user" and message.get("content", "").startswith("{")
        and "evidenceId" in message["content"].splitlines()[0]
    )
    answer = json.loads(_unknown_answer())
    answer["sections"][0]["claims"] = [{
        "text": "The retained root excerpt is available as source evidence.",
        "basis": "static",
        "evidenceIds": [evidence_id],
        "relationEvidenceId": None,
    }]
    return json.dumps(answer, separators=(",", ":"))


class _SchedulingProvider:
    def __init__(self, *, tool_turns: int = 0, reject_bound: bool = False,
                 relation_turn: bool = False, search_limit: int = 20,
                 input_extra: int = 0, cite_root: bool = False) -> None:
        self.tool_turns = tool_turns
        self.reject_bound = reject_bound
        self.relation_turn = relation_turn
        self.search_limit = search_limit
        self.input_extra = input_extra
        self.cite_root = cite_root
        self.prepared: list[tuple[PreparedRequest, dict[str, Any]]] = []
        self.dispatches = 0
        self.closed = False

    async def prepare(self, draft: ProviderDraft) -> PreparedRequest:
        logical = cast(dict[str, Any], json.loads(draft.body))
        wire = json.dumps(
            {"messages": logical["messages"]},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        source = tuple(
            WireOccurrence(item["pointer"], item["start"], item["end"], item["file"])
            for item in logical["sourceTags"]
        )
        metadata = tuple(
            WireOccurrence(item["pointer"], item["start"], item["end"], item["file"])
            for item in logical.get("metadataTags", [])
        )
        input_bound = len(wire) + self.input_extra + (20_000 if self.reject_bound else 0)
        prepared = PreparedRequest(
            f"prepared-{len(self.prepared)}",
            "POST",
            "https://provider.invalid:443/v1/chat",
            wire,
            draft.max_output_tokens,
            draft.deadline,
            input_bound,
            source,
            metadata,
            True,
        )
        self.prepared.append((prepared, logical))
        return prepared

    async def dispatch(self, prepared: PreparedRequest) -> AsyncIterator[ProviderEvent]:
        self.dispatches += 1
        logical = next(value for handle, value in self.prepared if handle is prepared)
        if logical["mode"] == "tools" and self.relation_turn and self.dispatches == 1:
            yield ProviderEvent("tool", json.dumps({
                "id": "relations-1",
                "name": "get_local_relations",
                "arguments": {
                    "seed": {"kind": "code", "id": "seed"},
                    "hops": 2,
                    "max_nodes": 1,
                    "max_edges": 1,
                },
            }, separators=(",", ":")).encode("utf-8"))
        elif logical["mode"] == "tools" and self.dispatches <= self.tool_turns:
            yield ProviderEvent("tool", json.dumps({
                "id": f"search-{self.dispatches}",
                "name": "search_symbols",
                "arguments": {"query": "", "limit": self.search_limit},
            }, separators=(",", ":")).encode("utf-8"))
        elif logical["mode"] == "final":
            yield ProviderEvent("answer", json.dumps({
                "index": 0,
                "last": True,
                "content": _root_answer(logical) if self.cite_root else _unknown_answer(),
            }, separators=(",", ":")).encode("utf-8"))
        yield ProviderEvent(
            "usage",
            b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}',
        )

    async def cancel(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


class _ExcerptBudgetProvider(_SchedulingProvider):
    def __init__(self, *, force_input_pressure: bool = True,
                 cite_root: bool = False) -> None:
        super().__init__(cite_root=cite_root)
        self.force_input_pressure = force_input_pressure

    async def prepare(self, draft: ProviderDraft) -> PreparedRequest:
        prepared = await super().prepare(draft)
        if self.force_input_pressure and len(self.prepared) == 1:
            prepared = replace(prepared, input_token_upper_bound=8_000)
            self.prepared[-1] = (prepared, self.prepared[-1][1])
        return prepared

    async def dispatch(self, prepared: PreparedRequest) -> AsyncIterator[ProviderEvent]:
        self.dispatches += 1
        logical = next(value for handle, value in self.prepared if handle is prepared)
        if logical["mode"] == "tools" and self.dispatches == 1:
            yield ProviderEvent("tool", json.dumps({
                "id": "long-excerpt",
                "name": "read_source_excerpt",
                "arguments": {
                    "file_path": "app.py",
                    "start_line": 1,
                    "end_line": 1,
                },
            }, separators=(",", ":")).encode("utf-8"))
        elif logical["mode"] == "final":
            yield ProviderEvent("answer", json.dumps({
                "index": 0,
                "last": True,
                "content": _root_answer(logical) if self.cite_root else _unknown_answer(),
            }, separators=(",", ":")).encode("utf-8"))
        yield ProviderEvent(
            "usage",
            b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}',
        )


class _CatalogProvider(_ExcerptBudgetProvider):
    def __init__(self, *, invalid_first: bool = False) -> None:
        super().__init__(force_input_pressure=False)
        self.invalid_first = invalid_first
        self.catalogs: list[dict[str, list[str]]] = []

    async def dispatch(self, prepared: PreparedRequest) -> AsyncIterator[ProviderEvent]:
        logical = next(value for handle, value in self.prepared if handle is prepared)
        if logical["mode"] != "final":
            async for event in super().dispatch(prepared):
                yield event
            return
        self.dispatches += 1
        catalog = _guidance_catalog(logical)
        self.catalogs.append(catalog)
        ids = catalog["receiptEvidenceIds"]
        answer = json.loads(_unknown_answer())
        if ids or (self.invalid_first and len(self.catalogs) == 1):
            answer["sections"][0]["claims"] = [{
                "text": "Saved source is available.", "basis": "static",
                "evidenceIds": ["s0000" if self.invalid_first and len(self.catalogs) == 1 else ids[0]],
                "relationEvidenceId": None,
            }]
        raw = json.dumps(answer, separators=(",", ":"))
        if len(self.catalogs) == 2:
            assert len(raw.encode("utf-8")) <= 2_000
        yield ProviderEvent("answer", json.dumps({
            "index": 0, "last": True, "content": raw}, separators=(",", ":")).encode())
        yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')


class _OmittedRelationBindingProvider(_SchedulingProvider):
    async def prepare(self, draft: ProviderDraft) -> PreparedRequest:
        prepared = await super().prepare(draft)
        if len(self.prepared) == 1:
            prepared = replace(prepared, input_token_upper_bound=8_000)
            self.prepared[-1] = (prepared, self.prepared[-1][1])
        return prepared

    async def dispatch(self, prepared: PreparedRequest) -> AsyncIterator[ProviderEvent]:
        self.dispatches += 1
        logical = next(value for handle, value in self.prepared if handle is prepared)
        if logical["mode"] == "tools" and self.dispatches == 1:
            calls = [{
                "id": f"empty-search-{index}",
                "name": "search_symbols",
                "arguments": {"query": "no-such-symbol", "limit": 1},
            } for index in range(6)]
            calls.extend(({
                "id": "relations",
                "name": "get_local_relations",
                "arguments": {"seed": {"kind": "code", "id": "seed"},
                              "hops": 2, "max_nodes": 50, "max_edges": 100},
            }, {
                "id": "source",
                "name": "read_source_excerpt",
                "arguments": {"file_path": "app.py", "start_line": 1, "end_line": 1,
                              "start_column": 0, "end_column": 5},
            }))
            for call in calls:
                yield ProviderEvent("tool", json.dumps(
                    call, separators=(",", ":")).encode("utf-8"))
        elif logical["mode"] == "final":
            yield ProviderEvent("answer", json.dumps({
                "index": 0, "last": True,
                "content": _unknown_answer(),
            }, separators=(",", ":")).encode("utf-8"))
        yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')


class _RepairHeadroomProvider(_SchedulingProvider):
    def __init__(self, *, inflate_repair_source: bool = False,
                 inflate_repair_input: bool = False) -> None:
        super().__init__(tool_turns=1)
        self.inflate_repair_source = inflate_repair_source
        self.inflate_repair_input = inflate_repair_input
        self.final_dispatches = 0

    async def prepare(self, draft: ProviderDraft) -> PreparedRequest:
        prepared = await super().prepare(draft)
        if self.inflate_repair_input and self.final_dispatches == 1:
            prepared = replace(prepared, input_token_upper_bound=100_001)
            self.prepared[-1] = (prepared, self.prepared[-1][1])
        if self.inflate_repair_source and len(self.prepared) == 3:
            logical = self.prepared[-1][1]
            extra = next(
                WireOccurrence(f"/messages/{index}/content", 0, min(512, len(message["content"])),
                               "app.py")
                for index, message in enumerate(logical["messages"])
                if message.get("role") == "tool" and len(message.get("content", "")) >= 400
            )
            prepared = replace(
                prepared,
                source_occurrences=tuple(sorted(
                    (*prepared.source_occurrences, extra),
                    key=lambda item: (item.pointer, item.start, item.end, item.file),
                )),
            )
            self.prepared[-1] = (prepared, logical)
        return prepared

    async def dispatch(self, prepared: PreparedRequest) -> AsyncIterator[ProviderEvent]:
        logical = next(value for handle, value in self.prepared if handle is prepared)
        if logical["mode"] != "final":
            async for event in super().dispatch(prepared):
                yield event
            return
        self.dispatches += 1
        self.final_dispatches += 1
        answer = '{"sections":[]}' if self.final_dispatches == 1 else _unknown_answer()
        yield ProviderEvent("answer", json.dumps({
            "index": 0, "last": True, "content": answer,
        }, separators=(",", ":")).encode("utf-8"))
        yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')


class RetrievalBudgetServiceTests(unittest.TestCase):
    def test_service_receipt_catalog_and_empty_gap_preserve_registration_rules(self) -> None:
        for raw, retained in ((b"value = 1\n", True), (b"", False), (b"       \n", True)):
            with self.subTest(retained=retained, whitespace=raw == b"       \n"):
                snapshot, provider, service = self._run(
                    raw, _CatalogProvider(), production_budget=True)
                self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
                ids = provider.catalogs[0]["receiptEvidenceIds"]
                self.assertEqual(len(ids), int(retained))
                self.assertEqual(provider.catalogs[0]["relationEvidenceIds"], [])
                self.assertNotIn("s0000", ids)
                record = service._conversations.logical_state()["conversations"][0]
                answer = json.loads(record["results"][snapshot.job_id]["answer"])
                self.assertEqual([item["evidenceId"] for item in answer["evidence"]], ids)
                if not retained:
                    self.assertTrue(answer["incomplete"])
                    self.assertIn("No source characters", " ".join(answer["coverageGaps"]))
                    self.assertEqual(record["evidence"], {})

    def test_service_frozen_receipts_survive_compact_repair_and_root_first_final(self) -> None:
        snapshot, provider, service = self._run(
            b"value = 1\n", _CatalogProvider(invalid_first=True), production_budget=True)
        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        self.assertEqual(len(provider.catalogs), 2)
        self.assertEqual(provider.catalogs[0], provider.catalogs[1])
        record = service._conversations.logical_state()["conversations"][0]
        answer = json.loads(record["results"][snapshot.job_id]["answer"])
        self.assertTrue(answer["incomplete"])
        self.assertIn("condensed", " ".join(answer["coverageGaps"]).lower())

        raw = ('note = "' + "中" * 3_900 + '"\n').encode("utf-8")
        completed, root_provider, root_service = self._run(
            raw, _SchedulingProvider(cite_root=True), root=True)
        self.assertEqual(completed.status, JobStatus.COMPLETED, completed.issue)
        self.assertEqual(root_provider.dispatches, 1)
        catalog = _guidance_catalog(root_provider.prepared[0][1])
        root_record = root_service._conversations.logical_state()["conversations"][0]
        root_answer = json.loads(root_record["results"][completed.job_id]["answer"])
        self.assertEqual(catalog["receiptEvidenceIds"],
                         [item["evidenceId"] for item in root_answer["evidence"]])
        self.assertEqual(catalog["relationEvidenceIds"], [])

    def test_candidate_catalog_previews_do_not_bind_or_consume_receipts(self) -> None:
        tools = importlib.import_module("codeviz.assistant.tools")
        service_module = importlib.import_module("codeviz.assistant.service")
        original_factory = service_module.create_tool_session
        original_preview = ToolLoopTranscript.preview_tool_results_completion
        sessions: list[Any] = []
        observed: list[tuple[int, int, int]] = []

        def create(*args: Any, **kwargs: Any) -> Any:
            session = original_factory(*args, **kwargs)
            sessions.append(session)
            return session

        def preview(transcript: ToolLoopTranscript, *args: Any, **kwargs: Any) -> Any:
            session = sessions[-1]
            before = (len(session._bindings), len(session._receipts), session._registry.entry_count)
            draft = transcript.current(512)
            result = original_preview(transcript, *args, **kwargs)
            self.assertEqual(transcript.current(512), draft)
            self.assertEqual(before, (len(session._bindings), len(session._receipts),
                                      session._registry.entry_count))
            observed.append(before)
            return result

        with (patch.object(service_module, "create_tool_session", side_effect=create),
              patch.object(ToolLoopTranscript, "preview_tool_results_completion", new=preview),
              patch.object(tools.ToolSession, "bind_evidence", autospec=True,
                           side_effect=tools.ToolSession.bind_evidence) as bind):
            snapshot, _provider, _service = self._run(
                b"value = 1\n", _CatalogProvider(), production_budget=True)
            self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
            self.assertEqual(bind.call_count, 1)
        self.assertTrue(observed)
        self.assertTrue(all(value == (0, 1, 1) for value in observed))

    def test_empty_excerpt_gap_has_no_binding_and_removed_id_cannot_be_cited(self) -> None:
        tools = importlib.import_module("codeviz.assistant.tools")
        with patch.object(tools.ToolSession, "bind_evidence", autospec=True) as bind:
            snapshot, provider, service = self._run(
                b"", _CatalogProvider(invalid_first=True), production_budget=True)
            self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
            bind.assert_not_called()
        self.assertEqual(provider.catalogs, [
            {"receiptEvidenceIds": [], "relationEvidenceIds": []}] * 2)
        repair_message = provider.prepared[-1][1]["messages"][-1]["content"]
        self.assertIn("unprovided_id", repair_message)
        record = service._conversations.logical_state()["conversations"][0]
        self.assertEqual((record["evidence"], record["bindings"]), ({}, {}))

    def test_root_catalog_failures_keep_transport_closed(self) -> None:
        service_module = importlib.import_module("codeviz.assistant.service")
        tools = importlib.import_module("codeviz.assistant.tools")
        original_lookup = tools.ToolSession.lookup_binding
        raw = ('note = "' + "中" * 3_900 + '"\n').encode("utf-8")
        for failure in ("admit", "wire", "binding", "empty"):
            with self.subTest(failure=failure):
                provider = _SchedulingProvider(reject_bound=failure == "admit")

                def lookup(session: Any, *args: Any, bound_failure: str = failure,
                           bound_provider: _SchedulingProvider = provider, **kwargs: Any) -> Any:
                    if bound_failure == "binding" and bound_provider.prepared:
                        raise AgentError("evidence_unavailable")
                    return original_lookup(session, *args, **kwargs)

                with (patch.object(tools.ToolSession, "lookup_binding", new=lookup),
                      patch.object(service_module, "verify_root_wire",
                                   side_effect=service_module.ResponseError()
                                   if failure == "wire" else None,
                                   wraps=None if failure == "wire" else service_module.verify_root_wire)):
                    snapshot, provider, _service = self._run(
                        b"" if failure == "empty" else raw, provider, root=True)
                self.assertEqual(snapshot.status, JobStatus.FAILED)
                self.assertEqual(provider.dispatches, 0)
                self.assertTrue(snapshot.resources_released)

    @staticmethod
    def _wait(service: AssistantService, job_id: str) -> JobSnapshot:
        deadline = time.monotonic() + 5
        snapshot = service.get(job_id)
        while not snapshot.resources_released and time.monotonic() < deadline:
            time.sleep(0.005)
            snapshot = service.get(job_id)
        return snapshot

    def _run(
        self,
        raw: bytes,
        provider: _SchedulingProvider,
        *,
        root: bool = False,
        seed_search: bool = False,
        production_budget: bool = False,
        explanation_budget: object | None = None,
    ) -> tuple[JobSnapshot, _SchedulingProvider, AssistantService]:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name).resolve()
        case = make_source_case(project, raw)
        case.consent = replace(case.consent, provider_origin="https://provider.invalid:443")
        binding = case.snapshot.binding
        identity = PublicationIdentity(
            1,
            binding.project_epoch,
            binding.analysis_generation,
            os.path.normcase(os.path.normpath(str(project))),
            project,
            (),
            AnalysisStatus.READY,
            True,
        )
        fingerprint = scope_fingerprint(case.consent.allowed_files)
        project_key = identity.project_key
        assert project_key is not None
        authority = AuthorityState(
            True,
            1,
            1,
            project_key,
            "https://provider.invalid:443",
            fingerprint,
            (("conversation", 0),),
            "fake",
            "https://provider.invalid:443/v1/chat",
        )
        logical = {
            "version": 1,
            "mode": "tools",
            "messages": [{"role": "user", "content": "Explain."}],
            "tools": [
                {
                    "name": "search_symbols",
                    "description": "Search symbols.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
                {
                    "name": "read_source_excerpt",
                    "description": "Read source proof.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
                {
                    "name": "get_local_relations",
                    "description": "Read local graph relations.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            ],
            "answerSchema": None,
            "sourceTags": [],
            "metadataTags": [],
        }
        budget_config = (EXPLANATION_BUDGET if production_budget else
                         cast(Any, explanation_budget))
        request = JobRequest(
            RequestScope(binding.project_epoch, binding.analysis_generation, "budget-service"),
            "conversation",
            "Explain.",
            "context",
            "fake",
            1,
            1,
            fingerprint,
            ProviderDraft(
                json.dumps(logical, separators=(",", ":")).encode("utf-8"),
                512 if budget_config is None else budget_config.final_output_tokens,
                time.monotonic() + 120,
            ),
            identity,
            RootRetrieval(
                AnchorRecord("app.py", 1, None, 1, None),
                EntityRef("code", "seed"),
            ) if root else None,
            seed_search,
            budget=budget_config,
        )
        service = AssistantService(
            lambda: provider,
            explanation_budget=budget_config,
        )
        self.addCleanup(service.shutdown)
        service._replace_publication(identity, case.snapshot)
        service._replace_source_consent(case.consent)
        service._replace_authority(authority)
        started = service.start(request)
        return self._wait(service, started.job_id), provider, service

    def test_deepseek_budget_runs_four_retrieval_rounds_then_full_final(self) -> None:
        deepseek = importlib.import_module(
            "codeviz.assistant._deepseek_admission")._deepseek_explanation_budget()
        snapshot, provider, service = self._run(
            b"value = 1\n", _SchedulingProvider(tool_turns=8),
            explanation_budget=deepseek)

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        self.assertEqual([logical["mode"] for _handle, logical in provider.prepared],
                         ["tools"] * 4 + ["final"])
        self.assertEqual([handle.max_output_tokens for handle, _logical in provider.prepared],
                         [512] * 4 + [4_096])
        record = service._conversations.logical_state()["conversations"][0]
        answer = json.loads(record["results"][snapshot.job_id]["answer"])
        self.assertTrue(answer["incomplete"])

    def test_configured_retrieval_preserves_input_for_one_repair(self) -> None:
        deepseek = importlib.import_module(
            "codeviz.assistant._deepseek_admission")._deepseek_explanation_budget()
        provider = _RepairHeadroomProvider()
        provider.tool_turns = 4
        provider.input_extra = 16_000
        snapshot, provider, _service = self._run(
            b"value = 1\n", provider, explanation_budget=deepseek)

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        self.assertEqual(provider.final_dispatches, 2)
        self.assertLess(provider.dispatches - provider.final_dispatches, 4)
        reserved_input = sum(
            cast(int, handle.input_token_upper_bound) for handle, _logical in provider.prepared)
        self.assertLessEqual(reserved_input, deepseek.max_input_tokens)
        self.assertTrue(snapshot.resources_released)

    def test_deepseek_invalid_final_has_one_reserved_repair(self) -> None:
        deepseek = importlib.import_module(
            "codeviz.assistant._deepseek_admission")._deepseek_explanation_budget()
        snapshot, provider, _service = self._run(
            b"value = 1\n", _RepairHeadroomProvider(),
            explanation_budget=deepseek)

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        self.assertEqual(provider.final_dispatches, 2)
        self.assertEqual([handle.max_output_tokens for handle, _logical in provider.prepared],
                         [512, 512, 4_096, 2_000])

    def test_configured_projection_keeps_repair_input_headroom_without_false_evidence(self) -> None:
        deepseek = importlib.import_module(
            "codeviz.assistant._deepseek_admission")._deepseek_explanation_budget()
        provider = _ExcerptBudgetProvider(force_input_pressure=False)
        provider.input_extra = 6_000
        snapshot, provider, service = self._run(
            ('note = "' + 'x' * 2_000 + '"\n').encode(), provider,
            explanation_budget=replace(deepseek, max_input_tokens=24_000))

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        handle, logical = provider.prepared[-1]
        projected = next(json.loads(message["content"])["result"]
                         for message in logical["messages"] if message.get("role") == "tool")
        self.assertEqual(projected["kind"], "retrieval_gap")
        self.assertEqual(handle.source_occurrences, ())
        record = service._conversations.logical_state()["conversations"][0]
        answer = json.loads(record["results"][snapshot.job_id]["answer"])
        self.assertTrue(answer["incomplete"])
        self.assertEqual(answer["evidence"], [])

    def test_unexpected_repair_wire_overhead_still_fails_before_dispatch(self) -> None:
        deepseek = importlib.import_module(
            "codeviz.assistant._deepseek_admission")._deepseek_explanation_budget()
        snapshot, provider, service = self._run(
            b"value = 1\n", _RepairHeadroomProvider(inflate_repair_input=True),
            explanation_budget=deepseek)

        self.assertEqual(snapshot.status, JobStatus.FAILED)
        self.assertEqual(snapshot.issue.code, "AI_BUDGET_EXCEEDED")
        self.assertEqual(provider.final_dispatches, 1)
        self.assertEqual(provider.prepared[-1][0].max_output_tokens, 2_000)
        self.assertEqual(len(provider.prepared), provider.dispatches + 1)
        self.assertTrue(snapshot.resources_released)
        record = service._conversations.logical_state()["conversations"][0]
        self.assertEqual(record["results"], {})

    def test_deepseek_live_shape_keeps_full_final_after_three_ordinary_sends(self) -> None:
        deepseek = importlib.import_module(
            "codeviz.assistant._deepseek_admission")._deepseek_explanation_budget()
        snapshot, provider, _service = self._run(
            b"value = 1\n", _SchedulingProvider(tool_turns=2),
            explanation_budget=deepseek)

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        self.assertEqual([handle.max_output_tokens for handle, _logical in provider.prepared],
                         [512, 512, 512, 4_096])

    def test_deepseek_invalid_transcript_never_prepares_initial_4k_tools_draft(self) -> None:
        deepseek = importlib.import_module(
            "codeviz.assistant._deepseek_admission")._deepseek_explanation_budget()
        provider = _SchedulingProvider()
        with patch("codeviz.assistant.service.ToolLoopTranscript.maybe_create", return_value=None):
            snapshot, provider, _service = self._run(
                b"value = 1\n", provider, explanation_budget=deepseek)

        self.assertEqual(snapshot.status, JobStatus.FAILED)
        self.assertEqual(snapshot.issue.code, "AI_PROTOCOL_INCOMPATIBLE")
        self.assertEqual((len(provider.prepared), provider.dispatches), (0, 0))

    def test_long_root_goes_directly_to_final_without_spending_an_ordinary_send(self) -> None:
        raw = ('note = "' + "中" * 3_900 + '"\n').encode("utf-8")

        snapshot, provider, service = self._run(raw, _SchedulingProvider(), root=True)

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, (
            snapshot.issue,
            provider.dispatches,
            [(logical["mode"], len(handle.body), handle.input_token_upper_bound,
              handle.max_output_tokens) for handle, logical in provider.prepared],
        ))
        self.assertEqual(provider.dispatches, 1)
        self.assertEqual([logical["mode"] for _handle, logical in provider.prepared], ["final"])
        self.assertEqual(provider.prepared[0][0].max_output_tokens, 512)
        record = service._conversations.logical_state()["conversations"][0]
        answer = json.loads(record["results"][snapshot.job_id]["answer"])
        self.assertTrue(answer["incomplete"])
        self.assertIn("preserve the final-answer budget", answer["coverageGaps"][0])

    def test_ordinary_sends_stop_with_exactly_512_reserved_for_final(self) -> None:
        snapshot, provider, _service = self._run(
            b"value = 1\n", _SchedulingProvider(tool_turns=5))

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, (
            snapshot.issue,
            provider.dispatches,
            [(logical["mode"], len(handle.body), handle.input_token_upper_bound,
              handle.max_output_tokens) for handle, logical in provider.prepared],
        ))
        self.assertEqual(
            [logical["mode"] for _handle, logical in provider.prepared],
            ["tools", "tools", "tools", "final"],
        )
        self.assertEqual([handle.max_output_tokens for handle, _ in provider.prepared],
                         [512, 512, 464, 512])

    def test_actual_admission_rejection_never_dispatches_or_retries(self) -> None:
        snapshot, provider, _service = self._run(
            b"value = 1\n",
            _SchedulingProvider(reject_bound=True),
        )

        self.assertEqual(snapshot.status, JobStatus.FAILED)
        self.assertEqual(snapshot.issue.code, "AI_BUDGET_EXCEEDED")
        self.assertEqual(len(provider.prepared), 1)
        self.assertEqual(provider.dispatches, 0)

    def test_production_budget_runs_eight_retrieval_rounds_beyond_legacy_input(self) -> None:
        snapshot, provider, _service = self._run(
            b"value = 1\n",
            _SchedulingProvider(tool_turns=8, input_extra=3_000),
            production_budget=True,
        )

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        self.assertEqual(provider.dispatches, 9)
        reserved_input = sum(
            cast(int, handle.input_token_upper_bound)
            for handle, _logical in provider.prepared
        )
        self.assertGreater(reserved_input, 20_000)
        self.assertLessEqual(reserved_input, 100_000)
        self.assertEqual(
            [logical["mode"] for _handle, logical in provider.prepared],
            ["tools"] * 8 + ["final"],
        )
        self.assertEqual(
            [handle.max_output_tokens for handle, _logical in provider.prepared],
            [512] * 8 + [2_000],
        )

    def test_source_headroom_ends_retrieval_before_hard_rejection_and_keeps_root_proof(self) -> None:
        raw = ('note = "' + "中" * 3_900 + '"\n').encode("utf-8")
        snapshot, provider, service = self._run(
            raw, _SchedulingProvider(tool_turns=8, cite_root=True),
            root=True, production_budget=True)

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        self.assertEqual(provider.dispatches, 2)
        self.assertEqual([logical["mode"] for _handle, logical in provider.prepared],
                         ["tools", "final"])
        sent_source_characters = sum(
            occurrence.end - occurrence.start
            for handle, _logical in provider.prepared
            for occurrence in handle.source_occurrences
        )
        self.assertLessEqual(sent_source_characters, 12_000)
        final_handle = provider.prepared[-1][0]
        self.assertTrue(final_handle.source_occurrences)
        record = service._conversations.logical_state()["conversations"][0]
        answer = json.loads(record["results"][snapshot.job_id]["answer"])
        self.assertEqual(len(answer["evidence"]), 1)
        evidence_id = answer["evidence"][0]["evidenceId"]
        lease = service._borrow_evidence("conversation", evidence_id)
        try:
            self.assertEqual(lease.resolve_evidence(evidence_id).proof.evidence_id, evidence_id)
        finally:
            lease.release()
        self.assertTrue(answer["incomplete"])
        self.assertIn("preserve the final-answer budget", " ".join(answer["coverageGaps"]))

    def test_source_headroom_omits_new_excerpt_without_removing_existing_root_proof(self) -> None:
        raw = ('note = "' + "中" * 3_900 + '"\n').encode("utf-8")
        snapshot, provider, service = self._run(
            raw, _ExcerptBudgetProvider(force_input_pressure=False, cite_root=True),
            root=True, production_budget=True)

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        final_handle, final_logical = provider.prepared[-1]
        projected = next(json.loads(message["content"])["result"]
                         for message in final_logical["messages"]
                         if message.get("role") == "tool")
        self.assertEqual(projected["omittedTypes"], ["source_excerpt"])
        self.assertNotIn("evidenceId", json.dumps(projected))
        self.assertNotIn("app.py", json.dumps(projected))
        self.assertEqual(len(final_handle.source_occurrences), 1)
        record = service._conversations.logical_state()["conversations"][0]
        answer = json.loads(record["results"][snapshot.job_id]["answer"])
        self.assertEqual(len(answer["evidence"]), 1)
        evidence_id = answer["evidence"][0]["evidenceId"]
        lease = service._borrow_evidence("conversation", evidence_id)
        try:
            self.assertEqual(lease.resolve_evidence(evidence_id).proof.evidence_id, evidence_id)
        finally:
            lease.release()
        self.assertTrue(answer["incomplete"])
        self.assertTrue(any("source_excerpt" in gap for gap in answer["coverageGaps"]))

    def test_ninth_file_projection_falls_back_to_complete_zero_item_omission(self) -> None:
        budget = importlib.import_module("codeviz.assistant.retrieval_budget")
        planner_type = budget.SourceHeadroomPlanner
        seeded_files = ("app.py", *(f"base-{index}.py" for index in range(1, 8)))

        class SeededPlanner:
            def __init__(self, limits: BudgetLimits) -> None:
                self._planner = planner_type(limits)

            def fits(self, usage: BudgetUsage, *drafts: ProviderDraft) -> bool:
                return self._planner.fits(replace(usage, files=seeded_files), *drafts)

        def ninth_file_projection(result: object, *, snapshot: object,
                                  maximum_bytes: int | None = None) -> ToolProjection:
            projection = project_tool_result(
                cast(Any, result), snapshot=cast(Any, snapshot), maximum_bytes=maximum_bytes)
            if not projection.targets:
                return projection
            metadata = tuple((start, end, "file-8.py")
                             for start, end, _path in projection.metadata_spans)
            return ToolProjection(
                projection.content, projection.source_spans, metadata, projection.targets)

        raw = b"root = 1\n"
        with (
            patch("codeviz.assistant.service.SourceHeadroomPlanner", SeededPlanner),
            patch("codeviz.assistant.service.project_tool_result",
                  side_effect=ninth_file_projection),
        ):
            snapshot, provider, service = self._run(
                raw, _SchedulingProvider(tool_turns=1, cite_root=True),
                root=True, production_budget=True)

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        final_handle, final_logical = provider.prepared[-1]
        projected = next(json.loads(message["content"])["result"]
                         for message in final_logical["messages"]
                         if message.get("role") == "tool")
        self.assertEqual(projected["kind"], "symbol_search")
        self.assertEqual(projected["results"], [])
        self.assertGreater(projected["omittedCount"], 0)
        self.assertFalse(any(item.file == "file-8.py"
                             for item in final_handle.metadata_occurrences))
        record = service._conversations.logical_state()["conversations"][0]
        answer = json.loads(record["results"][snapshot.job_id]["answer"])
        self.assertTrue(answer["incomplete"])
        self.assertEqual(len(answer["evidence"]), 1)

    def test_repair_uses_reserved_source_headroom_and_wire_overrun_never_dispatches(self) -> None:
        raw = ('note = "' + "中" * 3_900 + '"\n').encode("utf-8")
        completed, allowed, _service = self._run(
            raw, _RepairHeadroomProvider(), root=True, production_budget=True)
        self.assertEqual(completed.status, JobStatus.COMPLETED, completed.issue)
        self.assertEqual(allowed.final_dispatches, 2)
        self.assertEqual([logical["mode"] for _handle, logical in allowed.prepared],
                         ["tools", "final", "final"])

        failed, rejected, failed_service = self._run(
            raw, _RepairHeadroomProvider(inflate_repair_source=True),
            root=True, production_budget=True)
        self.assertEqual(failed.status, JobStatus.FAILED)
        self.assertEqual(failed.issue.code, "AI_BUDGET_EXCEEDED")
        self.assertEqual((len(rejected.prepared), rejected.dispatches), (3, 2))
        record = failed_service._conversations.logical_state()["conversations"][0]
        self.assertEqual(record["results"], {})
        self.assertTrue(record["evidence"])

    def test_production_input_rejection_remains_pre_dispatch_and_is_not_retried(self) -> None:
        snapshot, provider, _service = self._run(
            b"value = 1\n",
            _SchedulingProvider(input_extra=100_000),
            production_budget=True,
        )

        self.assertEqual(snapshot.status, JobStatus.FAILED)
        self.assertEqual(snapshot.issue.code, "AI_BUDGET_EXCEEDED")
        self.assertEqual(len(provider.prepared), 1)
        self.assertEqual(provider.dispatches, 0)

    def test_empty_seed_search_is_not_forwarded_as_a_false_retrieval_result(self) -> None:
        snapshot, provider, _service = self._run(
            b"value = 1\n",
            _SchedulingProvider(),
            seed_search=True,
        )

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        first = provider.prepared[0][1]
        self.assertFalse(any(message["role"] == "tool" for message in first["messages"]))

    def test_omitted_excerpt_never_registers_receipt_and_marks_coverage_gap(self) -> None:
        raw = ('note = "' + "中" * 3_900 + '"\n').encode("utf-8")

        snapshot, provider, service = self._run(raw, _ExcerptBudgetProvider())

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        self.assertEqual([logical["mode"] for _handle, logical in provider.prepared],
                         ["tools", "final"])
        final_handle, final_logical = provider.prepared[-1]
        projected = next(
            json.loads(message["content"])["result"]
            for message in final_logical["messages"]
            if message["role"] == "tool"
        )
        self.assertEqual(projected["omittedTypes"], ["source_excerpt"])
        self.assertNotIn("evidenceId", json.dumps(projected))
        self.assertEqual(final_handle.source_occurrences, ())
        self.assertEqual(_guidance_catalog(final_logical), {
            "receiptEvidenceIds": [], "relationEvidenceIds": []})
        record = service._conversations.logical_state()["conversations"][0]
        self.assertEqual(record["evidence"], {})
        self.assertEqual(record["bindings"], {})
        answer = json.loads(record["results"][snapshot.job_id]["answer"])
        self.assertTrue(answer["incomplete"])
        self.assertTrue(any("source_excerpt" in gap for gap in answer["coverageGaps"]))

    def test_omitted_relation_in_same_batch_is_not_trusted_for_excerpt_binding(self) -> None:
        observed: list[tuple[TrustedTarget, ...]] = []

        def observing_target(
            anchor: AnchorRecord,
            candidates: tuple[TrustedTarget, ...],
        ) -> EntityRef | RelationKey | None:
            observed.append(candidates)
            return choose_target(anchor, candidates)

        with patch("codeviz.assistant.service.choose_target", side_effect=observing_target):
            snapshot, provider, _service = self._run(
                b"value = 1\n", _OmittedRelationBindingProvider())

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        final_logical = provider.prepared[-1][1]
        results = [json.loads(message["content"])["result"]
                   for message in final_logical["messages"] if message.get("role") == "tool"]
        relations = next(value for value in results if value["kind"] == "local_relations")
        self.assertGreater(relations["omittedCount"]["relations"], 0)
        kept = {(item["key"]["collectionKind"], item["key"]["originalIndex"])
                for item in relations["relations"]}
        trusted = {(candidate.target.collection_kind, candidate.target.original_index)
                   for candidate in observed[-1]
                   if not isinstance(candidate.target, EntityRef)}
        self.assertEqual(trusted, kept)

    def test_source_truncated_relations_mark_answer_incomplete_without_fake_count(self) -> None:
        snapshot, provider, service = self._run(
            b"value = 1\n",
            _SchedulingProvider(relation_turn=True),
        )

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        final_logical = provider.prepared[-1][1]
        projected = next(
            json.loads(message["content"])["result"]
            for message in final_logical["messages"]
            if message["role"] == "tool"
        )
        self.assertTrue(projected["truncated"])
        self.assertNotIn("omittedCount", projected)
        self.assertIn("coverageGap", projected)
        record = service._conversations.logical_state()["conversations"][0]
        answer = json.loads(record["results"][snapshot.job_id]["answer"])
        self.assertTrue(answer["incomplete"])
        self.assertTrue(any("truncated" in gap for gap in answer["coverageGaps"]))

    def test_bounded_search_page_marks_answer_incomplete_without_budget_count(self) -> None:
        snapshot, provider, service = self._run(
            b"value = 1\n",
            _SchedulingProvider(tool_turns=1, search_limit=1),
        )

        self.assertEqual(snapshot.status, JobStatus.COMPLETED, snapshot.issue)
        final_logical = provider.prepared[-1][1]
        projected = next(
            json.loads(message["content"])["result"]
            for message in final_logical["messages"]
            if message["role"] == "tool"
        )
        self.assertTrue(projected["truncated"])
        self.assertNotIn("omittedCount", projected)
        self.assertNotIn("budget", projected["coverageGap"].lower())
        record = service._conversations.logical_state()["conversations"][0]
        answer = json.loads(record["results"][snapshot.job_id]["answer"])
        self.assertTrue(answer["incomplete"])
        self.assertTrue(any("bounded" in gap for gap in answer["coverageGaps"]))


if __name__ == "__main__":
    unittest.main()
