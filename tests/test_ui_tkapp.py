"""Desktop shell stays importable without constructing a display window."""

import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.core import SourceAnchor, ViewKind, VisibleGraph, VisibleNode
from codeviz.navigation import plan_reveal
from codeviz.persistence.store import CacheStore
from codeviz.search import search_project
from codeviz.ui.controller import AnalysisStatus
from codeviz.ui.presenter import build_overview, build_problems
from codeviz.ui.tkapp import CodeVizApp, main


class TkAppImportTests(unittest.TestCase):
    def test_app_entry_points_are_lazy_callables(self):
        self.assertTrue(callable(CodeVizApp))
        self.assertTrue(callable(main))

    def test_package_exposes_desktop_launcher_without_runtime_dependency(self):
        manifest = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(manifest["project"]["scripts"]["codeviz"],
                         "codeviz.ui.tkapp:main")
        self.assertEqual(manifest["project"]["dependencies"], [])

    def test_launcher_explains_missing_tk(self):
        with patch("codeviz.ui.tkapp.CodeVizApp", side_effect=ImportError("no tkinter")):
            with self.assertRaisesRegex(SystemExit, "Tkinter"):
                main()


class TkAppLiveTests(unittest.TestCase):
    def _buttons(self, parent):
        for child in parent.winfo_children():
            if isinstance(child, self.app.tk.Button):
                yield child
            yield from self._buttons(child)

    def _labels(self, parent):
        for child in parent.winfo_children():
            if isinstance(child, self.app.tk.Label):
                yield child
            yield from self._labels(child)

    def setUp(self):
        self.cache_dir = TemporaryDirectory()
        self.addCleanup(self.cache_dir.cleanup)
        try:
            with patch("codeviz.ui.tkapp.CacheStore",
                       return_value=CacheStore(Path(self.cache_dir.name))):
                self.app = CodeVizApp()
        except Exception as error:
            import tkinter

            if isinstance(error, tkinter.TclError):
                self.skipTest("Tk display unavailable")
            raise
        self.app.root.withdraw()

    def tearDown(self):
        self.app.close()

    def test_reset_destroys_detached_source_widgets(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text("value = 1\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app._open_source(SourceAnchor("run.py", 1), False)
            self.app._open_source(SourceAnchor("run.py", 1), True)
            frames = tuple(self.app._source_tabs.values())
            self.app._reset_editors()
            self.assertTrue(all(not frame.winfo_exists() for frame in frames))

    def test_project_picker_is_unavailable_while_analysis_runs(self):
        self.app._analyzing = True
        with patch.object(self.app.filedialog, "askdirectory", return_value="") as askdirectory:
            self.app._choose_project()
        askdirectory.assert_not_called()

    def test_overview_model_choice_opens_its_architecture(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "model.py").write_text(
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, x): return x\n", encoding="utf-8")
            self.app.controller.open_project(root)
            analysis = self.app.controller.analyze()
            model = build_overview(analysis, root).models[0]
            self.app._render_overview()

            choice = next(button for button in self._buttons(self.app.overview_tab)
                          if button.cget("text") == model.name)
            choice.invoke()
            self.assertEqual(self.app._active_kind, ViewKind.ARCHITECTURE)
            self.assertEqual(self.app._roots[ViewKind.ARCHITECTURE], model.id)

    def test_overview_health_displays_resolved_symbol_count(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.py").write_text("value = 1\nother = value\n", encoding="utf-8")
            self.app.controller.open_project(root)
            analysis = self.app.controller.analyze()
            count = build_overview(analysis, root).health.symbols_resolved
            self.app._render_overview()
            self.assertTrue(any(f"Resolved {count}" in label.cget("text")
                                for label in self._labels(self.app.overview_tab)))

    def test_overview_opens_independent_analysis_issues_tab(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bad.py").write_text("def broken(:\n", encoding="utf-8")
            self.app.controller.open_project(root)
            analysis = self.app.controller.analyze()
            expected_code = build_problems(analysis)[0].code
            self.app._render_overview()
            choice = next(button for button in self._buttons(self.app.overview_tab)
                          if button.cget("text") == "Analysis Issues")
            choice.invoke()
            self.assertEqual(self.app.main_tabs.tab(self.app.main_tabs.select(), "text"),
                             "Problems")
            issue_rows = self.app.problems_tree.get_children()
            self.assertTrue(any(expected_code in self.app.problems_tree.item(row, "values")
                                for row in issue_rows))

    def test_graph_selection_inspector_and_split_source_share_real_anchor(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text(
                "def train(loss, optimizer):\n"
                "    loss.backward()\n"
                "    optimizer.step()\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._open_graph(ViewKind.EXECUTION)
            node = next(item for item in self.app._views[ViewKind.EXECUTION].nodes
                        if item.display_name == "ParameterUpdate")
            self.app._on_select(node.id)
            self.assertIn("optimizer.step", self.app.inspector_text.get("1.0", "end"))
            self.assertEqual(self.app.side_button.cget("state"), "normal")
            self.app.side_button.invoke()
            self.assertTrue(self.app._side_open)
            source = self.app._source_widgets[("run.py", True)]
            self.assertIn("optimizer.step()", source.get("1.0", "end"))
            self.assertTrue(source.tag_ranges("focus"))

    def test_search_reveal_symbol_inspector_and_side_source(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text(
                "def train_epoch():\n    loss = 1\n    return loss\n"
                "def validate():\n    loss = 2\n    return loss\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._set_activity("Search")
            self.app.search_entry.insert(0, "loss")
            self.app._refresh_search()
            rows = self.app.sidebar_tree.get_children()
            self.assertEqual(len(rows), 2)
            chosen = next(row for row in rows if "validate.loss" in
                          self.app.sidebar_tree.item(row, "text"))
            self.app.sidebar_tree.selection_set(chosen)
            self.app._open_search_source()
            self.assertIn("loss = 2", self.app._source_widgets[("run.py", False)].get("1.0", "end"))
            self.app._reveal_search_result()
            self.assertEqual(self.app._active_kind, ViewKind.DATA_FLOW)
            self.assertIn("run.py:5", self.app.inspector_text.get("1.0", "end"))
            self.app.side_button.invoke()
            self.assertTrue(self.app._side_open)
            self.assertIn("loss = 2", self.app._source_widgets[("run.py", True)].get("1.0", "end"))

    def test_search_semantic_result_reveals_training_event(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text(
                "def train(loss, optimizer):\n"
                "    loss.backward()\n    optimizer.step()\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._set_activity("Search")
            self.app.search_entry.insert(0, "反向传播")
            self.app._refresh_search()
            rows = self.app.sidebar_tree.get_children()
            self.assertEqual(len(rows), 1)
            self.app.sidebar_tree.selection_set(rows[0])
            self.app._reveal_search_result()
            self.assertEqual(self.app._active_kind, ViewKind.EXECUTION)
            self.assertIn("loss.backward", self.app.inspector_text.get("1.0", "end"))

    def test_symbols_browse_and_new_project_discard_old_search_rows(self):
        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            original = Path(first)
            replacement = Path(second)
            (original / "run.py").write_text("value = 1\n", encoding="utf-8")
            (replacement / "other.py").write_text("fresh = 2\n", encoding="utf-8")
            self.app.controller.open_project(original)
            self.app.controller.analyze()
            self.app._set_activity("Search")
            self.app.search_entry.insert(0, "value")
            self.app._refresh_search()
            self.assertTrue(self.app._search_rows)
            self.app._set_activity("Symbols")
            self.assertEqual(self.app.search_entry.get(), "")
            self.assertTrue(self.app._search_rows)
            self.app.controller.open_project(replacement)
            self.app._reset_editors()
            self.app._set_activity("Search")
            self.assertFalse(self.app._search_rows)

    def test_data_flow_step_click_selects_without_invalid_recentering(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text(
                "def train(x):\n    loss = x\n    return loss\n", encoding="utf-8")
            self.app.controller.open_project(root)
            analysis = self.app.controller.analyze()
            result = next(item for item in search_project(analysis, "loss").results
                          if item.source_kind == "symbol" and item.name == "loss")
            target = plan_reveal(analysis, result)
            self.app._open_graph(target.view_kind, target.root_id,
                                 reveal_id=target.selected_id)
            view = self.app._views[ViewKind.DATA_FLOW]
            step = next(node for node in view.nodes
                        if analysis.flow.graph.nodes[node.id].kind.value == "FlowStep")
            with patch.object(self.app.messagebox, "showerror") as showerror:
                self.app._on_select(step.id)
            showerror.assert_not_called()
            self.assertEqual(self.app._selected_id, step.id)
            self.assertIn("SOURCE", self.app.inspector_text.get("1.0", "end"))

    def test_new_search_reveal_resets_local_context_to_one_hop(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text(
                "def train_epoch():\n    loss = 1\n    return loss\n"
                "def validate():\n    loss = 2\n    return loss\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._set_activity("Search")
            self.app.search_entry.insert(0, "loss")
            self.app._refresh_search()
            rows = self.app.sidebar_tree.get_children()
            self.app.sidebar_tree.selection_set(rows[0])
            self.app._reveal_search_result()
            self.app._change_context(ViewKind.DATA_FLOW, direction="downstream", hop_delta=4)
            self.assertEqual(self.app._views[ViewKind.DATA_FLOW].query_context["hops"], 5)
            self.app.sidebar_tree.selection_set(rows[1])
            self.app._reveal_search_result()
            context = self.app._views[ViewKind.DATA_FLOW].query_context
            self.assertEqual(context["hops"], 1)
            self.assertEqual(context["direction"], "both")

    def test_reveal_neighbor_in_same_graph_also_resets_search_context(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text(
                "def train(x):\n    loss = x\n    prediction = loss\n"
                "    return prediction\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._set_activity("Search")
            self.app.search_entry.insert(0, "loss")
            self.app._refresh_search()
            loss_row = next(row for row, result in self.app._search_rows.items()
                            if result.name == "loss" and result.source_kind == "symbol")
            self.app.sidebar_tree.selection_set(loss_row)
            self.app._reveal_search_result()
            self.app._change_context(ViewKind.DATA_FLOW, direction="downstream", hop_delta=4)
            self.app.search_entry.delete(0, "end")
            self.app.search_entry.insert(0, "prediction")
            self.app._refresh_search()
            prediction_row = next(row for row, result in self.app._search_rows.items()
                                  if result.name == "prediction" and result.source_kind == "symbol")
            self.app.sidebar_tree.selection_set(prediction_row)
            self.app._reveal_search_result()
            self.assertEqual(self.app._views[ViewKind.DATA_FLOW].query_context["hops"], 1)
            self.assertEqual(self.app._views[ViewKind.DATA_FLOW].query_context["direction"], "both")

    def test_switching_graph_kind_does_not_reuse_unrelated_selection(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "model.py").write_text(
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, x): return x\n", encoding="utf-8")
            (root / "run.py").write_text(
                "def train(optimizer):\n    optimizer.step()\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._open_graph(ViewKind.ARCHITECTURE)
            model_id = self.app._views[ViewKind.ARCHITECTURE].root_ids[0]
            self.app._on_select(model_id)
            with patch.object(self.app.messagebox, "showerror") as showerror:
                self.app._set_activity("Execution")
            showerror.assert_not_called()
            self.assertEqual(self.app._active_kind, ViewKind.EXECUTION)
            self.assertIsNone(self.app._selected_id)

    def test_tab_switch_updates_inspector_to_active_graph(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "model.py").write_text(
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, x): return x\n", encoding="utf-8")
            (root / "run.py").write_text(
                "def train(optimizer):\n    optimizer.step()\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._open_graph(ViewKind.ARCHITECTURE)
            self.app._open_graph(ViewKind.EXECUTION)
            arch_frame = self.app._graph_tabs[ViewKind.ARCHITECTURE][0]
            exec_frame = self.app._graph_tabs[ViewKind.EXECUTION][0]
            self.app.main_tabs.select(arch_frame)
            self.app._tab_changed(None)
            self.app._on_select(self.app._views[ViewKind.ARCHITECTURE].root_ids[0])
            self.app.main_tabs.select(exec_frame)
            self.app._tab_changed(None)
            self.assertIsNone(self.app._selected_id)
            self.assertEqual(self.app.source_button.cget("state"), "disabled")

    def test_reanalysis_hides_previous_overview_until_finished(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "model.py").write_text(
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, x): return x\n", encoding="utf-8")
            self.app.controller.open_project(root)
            analysis = self.app.controller.analyze()
            model_name = build_overview(analysis, root).models[0].name
            self.app._render_overview()
            self.assertTrue(any(button.cget("text") == model_name
                                for button in self._buttons(self.app.overview_tab)))
            with patch("codeviz.ui.tkapp.Thread"):
                self.app._start_analyze()
            self.assertFalse(any(button.cget("text") == model_name
                                 for button in self._buttons(self.app.overview_tab)))

    def test_reanalysis_clears_old_explorer_entries(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "model.py").write_text(
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, x): return x\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._set_activity("Explorer")
            before = [self.app.sidebar_tree.item(item, "text")
                      for parent in self.app.sidebar_tree.get_children()
                      for item in self.app.sidebar_tree.get_children(parent)]
            self.assertTrue(any("Net" in text for text in before))
            with patch("codeviz.ui.tkapp.Thread"):
                self.app._start_analyze()
            self.assertFalse(self.app._sidebar_targets)
            self.assertIn("Analyzing", self.app.sidebar_tree.item(
                self.app.sidebar_tree.get_children()[0], "text"))

    def test_failed_reanalysis_restores_explorer_and_keeps_recent_project(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "model.py").write_text(
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, x): return x\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._set_activity("Explorer")
            with patch("codeviz.ui.tkapp.Thread"):
                self.app._start_analyze()
            self.app.controller.analysis = None
            self.app.controller.status = AnalysisStatus.FAILED
            self.app._messages.put(("error", "scan failed"))
            with patch.object(self.app.messagebox, "showerror"):
                self.app._poll_messages()
            root_labels = [self.app.sidebar_tree.item(item, "text")
                           for item in self.app.sidebar_tree.get_children()]
            self.assertIn("Click Analyze Project", root_labels)
            self.assertIn("Recent Projects", root_labels)
            self.assertTrue(all(action == "recent" for action, _ in
                                self.app._sidebar_targets.values()))

    def test_outdated_cache_warning_appears_in_overview_graph_and_source(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "run.py"
            source.write_text("def train(): return 1\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            source.write_text("def train(): return 2\n", encoding="utf-8")
            self.app._open_project_path(root)
            self.assertEqual(self.app.controller.status, AnalysisStatus.OUTDATED)
            self.assertTrue(any("OUTDATED" in label.cget("text")
                                for label in self._labels(self.app.overview_tab)))
            self.app._open_graph(ViewKind.ARCHITECTURE)
            self.assertIn("OUTDATED", self.app._graph_warnings[ViewKind.ARCHITECTURE].cget(
                "text"))
            self.app._open_source(SourceAnchor("run.py", 1), False)
            self.assertIn("OUTDATED", self.app._source_warnings[("run.py", False)].cget(
                "text"))

    def test_reopen_restores_graph_source_tabs_and_selection(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text(
                "import torch.nn as nn\nclass Net(nn.Module):\n"
                "    def forward(self, x): return x\n", encoding="utf-8")
            self.app._open_project_path(root)
            self.app.controller.analyze()
            self.app._open_graph(ViewKind.ARCHITECTURE)
            visible = self.app._views[ViewKind.ARCHITECTURE]
            selected = visible.nodes[0].id
            self.app._on_select(selected)
            self.app._open_source(SourceAnchor("run.py", 1), False)
            self.app._open_graph(ViewKind.ARCHITECTURE, reveal_id=selected)
            self.app.root.update()
            self.app._sync_layout()
            self.assertIn("graph:Architecture", self.app.controller.view_state.open_tabs)
            self.assertIn("source:0:run.py", self.app.controller.view_state.open_tabs)
            self.app._open_project_path(root)
            self.assertEqual(self.app.controller.status, AnalysisStatus.READY)
            self.assertIn(ViewKind.ARCHITECTURE, self.app._graph_tabs)
            self.assertIn(("run.py", False), self.app._source_tabs)
            self.assertEqual(self.app._selected_id, selected)

    def test_mvp_fixture_three_paths_are_navigable_in_desktop(self):
        project = Path(__file__).parent / "fixtures" / "mvp_project"
        self.app._open_project_path(project)
        self.app.controller.analyze()
        self.app._render_overview()

        model_button = next(button for button in self._buttons(self.app.overview_tab)
                            if button.cget("text") == "model.Classifier")
        model_button.invoke()
        self.assertEqual(self.app._active_kind, ViewKind.ARCHITECTURE)
        architecture = self.app._views[ViewKind.ARCHITECTURE]
        classifier_id = self.app._roots[ViewKind.ARCHITECTURE]
        encoder = next(node for node in architecture.nodes
                       if node.code_name.endswith(".encoder"))
        self.app._on_drill_down(encoder.id)
        self.assertEqual(self.app._roots[ViewKind.ARCHITECTURE],
                         encoder.metadata.get("target_definition_id"))
        self.assertTrue(any(node.display_name.startswith("layers") for node in
                            self.app._views[ViewKind.ARCHITECTURE].nodes))
        self.app._go_back()
        self.assertEqual(self.app._roots[ViewKind.ARCHITECTURE], classifier_id)
        baseline_button = next(button for button in self._buttons(self.app.overview_tab)
                               if button.cget("text") == "model.Baseline")
        baseline_button.invoke()
        self.app._go_back()
        self.assertEqual(self.app.main_tabs.select(), str(self.app.overview_tab))

        train_button = next(button for button in self._buttons(self.app.overview_tab)
                            if button.cget("text") == "train_epoch")
        train_button.invoke()
        execution = self.app._views[ViewKind.EXECUTION]
        epoch = next(node for node in execution.nodes if node.display_name == "EpochLoop")
        self.app._on_drill_down(epoch.id)
        batch = next(node for node in self.app._views[ViewKind.EXECUTION].nodes
                     if node.display_name == "BatchLoop")
        self.app._on_drill_down(batch.id)
        update = next(node for node in self.app._views[ViewKind.EXECUTION].nodes
                      if node.display_name == "ParameterUpdate")
        self.app._on_select(update.id)
        self.app.source_button.invoke()
        source = self.app.controller.read_source(update.source_anchors[0])
        self.assertEqual(source.text.splitlines()[source.start_line - 1].strip(),
                         "optimizer.step()")
        backward = next(node for node in self.app._views[ViewKind.EXECUTION].nodes
                        if node.display_name == "BackwardPass")
        self.app._on_select(backward.id)
        self.app.source_button.invoke()
        self.assertIn("loss.backward()", self.app._source_widgets[("run.py", False)].get(
            "1.0", "end"))

        self.app._set_activity("Search")
        self.app.search_entry.insert(0, "loss")
        self.app._refresh_search()
        chosen = next(row for row in self.app.sidebar_tree.get_children()
                      if "run.train_epoch.loss" in self.app.sidebar_tree.item(row, "text"))
        self.app.sidebar_tree.selection_set(chosen)
        self.app._reveal_search_result()
        self.assertEqual(self.app._active_kind, ViewKind.DATA_FLOW)
        self.assertIn("SOURCE", self.app.inspector_text.get("1.0", "end"))
        self.app.side_button.invoke()
        self.assertIn("loss = criterion", self.app._source_widgets[("run.py", True)].get(
            "1.0", "end"))

    def test_aggregate_drill_down_exposes_hidden_member_choices(self):
        kind = ViewKind.ARCHITECTURE
        aggregate = VisibleNode("aggregate", "2 more", member_ids=("first", "second"))
        self.app._views[kind] = VisibleGraph(kind, (aggregate,))
        self.app._active_kind = kind
        self.app._on_drill_down(aggregate.id)
        self.assertEqual(self.app.sidebar_title.cget("text"), "HIDDEN MEMBERS")
        self.assertEqual({value for action, value in self.app._sidebar_targets.values()
                          if action == "reveal"}, {"first", "second"})
        second = next(item for item, target in self.app._sidebar_targets.items()
                      if target == ("reveal", "second"))
        self.app.sidebar_tree.selection_set(second)
        with patch.object(self.app, "_open_graph") as open_graph:
            self.app._activate_sidebar(None)
        self.assertEqual(self.app._selections[kind], "second")
        open_graph.assert_called_once_with(kind, None)

    def test_hidden_member_choice_reveals_real_large_project_model(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = "import torch.nn as nn\n" + "".join(
                f"class Model{index}(nn.Module):\n"
                "    def forward(self, x): return x\n"
                for index in range(105))
            (root / "models.py").write_text(source, encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._open_graph(ViewKind.ARCHITECTURE)
            aggregate = next(node for node in self.app._views[ViewKind.ARCHITECTURE].nodes
                             if node.member_ids)
            target = aggregate.member_ids[-1]
            self.app._on_select(aggregate.id)
            self.app._on_drill_down(aggregate.id)
            row = next(item for item, entry in self.app._sidebar_targets.items()
                       if entry == ("reveal", target))
            self.app.sidebar_tree.selection_set(row)
            self.app._activate_sidebar(None)
            self.assertIn(target, {node.id for node in
                                   self.app._views[ViewKind.ARCHITECTURE].nodes})
            target_name = next(node.display_name for node in
                               self.app._views[ViewKind.ARCHITECTURE].nodes
                               if node.id == target)
            self.assertIn(target_name,
                          self.app._breadcrumbs[ViewKind.ARCHITECTURE].cget("text"))
            with patch.object(self.app.messagebox, "showerror") as showerror:
                self.app._go_back()
            showerror.assert_not_called()
            self.assertEqual(self.app.main_tabs.select(),
                             str(self.app._graph_tabs[ViewKind.ARCHITECTURE][0]))
            self.assertIsNone(self.app._selected_id)
            self.assertNotIn(target, {node.id for node in
                                      self.app._views[ViewKind.ARCHITECTURE].nodes})

    def test_terminal_node_drill_does_not_implicitly_open_source(self):
        kind = ViewKind.EXECUTION
        node = VisibleNode("terminal", "Parameter Update",
                           source_anchors=(SourceAnchor("run.py", 3),))
        self.app._views[kind] = VisibleGraph(kind, (node,))
        self.app._active_kind = kind
        with patch.object(self.app, "_open_source") as open_source:
            self.app._on_drill_down(node.id)
        open_source.assert_not_called()
        self.assertIn("Open Source", self.app.status_label.cget("text"))

    def test_empty_architecture_has_visible_explanation(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data.py").write_text("value = 1\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._open_graph(ViewKind.ARCHITECTURE)
            graph = self.app._graph_tabs[ViewKind.ARCHITECTURE][1]
            items = graph.canvas.find_withtag("empty-state")
            self.assertTrue(items)
            self.assertIn("No model", graph.canvas.itemcget(items[0], "text"))

    def test_back_in_execution_does_not_collapse_architecture(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "model.py").write_text(
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.layer = nn.Linear(2, 2)\n"
                "    def forward(self, x): return self.layer(x)\n", encoding="utf-8")
            (root / "run.py").write_text(
                "def train(optimizer):\n    optimizer.step()\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._open_graph(ViewKind.ARCHITECTURE)
            model = next(node for node in self.app._views[ViewKind.ARCHITECTURE].nodes
                         if node.metadata.get("has_children"))
            self.app._on_drill_down(model.id)
            self.assertIn(model.id, self.app.controller.view_state.expanded_ids)
            self.assertIn(model.display_name,
                          self.app._breadcrumbs[ViewKind.ARCHITECTURE].cget("text"))
            self.app._open_graph(ViewKind.EXECUTION)
            self.app._go_back()
            self.assertIn(model.id, self.app.controller.view_state.expanded_ids)
            self.assertEqual(self.app.main_tabs.select(), str(self.app.overview_tab))

    def test_switching_model_root_clears_old_breadcrumb(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = "import torch.nn as nn\n" + "".join(
                f"class Model{index}(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.layer = nn.Linear(2, 2)\n"
                "    def forward(self, x): return self.layer(x)\n"
                for index in range(2))
            (root / "models.py").write_text(source, encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            models = build_overview(self.app.controller.analysis, root).models
            self.app._open_graph(ViewKind.ARCHITECTURE, models[0].id)
            first = next(node for node in self.app._views[ViewKind.ARCHITECTURE].nodes
                         if node.id == models[0].id)
            self.app._on_drill_down(first.id)
            self.assertIn(first.id, self.app.controller.view_state.expanded_ids)
            self.app._open_graph(ViewKind.ARCHITECTURE, models[1].id)
            self.assertNotIn(first.display_name,
                             self.app._breadcrumbs[ViewKind.ARCHITECTURE].cget("text"))
            self.assertNotIn("Expanded component",
                             self.app._breadcrumbs[ViewKind.ARCHITECTURE].cget("text"))
            self.assertNotIn(first.id, self.app.controller.view_state.expanded_ids)


if __name__ == "__main__":
    unittest.main()
