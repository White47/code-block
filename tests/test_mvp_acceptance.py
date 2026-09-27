"""End-to-end, source-backed MVP paths over one fixed Python/PyTorch fixture."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from codeviz.analysis.project import analyze_project
from codeviz.core import Confidence, EdgeType, NodeKind, ViewKind
from codeviz.navigation import plan_reveal
from codeviz.persistence.store import CacheStore
from codeviz.search import search_project
from codeviz.semantic import TrainingRole
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.presenter import build_inspector, build_overview
from codeviz.ui.tkapp import CodeVizApp
from codeviz.views import ViewRequest, build_visible_graph

PROJECT = Path(__file__).parent / "fixtures" / "mvp_project"


class MvpAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.analysis = analyze_project(PROJECT)

    def test_architecture_path_reaches_real_source_after_drill_down(self):
        overview = build_overview(self.analysis, PROJECT)
        models = {item.name: item for item in overview.models}
        self.assertIn("model.Classifier", models)
        self.assertIn("model.Baseline", models)
        classifier = models["model.Classifier"]
        controller = AppController()
        controller.open_project(PROJECT)
        controller.analyze()
        first = controller.view(ViewKind.ARCHITECTURE, classifier.id)
        self.assertEqual(first.root_ids, (classifier.id,))
        encoder = next(node for node in first.nodes if node.code_name.endswith(".encoder"))
        target_id = encoder.metadata.get("target_definition_id")
        self.assertIsInstance(target_id, str)
        detailed = controller.view(ViewKind.ARCHITECTURE, target_id)
        self.assertEqual(detailed.root_ids, (target_id,))
        self.assertTrue(any(node.display_name.startswith("layers") for node in detailed.nodes))
        source_node = next(node for node in detailed.nodes if node.id == target_id)
        inspector = build_inspector(detailed, source_node.id, controller.analysis)
        self.assertTrue(inspector.can_open_source)
        document = controller.read_source(inspector.source_anchors[0])
        self.assertIn("class Encoder", document.text.splitlines()[document.start_line - 1])
        self.assertEqual(document.file_path, "model.py")
        self.assertTrue(any(item.container_kind == "ModuleList" and item.repeat_count == 2
                            for item in self.analysis.model.instances))

    def test_training_path_has_two_entries_shared_model_and_source(self):
        training = self.analysis.training
        self.assertIsNotNone(training)
        entries = {entry.name: entry for entry in training.entry_points}
        self.assertIn("train_epoch", entries)
        self.assertIn("evaluate", entries)
        classifier = next(item for item in self.analysis.model.definitions
                          if item.qualified_name == "model.Classifier")
        self.assertIn(classifier.symbol_id, entries["train_epoch"].model_ids)
        self.assertIn(classifier.symbol_id, entries["evaluate"].model_ids)
        events_by_id = {event.id: event for event in training.events}
        roles = {events_by_id[item].role for item in entries["train_epoch"].flow_node_ids}
        self.assertTrue({TrainingRole.EPOCH_LOOP, TrainingRole.BATCH_LOOP,
                         TrainingRole.FORWARD_PASS, TrainingRole.LOSS_CALCULATION,
                         TrainingRole.BACKWARD_PASS,
                         TrainingRole.PARAMETER_UPDATE} <= roles)
        view = build_visible_graph(self.analysis, ViewRequest(
            ViewKind.EXECUTION, root_id=entries["train_epoch"].id))
        self.assertEqual(view.root_ids, (entries["train_epoch"].id,))
        self.assertTrue(any(edge.type == EdgeType.LOOP_BACK for edge in view.edges) or
                        any(node.display_name == "EpochLoop" for node in view.nodes))
        backward = next(item for item in training.events
                        if item.role == TrainingRole.BACKWARD_PASS)
        source = AppController()
        source.open_project(PROJECT)
        document = source.read_source(backward.source_anchor)
        self.assertEqual(document.text.splitlines()[document.start_line - 1].strip(),
                         "loss.backward()")
        update = next(item for item in training.events
                      if item.role == TrainingRole.PARAMETER_UPDATE)
        document = source.read_source(update.source_anchor)
        self.assertEqual(document.text.splitlines()[document.start_line - 1].strip(),
                         "optimizer.step()")

    def test_search_path_disambiguates_loss_and_reveals_local_context(self):
        results = search_project(self.analysis, "loss").results
        local = [item for item in results if item.source_kind == "symbol" and
                 item.name == "loss"]
        self.assertEqual({item.qualified_name for item in local},
                         {"run.train_epoch.loss", "run.evaluate.loss"})
        target = plan_reveal(self.analysis, local[0])
        self.assertEqual(target.view_kind, ViewKind.DATA_FLOW)
        view = build_visible_graph(self.analysis, ViewRequest(
            target.view_kind, root_id=target.root_id, selected_id=target.selected_id))
        self.assertIn(target.selected_id, {node.id for node in view.nodes})
        inspector = build_inspector(view, target.selected_id, self.analysis)
        self.assertTrue(inspector.can_open_source)
        semantic = search_project(self.analysis, "反向传播").results
        self.assertTrue(semantic)
        semantic_target = plan_reveal(self.analysis, semantic[0])
        self.assertEqual(semantic_target.view_kind, ViewKind.EXECUTION)
        self.assertLessEqual(len(view.nodes), 100)

    def test_three_golden_paths_restore_canvas_state_and_source_identity(self):
        with TemporaryDirectory() as data_dir:
            store = CacheStore(Path(data_dir))
            controller = AppController(store)
            controller.open_project(PROJECT)
            analysis = controller.analyze()
            expected_status = controller.status
            overview = build_overview(analysis, PROJECT)
            classifier = next(item for item in overview.models
                              if item.name == "model.Classifier")
            architecture = controller.view(ViewKind.ARCHITECTURE, classifier.id)
            encoder = next(node for node in architecture.nodes
                           if node.code_name.endswith(".encoder"))
            assert analysis.training is not None
            train = next(entry for entry in analysis.training.entry_points
                         if entry.name == "train_epoch")
            backward_event = next(
                event for event in analysis.training.events
                if event.role == TrainingRole.BACKWARD_PASS)
            execution = controller.view(
                ViewKind.EXECUTION, train.id, backward_event.id)
            backward = next(node for node in execution.nodes
                            if node.id == backward_event.id)
            loss = next(item for item in search_project(analysis, "loss").results
                        if item.qualified_name == "run.train_epoch.loss")
            reveal = plan_reveal(analysis, loss)
            assert reveal.view_kind is not None and reveal.selected_id is not None
            data_flow = controller.view(
                reveal.view_kind, reveal.root_id, reveal.selected_id)
            loss_node = next(node for node in data_flow.nodes
                             if node.id == reveal.selected_id)

            paths = (
                (ViewKind.ARCHITECTURE, classifier.id, encoder),
                (ViewKind.EXECUTION, train.id, backward),
                (reveal.view_kind, reveal.root_id, loss_node),
            )
            for index, (kind, root_id, selected) in enumerate(paths):
                with self.subTest(path=kind.value):
                    zoom = 1.15 + index * 0.1
                    pan = (24.0 + index, -12.0 - index)
                    island_position = (640.0 + index * 20, 180.0)
                    controller.save_viewport(kind, zoom, pan)
                    controller.open_detail_island(kind, selected.id, island_position)
                    controller.save_layout(
                        kind, selected.id, (f"graph:{kind.value}",),
                        {kind.value: root_id})

                    reopened = AppController(store)
                    reopened.open_project(PROJECT)
                    restored = reopened.view(kind, root_id, selected.id)
                    restored_node = next(node for node in restored.nodes
                                         if node.id == selected.id)

                    self.assertEqual(reopened.status, expected_status)
                    self.assertEqual(reopened.view_state.selected_id, selected.id)
                    viewport = reopened.view_state.viewport_by_view[kind.value]
                    self.assertEqual((viewport.zoom, viewport.pan), (zoom, pan))
                    island = next(
                        item for item in reopened.detail_islands(kind)
                        if item.source_node_id == selected.id)
                    self.assertEqual(island.position, island_position)
                    self.assertEqual(restored_node.source_anchors[0],
                                     selected.source_anchors[0])
                    restored_analysis = reopened.analysis
                    assert restored_analysis is not None
                    self.assertEqual(
                        build_inspector(restored, selected.id,
                                        restored_analysis).source_anchors[0],
                        selected.source_anchors[0])
                    controller = reopened

    def test_deleted_detail_node_is_pruned_without_losing_reopened_main_graph(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            source = project / "models.py"
            source.write_text(
                "import torch.nn as nn\n"
                "class Keep(nn.Module):\n"
                "    def forward(self, x): return x\n"
                "class Removed(nn.Module):\n"
                "    def forward(self, x): return x\n", encoding="utf-8")
            store = CacheStore(Path(data_dir))
            first = AppController(store)
            first.open_project(project)
            first.analyze()
            graph = first.view(ViewKind.ARCHITECTURE)
            removed = next(node for node in graph.nodes
                           if node.display_name == "Removed")
            first.open_detail_island(ViewKind.ARCHITECTURE, removed.id, None)
            first.save_layout(ViewKind.ARCHITECTURE, removed.id,
                              ("graph:Architecture",), {"Architecture": None})
            source.write_text(
                "import torch.nn as nn\n"
                "class Keep(nn.Module):\n"
                "    def forward(self, x): return x\n", encoding="utf-8")
            first.analyze()

            reopened = AppController(store)
            reopened.open_project(project)
            main = reopened.view(ViewKind.ARCHITECTURE)
            app = CodeVizApp.__new__(CodeVizApp)
            app.controller = reopened
            app._roots = {ViewKind.ARCHITECTURE: None}
            app.language = "en"
            app.status_label = SimpleNamespace(configure=lambda **_options: None)

            self.assertEqual(app._detail_pairs(ViewKind.ARCHITECTURE), ())
            self.assertEqual(reopened.detail_islands(ViewKind.ARCHITECTURE), ())
            self.assertEqual(reopened.project_root, project.resolve())
            self.assertIsNotNone(reopened.analysis)
            self.assertTrue(any(node.display_name == "Keep" for node in main.nodes))

    def test_bad_file_is_isolated_and_source_is_never_executed(self):
        self.assertIn("trap.py", {file.relative_path for file in self.analysis.files})
        self.assertTrue(any(issue.code == "SYNTAX_ERROR" and issue.file_path == "broken.py"
                            for issue in self.analysis.issues))
        self.assertNotIn("data/ignored.py", {file.relative_path for file in self.analysis.files})
        graph = self.analysis.flow.graph
        callback = next(edge for edge in graph.edges
                        if edge.type == EdgeType.CALLS and
                        graph.nodes[edge.source].name == "callback")
        self.assertEqual(callback.confidence, Confidence.UNRESOLVED)
        self.assertEqual(graph.nodes[callback.target].kind, NodeKind.UNRESOLVED_SYMBOL)
        controller = AppController()
        controller.open_project(PROJECT)
        controller.analyze()
        self.assertEqual(controller.status, AnalysisStatus.PARTIAL)
        overview = build_overview(controller.analysis, PROJECT)
        self.assertEqual(overview.health.files_parsed, overview.health.files_total - 1)
