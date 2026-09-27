"""Desktop shell stays importable without constructing a display window."""

import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from codeviz.core import SourceAnchor, ViewKind, VisibleGraph, VisibleNode
from codeviz.navigation import plan_reveal
from codeviz.persistence.store import CacheStore
from codeviz.search import search_project
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.presenter import build_overview, build_problems
from codeviz.ui.tkapp import CodeVizApp, main


class TkAppImportTests(unittest.TestCase):
    def test_detail_canvas_labels_have_chinese_translations(self):
        from codeviz.ui.i18n import translate

        phrases = {
            "Fit": "适应窗口",
            "Detail": "详情",
            "Close a detail view before opening another":
                "请先关闭一个详情视图，再打开另一个",
            "No local details available": "没有可用的局部详情",
            "Zoom {percent}%": "缩放 {percent}%",
            "Single-click inspects · double-click or + opens details · "
            "source opens separately": "单击查看 · 双击或点击 + 打开详情 · 源码单独打开",
        }
        for english, chinese in phrases.items():
            with self.subTest(english=english):
                self.assertEqual(translate("en", english), english)
                self.assertEqual(translate("zh-CN", english), chinese)

    def test_initial_geometry_keeps_titlebar_inside_common_screens(self):
        import codeviz.ui.tkapp as desktop

        self.assertTrue(hasattr(desktop, "initial_geometry"))
        for screen_width, screen_height in ((1366, 768), (1920, 1080), (1024, 768)):
            with self.subTest(screen=(screen_width, screen_height)):
                width, height, left, top = desktop.initial_geometry(
                    screen_width, screen_height)
                self.assertLessEqual(width, screen_width - 40)
                self.assertLessEqual(height, screen_height - 80)
                self.assertGreaterEqual(left, 0)
                self.assertGreaterEqual(top, 0)
                self.assertLessEqual(left + width, screen_width)
                self.assertLessEqual(top + height, screen_height - 40)

    def test_corrupt_preferences_show_warning_without_blocking_window(self):
        with TemporaryDirectory() as temporary:
            (Path(temporary) / "preferences.json").write_text("{bad json", encoding="utf-8")
            try:
                with patch("codeviz.ui.tkapp.CacheStore",
                           return_value=CacheStore(Path(temporary))):
                    app = CodeVizApp()
            except Exception as error:
                import tkinter

                if isinstance(error, tkinter.TclError):
                    self.skipTest("Tk display unavailable")
                raise
            try:
                self.assertEqual(app.language, "zh-CN")
                self.assertIn("语言设置", app.status_label.cget("text"))
            finally:
                app.close()

    def test_fresh_app_defaults_to_chinese_chrome(self):
        with TemporaryDirectory() as temporary:
            try:
                with patch("codeviz.ui.tkapp.CacheStore",
                           return_value=CacheStore(Path(temporary))):
                    app = CodeVizApp()
            except Exception as error:
                import tkinter

                if isinstance(error, tkinter.TclError):
                    self.skipTest("Tk display unavailable")
                raise
            try:
                self.assertEqual(app.language, "zh-CN")
                self.assertEqual(app.main_tabs.tab(str(app.overview_tab), "text"), "项目概览")
                self.assertEqual(app.root.title(), "代码地图 — Python 项目理解")
            finally:
                app.close()

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


