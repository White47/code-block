"""Desktop shell stays importable without constructing a display window."""

import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.core import SourceAnchor, ViewKind
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


class TkAppLiveTests(unittest.TestCase):
    def _buttons(self, parent):
        for child in parent.winfo_children():
            if isinstance(child, self.app.tk.Button):
                yield child
            yield from self._buttons(child)

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


if __name__ == "__main__":
    unittest.main()
