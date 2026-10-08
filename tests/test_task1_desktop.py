"""Generic Python desktop bridge, cache, source and restoration integration."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge


class GenericDesktopTests(unittest.TestCase):
    def test_generic_flow_source_detail_and_cached_workspace_round_trip(self):
        with TemporaryDirectory() as source_dir, TemporaryDirectory() as cache_dir:
            root = Path(source_dir)
            (root / "app.py").write_text(
                'def main():\n    value = str(1)\n    return value\n'
                'if __name__ == "__main__": main()\n', encoding="utf-8")
            store = CacheStore(Path(cache_dir))
            controller = AppController(store)
            bridge = DesktopBridge(controller, store, PreferencesStore(Path(cache_dir)))
            self.assertTrue(bridge.open_project(str(root))["ok"])
            self.assertTrue(bridge.analyze()["ok"])
            overview = bridge.get_overview()["data"]
            choice = overview["entries"][0]
            self.assertEqual(choice["viewKind"], "FunctionFlow")
            root_id = choice["id"]
            view = bridge.get_view("FunctionFlow", root_id)["data"]
            call = next(node for node in view["nodes"] if node["role"] == "CallSite")
            selected_id = call["id"]
            inspector = bridge.get_inspector("FunctionFlow", root_id, selected_id)
            self.assertTrue(inspector["ok"])
            self.assertTrue(inspector["data"]["evidence"])
            source = bridge.read_source(inspector["data"]["sourceAnchors"][0])
            self.assertTrue(source["ok"])
            self.assertIn("str(1)", source["data"]["text"])
            detail = bridge.get_detail_view("FunctionFlow", root_id, selected_id)
            self.assertTrue(detail["ok"])
            self.assertTrue(bridge.open_detail_island("FunctionFlow", selected_id, None)["ok"])
            self.assertTrue(bridge.move_node(root_id, {"x": 15., "y": 30.}, "FunctionFlow")["ok"])
            self.assertTrue(bridge.save_layout({
                "activeView": "FunctionFlow", "selectedId": selected_id,
                "openTabs": ["Overview", "FunctionFlow"],
                "graphRoots": {"FunctionFlow": root_id},
            })["ok"])
            reopened_controller = AppController(store)
            reopened = DesktopBridge(reopened_controller, store,
                                     PreferencesStore(Path(cache_dir)))
            self.assertTrue(reopened.open_project(str(root))["ok"])
            self.assertTrue(all(file.tree is None for file in reopened_controller.analysis.files))
            self.assertEqual(reopened.get_overview()["data"], overview)
            restored = reopened.get_view_state()["data"]
            self.assertEqual(restored["selectedId"], selected_id)
            self.assertEqual(restored["positionsByView"]["FunctionFlow"][root_id], [15., 30.])
            self.assertTrue(restored["detailIslands"])
            self.assertTrue(reopened.get_view("FunctionFlow", root_id, selected_id)["ok"])
            self.assertFalse(reopened.get_view("FunctionFlow", "missing")["ok"])
            self.assertFalse(reopened.get_view("FunctionFlow", root_id, "missing")["ok"])