class TkAppHeadlessTests(unittest.TestCase):
    fixture_root = Path(__file__).parent / "fixtures" / "mvp_project"

    def test_render_validation_prunes_three_stale_islands_before_valid_open(self):
        with TemporaryDirectory() as data_dir:
            app = CodeVizApp.__new__(CodeVizApp)
            app.controller = AppController(CacheStore(Path(data_dir)))
            app.controller.open_project(self.fixture_root)
            app.controller.analyze()
            kind = ViewKind.ARCHITECTURE
            app._roots = {kind: None}
            app.language = "en"
            messages = []
            app.status_label = SimpleNamespace(
                configure=lambda **options: messages.append(options["text"]))
            for index in range(3):
                app.controller.open_detail_island(kind, f"stale-{index}", None)

            pairs = app._detail_pairs(kind)

            self.assertEqual(pairs, ())
            self.assertEqual(app.controller.detail_islands(kind), ())
            self.assertEqual(len(messages), 3)
            valid = app.controller.view(kind).nodes[0]
            opened = app.controller.open_detail_island(kind, valid.id, None)
            self.assertEqual(opened.source_node_id, valid.id)

    def test_reopened_missing_source_island_is_pruned_without_losing_analysis(self):
        with TemporaryDirectory() as data_dir:
            store = CacheStore(Path(data_dir))
            first = AppController(store)
            first.open_project(self.fixture_root)
            expected = first.analyze()
            expected_symbol_ids = tuple(expected.symbol_index.entries)
            expected_code_ids = tuple(expected.flow.graph.nodes)
            assert expected.semantic is not None
            expected_semantic_ids = tuple(
                item.id for item in expected.semantic.graph.components)
            first.open_detail_island(
                ViewKind.ARCHITECTURE, "missing-source-node", (600.0, 160.0))
            store.save_annotations(self.fixture_root, {"note": "separate boundary"})

            reopened = AppController(store)
            reopened.open_project(self.fixture_root)
            app = CodeVizApp.__new__(CodeVizApp)
            app.controller = reopened
            app._roots = {ViewKind.ARCHITECTURE: None}
            app.language = "en"
            app.status_label = SimpleNamespace(configure=lambda **_options: None)

            self.assertEqual(app._detail_pairs(ViewKind.ARCHITECTURE), ())
            self.assertEqual(reopened.detail_islands(ViewKind.ARCHITECTURE), ())
            restored = reopened.analysis
            assert restored is not None
            self.assertEqual(
                tuple(restored.symbol_index.entries),
                expected_symbol_ids)
            self.assertEqual(tuple(restored.flow.graph.nodes), expected_code_ids)
            assert restored.semantic is not None
            self.assertEqual(
                tuple(item.id for item in restored.semantic.graph.components),
                expected_semantic_ids)
            self.assertEqual(store.load_annotations(self.fixture_root),
                             {"note": "separate boundary"})

    def test_layout_persists_main_selection_not_transient_detail_selection(self):
        saved = []
        app = CodeVizApp.__new__(CodeVizApp)
        app._restoring_layout = False
        app.controller = SimpleNamespace(
            project_root=self.fixture_root,
            save_layout=lambda *values: saved.append(values),
        )
        app.main_tabs = SimpleNamespace(tabs=lambda: ())
        app.side_tabs = SimpleNamespace(tabs=lambda: ())
        app.overview_tab = object()
        app._graph_tabs = {}
        app._problems_tab = None
        app._source_tabs = {}
        app._active_kind = ViewKind.EXECUTION
        app._selected_id = "detail-parameter-update"
        app._selected_context = "Execution:epoch-loop"
        app._selections = {ViewKind.EXECUTION: "main-epoch-loop"}
        app._roots = {ViewKind.EXECUTION: "train-entry"}

        app._sync_layout()

        self.assertEqual(saved[0][0], ViewKind.EXECUTION)
        self.assertEqual(saved[0][1], "main-epoch-loop")

    def test_restored_visible_execution_selection_keeps_main_graph_shape(self):
        with TemporaryDirectory() as data_dir:
            store = CacheStore(Path(data_dir))
            controller = AppController(store, ignore_paths=("data",))
            controller.open_project(self.fixture_root)
            analysis = controller.analyze()
            assert analysis.semantic is not None
            entry = next(component for component in analysis.semantic.graph.components
                         if component.display_name == "train_epoch")
            main = controller.view(ViewKind.EXECUTION, entry.id)
            epoch = next(node for node in main.nodes
                         if node.display_name == "EpochLoop")
            controller.save_layout(
                ViewKind.EXECUTION, epoch.id, ("graph:Execution",),
                {ViewKind.EXECUTION.value: entry.id})

            restored_controller = AppController(store, ignore_paths=("data",))
            restored_controller.open_project(self.fixture_root)
            app = CodeVizApp.__new__(CodeVizApp)
            app.controller = restored_controller
            app._context_hops = {}
            app._context_direction = {}
            restored = app._query_main_view(
                ViewKind.EXECUTION, entry.id,
                restored_controller.view_state.selected_id)

            self.assertEqual(restored.root_ids, main.root_ids)
            self.assertEqual(tuple(node.id for node in restored.nodes),
                             tuple(node.id for node in main.nodes))

    def test_island_header_source_falls_back_to_original_main_instance(self):
        with TemporaryDirectory() as data_dir:
            app = CodeVizApp.__new__(CodeVizApp)
            app.controller = AppController(CacheStore(Path(data_dir)))
            app.controller.open_project(self.fixture_root)
            app.controller.analyze()
            kind = ViewKind.ARCHITECTURE
            architecture = app.controller.view(kind)
            classifier = next(node for node in architecture.nodes
                              if node.display_name == "Classifier")
            main = app.controller.view(kind, classifier.id)
            encoder = next(node for node in main.nodes
                           if node.display_name == "encoder")
            detail = app.controller.detail_view(kind, classifier.id, encoder.id)
            stale = next(node for node in detail.nodes
                         if node.id != detail.root_ids[0])
            island_id = f"{kind.value}:{encoder.id}"
            graph = SimpleNamespace(detail_view=lambda _island_id: detail)
            app._active_kind = kind
            app._views = {kind: main}
            app._graph_tabs = {kind: (None, graph)}
            app._selected_id = stale.id
            app._selected_context = island_id
            app._inspection_view = detail
            app._selections = {kind: encoder.id}
            app._update_inspector = lambda: None
            app._sync_layout = lambda: None
            opened = []
            app._open_source = lambda anchor, to_side: opened.append((anchor, to_side))

            app._open_canvas_source(encoder.id, island_id)

            self.assertEqual(opened, [(encoder.source_anchors[0], False)])
            self.assertEqual(app._selected_id, encoder.id)
            self.assertIsNone(app._selected_context)
            self.assertIs(app._inspection_view, main)


