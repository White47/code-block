"""Review regressions for aggregate selection and file-level CFG omission."""

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.test_flow import analyze_files

from codeviz.core import ViewKind
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge
from codeviz.views import ViewRequest, build_visible_graph


class FunctionFlowReviewRegressions(unittest.TestCase):
    def test_invalid_scope_declaration_marks_real_controller_partial(self):
        with TemporaryDirectory() as source_dir:
            root = Path(source_dir)
            (root / "app.py").write_text(
                "def broken(x):\n    global x\n    return 1\n"
                "def intact():\n    return 2\n", encoding="utf-8")
            controller = AppController()
            controller.open_project(root)
            result = controller.analyze()
            self.assertEqual(controller.status, AnalysisStatus.PARTIAL)
            conflict = next(issue for issue in result.issues
                            if issue.code == "SCOPE_DECLARATION_CONFLICT")
            self.assertIsNotNone(conflict.source_anchor)
            broken = next(entry for entry in result.symbol_index.entries.values()
                          if entry.qualified_name == "app.broken")
            view = controller.view(ViewKind.FUNCTION_FLOW, broken.id)
            self.assertIn("SCOPE_DECLARATION_CONFLICT", {issue.code for issue in view.issues})
            intact = next(entry for entry in result.symbol_index.entries.values()
                          if entry.qualified_name == "app.intact")
            self.assertFalse(controller.view(ViewKind.FUNCTION_FLOW, intact.id).issues)

    def test_invalid_scope_declaration_is_visible_in_cached_function_flow(self):
        result = analyze_files({"app.py": "def broken(x):\n    global x\n    return 1\n"})
        cached = replace(result, files=tuple(replace(file, tree=None) for file in result.files))
        broken = next(entry for entry in result.symbol_index.entries.values()
                      if entry.qualified_name == "app.broken")
        view = build_visible_graph(cached, ViewRequest(ViewKind.FUNCTION_FLOW, broken.id))
        self.assertIn("SCOPE_DECLARATION_CONFLICT", {issue.code for issue in view.issues})

    def test_ordinary_unresolved_name_does_not_mark_controller_partial(self):
        with TemporaryDirectory() as source_dir:
            root = Path(source_dir)
            (root / "app.py").write_text("def run():\n    return missing\n", encoding="utf-8")
            controller = AppController()
            controller.open_project(root)
            controller.analyze()
            self.assertEqual(controller.status, AnalysisStatus.READY)

    def test_aggregate_inspector_refresh_cached_restore_and_member_navigation(self):
        with TemporaryDirectory() as source_dir, TemporaryDirectory() as cache_dir:
            root = Path(source_dir)
            (root / "app.py").write_text(
                "def main():\n" + "".join(f"    v{i} = {i}\n" for i in range(130)),
                encoding="utf-8",
            )
            store = CacheStore(Path(cache_dir))
            controller = AppController(store)
            bridge = DesktopBridge(controller, store, PreferencesStore(Path(cache_dir)))
            self.assertTrue(bridge.open_project(str(root))["ok"])
            self.assertTrue(bridge.analyze()["ok"])
            root_id = bridge.get_overview()["data"]["functions"][0]["id"]
            initial = bridge.get_view("FunctionFlow", root_id)["data"]
            aggregate = next(node for node in initial["nodes"] if node["memberIds"])
            aggregate_id = aggregate["id"]
            tiny = build_visible_graph(controller.analysis, ViewRequest(
                ViewKind.FUNCTION_FLOW, root_id, selected_id=aggregate_id, max_nodes=1))
            self.assertEqual([node.id for node in tiny.nodes], [aggregate_id])
            self.assertTrue(tiny.nodes[0].member_ids)
            refreshed = bridge.get_view("FunctionFlow", root_id, aggregate_id)
            self.assertTrue(refreshed["ok"], refreshed)
            self.assertEqual(len(refreshed["data"]["nodes"]), 100)
            inspector = bridge.get_inspector("FunctionFlow", root_id, aggregate_id)
            self.assertTrue(inspector["ok"], inspector)
            self.assertEqual(inspector["data"]["memberIds"], aggregate["memberIds"])
            self.assertFalse(inspector["data"]["canOpenSource"])
            detail = bridge.get_detail_view("FunctionFlow", root_id, aggregate_id)
            self.assertTrue(detail["ok"], detail)
            member = aggregate["memberIds"][-1]
            focused = bridge.get_view("FunctionFlow", root_id, member)
            self.assertTrue(focused["ok"], focused)
            self.assertIn(member, {node["id"] for node in focused["data"]["nodes"]})
            member_inspector = bridge.get_inspector("FunctionFlow", root_id, member)
            self.assertTrue(member_inspector["ok"], member_inspector)
            self.assertTrue(bridge.read_source(member_inspector["data"]["sourceAnchors"][0])["ok"])
            self.assertTrue(bridge.save_layout({
                "activeView": "FunctionFlow", "selectedId": aggregate_id,
                "openTabs": ["Overview", "FunctionFlow"],
                "graphRoots": {"FunctionFlow": root_id},
            })["ok"])
            restored_controller = AppController(store)
            restored = DesktopBridge(restored_controller, store, PreferencesStore(Path(cache_dir)))
            self.assertTrue(restored.open_project(str(root))["ok"])
            self.assertTrue(all(file.tree is None for file in restored_controller.analysis.files))
            self.assertEqual(restored.get_view_state()["data"]["selectedId"], aggregate_id)
            self.assertTrue(restored.get_view("FunctionFlow", root_id, aggregate_id)["ok"])
            self.assertFalse(restored.get_view("FunctionFlow", root_id, "aggregate:FunctionFlow:bad")["ok"])
            # A once-valid aggregate becomes stale if the scope no longer folds.
            with self.assertRaises(ValueError):
                build_visible_graph(controller.analysis, ViewRequest(
                    ViewKind.FUNCTION_FLOW, root_id, selected_id=aggregate_id, max_nodes=200))

    def test_cfg_budget_diagnostic_covers_later_omitted_scope_not_other_file(self):
        with patch("codeviz.analysis.control_flow._MAX_CFG_NODES", 2):
            result = analyze_files({
                "app.py": "def first():\n    a=1\n    b=2\n    c=3\ndef later():\n    work()\n",
                "other.py": "def intact():\n    return 1\n",
            })
        later = next(entry for entry in result.symbol_index.entries.values()
                     if entry.qualified_name == "app.later")
        view = build_visible_graph(result, ViewRequest(ViewKind.FUNCTION_FLOW, later.id))
        self.assertTrue(any(issue.code == "CFG_PARTIAL" for issue in view.issues), view.issues)
        issue = next(issue for issue in result.flow.issues
                     if issue.code == "CFG_PARTIAL" and "budget" in issue.message)
        self.assertIn("file-level", issue.message)
        self.assertIn("scopes", issue.message)
        self.assertEqual((issue.source_anchor.start_line, issue.source_anchor.end_line), (1, 6))
        intact = next(entry for entry in result.symbol_index.entries.values()
                      if entry.qualified_name == "other.intact")
        other = build_visible_graph(result, ViewRequest(ViewKind.FUNCTION_FLOW, intact.id))
        self.assertFalse(other.issues)
        cached = replace(result, files=tuple(replace(file, tree=None) for file in result.files))
        cached_view = build_visible_graph(cached, ViewRequest(ViewKind.FUNCTION_FLOW, later.id))
        self.assertEqual(cached_view.issues, view.issues)


if __name__ == "__main__":
    unittest.main()
