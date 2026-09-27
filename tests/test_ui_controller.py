"""Desktop controller keeps source access read-only and project-scoped."""

import builtins
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.core import SourceAnchor, ViewKind
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.presenter import build_problems
from codeviz.views import ViewportState


class ControllerTests(unittest.TestCase):
    fixture_root = Path(__file__).parent / "fixtures" / "mvp_project"

    def test_layout_fallback_issue_is_visible_bounded_and_not_an_analysis_fact(self):
        controller = AppController()
        controller.open_project(self.fixture_root)
        analysis = controller.analyze()
        analysis_issues = analysis.issues

        with patch("codeviz.views.layout._ranks",
                   side_effect=RuntimeError("forced layout failure")):
            first = controller.view(ViewKind.ARCHITECTURE)
            second = controller.view(ViewKind.ARCHITECTURE)

        ordered = [first.layout_hints[node_id]
                   for node_id in sorted(first.layout_hints)]
        self.assertTrue(all(point[0] == 0.0 for point in ordered))
        self.assertEqual([point[1] for point in ordered],
                         sorted(point[1] for point in ordered))
        self.assertEqual(first.issues, second.issues)
        self.assertEqual(len(first.issues), 1)
        self.assertEqual(len(controller.view_issues), 1)
        self.assertEqual(analysis.issues, analysis_issues)
        visible = build_problems(analysis, controller.view_issues)
        self.assertTrue(any(issue.code == "LAYOUT_FALLBACK" for issue in visible))
        self.assertTrue(any("Architecture" in issue.file_path for issue in visible
                            if issue.code == "LAYOUT_FALLBACK"))
        self.assertTrue(all(not issue.can_open_source for issue in visible
                            if issue.code == "LAYOUT_FALLBACK"))

    def test_normal_layout_adds_no_view_issue(self):
        controller = AppController()
        controller.open_project(self.fixture_root)
        controller.analyze()

        view = controller.view(ViewKind.ARCHITECTURE)

        self.assertEqual(view.issues, ())
        self.assertEqual(controller.view_issues, ())

    def test_open_project_rejects_switch_during_analysis(self):
        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            controller = AppController()
            controller.open_project(Path(first))
            original_root = controller.project_root
            controller.status = AnalysisStatus.ANALYZING
            with self.assertRaises(RuntimeError):
                controller.open_project(Path(second))
            self.assertEqual(controller.project_root, original_root)
            self.assertEqual(controller.status, AnalysisStatus.ANALYZING)

    def test_open_requires_explicit_analysis_and_new_project_clears_old_facts(self):
        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            first_root, second_root = Path(first), Path(second)
            (first_root / "run.py").write_text(
                "def train(optimizer):\n    optimizer.step()\n", encoding="utf-8")
            (second_root / "other.py").write_text("answer = 42\n", encoding="utf-8")
            controller = AppController()
            controller.open_project(first_root)
            self.assertIsNone(controller.analysis)
            self.assertEqual(controller.status, AnalysisStatus.NOT_ANALYZED)
            with self.assertRaises(RuntimeError):
                controller.view(ViewKind.EXECUTION)
            analysis = controller.analyze()
            self.assertIs(analysis, controller.analysis)
            self.assertEqual(controller.status, AnalysisStatus.READY)
            self.assertTrue(controller.view(ViewKind.EXECUTION).nodes)
            controller.open_project(second_root)
            self.assertIsNone(controller.analysis)
            self.assertEqual(controller.status, AnalysisStatus.NOT_ANALYZED)

    def test_partial_analysis_keeps_available_result(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "good.py").write_text("value = 1\n", encoding="utf-8")
            (root / "bad.py").write_text("def broken(:\n", encoding="utf-8")
            controller = AppController()
            controller.open_project(root)
            result = controller.analyze()
            self.assertEqual(controller.status, AnalysisStatus.PARTIAL)
            self.assertIs(result, controller.analysis)
            self.assertTrue(any(item.relative_path == "good.py" and item.tree is not None
                                for item in result.files))

    def test_failed_analysis_has_no_stale_result(self):
        with TemporaryDirectory() as temporary:
            controller = AppController()
            controller.open_project(Path(temporary))
            with patch("codeviz.ui.controller.analyze_project", side_effect=OSError("scan failed")):
                with self.assertRaisesRegex(OSError, "scan failed"):
                    controller.analyze()
            self.assertEqual(controller.status, AnalysisStatus.FAILED)
            self.assertIsNone(controller.analysis)

    def test_source_navigation_returns_exact_lines_and_never_writes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "train.py"
            original = "def train():\n    loss = 1\n    return loss\n"
            source.write_text(original, encoding="utf-8")
            controller = AppController()
            controller.open_project(root)
            document = controller.read_source(SourceAnchor("train.py", 2, end_line=3))
            self.assertEqual(document.file_path, "train.py")
            self.assertEqual(document.start_line, 2)
            self.assertEqual(document.end_line, 3)
            self.assertIn("loss = 1", document.text)
            self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_source_navigation_rejects_symlink_escape(self):
        with TemporaryDirectory() as project, TemporaryDirectory() as outside:
            root = Path(project)
            target = Path(outside) / "secret.py"
            target.write_text("secret = 1\n", encoding="utf-8")
            link = root / "linked.py"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlink creation unavailable in this environment")
            controller = AppController()
            controller.open_project(root)
            with self.assertRaises(ValueError):
                controller.read_source(SourceAnchor("linked.py", 1))

    def test_source_navigation_rejects_file_changed_between_check_and_open(self):
        with TemporaryDirectory() as project, TemporaryDirectory() as outside:
            root = Path(project)
            (root / "public.py").write_text("public = 1\n", encoding="utf-8")
            secret = Path(outside) / "secret.py"
            secret.write_text("secret = 1\n", encoding="utf-8")
            controller = AppController()
            controller.open_project(root)

            def diverted_open(_path, *args, **kwargs):
                return builtins.open(secret, *args, **kwargs)

            with patch.object(Path, "open", diverted_open):
                with self.assertRaises(ValueError):
                    controller.read_source(SourceAnchor("public.py", 1))

    def test_open_handle_path_rejects_stale_resolve_and_stat(self):
        with TemporaryDirectory() as project, TemporaryDirectory() as outside:
            root = Path(project)
            public = root / "public.py"
            public.write_text("public = 1\n", encoding="utf-8")
            secret = Path(outside) / "secret.py"
            secret.write_text("secret = 1\n", encoding="utf-8")
            secret_stat = secret.stat()
            original_stat = Path.stat
            controller = AppController()
            controller.open_project(root)

            def stale_stat(path, *args, **kwargs):
                return secret_stat if path == public else original_stat(path, *args, **kwargs)

            def diverted_open(_path, *args, **kwargs):
                return builtins.open(secret, *args, **kwargs)

            with patch.object(Path, "stat", stale_stat), patch.object(Path, "open", diverted_open):
                with self.assertRaises(ValueError):
                    controller.read_source(SourceAnchor("public.py", 1))

    def test_expanding_view_does_not_change_analysis_facts(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text(
                "def train(loss, optimizer):\n"
                "    for epoch in range(2):\n"
                "        loss.backward()\n"
                "        optimizer.step()\n", encoding="utf-8")
            controller = AppController()
            controller.open_project(root)
            analysis = controller.analyze()
            original = analysis.semantic.graph
            collapsed = controller.view(ViewKind.EXECUTION)
            loop = next(node for node in collapsed.nodes if node.display_name == "EpochLoop")
            controller.expand(loop.id)
            expanded = controller.view(ViewKind.EXECUTION)
            self.assertGreater(len(expanded.nodes), len(collapsed.nodes))
            self.assertEqual(analysis.semantic.graph, original)
            controller.collapse(loop.id)
            self.assertEqual(controller.view(ViewKind.EXECUTION).nodes, collapsed.nodes)
            controller.move_node(loop.id, (90.0, 120.0))
            self.assertEqual(controller.view(ViewKind.EXECUTION).layout_hints[loop.id],
                             (90.0, 120.0))

    def test_detail_view_is_bounded_source_backed_and_isolated_in_all_contexts(self):
        controller = AppController(ignore_paths=("data",))
        controller.open_project(self.fixture_root)
        analysis = controller.analyze()
        self.assertIsNotNone(analysis.semantic)
        self.assertIsNotNone(analysis.flow)
        assert analysis.semantic is not None
        assert analysis.flow is not None

        architecture = controller.view(ViewKind.ARCHITECTURE)
        architecture_focus = next(
            node for node in architecture.nodes if node.metadata.get("has_children"))
        self.assertIn(architecture_focus.id, architecture.root_ids)
        execution_root = next(
            component.id for component in analysis.semantic.graph.components
            if component.role == "EntryPoint"
        )
        execution = controller.view(ViewKind.EXECUTION, execution_root)
        execution_focus = next(
            node for node in execution.nodes
            if node.id != execution_root and node.role != "DataNode"
        )
        symbol_focus = next(
            entry for entry in analysis.symbol_index.entries.values()
            if entry.name == "train_epoch"
        )
        data_focus = analysis.flow.data_nodes[0]

        controller.expand("main-only-expansion")
        controller.move_node(architecture_focus.id, (901.0, 777.0))
        controller.save_viewport(ViewKind.ARCHITECTURE, 1.6, (31.0, -17.0))
        cases = (
            (ViewKind.ARCHITECTURE, architecture_focus.id, architecture_focus.id),
            (ViewKind.EXECUTION, execution_root, execution_focus.id),
            (ViewKind.SYMBOL, None, symbol_focus.id),
            (ViewKind.DATA_FLOW, None, data_focus.id),
        )
        for kind, root_id, source_id in cases:
            with self.subTest(kind=kind):
                semantic_before = deepcopy(analysis.semantic.graph)
                flow_graph_before = deepcopy(analysis.flow.graph.to_dict())
                data_nodes_before = deepcopy(analysis.flow.data_nodes)
                symbol_facts_before = deepcopy((
                    analysis.symbol_index.entries,
                    analysis.symbol_index.references,
                    analysis.symbol_index.scope_declarations,
                ))
                state_before = controller.view_state

                detail = controller.detail_view(kind, root_id, source_id)

                self.assertIn(source_id, {node.id for node in detail.nodes})
                self.assertLessEqual(len(detail.nodes), 18)
                self.assertLessEqual(len(detail.edges), 40)
                self.assertEqual(detail.query_context.get("hops"), 1)
                self.assertEqual(detail.max_nodes, 18)
                self.assertEqual(detail.max_edges, 40)
                self.assertTrue(all(node.source_anchors or node.member_ids
                                    for node in detail.nodes))
                self.assertEqual(analysis.semantic.graph, semantic_before)
                self.assertEqual(analysis.flow.graph.to_dict(), flow_graph_before)
                self.assertEqual(analysis.flow.data_nodes, data_nodes_before)
                self.assertEqual(
                    (analysis.symbol_index.entries,
                     analysis.symbol_index.references,
                     analysis.symbol_index.scope_declarations),
                    symbol_facts_before,
                )
                self.assertEqual(controller.view_state, state_before)

        self.assertNotEqual(
            controller.detail_view(
                ViewKind.ARCHITECTURE,
                architecture_focus.id,
                architecture_focus.id,
            ).layout_hints.get(architecture_focus.id),
            (901.0, 777.0),
        )

    def test_architecture_instance_detail_navigates_to_target_definition_source(self):
        controller = AppController(ignore_paths=("data",))
        controller.open_project(self.fixture_root)
        controller.analyze()
        architecture = controller.view(ViewKind.ARCHITECTURE)
        classifier = next(node for node in architecture.nodes
                          if node.display_name == "Classifier")
        classifier_view = controller.view(ViewKind.ARCHITECTURE, classifier.id)
        encoder = next(node for node in classifier_view.nodes
                       if node.display_name == "encoder")

        detail = controller.detail_view(
            ViewKind.ARCHITECTURE, classifier.id, encoder.id)

        self.assertEqual(detail.root_ids, (encoder.metadata["target_definition_id"],))
        layers = next(node for node in detail.nodes
                      if node.display_name.startswith("layers"))
        document = controller.read_source(layers.source_anchors[0])
        self.assertIn("nn.ModuleList", document.text.splitlines()[
            document.start_line - 1])

    def test_execution_loop_detail_contains_nested_source_actions(self):
        controller = AppController(ignore_paths=("data",))
        controller.open_project(self.fixture_root)
        analysis = controller.analyze()
        assert analysis.semantic is not None
        entry = next(component for component in analysis.semantic.graph.components
                     if component.display_name == "train_epoch")
        execution = controller.view(ViewKind.EXECUTION, entry.id)
        epoch = next(node for node in execution.nodes
                     if node.display_name == "EpochLoop")

        detail = controller.detail_view(ViewKind.EXECUTION, entry.id, epoch.id)

        names = {node.display_name: node for node in detail.nodes}
        self.assertIn("BatchLoop", names)
        for name, source_text in (("BackwardPass", "loss.backward()"),
                                  ("ParameterUpdate", "optimizer.step()")):
            with self.subTest(name=name):
                node = names[name]
                document = controller.read_source(node.source_anchors[0])
                self.assertEqual(document.text.splitlines()[
                    document.start_line - 1].strip(), source_text)

    def test_detail_view_normalizes_invalid_semantic_and_code_context_nodes(self):
        controller = AppController(ignore_paths=("data",))
        controller.open_project(self.fixture_root)
        controller.analyze()

        for kind in (ViewKind.ARCHITECTURE, ViewKind.SYMBOL):
            with self.subTest(kind=kind), self.assertRaisesRegex(
                    ValueError, r"^node has no local detail graph$"):
                controller.detail_view(kind, None, "outside-node")

    def test_detail_view_preserves_unexpected_builder_value_error(self):
        controller = AppController(ignore_paths=("data",))
        controller.open_project(self.fixture_root)
        controller.analyze()

        with patch(
            "codeviz.ui.controller.build_visible_graph",
            side_effect=ValueError("internal invariant broken"),
        ), self.assertRaisesRegex(ValueError, r"^internal invariant broken$"):
            controller.detail_view(ViewKind.ARCHITECTURE, None, "model")

    def test_detail_view_requires_analysis(self):
        controller = AppController()

        with self.assertRaisesRegex(
                RuntimeError, "^analyze the project before opening node details$"):
            controller.detail_view(ViewKind.ARCHITECTURE, None, "model")

    def test_reset_positions_keeps_analysis_and_navigation_state(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text("def train(): return 1\n", encoding="utf-8")
            controller = AppController()
            controller.open_project(root)
            analysis = controller.analyze()
            controller.move_node("custom-node", (220.0, 100.0))
            controller.expand("expanded-node")
            controller.save_layout(ViewKind.ARCHITECTURE, "selected-node",
                                   ("graph:Architecture",),
                                   {"Architecture": "model-a"})
            controller.save_viewport(ViewKind.ARCHITECTURE, 1.4, (25.0, -10.0))
            island = controller.open_detail_island(
                ViewKind.ARCHITECTURE, "model-a", (620.0, 180.0))
            controller.reset_positions()
            self.assertEqual(controller.view_state.positions, {})
            self.assertEqual(controller.view_state.expanded_ids, ("expanded-node",))
            self.assertEqual(controller.view_state.active_view, ViewKind.ARCHITECTURE)
            self.assertEqual(controller.view_state.selected_id, "selected-node")
            self.assertEqual(controller.view_state.open_tabs, ("graph:Architecture",))
            self.assertEqual(controller.view_state.graph_roots,
                             {"Architecture": "model-a"})
            self.assertEqual(controller.view_state.viewport_by_view["Architecture"],
                             ViewportState(1.4, (25.0, -10.0)))
            self.assertEqual(controller.view_state.detail_islands,
                             (island.__class__(island.id, island.view_kind,
                                               island.source_node_id, None),))
            self.assertIs(controller.analysis, analysis)

    def test_canvas_state_mutations_are_bounded_and_persisted(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            store = CacheStore(Path(data_dir))
            controller = AppController(store)
            controller.open_project(project)

            controller.save_viewport(ViewKind.ARCHITECTURE, 1.4, (25.0, -10.0))
            first = controller.open_detail_island(
                ViewKind.ARCHITECTURE, "model-a", (620.0, 180.0))
            self.assertIs(controller.open_detail_island(
                ViewKind.ARCHITECTURE, "model-a", None), first)
            controller.open_detail_island(ViewKind.ARCHITECTURE, "model-b", None)
            controller.open_detail_island(ViewKind.ARCHITECTURE, "model-c", None)
            with self.assertRaisesRegex(
                    ValueError, "close a detail island before opening another"):
                controller.open_detail_island(ViewKind.ARCHITECTURE, "model-d", None)

            controller.move_detail_island(first.id, (700.0, 220.0))
            self.assertEqual(controller.detail_islands(ViewKind.ARCHITECTURE)[0].position,
                             (700.0, 220.0))
            with self.assertRaisesRegex(ValueError, "detail island is not open"):
                controller.move_detail_island("Architecture:missing", (0.0, 0.0))
            controller.close_detail_island("Architecture:model-b")
            controller.close_detail_island("Architecture:missing")

            restored = store.load_view_state(project)
            self.assertEqual(restored.viewport_by_view["Architecture"],
                             ViewportState(1.4, (25.0, -10.0)))
            self.assertEqual(restored.detail_islands,
                             controller.detail_islands(ViewKind.ARCHITECTURE))



if __name__ == "__main__":
    unittest.main()