class TkAppLiveTests(unittest.TestCase):
    def test_inspector_relations_use_names_and_bound_long_lists(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = ("import torch.nn as nn\n"
                      "class Net(nn.Module):\n"
                      "    def __init__(self):\n" + "".join(
                          f"        self.layer{index} = nn.Linear(2, 2)\n"
                          for index in range(20)))
            (root / "model.py").write_text(source, encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._open_graph(ViewKind.ARCHITECTURE)
            model = next(node for node in self.app._views[ViewKind.ARCHITECTURE].nodes
                         if node.role == "ModelDefinition")
            self.app._on_select(model.id)
            preview = self.app.inspector_text.get("1.0", "end")
            self.assertIn("layer0", preview)
            self.assertIn("more relations", preview)
            self.assertNotIn("node_", preview)

    def test_long_source_anchor_has_bounded_inspector_preview_but_full_source(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = ("import torch.nn as nn\n"
                      "class Net(nn.Module):\n"
                      "    def __init__(self):\n"
                      "        super().__init__()\n" + "".join(
                          f"        self.layer{index} = nn.Linear(2, 2)\n"
                          for index in range(30)))
            (root / "model.py").write_text(source, encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._open_graph(ViewKind.ARCHITECTURE)
            model = next(node for node in self.app._views[ViewKind.ARCHITECTURE].nodes
                         if node.role == "ModelDefinition")
            self.app._on_select(model.id)
            preview = self.app.inspector_text.get("1.0", "end")
            self.assertIn("more source lines", preview)
            self.assertNotIn("self.layer29", preview)
            self.app.source_button.invoke()
            self.assertIn("self.layer29", self.app._source_widgets[("model.py", False)].get(
                "1.0", "end"))

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
        (Path(self.cache_dir.name) / "preferences.json").write_text(
            '{"version":1,"language":"en"}', encoding="utf-8")
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

    def test_reset_project_layout_button_clears_saved_positions_only(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text("def train(): return 1\n", encoding="utf-8")
            self.app.controller.open_project(root)
            analysis = self.app.controller.analyze()
            self.app._open_graph(ViewKind.ARCHITECTURE)
            self.app.controller.move_node("custom-node", (400.0, 120.0))
            button = next(button for button in self._buttons(self.app.root)
                          if button.cget("text") == "Reset Project Layout")
            button.invoke()
            self.assertEqual(self.app.controller.view_state.positions, {})
            self.assertIs(self.app.controller.analysis, analysis)

    def test_tab_close_removes_only_requested_graph_source_or_problem(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text("def train(): return 1\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._open_graph(ViewKind.ARCHITECTURE)
            graph_frame = self.app._graph_tabs[ViewKind.ARCHITECTURE][0]
            self.assertNotEqual(self.app.main_tabs.tab(str(graph_frame), "image"), "")
            self.app._open_source(SourceAnchor("run.py", 1), False)
            source_frame = self.app._source_tabs[("run.py", False)]
            self.app._open_problems()
            problems_frame = self.app._problems_tab
            self.assertIsNotNone(problems_frame)
            self.assertFalse(self.app._close_tab(self.app.main_tabs,
                                                 str(self.app.overview_tab)))
            self.assertTrue(self.app._close_tab(self.app.main_tabs, str(source_frame)))
            self.assertFalse(source_frame.winfo_exists())
            self.assertIn(ViewKind.ARCHITECTURE, self.app._graph_tabs)
            self.assertNotIn("source:0:run.py", self.app.controller.view_state.open_tabs)
            self.assertTrue(self.app._close_tab(self.app.main_tabs, str(graph_frame)))
            self.assertNotIn(ViewKind.ARCHITECTURE, self.app._graph_tabs)
            self.assertTrue(self.app._close_tab(self.app.main_tabs, str(problems_frame)))
            self.assertEqual(self.app.main_tabs.tabs(), (str(self.app.overview_tab),))
            self.app._open_source(SourceAnchor("run.py", 1), False)
            self.assertIn(("run.py", False), self.app._source_tabs)

    def test_closing_last_side_source_collapses_split_editor(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text("value = 1\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app._open_source(SourceAnchor("run.py", 1), True)
            frame = self.app._source_tabs[("run.py", True)]
            self.assertTrue(self.app._side_open)
            self.assertTrue(self.app._close_tab(self.app.side_tabs, str(frame)))
            self.assertFalse(self.app._side_open)
            self.assertNotIn(("run.py", True), self.app._source_tabs)
            self.app._open_source(SourceAnchor("run.py", 1), True)
            self.assertTrue(self.app._side_open)
            self.app._open_source(SourceAnchor("run.py", 1), False)
            main_frame = self.app._source_tabs[("run.py", False)]
            self.app._open_source(SourceAnchor("run.py", 1), True)
            self.assertEqual(
                self.app._close_active_tab(SimpleNamespace(widget=self.app.main_tabs)), "break")
            self.assertFalse(self.app._side_open)
            self.assertIn(str(main_frame), self.app.main_tabs.tabs())

    def test_settings_switches_language_without_reanalyzing_or_editing_source(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "run.py"
            source.write_text("def train(): return 1\n", encoding="utf-8")
            self.app.controller.open_project(root)
            analysis = self.app.controller.analyze()
            self.app._open_graph(ViewKind.ARCHITECTURE)
            self.app._open_source(SourceAnchor("run.py", 1), False)
            self.app._open_problems()
            self.assertEqual(self.app.problems_tree.heading("code", "text"), "CODE")
            before = self.app._source_widgets[("run.py", False)].get("1.0", "end")
            self.app._set_activity("Settings")
            self.assertEqual(tuple(self.app.language_selector["values"]),
                             ("简体中文", "English"))
            self.app._set_language("zh-CN")
            self.assertEqual(self.app.main_tabs.tab(str(self.app.overview_tab), "text"),
                             "项目概览")
            self.assertEqual(self.app.main_tabs.tab(
                str(self.app._graph_tabs[ViewKind.ARCHITECTURE][0]), "text"), "架构图")
            self.assertTrue(self.app._breadcrumbs[ViewKind.ARCHITECTURE].cget(
                "text").startswith("架构图"))
            self.assertEqual(self.app.problems_tree.heading("code", "text"), "代码")
            self.assertIn("选择图中的节点", self.app.inspector_text.get("1.0", "end"))
            self.assertEqual(self.app.source_button.cget("text"), "打开源码")
            self.assertEqual(self.app._source_widgets[("run.py", False)].get("1.0", "end"),
                             before)
            self.assertIs(self.app.controller.analysis, analysis)
            self.app._open_graph(ViewKind.ARCHITECTURE)
            self.assertIn("节点", self.app.status_label.cget("text"))
            self.assertNotIn("visible nodes", self.app.status_label.cget("text"))
            graph_frame = self.app._graph_tabs[ViewKind.ARCHITECTURE][0]
            self.app._close_tab(self.app.main_tabs, str(graph_frame))
            self.app._open_graph(ViewKind.ARCHITECTURE)
            self.assertEqual(self.app.main_tabs.tab(
                str(self.app._graph_tabs[ViewKind.ARCHITECTURE][0]), "text"), "架构图")
            self.app._set_language("en")
            self.assertEqual(self.app.main_tabs.tab(str(self.app.overview_tab), "text"),
                             "Project Overview")
            self.assertIn("visible nodes", self.app.status_label.cget("text"))
            self.assertEqual(source.read_text(encoding="utf-8"), "def train(): return 1\n")

    def test_failed_language_save_keeps_session_choice_and_warns(self):
        with patch.object(self.app.preferences, "save", side_effect=OSError("denied")):
            self.app._set_language("zh-CN")
        self.assertEqual(self.app.language, "zh-CN")
        self.assertIn("未能保存", self.app.status_label.cget("text"))

    def test_chinese_inspector_overview_and_search_keep_code_names(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "Architecture"
            root.mkdir()
            (root / "model.py").write_text(
                "import torch.nn as nn\nclass Architecture(nn.Module):\n"
                "    def forward(self, x): return x\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._set_language("zh-CN")
            self.app._open_graph(ViewKind.ARCHITECTURE)
            self.assertTrue(self.app._breadcrumbs[ViewKind.ARCHITECTURE].cget(
                "text").startswith("架构图"))
            model = next(node for node in self.app._views[ViewKind.ARCHITECTURE].nodes
                         if node.role == "ModelDefinition")
            self.app._on_select(model.id)
            inspector = self.app.inspector_text.get("1.0", "end")
            self.assertIn("真实代码名", inspector)
            self.assertIn("Architecture", inspector)
            self.assertIn("概览", inspector)
            self.assertNotIn("OVERVIEW", inspector)
            self.assertIn("model.Architecture", [button.cget("text")
                                                  for button in self._buttons(
                                                      self.app.overview_tab)])
            self.assertIn("Architecture", [label.cget("text")
                                            for label in self._labels(self.app.overview_tab)])
            labels = [label.cget("text") for label in self._labels(self.app.overview_tab)]
            self.assertTrue(any("文件" in label for label in labels))
            self.app._set_activity("Explorer")
            self.assertIn("模型", [self.app.sidebar_tree.item(item, "text")
                                   for item in self.app.sidebar_tree.get_children()])
            self.app._open_problems()
            self.assertEqual(self.app.problems_tree.heading("code", "text"), "代码")
            self.assertEqual(self.app.problems_tree.heading("location", "text"), "位置")
            self.assertEqual(self.app.problems_tree.heading("message", "text"), "消息")

    def test_chinese_search_prompt_and_source_error_title(self):
        self.app._set_language("zh-CN")
        self.app._set_activity("Search")
        self.app._reveal_search_result()
        self.assertIn("请先选择搜索结果", self.app.status_label.cget("text"))
        with patch.object(self.app.messagebox, "showerror") as showerror:
            self.app._open_source(SourceAnchor("missing.py", 1), False)
        self.assertEqual(showerror.call_args.args[0], "无法打开源码")


    def test_close_marker_click_and_shortcut_leave_overview_open(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text("def train(): return 1\n", encoding="utf-8")
            self.app.controller.open_project(root)
            self.app.controller.analyze()
            self.app._open_graph(ViewKind.ARCHITECTURE)
            graph_frame = self.app._graph_tabs[ViewKind.ARCHITECTURE][0]
            self.app.root.deiconify()
            self.app.root.update()
            self.assertEqual(self.app.main_tabs.tab(str(self.app.overview_tab), "image"), "")
            self.assertNotEqual(self.app.main_tabs.tab(str(graph_frame), "image"), "")
            graph_index = self.app.main_tabs.index(str(graph_frame))
            y = 14
            tab_x = [x for x in range(self.app.main_tabs.winfo_width())
                     if str(self.app.main_tabs.tk.call(
                         self.app.main_tabs._w, "identify", "tab", x, y)) == str(graph_index)]
            self.assertGreater(len(tab_x), 30)
            ordinary_click = SimpleNamespace(widget=self.app.main_tabs,
                                             x=tab_x[0] + 12, y=y)
            self.assertIsNone(self.app._on_tab_click(ordinary_click))
            event = SimpleNamespace(widget=self.app.main_tabs, x=tab_x[-1] - 8, y=y)
            self.assertEqual(self.app._on_tab_click(event), "break")
            self.assertNotIn(ViewKind.ARCHITECTURE, self.app._graph_tabs)
            self.assertEqual(self.app._close_active_tab(SimpleNamespace(widget=self.app.main_tabs)),
                             "break")
            self.assertEqual(self.app.main_tabs.tabs(), (str(self.app.overview_tab),))

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
        architecture_island, = self.app.controller.detail_islands(
            ViewKind.ARCHITECTURE)
        architecture_detail = self.app._graph_tabs[ViewKind.ARCHITECTURE][1].detail_view(
            architecture_island.id)
        self.assertEqual(self.app._roots[ViewKind.ARCHITECTURE], classifier_id)
        self.assertTrue(any(node.display_name.startswith("layers") for node in
                            architecture_detail.nodes))
        self.app._close_detail_island(architecture_island.id)
        self.app._go_back()
        self.assertEqual(self.app.main_tabs.select(), str(self.app.overview_tab))
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
        execution_island, = self.app.controller.detail_islands(ViewKind.EXECUTION)
        execution_detail = self.app._graph_tabs[ViewKind.EXECUTION][1].detail_view(
            execution_island.id)
        self.assertTrue(any(node.display_name == "BatchLoop"
                            for node in execution_detail.nodes))
        update = next(node for node in execution_detail.nodes
                      if node.display_name == "ParameterUpdate")
        self.app._on_select(update.id, execution_island.id)
        self.app.source_button.invoke()
        source = self.app.controller.read_source(update.source_anchors[0])
        self.assertEqual(source.text.splitlines()[source.start_line - 1].strip(),
                         "optimizer.step()")
        backward = next(node for node in execution_detail.nodes
                        if node.display_name == "BackwardPass")
        self.app._on_select(backward.id, execution_island.id)
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

    def test_mvp_detail_island_inspects_source_and_closes_to_main_selection(self):
        project = Path(__file__).parent / "fixtures" / "mvp_project"
        self.app._open_project_path(project)
        self.app.controller.analyze()
        self.app._open_graph(ViewKind.ARCHITECTURE)

        kind = ViewKind.ARCHITECTURE
        main_view = self.app._views[kind]
        root = self.app._roots[kind]
        expandable = next(node for node in main_view.nodes
                          if node.metadata.get("has_children"))
        self.app._on_select(expandable.id, None)
        self.assertEqual(self.app._selected_id, expandable.id)
        self.assertEqual(self.app.controller.detail_islands(kind), ())

        self.app._open_detail_island(expandable.id)
        island, = self.app.controller.detail_islands(kind)
        graph = self.app._graph_tabs[kind][1]
        detail = graph.detail_view(island.id)
        detail_node = next(node for node in detail.nodes
                           if node.id != expandable.id and node.source_anchors)
        self.assertIs(self.app._views[kind], main_view)
        self.assertEqual(self.app._roots[kind], root)
        self.assertEqual(self.app._expansion_history.get(kind, []), [])

        self.app._on_select(detail_node.id, island.id)
        self.assertIn(detail_node.display_name,
                      self.app.inspector_text.get("1.0", "end"))
        self.app._open_selected_source(False)
        self.assertIn((detail_node.source_anchors[0].file_path, False),
                      self.app._source_tabs)

        self.app._close_detail_island(island.id)
        self.assertEqual(self.app.controller.detail_islands(kind), ())
        self.assertEqual(self.app._selected_id, expandable.id)
        self.assertIsNone(self.app._selected_context)
        self.assertIs(self.app._inspection_view, main_view)

    def test_detail_island_limit_reports_localized_action(self):
        project = Path(__file__).parent / "fixtures" / "mvp_project"
        self.app._open_project_path(project)
        self.app.controller.analyze()
        self.app._open_graph(ViewKind.ARCHITECTURE)
        expandable = [node for node in self.app._views[ViewKind.ARCHITECTURE].nodes
                      if node.metadata.get("has_children")]

        for node in expandable[:3]:
            self.app._open_detail_island(node.id)
        self.app._open_detail_island(expandable[3].id)

        self.assertEqual(len(self.app.controller.detail_islands(
            ViewKind.ARCHITECTURE)), 3)
        self.assertEqual(self.app.status_label.cget("text"),
                         "Close a detail view before opening another")

    def test_reopen_restores_islands_and_failed_detail_keeps_outdated_main_graph(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "model.py"
            source.write_text(
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.layer = nn.Linear(2, 2)\n"
                "    def forward(self, x): return self.layer(x)\n", encoding="utf-8")
            self.app._open_project_path(root)
            self.app.controller.analyze()
            self.app._open_graph(ViewKind.ARCHITECTURE)
            kind = ViewKind.ARCHITECTURE
            model = next(node for node in self.app._views[kind].nodes
                         if node.metadata.get("has_children"))
            self.app._open_detail_island(model.id)
            restored_id = self.app.controller.detail_islands(kind)[0].id
            self.app.root.update()
            self.app._sync_layout()

            source.write_text(source.read_text(encoding="utf-8") + "\n# changed\n",
                              encoding="utf-8")
            self.app._open_project_path(root)

            graph = self.app._graph_tabs[kind][1]
            self.assertEqual(self.app.controller.status, AnalysisStatus.OUTDATED)
            self.assertEqual(self.app.controller.detail_islands(kind)[0].id, restored_id)
            self.assertEqual(graph.detail_view(restored_id).kind, kind)
            self.assertIn("OUTDATED", self.app._graph_warnings[kind].cget("text"))

            second = next(node for node in self.app._views[kind].nodes
                          if node.id != model.id)
            self.app._open_detail_island(second.id)
            second_id = next(island.id for island in
                             self.app.controller.detail_islands(kind)
                             if island.source_node_id == second.id)
            main_view = self.app._views[kind]
            original_detail_view = self.app.controller.detail_view

            def detail_view(view_kind, root_id, source_node_id):
                if source_node_id == model.id:
                    raise ValueError("detail query failed")
                return original_detail_view(view_kind, root_id, source_node_id)

            with patch.object(self.app.controller, "detail_view",
                              side_effect=detail_view):
                self.app._render_active_graph(kind)

            self.assertIs(self.app._views[kind], main_view)
            self.assertIs(graph.main_view, main_view)
            self.assertEqual(graph.detail_view(second_id).kind, kind)
            with self.assertRaises(KeyError):
                graph.detail_view(restored_id)
            self.assertIn("No local details available",
                          self.app.status_label.cget("text"))
            self.assertIn("OUTDATED", self.app._graph_warnings[kind].cget("text"))

    def test_aggregate_drill_down_exposes_hidden_member_choices(self):
        kind = ViewKind.ARCHITECTURE
        aggregate = VisibleNode("aggregate", "2 more", member_ids=("first", "second"))
        self.app._views[kind] = VisibleGraph(kind, (aggregate,))
        self.app._active_kind = kind
        self.app._open_detail_island(aggregate.id)
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
            self.app._open_detail_island(aggregate.id)
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
        with (patch.object(self.app, "_open_source") as open_source,
              patch.object(self.app, "_open_detail_island") as open_detail):
            self.app._on_drill_down(node.id)
        open_source.assert_not_called()
        open_detail.assert_called_once_with(node.id)

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

    def test_back_in_execution_does_not_close_architecture_detail(self):
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
            island, = self.app.controller.detail_islands(ViewKind.ARCHITECTURE)
            self.assertNotIn(model.id, self.app.controller.view_state.expanded_ids)
            self.app._open_graph(ViewKind.EXECUTION)
            self.app._go_back()
            self.assertEqual(self.app.controller.detail_islands(ViewKind.ARCHITECTURE),
                             (island,))
            self.assertEqual(self.app.main_tabs.select(), str(self.app.overview_tab))

    def test_switching_model_root_does_not_mutate_expansion_history(self):
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
            self.assertNotIn(first.id, self.app.controller.view_state.expanded_ids)
            self.app._open_graph(ViewKind.ARCHITECTURE, models[1].id)
            self.assertNotIn(first.display_name,
                             self.app._breadcrumbs[ViewKind.ARCHITECTURE].cget("text"))
            self.assertNotIn("Expanded component",
                             self.app._breadcrumbs[ViewKind.ARCHITECTURE].cget("text"))
            self.assertNotIn(first.id, self.app.controller.view_state.expanded_ids)


if __name__ == "__main__":
    unittest.main()
