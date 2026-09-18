"""Desktop shell stays importable without constructing a display window."""

import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.core import SourceAnchor, ViewKind, VisibleGraph, VisibleNode
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
        try:
            self.app = CodeVizApp()
        except Exception as error:
            import tkinter

            if isinstance(error, tkinter.TclError):
                self.skipTest("Tk display unavailable")
            raise
        self.app.root.withdraw()

    def tearDown(self):
        self.app.root.destroy()

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

    def test_failed_reanalysis_restores_explorer_empty_state(self):
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
            self.assertEqual(self.app.sidebar_tree.item(
                self.app.sidebar_tree.get_children()[0], "text"), "Click Analyze Project")
            self.assertFalse(self.app._sidebar_targets)

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
            self.app._go_back()
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
