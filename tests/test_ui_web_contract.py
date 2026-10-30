import ast
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codeviz.core import (
    Confidence,
    EdgeType,
    SourceAnchor,
    ViewIssue,
    ViewKind,
    VisibleAggregation,
    VisibleEdge,
    VisibleGraph,
    VisibleLegendItem,
    VisibleNode,
)
from codeviz.navigation import RevealTarget
from codeviz.persistence.store import CacheStore
from codeviz.search import SearchResponse, SearchResult
from codeviz.ui import web_contract
from codeviz.ui.controller import AnalysisStatus, SourceDocument
from codeviz.ui.presenter import (
    HealthData,
    InspectorData,
    OverviewChoice,
    OverviewData,
    ProblemData,
    RelationData,
)
from codeviz.ui.web_bridge import DesktopBridge
from codeviz.ui.web_contract import (
    SCHEMA_VERSION,
    error_payload,
    graph_payload,
    inspector_payload,
    overview_payload,
    preferences_payload,
    problem_payload,
    reveal_target_payload,
    search_response_payload,
    session_payload,
    source_payload,
    success_payload,
    view_state_payload,
)
from codeviz.views.state import DetailIslandState, ViewportState, ViewState


class WebContractTests(unittest.TestCase):
    def test_assistant_failure_envelope_never_formats_raw_diagnostics(self) -> None:
        builder = getattr(web_contract, "assistant_error_payload", None)
        self.assertTrue(callable(builder), "Missing assistant error envelope")
        self.assertEqual(builder("AI_EVIDENCE_INVALID"), {
            "ok": False, "schemaVersion": SCHEMA_VERSION,
            "error": {"code": "AI_EVIDENCE_INVALID",
                      "message": "Assistant evidence validation failed."},
        })

        class Diagnostic:
            def __str__(self):
                raise AssertionError("Raw assistant diagnostics must not be formatted")

        self.assertEqual(builder(Diagnostic()), builder("AI_PROTOCOL_INCOMPATIBLE"))

    def test_graph_payload_is_json_safe_and_preserves_visible_contract(self) -> None:
        anchor = SourceAnchor("model.py", 3, 4, 5, 8)
        node = VisibleNode(
            "model",
            "Model",
            semantic_component_ids=("semantic:model",),
            code_node_ids=("code:model",),
            source_anchors=(anchor,),
            code_name="pkg.Model",
            role="ModelDefinition",
            confidence=Confidence.EXACT,
            metadata={"has_children": True, "shape": (None, 32)},
        )
        child = VisibleNode("child", "Child", member_ids=("hidden",))
        edge = VisibleEdge(
            "model",
            "child",
            EdgeType.CONTAINS,
            code_edge_ids=("edge:1",),
            source_anchors=(anchor,),
            confidence=Confidence.PROBABLE,
        )
        graph = VisibleGraph(
            ViewKind.ARCHITECTURE,
            (node, child),
            root_ids=("model",),
            edges=(edge,),
            aggregations=(VisibleAggregation("child", ("hidden",)),),
            layout_kind="hierarchical",
            layout_hints={"model": (12.5, 24.0)},
            legend=(VisibleLegendItem(EdgeType.CONTAINS, "Contains", "dotted"),),
            hidden_counts={"nodes": 1},
            query_context={"root_id": "model", "hops": 1},
            issues=(ViewIssue("LAYOUT_FALLBACK", "fallback", ViewKind.ARCHITECTURE),),
        )

        payload = graph_payload(graph)

        self.assertEqual(payload["viewKind"], "Architecture")
        self.assertEqual(payload["nodes"][0]["confidence"], "Exact")  # type: ignore[index]
        self.assertEqual(payload["nodes"][0]["sourceAnchors"][0], {  # type: ignore[index]
            "filePath": "model.py",
            "startLine": 3,
            "startColumn": 4,
            "endLine": 5,
            "endColumn": 8,
        })
        self.assertEqual(payload["nodes"][0]["metadata"], {  # type: ignore[index]
            "hasChildren": True,
            "shape": [None, 32],
        })
        self.assertEqual(payload["layoutHints"], {"model": [12.5, 24.0]})
        self.assertEqual(payload["queryContext"], {"rootId": "model", "hops": 1})
        json.dumps(payload, allow_nan=False)

    def test_overview_payload_is_json_safe(self) -> None:
        data = OverviewData(
            "demo",
            "C:/demo",
            "Python",
            "PyTorch",
            (OverviewChoice("model", "Model", SourceAnchor("model.py", 2)),),
            (OverviewChoice("train", "train", SourceAnchor("train.py", 4)),),
            ("src", "tests"),
            HealthData(2, 3, 4, 5, 6, 7, 8),
        )

        payload = overview_payload(data)

        self.assertEqual(payload["models"][0]["sourceAnchor"]["filePath"],  # type: ignore[index]
                         "model.py")
        self.assertEqual(payload["mainAreas"], ["src", "tests"])
        self.assertEqual(payload["health"]["filesParsed"], 2)  # type: ignore[index]
        json.dumps(payload, allow_nan=False)

    def test_inspector_payload_is_json_safe(self) -> None:
        data = InspectorData(
            "node",
            "Node",
            "pkg.Node",
            "Layer",
            Confidence.PROBABLE,
            ("static evidence",),
            ("child",),
            (RelationData(EdgeType.CALLS, "callee", "out", Confidence.EXACT),),
            (("shape", "[1, 2]"),),
            (SourceAnchor("model.py", 7),),
            ("member",),
            True,
        )

        payload = inspector_payload(data)

        self.assertEqual(payload["confidence"], "Probable")
        self.assertEqual(payload["relations"][0]["type"], "CALLS")  # type: ignore[index]
        self.assertEqual(payload["data"], [["shape", "[1, 2]"]])
        self.assertEqual(payload["sourceAnchors"][0]["endLine"], 7)  # type: ignore[index]
        json.dumps(payload, allow_nan=False)

    def test_problem_and_source_payloads_are_json_safe(self) -> None:
        problem = problem_payload(ProblemData("PARSE", "bad syntax", "bad.py", 9))
        source = source_payload(SourceDocument("train.py", "print('ok')\n", 1, 1))

        self.assertEqual(problem, {
            "code": "PARSE",
            "message": "bad syntax",
            "filePath": "bad.py",
            "line": 9,
            "canOpenSource": True,
        })
        self.assertEqual(source, {
            "filePath": "train.py",
            "text": "print('ok')\n",
            "startLine": 1,
            "endLine": 1,
        })
        json.dumps(problem, allow_nan=False)
        json.dumps(source, allow_nan=False)

    def test_envelopes_are_versioned_and_do_not_leak_exception_details(self) -> None:
        success = success_payload({"value": 1}, "request-0")
        invalid = error_payload(ValueError("secret invalid detail"), "request-1")
        invalid_type = error_payload(TypeError("secret type detail"))
        safe_invalid = error_payload(
            TypeError("secret type detail"), safe_message="hops must be between 1 and 5")
        not_ready = error_payload(RuntimeError("secret controller state"))
        internal = error_payload(Exception("secret filesystem detail"))

        self.assertEqual(success, {
            "ok": True,
            "schemaVersion": SCHEMA_VERSION,
            "requestId": "request-0",
            "data": {"value": 1},
        })
        self.assertEqual(invalid, {
            "ok": False,
            "schemaVersion": SCHEMA_VERSION,
            "requestId": "request-1",
            "error": {
                "code": "INVALID_REQUEST",
                "message": "The request is invalid.",
            },
        })
        self.assertEqual(safe_invalid["error"], {
            "code": "INVALID_REQUEST",
            "message": "hops must be between 1 and 5",
        })
        self.assertEqual(invalid_type["error"], {
            "code": "INVALID_REQUEST",
            "message": "The request is invalid.",
        })
        self.assertEqual(not_ready["error"], {
            "code": "NOT_READY",
            "message": "The requested data is not ready.",
        })
        self.assertEqual(internal, {
            "ok": False,
            "schemaVersion": SCHEMA_VERSION,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An internal error occurred.",
            },
        })
        encoded = json.dumps(
            [success, invalid, invalid_type, safe_invalid, not_ready, internal],
            allow_nan=False)
        self.assertNotIn("secret", encoded)

    def test_success_envelope_rejects_non_json_values_recursively(self) -> None:
        invalid_values = (
            object(),
            ast.parse("value = 1"),
            Path("project.py"),
            Confidence.EXACT,
            ("tuple",),
            float("nan"),
            float("inf"),
            {"nested": [object()]},
            {1: "non-string key"},
        )

        for value in invalid_values:
            with self.subTest(value=type(value).__name__):
                with self.assertRaises((TypeError, ValueError)):
                    success_payload(value)  # type: ignore[arg-type]

        cyclic: list[object] = []
        cyclic.append(cyclic)
        with self.assertRaisesRegex(ValueError, "cyclic"):
            success_payload(cyclic)

        payload = success_payload({
            "none": None,
            "bool": True,
            "string": "value",
            "int": 1,
            "float": 1.5,
            "list": [{"nested": False}],
        })
        json.dumps(payload, allow_nan=False)

    def test_every_dto_can_be_wrapped_in_a_success_envelope(self) -> None:
        anchor = SourceAnchor("model.py", 1, 0, 1, 4)
        graph = VisibleGraph(
            ViewKind.SYMBOL,
            (VisibleNode("node", "Node", code_node_ids=("node",),
                         source_anchors=(anchor,)),),
            root_ids=("node",),
            hidden_counts={"nodes": 0},
        )
        overview = OverviewData(
            "demo", "C:/demo", "Python", None,
            (OverviewChoice("node", "Node", anchor),), (), (),
            HealthData(1, 1, 1, 1, 0, 0, 0),
        )
        inspector = InspectorData(
            "node", "Node", "Node", None, Confidence.EXACT,
            (), (), (), (), (anchor,), (), True,
        )
        result = SearchResult(
            "node", "symbol", "Node", "Node", "Class", "module",
            anchor, Confidence.EXACT,
        )
        dtos = (
            graph_payload(graph),
            overview_payload(overview),
            inspector_payload(inspector),
            problem_payload(ProblemData("ISSUE", "message", "model.py", 1)),
            source_payload(SourceDocument("model.py", "class Node: ...\n", 1, 1)),
            session_payload(
                Path("C:/demo"), AnalysisStatus.READY, (), None, (Path("C:/demo"),)),
            search_response_payload(
                SearchResponse((result,), 1, False),
                (RevealTarget(ViewKind.SYMBOL, "node", "node"),),
            ),
            preferences_payload("en", None),
            view_state_payload(ViewState()),
        )

        envelopes = [success_payload(dto) for dto in dtos]

        self.assertTrue(all(envelope["ok"] is True for envelope in envelopes))
        json.dumps(envelopes, allow_nan=False)

    def test_success_envelope_clones_nested_mutable_data(self) -> None:
        original = {"nested": {"items": [1, {"name": "before"}]}}

        envelope = success_payload(original)  # type: ignore[arg-type]
        original["nested"]["items"].append(2)  # type: ignore[index,union-attr]
        original["nested"]["items"][1]["name"] = "after"  # type: ignore[index]
        original["added"] = True

        self.assertEqual(envelope["data"], {
            "nested": {"items": [1, {"name": "before"}]},
        })
        json.dumps(envelope, allow_nan=False)

    def test_source_anchor_payload_rejects_invalid_line_and_column_values(self) -> None:
        invalid_fields = {
            "start_line": (None, 0, -1, True, 1.0, float("nan")),
            "end_line": (None, 0, -1, True, 2.0, float("nan")),
            "start_column": (-1, True, 1.0, float("nan")),
            "end_column": (-1, True, 1.0, float("nan")),
        }
        for field, values in invalid_fields.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    anchor = SourceAnchor("model.py", 1, 0, 1, 0)
                    object.__setattr__(anchor, field, value)
                    data = OverviewData(
                        "demo", "C:/demo", "Python", None,
                        (OverviewChoice("model", "Model", anchor),), (), (),
                        HealthData(1, 1, 1, 1, 0, 0, 0),
                    )
                    with self.assertRaisesRegex(ValueError, field):
                        overview_payload(data)

        anchor = SourceAnchor("model.py", 1)
        payload = overview_payload(OverviewData(
            "demo", "C:/demo", "Python", None,
            (OverviewChoice("model", "Model", anchor),), (), (),
            HealthData(1, 1, 1, 1, 0, 0, 0),
        ))
        json.dumps(payload, allow_nan=False)

    def test_graph_payload_rejects_invalid_hidden_counts(self) -> None:
        invalid_entries = (
            (1, 0),
            ("nodes", True),
            ("nodes", -1),
            ("nodes", 1.0),
        )
        for key, value in invalid_entries:
            with self.subTest(key=key, value=value):
                graph = VisibleGraph(
                    ViewKind.SYMBOL,
                    (VisibleNode("node", "Node", code_node_ids=("node",)),),
                )
                graph.hidden_counts[key] = value  # type: ignore[index]
                with self.assertRaisesRegex(ValueError, "hiddenCounts"):
                    graph_payload(graph)

    def test_session_preferences_and_view_state_payloads_are_complete(self) -> None:
        state = ViewState(
            expanded_ids=("expanded",),
            positions={"legacy": (1.0, 2.0)},
            active_view=ViewKind.ARCHITECTURE,
            selected_id="selected",
            open_tabs=("Overview", "Architecture:model"),
            graph_roots={"Architecture": "model", "Execution": None},
            zoom=1.25,
            pan=(3.0, 4.0),
            filters=("Exact",),
            viewport_by_view={"Architecture": ViewportState(1.5, (5.0, 6.0))},
            detail_islands=(DetailIslandState(
                "Architecture:layer", ViewKind.ARCHITECTURE, "layer", (7.0, 8.0)),),
            positions_by_view={"Architecture": {"model": (9.0, 10.0)}},
        )

        session = session_payload(
            Path("C:/demo"), AnalysisStatus.OUTDATED,
            ("source changed",), "cache unavailable", (Path("C:/one"), Path("C:/two")))
        preferences = preferences_payload("zh-CN", "preferences reset")
        view_state = view_state_payload(state)

        self.assertEqual(session, {
            "projectPath": str(Path("C:/demo")),
            "status": "Outdated",
            "hasAnalysis": False,
            "outdatedReasons": ["source changed"],
            "cacheIssue": "cache unavailable",
            "recentProjects": [str(Path("C:/one")), str(Path("C:/two"))],
        })
        self.assertEqual(preferences, {
            "language": "zh-CN",
            "issue": "preferences reset",
        })
        self.assertNotIn("ignorePaths", preferences)
        self.assertEqual(view_state["activeView"], "Architecture")
        self.assertEqual(view_state["positions"], {"legacy": [1.0, 2.0]})
        self.assertEqual(view_state["viewportByView"], {
            "Architecture": {"zoom": 1.5, "pan": [5.0, 6.0]},
        })
        self.assertEqual(view_state["detailIslands"][0]["position"], [7.0, 8.0])  # type: ignore[index]
        self.assertEqual(view_state["positionsByView"], {
            "Architecture": {"model": [9.0, 10.0]},
        })
        json.dumps([session, preferences, view_state], allow_nan=False)

    def test_bridge_adds_nullable_identity_without_changing_session_payload(self) -> None:
        base = session_payload(None, AnalysisStatus.NOT_ANALYZED, (), None, ())
        with TemporaryDirectory() as temporary:
            response = DesktopBridge(cache_store=CacheStore(Path(temporary))).get_session()
        self.assertTrue(response["ok"], response)
        session = response["data"]
        self.assertEqual(set(session) - set(base), {
            "fileSystemCaseSensitive", "projectEpoch", "analysisGeneration"})
        self.assertIsNone(session["projectEpoch"])
        self.assertIsNone(session["analysisGeneration"])
        json.dumps(response, allow_nan=False)

    def test_search_payload_includes_python_planned_reveal_target(self) -> None:
        result = SearchResult(
            "symbol:train", "symbol", "train", "pkg.train", "Function", "module",
            SourceAnchor("train.py", 4, 1, 8, 1), Confidence.EXACT)
        response = SearchResponse((result,), 1, False)
        reveal = RevealTarget(ViewKind.EXECUTION, "entry:train", "symbol:train")

        payload = search_response_payload(response, (reveal,))

        self.assertEqual(payload["results"][0]["revealTarget"], {  # type: ignore[index]
            "viewKind": "Execution",
            "rootId": "entry:train",
            "selectedId": "symbol:train",
            "reason": None,
        })
        self.assertEqual(reveal_target_payload(reveal),
                         payload["results"][0]["revealTarget"])  # type: ignore[index]
        self.assertEqual(payload["totalCount"], 1)
        self.assertFalse(payload["truncated"])
        json.dumps(payload, allow_nan=False)

    def test_search_payload_rejects_unpaired_reveal_targets(self) -> None:
        response = SearchResponse((), 0, False)

        with self.assertRaisesRegex(ValueError, "reveal target"):
            search_response_payload(response, (RevealTarget(None, None, None),))

    def test_non_finite_graph_and_view_state_values_are_rejected(self) -> None:
        graph = VisibleGraph(
            ViewKind.SYMBOL,
            (VisibleNode("node", "Node", code_node_ids=("node",)),),
            layout_hints={"node": (float("nan"), 0.0)},
        )
        with self.assertRaisesRegex(ValueError, "finite"):
            graph_payload(graph)

        mutations = (
            lambda state: object.__setattr__(state, "zoom", float("inf")),
            lambda state: object.__setattr__(state, "pan", (0.0, float("nan"))),
            lambda state: state.positions.update({"node": (float("inf"), 0.0)}),
            lambda state: state.positions_by_view.update(
                {"Architecture": {"node": (0.0, float("nan"))}}),
            lambda state: state.viewport_by_view.update(
                {"Architecture": ViewportState(1.0, (0.0, 0.0))}) or
                object.__setattr__(state.viewport_by_view["Architecture"],
                                   "zoom", float("inf")),
            lambda state: state.viewport_by_view.update(
                {"Architecture": ViewportState(1.0, (0.0, 0.0))}) or
                object.__setattr__(state.viewport_by_view["Architecture"],
                                   "pan", (float("nan"), 0.0)),
            lambda state: object.__setattr__(
                state, "detail_islands", (DetailIslandState(
                    "Architecture:node", ViewKind.ARCHITECTURE, "node"),)) or
                object.__setattr__(state.detail_islands[0], "position",
                                   (0.0, float("inf"))),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                state = ViewState()
                mutate(state)
                with self.assertRaisesRegex(ValueError, "finite"):
                    view_state_payload(state)

    def test_unknown_metadata_and_query_context_do_not_leak(self) -> None:
        secret = object()
        graph = VisibleGraph(
            ViewKind.SYMBOL,
            (VisibleNode(
                "node",
                "Node",
                code_node_ids=("node",),
                metadata={"has_children": False, "secret": secret},
            ),),
            query_context={"hops": 1, "secret": secret},
        )

        payload = graph_payload(graph)

        self.assertEqual(payload["nodes"][0]["metadata"], {"hasChildren": False})  # type: ignore[index]
        self.assertEqual(payload["queryContext"], {"hops": 1})
        self.assertNotIn("secret", json.dumps(payload, allow_nan=False))


if __name__ == "__main__":
    unittest.main()
