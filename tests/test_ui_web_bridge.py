"""The pywebview bridge is bounded, JSON-safe, and owns desktop state."""

import json
import math
import os
import threading
import unittest
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge


class DesktopBridgeTests(unittest.TestCase):
    fixture_root = Path(__file__).parent / "fixtures" / "mvp_project"

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        data_dir = Path(self.temporary.name)
        cache = CacheStore(data_dir)
        self.controller = AppController(cache)
        self.preferences = PreferencesStore(data_dir)
        self.bridge = DesktopBridge(self.controller, cache, self.preferences)

    def assert_success(self, response: dict[str, object]) -> dict[str, object]:
        self.assertTrue(response["ok"])
        self.assertEqual(response["schemaVersion"], 1)
        json.dumps(response)
        return response["data"]  # type: ignore[return-value]

    def assert_invalid(self, response: dict[str, object]) -> None:
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "INVALID_REQUEST")  # type: ignore[index]

    def analyze_fixture(self) -> None:
        self.assert_success(self.bridge.open_project(str(self.fixture_root)))
        data = self.assert_success(self.bridge.analyze())
        self.assertIn(data["status"], ("Ready", "Partial"))

    def test_owns_supplied_services_and_returns_session_envelopes(self) -> None:
        self.assertIs(self.bridge._controller, self.controller)
        self.assertIs(self.bridge._cache_store, self.controller.cache_store)
        self.assertIs(self.bridge._preferences_store, self.preferences)

        session = self.assert_success(self.bridge.get_session())
        self.assertEqual(session["status"], "NotAnalyzed")
        opened = self.assert_success(self.bridge.open_project(str(self.fixture_root)))
        self.assertEqual(opened["projectPath"], str(self.fixture_root.resolve()))

    def test_analysis_overview_views_detail_and_problems_are_json_safe(self) -> None:
        self.analyze_fixture()

        overview = self.assert_success(self.bridge.get_overview())
        self.assertEqual(overview["projectName"], "mvp_project")
        architecture = self.assert_success(
            self.bridge.get_view("Architecture", hops=1, direction="both"))
        self.assertEqual(architecture["viewKind"], "Architecture")
        detail = self.assert_success(self.bridge.get_detail_view(
            "Architecture", architecture["rootIds"][0], architecture["nodes"][0]["id"]))
        self.assertEqual(detail["viewKind"], "Architecture")
        self.assertIsInstance(self.assert_success(self.bridge.get_problems()), list)

    def test_search_plans_every_reveal_in_python(self) -> None:
        self.analyze_fixture()
        with patch("codeviz.ui.web_bridge.navigation.plan_reveal",
                   wraps=__import__("codeviz.navigation", fromlist=["plan_reveal"]).plan_reveal
                   ) as planner:
            results = self.assert_success(self.bridge.search("model", 20))

        self.assertEqual(planner.call_count, len(results["results"]))
        self.assertTrue(results["results"])
        self.assertTrue(all("revealTarget" in item for item in results["results"]))

    def test_search_current_view_is_explicitly_unsupported(self) -> None:
        self.analyze_fixture()
        self.assert_invalid(self.bridge.search("model", current_view={}))

    def test_symbol_and_data_flow_directions_use_real_one_way_graph_contexts(self) -> None:
        from codeviz.core import EdgeType, NodeKind
        root = Path(self.temporary.name) / "directions-project"
        root.mkdir()
        (root / "run.py").write_text(
            "def leaf():\n    return 1\n"
            "def middle():\n    return leaf()\n"
            "def caller():\n    return middle()\n"
            "def train(x):\n    loss = x\n    return loss\n",
            encoding="utf-8")
        self.assert_success(self.bridge.open_project(str(root)))
        self.assert_success(self.bridge.analyze())
        graph = self.bridge._analysis().flow.graph
        middle = next(node for node in graph.nodes.values()
                      if node.kind is NodeKind.FUNCTION and node.name == "middle")
        caller_edge = next(edge for edge in graph.edges
                           if edge.type is EdgeType.CALLS and edge.target == middle.id)
        loss = next(node for node in graph.nodes.values()
                    if node.kind is NodeKind.DATA and node.name == "loss")
        read_edge = next(edge for edge in graph.edges
                         if edge.type is EdgeType.READS and edge.target == loss.id)
        for kind, focus, incoming, outgoing in (
                ("Symbol", middle.id, caller_edge.source, None),
                ("DataFlow", loss.id, None, read_edge.source)):
            contexts = {}
            for direction in ("in", "out", "both"):
                context = self.assert_success(self.bridge.get_view(
                    kind, focus, focus, hops=1, direction=direction))
                contexts[direction] = {node["id"] for node in context["nodes"]}
                self.assertEqual(context["queryContext"]["direction"], direction)
                self.assertIn(focus, contexts[direction])
                self.assertFalse(context["queryContext"]["budgetTruncated"])
            self.assertNotEqual(contexts["in"], contexts["out"])
            self.assertEqual(contexts["both"], contexts["in"] | contexts["out"])
            if incoming is not None:
                self.assertIn(incoming, contexts["in"])
                self.assertNotIn(incoming, contexts["out"])
            if outgoing is not None:
                self.assertIn(outgoing, contexts["out"])
                self.assertNotIn(outgoing, contexts["in"])

    def test_inspector_uses_presenter_for_main_and_detail_views(self) -> None:
        self.analyze_fixture()
        graph = self.assert_success(self.bridge.get_view("Architecture"))
        root = graph["rootIds"][0]
        node = graph["nodes"][0]["id"]
        inspector = self.assert_success(self.bridge.get_inspector("Architecture", root, node))
        self.assertEqual(inspector["id"], node)
        self.assertIn("sourceAnchors", inspector)
        detail = self.assert_success(self.bridge.get_detail_view("Architecture", root, node))
        inspected = detail["nodes"][0]["id"]
        self.assert_success(self.bridge.get_inspector("Architecture", root, inspected, node))
        self.assert_invalid(self.bridge.get_inspector("Architecture", root, "missing"))

    def test_settings_are_separate_persistent_and_invalidate_fingerprint(self) -> None:
        self.analyze_fixture()
        self.assert_success(self.bridge.set_language("en"))
        self.assert_success(self.bridge.set_analysis_settings({"ignorePaths": ["model.py"]}))
        session = self.assert_success(self.bridge.get_session())
        self.assertEqual(session["status"], "Outdated")
        self.assertIn("analysis config changed", session["outdatedReasons"])
        self.assertEqual(self.assert_success(self.bridge.get_preferences())["language"], "en")
        info = self.assert_success(self.bridge.get_cache_info())
        self.assertTrue(info["exists"])
        self.assertEqual(info["status"], "Outdated")
        bridge = DesktopBridge(cache_store=self.bridge._cache_store)
        self.assertEqual(self.assert_success(bridge.get_analysis_settings())["ignorePaths"],
                         [])
        self.assert_success(bridge.open_project(str(self.fixture_root)))
        self.assertEqual(self.assert_success(bridge.get_analysis_settings())["ignorePaths"],
                         ["model.py"])
        self.assertEqual(self.assert_success(bridge.get_session())["status"], "Outdated")
        self.assert_success(bridge.analyze())
        self.assertNotEqual(self.assert_success(bridge.get_session())["status"], "Outdated")
        self.assertNotIn("model.py", [f.relative_path for f in bridge._analysis().files])

    def test_settings_validation_corruption_and_failed_save(self) -> None:
        self.assert_success(self.bridge.open_project(str(self.fixture_root)))
        for paths in (["../secret"], ["C:\\secret"], ["/secret"], [True], ["a"] * 257):
            self.assert_invalid(self.bridge.set_analysis_settings({"ignorePaths": paths}))
        self.assert_invalid(self.bridge.set_analysis_settings({"ignorePaths": [], "language": "en"}))
        self.assert_success(self.bridge.set_analysis_settings({"ignorePaths": ["a\\b/", "a/b"]}))
        self.assertEqual(self.assert_success(self.bridge.get_analysis_settings())["ignorePaths"],
                         ["a/b"])
        with patch("codeviz.ui.web_bridge.os.replace", side_effect=OSError("secret")), \
                self.assertLogs("codeviz.ui.web_bridge", level="ERROR"):
            response = self.bridge.set_analysis_settings({"ignorePaths": ["c"]})
        self.assertFalse(response["ok"])
        self.assertEqual(self.controller.ignore_paths, ("a/b",))
        self.bridge._settings_path.write_text("broken", encoding="utf-8")
        bridge = DesktopBridge(cache_store=self.bridge._cache_store)
        settings = self.assert_success(bridge.get_analysis_settings())
        self.assertEqual(settings["ignorePaths"], [])
        self.assertIsNotNone(settings["issue"])

    def _settings_projects(self) -> tuple[Path, Path]:
        roots = (Path(self.temporary.name) / "project-a",
                 Path(self.temporary.name) / "project-b")
        for root in roots:
            root.mkdir()
            (root / "a.py").write_text("a = 1\n", encoding="utf-8")
            (root / "b.py").write_text("b = 2\n", encoding="utf-8")
        return roots

    def test_settings_require_an_open_project_without_creating_global_rules(self) -> None:
        for payload in ({"ignorePaths": ["a.py"]}, {"unexpected": True}):
            response = self.bridge.set_analysis_settings(payload)
            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "NOT_READY")
        self.assertFalse(self.bridge._settings_path.exists())
        self.assertEqual(self.assert_success(self.bridge.get_analysis_settings())["ignorePaths"], [])
        self.assertEqual(self.bridge._settings_by_project, {})

    def test_project_settings_switch_restart_and_native_selection_are_isolated(self) -> None:
        project_a, project_b = self._settings_projects()
        self.assert_success(self.bridge.open_project(str(project_a)))
        self.assert_success(self.bridge.set_analysis_settings({"ignorePaths": ["a.py"]}))
        self.assert_success(self.bridge.analyze())
        self.assertEqual([file.relative_path for file in self.bridge._analysis().files], ["b.py"])
        window = Mock()
        self.bridge._bind_window(window, 20)
        window.create_file_dialog.return_value = (str(project_b),)
        self.assert_success(self.bridge.choose_project())
        self.assertEqual(self.assert_success(self.bridge.get_analysis_settings())["ignorePaths"], [])
        self.assert_success(self.bridge.set_analysis_settings({"ignorePaths": ["b.py"]}))
        self.assert_success(self.bridge.analyze())
        self.assertEqual([file.relative_path for file in self.bridge._analysis().files], ["a.py"])
        window.create_file_dialog.return_value = None
        self.assert_success(self.bridge.choose_project())
        self.assertEqual(self.assert_success(self.bridge.get_analysis_settings())["ignorePaths"],
                         ["b.py"])
        alias_a = project_b / ".." / project_a.name
        session = self.assert_success(self.bridge.open_project(str(alias_a)))
        self.assertEqual(session["status"], "Ready")
        self.assertEqual(self.assert_success(self.bridge.get_analysis_settings())["ignorePaths"],
                         ["a.py"])
        persisted = json.loads(self.bridge._settings_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["version"], 2)
        self.assertEqual(len(persisted["projects"]), 2)

        restarted = DesktopBridge(cache_store=self.bridge._cache_store)
        self.assertEqual(self.assert_success(restarted.get_analysis_settings())["ignorePaths"], [])
        restarted._bind_window(window, 20)
        window.create_file_dialog.return_value = (str(project_b),)
        session = self.assert_success(restarted.choose_project())
        self.assertEqual(session["status"], "Ready")
        self.assertEqual(self.assert_success(restarted.get_analysis_settings())["ignorePaths"],
                         ["b.py"])
        session = self.assert_success(restarted.open_project(str(project_a)))
        self.assertEqual(session["status"], "Ready")
        self.assertEqual(self.assert_success(restarted.get_analysis_settings())["ignorePaths"],
                         ["a.py"])
        self.assertEqual([file.relative_path for file in restarted._analysis().files], ["b.py"])

    def test_failed_settings_write_preserves_both_projects_and_current_analysis(self) -> None:
        project_a, project_b = self._settings_projects()
        for root, ignored in ((project_a, "a.py"), (project_b, "b.py")):
            self.assert_success(self.bridge.open_project(str(root)))
            self.assert_success(self.bridge.set_analysis_settings({"ignorePaths": [ignored]}))
            self.assert_success(self.bridge.analyze())
        self.assert_success(self.bridge.open_project(str(project_a)))
        before = self.bridge._settings_path.read_bytes()
        settings_map = dict(self.bridge._settings_by_project)
        analysis = self.controller.analysis
        with patch("codeviz.ui.web_bridge.os.replace", side_effect=OSError("private")), \
                self.assertLogs("codeviz.ui.web_bridge", level="ERROR"):
            response = self.bridge.set_analysis_settings({"ignorePaths": ["b.py"]})
        self.assertFalse(response["ok"])
        self.assertEqual(self.bridge._settings_path.read_bytes(), before)
        self.assertEqual(self.bridge._settings_by_project, settings_map)
        self.assertEqual(self.controller.ignore_paths, ("a.py",))
        self.assertIs(self.controller.analysis, analysis)
        self.assertEqual(self.controller.status, AnalysisStatus.READY)
        self.assertEqual(list(Path(self.temporary.name).glob(".analysis-settings-*")), [])
        restarted = DesktopBridge(cache_store=self.bridge._cache_store)
        for root, ignored in ((project_a, "a.py"), (project_b, "b.py")):
            self.assert_success(restarted.open_project(str(root)))
            self.assertEqual(self.assert_success(restarted.get_analysis_settings())["ignorePaths"],
                             [ignored])

    def test_failed_project_open_does_not_replace_active_project_settings(self) -> None:
        project_a, _ = self._settings_projects()
        self.assert_success(self.bridge.open_project(str(project_a)))
        self.assert_success(self.bridge.set_analysis_settings({"ignorePaths": ["a.py"]}))
        self.assert_invalid(self.bridge.open_project(str(project_a / "missing")))
        self.assertEqual(self.controller.project_root, project_a.resolve())
        self.assertEqual(self.controller.ignore_paths, ("a.py",))
        window = Mock()
        self.bridge._bind_window(window, 20)
        window.create_file_dialog.return_value = (str(project_a / "a.py"),)
        self.assert_invalid(self.bridge.choose_project())
        self.assertEqual(self.controller.ignore_paths, ("a.py",))

    def test_legacy_global_settings_are_preserved_but_never_implicitly_applied(self) -> None:
        project_a, project_b = self._settings_projects()
        legacy = {"version": 1, "ignorePaths": ["a.py"]}
        self.bridge._settings_path.write_text(json.dumps(legacy), encoding="utf-8")
        bridge = DesktopBridge(cache_store=self.bridge._cache_store)
        for root in (project_a, project_b):
            self.assert_success(bridge.open_project(str(root)))
            settings = self.assert_success(bridge.get_analysis_settings())
            self.assertEqual(settings["ignorePaths"], [])
            self.assertIn("version 1", settings["issue"])
            self.assertIn("not applied", settings["issue"])
        self.assertEqual(json.loads(bridge._settings_path.read_text(encoding="utf-8")), legacy)
        self.assert_success(bridge.open_project(str(project_a)))
        self.assert_success(bridge.set_analysis_settings({"ignorePaths": ["b.py"]}))
        saved = json.loads(bridge._settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["legacyIgnorePaths"], ["a.py"])
        restarted = DesktopBridge(cache_store=bridge._cache_store)
        self.assert_success(restarted.open_project(str(project_b)))
        self.assertEqual(self.assert_success(restarted.get_analysis_settings())["ignorePaths"], [])
        self.assertIn("not applied", self.assert_success(restarted.get_analysis_settings())["issue"])
        self.assert_success(restarted.open_project(str(project_a)))
        self.assertEqual(self.assert_success(restarted.get_analysis_settings())["ignorePaths"], ["b.py"])

    def test_project_settings_document_and_project_count_are_bounded(self) -> None:
        self.assert_success(self.bridge.open_project(str(self.fixture_root)))
        self.assert_invalid(self.bridge.set_analysis_settings({
            "ignorePaths": [f"{i}" + "界" * 4090 for i in range(100)],
        }))
        self.assertFalse(self.bridge._settings_path.exists())
        document = {"version": 2, "projects": {
            f"{i:064x}": {"ignorePaths": []} for i in range(256)
        }}
        self.bridge._settings_path.write_text(json.dumps(document), encoding="utf-8")
        restarted = DesktopBridge(cache_store=self.bridge._cache_store)
        self.assert_success(restarted.open_project(str(self.fixture_root)))
        before = restarted._settings_path.read_bytes()
        self.assert_invalid(restarted.set_analysis_settings({"ignorePaths": ["a.py"]}))
        self.assertEqual(restarted._settings_path.read_bytes(), before)
        self.assertEqual(restarted._controller.ignore_paths, ())

    def test_attached_controller_loads_its_own_project_settings_on_restart(self) -> None:
        self.assert_success(self.bridge.open_project(str(self.fixture_root)))
        self.assert_success(self.bridge.set_analysis_settings({"ignorePaths": ["model.py"]}))
        controller = AppController(self.bridge._cache_store, ignore_paths=("other-project",))
        controller.open_project(self.fixture_root)
        restarted = DesktopBridge(controller=controller)
        self.assertEqual(self.assert_success(restarted.get_analysis_settings())["ignorePaths"],
                         ["model.py"])

    def test_invalid_or_oversized_project_map_does_not_partially_load_rules(self) -> None:
        project_a, _ = self._settings_projects()
        key = self.bridge._project_settings_key(project_a)
        valid_record = {key: {"ignorePaths": ["a.py"]}}
        documents = (
            {"version": True, "projects": valid_record},
            {"version": 2, "projects": {**valid_record, "invalid-key": {"ignorePaths": []}}},
            {"version": 2, "projects": {key: {"ignorePaths": ["../outside"]}}},
            {"version": 2, "projects": valid_record, "legacyIgnorePaths": [False]},
        )
        contents = [json.dumps(document) for document in documents]
        contents.append(json.dumps({"version": 2, "projects": valid_record}) + " " * 1_100_000)
        for content in contents:
            with self.subTest(content_length=len(content)):
                self.bridge._settings_path.write_text(content, encoding="utf-8")
                restarted = DesktopBridge(cache_store=self.bridge._cache_store)
                self.assert_success(restarted.open_project(str(project_a)))
                settings = self.assert_success(restarted.get_analysis_settings())
                self.assertEqual(settings["ignorePaths"], [])
                self.assertIsNotNone(settings["issue"])
                self.assertEqual(restarted._settings_by_project, {})

    @unittest.skipUnless(os.name == "nt", "Windows paths are case-insensitive")
    def test_windows_project_path_case_does_not_create_distinct_settings(self) -> None:
        project_a, _ = self._settings_projects()
        self.assert_success(self.bridge.open_project(str(project_a)))
        self.assert_success(self.bridge.set_analysis_settings({"ignorePaths": ["a.py"]}))
        self.assert_success(self.bridge.open_project(str(project_a).upper()))
        self.assertEqual(self.assert_success(self.bridge.get_analysis_settings())["ignorePaths"],
                         ["a.py"])
        self.assert_success(self.bridge.set_analysis_settings({"ignorePaths": ["b.py"]}))
        self.assertEqual(len(self.bridge._settings_by_project), 1)

    def test_native_project_picker_cancel_and_selection(self) -> None:
        from unittest.mock import Mock
        window = Mock()
        self.bridge._bind_window(window, 20)
        window.create_file_dialog.return_value = None
        before = self.assert_success(self.bridge.get_session())
        self.assertEqual(self.assert_success(self.bridge.choose_project()), before)
        window.create_file_dialog.return_value = (str(self.fixture_root),)
        session = self.assert_success(self.bridge.choose_project())
        self.assertEqual(session["projectPath"], str(self.fixture_root.resolve()))
        self.assertFalse(window.create_file_dialog.call_args.kwargs["allow_multiple"])

    def test_unknown_node_writes_and_state_limit_are_rejected(self) -> None:
        self.analyze_fixture()
        self.assert_invalid(self.bridge.expand("missing"))
        self.assert_invalid(self.bridge.move_node("missing", {"x": 0, "y": 0}))
        from dataclasses import replace
        node = self.assert_success(self.bridge.get_view("Architecture"))["nodes"][0]["id"]
        self.controller.view_state = replace(self.controller.view_state,
                                             expanded_ids=tuple(f"legacy-{i}" for i in range(1000)))
        self.assert_invalid(self.bridge.expand(node))

    def test_full_analysis_ids_are_valid_without_default_visible_graph(self) -> None:
        from codeviz.core import NodeKind
        self.analyze_fixture()
        analysis = self.bridge._analysis()
        self.assertIsNotNone(analysis.flow)
        self.assertIsNotNone(analysis.model)
        self.assertIsNotNone(analysis.training)
        ids = []
        for kind in (NodeKind.CALL_SITE, NodeKind.CONTROL_BLOCK):
            candidates = [node.id for node in analysis.flow.graph.nodes.values()
                          if node.kind is kind]
            self.assertTrue(candidates, kind)
            ids.append(candidates[0])
        ids.extend(item.symbol_id for item in analysis.model.definitions)
        ids.extend(item.id for item in analysis.model.instances)
        ids.extend(item.id for item in analysis.model.invocations)
        ids.extend(item.id for item in analysis.training.entry_points)
        ids.extend(item.id for item in analysis.training.events)
        ids.extend(item.id for item in analysis.training.joins)
        # FullGraph identities must work even when no default graph contains them.
        with patch.object(self.controller, "view", side_effect=ValueError("not visible")) as view:
            for node_id in dict.fromkeys(ids):
                with self.subTest(node_id=node_id):
                    self.assert_success(self.bridge.move_node(
                        node_id, {"x": 10, "y": 20}, "Execution"))
                    self.assert_success(self.bridge.expand(node_id))
            view.assert_not_called()

    def test_view_state_roundtrip_and_immutable_session_snapshot(self) -> None:
        self.analyze_fixture()
        self.assert_success(self.bridge.save_layout({
            "activeView": "Architecture", "selectedId": None, "openTabs": ["Overview"],
            "graphRoots": {},
        }))
        self.assertEqual(self.assert_success(self.bridge.get_view_state())["activeView"], "Architecture")
        original = self.bridge._snapshot
        with self.bridge._lock:
            self.controller.status = AnalysisStatus.FAILED
            self.controller.project_root = None
            self.assertEqual(self.bridge._snapshot, original)
            snapshot = self.bridge._session_snapshot()
            self.assertEqual(snapshot["projectPath"], str(self.fixture_root.resolve()))
            self.assertNotEqual(snapshot["status"], "Failed")

    def test_layout_rejects_unknown_selection_and_roots_for_the_wrong_view(self) -> None:
        self.analyze_fixture()
        graph = self.assert_success(self.bridge.get_view("Architecture"))
        root = graph["rootIds"][0]
        payload = {"activeView": "Architecture", "selectedId": root,
                   "openTabs": ["Architecture"], "graphRoots": {"Architecture": root}}
        self.assert_success(self.bridge.save_layout(payload))
        original = self.controller.view_state
        for selection, roots in (("missing", {"Architecture": root}),
                                 (root, {"Architecture": "missing"}),
                                 (root, {"Execution": root})):
            self.assert_invalid(self.bridge.save_layout(
                {**payload, "selectedId": selection, "graphRoots": roots}))
            self.assertEqual(self.controller.view_state, original)
            self.assertEqual(self.bridge._cache_store.load_view_state(self.fixture_root), original)

    def test_old_layout_is_cleaned_on_reopen_without_losing_annotations(self) -> None:
        from dataclasses import replace

        from codeviz.core import ViewKind
        from codeviz.views import DetailIslandState, ViewportState
        self.analyze_fixture()
        graph = self.assert_success(self.bridge.get_view("Architecture"))
        root = graph["rootIds"][0]
        state = replace(
            self.controller.view_state, active_view=ViewKind.ARCHITECTURE,
            selected_id="missing", graph_roots={"Architecture": "missing", "Execution": root},
            expanded_ids=(root, "missing"), positions={root: (1, 2), "missing": (3, 4)},
            positions_by_view={"Architecture": {root: (5, 6), "missing": (7, 8)}},
            detail_islands=(DetailIslandState("valid", ViewKind.ARCHITECTURE, root),
                            DetailIslandState("invalid", ViewKind.ARCHITECTURE, "missing")),
            viewport_by_view={"Architecture": ViewportState(1.25, (10, 20))},
            open_tabs=("Overview", "Architecture"), filters=("custom",),
        )
        cache = self.bridge._cache_store
        notes = {root: "keep valid note", "missing": "keep orphan note"}
        cache.save_annotations(self.fixture_root, notes)
        cache.save_view_state(self.fixture_root, state)
        restarted = DesktopBridge(cache_store=cache)
        self.assert_success(restarted.open_project(str(self.fixture_root)))
        restored = self.assert_success(restarted.get_view_state())
        self.assertIsNone(restored["selectedId"])
        self.assertEqual(restored["graphRoots"], {"Architecture": None, "Execution": None})
        self.assertEqual(restored["expandedIds"], [root])
        self.assertEqual(restored["positions"], {root: [1, 2]})
        self.assertEqual(restored["positionsByView"], {"Architecture": {root: [5, 6]}})
        self.assertEqual([item["id"] for item in restored["detailIslands"]], ["valid"])
        self.assertEqual(cache.load_annotations(self.fixture_root), notes)
        self.assertEqual(restarted._controller.view_state.viewport_by_view, state.viewport_by_view)
        self.assertEqual(restarted._controller.view_state.filters, state.filters)
        self.assertEqual(restarted._controller.view_state.open_tabs, state.open_tabs)
        self.assertEqual(cache.load_view_state(self.fixture_root), restarted._controller.view_state)
        restarted._controller.view_state = replace(restarted._controller.view_state,
                                                   selected_id="missing")
        self.assertIsNone(self.assert_success(restarted.get_view_state())["selectedId"])

    @unittest.skipUnless(find_spec("webview"), "pywebview is not installed")
    def test_actual_pywebview_function_discovery_exposes_only_bridge_methods(self) -> None:
        from webview import util
        scripts = []
        loaded = threading.Event()
        window = SimpleNamespace(
            _js_api=self.bridge, _functions={}, _expose_lock=threading.Lock(),
            run_js=scripts.append,
            events=SimpleNamespace(before_load=threading.Event(),
                                   _pywebviewready=threading.Event(), loaded=loaded),
        )
        with patch.object(util, "load_js_files", return_value=("injected", "%(functions)s")):
            util.inject_pywebview("edgechromium", window)
            self.assertTrue(loaded.wait(2))
        names = {item["func"] for item in json.loads(scripts[-1])}
        expected = {name for name in dir(DesktopBridge)
                    if not name.startswith("_") and callable(getattr(DesktopBridge, name))}
        self.assertEqual(names, expected)
        self.assertFalse(any("." in name for name in names))

    def test_failed_analysis_publishes_final_snapshot(self) -> None:
        self.analyze_fixture()
        with patch("codeviz.ui.controller.analyze_project", side_effect=OSError("private")), \
                self.assertLogs("codeviz.ui.web_bridge", level="ERROR"):
            response = self.bridge.analyze()
        self.assertFalse(response["ok"])
        self.assertEqual(self.bridge._session_snapshot()["status"], "Failed")
        self.assertIsNotNone(self.controller.analysis)

    def test_preferences_source_and_layout_mutations_round_trip(self) -> None:
        self.analyze_fixture()
        node_id = self.assert_success(self.bridge.get_view("Architecture"))["nodes"][0]["id"]
        preferences = self.assert_success(
            self.bridge.set_preferences({"language": "en"}))
        self.assertEqual(preferences["language"], "en")
        self.assertEqual(self.assert_success(self.bridge.get_preferences())["language"], "en")
        self.assertEqual(
            self.assert_success(self.bridge.set_language("zh-CN"))["language"], "zh-CN")

        source = self.assert_success(self.bridge.read_source({
            "filePath": "model.py", "startLine": 1, "startColumn": None,
            "endLine": 2, "endColumn": None,
        }))
        self.assertEqual(source["filePath"], "model.py")

        state = self.assert_success(self.bridge.save_layout({
            "activeView": "Architecture", "selectedId": node_id,
            "openTabs": ["Overview", "Architecture"],
            "graphRoots": {"Architecture": None},
        }))
        self.assertEqual(state["activeView"], "Architecture")
        self.assertEqual(self.assert_success(
            self.bridge.move_node(node_id, {"x": 10, "y": 20}, "Architecture"))[
                "positionsByView"]["Architecture"][node_id], [10, 20])
        self.assertEqual(self.assert_success(
            self.bridge.save_viewport("Architecture", 1.25, {"x": 3, "y": -4}))[
                "viewportByView"]["Architecture"]["zoom"], 1.25)

        self.assert_success(self.bridge.expand(node_id))
        self.assert_success(self.bridge.collapse(node_id))
        island = self.assert_success(self.bridge.open_detail_island(
            "Architecture", node_id, {"x": 50, "y": 60}))
        island_id = island["detailIslands"][0]["id"]
        moved = self.assert_success(
            self.bridge.move_detail_island(island_id, {"x": 70, "y": 80}))
        self.assertEqual(moved["detailIslands"][0]["position"], [70, 80])
        self.assertEqual(self.assert_success(
            self.bridge.close_detail_island(island_id))["detailIslands"], [])

    def test_invalid_inputs_return_structured_errors_without_calling_core(self) -> None:
        invalid_calls = (
            lambda: self.bridge.open_project("x" * 32769),
            lambda: self.bridge.get_view("Unknown"),
            lambda: self.bridge.get_view("Architecture", hops=0),
            lambda: self.bridge.get_view("Architecture", direction="sideways"),
            lambda: self.bridge.search("x", 0),
            lambda: self.bridge.search("x", 101),
            lambda: self.bridge.set_preferences({"language": "en", "extra": True}),
            lambda: self.bridge.set_preferences({}),
            lambda: self.bridge.set_preferences({"language": 1}),
            lambda: self.bridge.move_node("n", {"x": math.inf, "y": 0}),
            lambda: self.bridge.save_layout({
                "activeView": None, "selectedId": None, "openTabs": [],
                "graphRoots": {}, "unexpected": True,
            }),
            lambda: self.bridge.read_source({
                "filePath": "model.py", "startLine": 1, "startColumn": None,
                "endLine": 1, "endColumn": None, "extra": 1,
            }),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                self.assert_invalid(call())

    def test_core_exceptions_never_cross_the_bridge(self) -> None:
        response = self.bridge.get_overview()
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "NOT_READY")  # type: ignore[index]

        for error in (OSError("private details"), SyntaxError("secret parser text"),
                      KeyError("secret key")):
            with self.subTest(error=type(error).__name__), \
                    patch.object(self.controller, "recent_projects", side_effect=error), \
                    self.assertLogs("codeviz.ui.web_bridge", level="ERROR") as logs:
                response = self.bridge.get_session()
            self.assertFalse(response["ok"])
            self.assertEqual(response["error"], {  # type: ignore[index]
                "code": "INTERNAL_ERROR",
                "message": "An internal error occurred.",
            })
            self.assertNotIn(str(error), json.dumps(response))
            self.assertTrue(any("Desktop bridge call failed" in item
                                for item in logs.output))

    def test_missing_project_path_is_a_redacted_invalid_request(self) -> None:
        secret_path = r"C:\Users\alice\missing-secret-project"
        response = self.bridge.open_project(secret_path)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], {  # type: ignore[index]
            "code": "INVALID_REQUEST",
            "message": "The project path is invalid.",
        })
        self.assertNotIn(secret_path, json.dumps(response))

    def test_value_error_with_secret_absolute_path_is_redacted(self) -> None:
        secret_path = r"C:\Users\alice\secret-project\private_model.py"
        with patch.object(
            self.controller,
            "view",
            side_effect=ValueError(f"could not parse {secret_path}"),
        ):
            response = self.bridge.get_view("Architecture")

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], {  # type: ignore[index]
            "code": "INVALID_REQUEST",
            "message": "The graph request is invalid.",
        })
        self.assertNotIn(secret_path, json.dumps(response))

    def test_state_writes_are_rejected_while_analysis_is_active(self) -> None:
        self.controller.status = AnalysisStatus.ANALYZING
        calls = (
            lambda: self.bridge.open_project(str(self.fixture_root)),
            lambda: self.bridge.set_language("en"),
            lambda: self.bridge.expand("node"),
            lambda: self.bridge.move_node("node", {"x": 1, "y": 2}),
        )
        for call in calls:
            with self.subTest(call=call):
                response = call()
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], "NOT_READY")  # type: ignore[index]

    def test_public_calls_are_serialized_by_a_reentrant_lock(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        original = self.controller.recent_projects

        def blocking_recent_projects():
            entered.set()
            release.wait(2)
            return original()

        with patch.object(self.controller, "recent_projects", blocking_recent_projects):
            first = threading.Thread(target=self.bridge.get_session)
            second_done = threading.Event()
            second = threading.Thread(
                target=lambda: (self.bridge.get_preferences(), second_done.set()))
            first.start()
            self.assertTrue(entered.wait(1))
            second.start()
            self.assertFalse(second_done.wait(0.05))
            release.set()
            first.join(2)
            second.join(2)
            self.assertTrue(second_done.is_set())

    def test_session_snapshot_does_not_block_behind_real_analyze(self) -> None:
        self.assert_success(self.bridge.open_project(str(self.fixture_root)))
        analysis = self.controller.analyze()
        entered = threading.Event()
        release = threading.Event()

        def blocking_analyze(*_args, **_kwargs):
            entered.set()
            release.wait(2)
            return analysis

        analyze_done = threading.Event()
        session_done = threading.Event()
        session_response: list[dict[str, object]] = []
        with patch("codeviz.ui.controller.analyze_project", blocking_analyze):
            analyze_thread = threading.Thread(
                target=lambda: (self.bridge.analyze(), analyze_done.set()))
            analyze_thread.start()
            self.assertTrue(entered.wait(1))
            session_thread = threading.Thread(target=lambda: (
                session_response.append(self.bridge.get_session()),
                session_done.set(),
            ))
            session_thread.start()
            try:
                self.assertTrue(session_done.wait(0.5), "get_session blocked on analysis")
                session = self.assert_success(session_response[0])
                self.assertEqual(session["status"], "Analyzing")
            finally:
                release.set()
                analyze_thread.join(2)
                session_thread.join(2)
        self.assertTrue(analyze_done.is_set())


if __name__ == "__main__":
    unittest.main()
