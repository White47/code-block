"""Project Structure detail delivery keeps honest symbol/source navigation."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codeviz.assistant.tool_contracts import EntityRef
from codeviz.core import NodeKind, ViewKind
from codeviz.persistence.store import CacheStore
from codeviz.ui.assistant_context import ViewNodeKey
from codeviz.ui.controller import AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge


class ProjectDetailPublicationTests(unittest.TestCase):
    def test_module_detail_maps_its_real_symbols_and_source(self):
        with TemporaryDirectory(prefix="codeviz-task5-detail-") as temporary:
            scope = Path(temporary)
            root = scope / "project"
            root.mkdir()
            (root / "app.py").write_text(
                "class Engine:\n"
                "    def run(self):\n"
                "        return 1\n"
                "\n"
                "def main():\n"
                "    return Engine().run()\n",
                encoding="utf-8",
            )
            store = CacheStore(scope / "cache")
            controller = AppController(store)
            bridge = DesktopBridge(
                controller, store, PreferencesStore(scope / "preferences"))
            self.assertTrue(bridge.open_project(str(root))["ok"])
            self.assertTrue(bridge.analyze()["ok"])
            snapshot = controller.capture_assistant_context().snapshot
            self.assertIsNotNone(snapshot)
            module = next(record for record in snapshot.symbol_inventory
                          if record.kind is NodeKind.MODULE
                          and record.file_path == "app.py")

            response = bridge.get_detail_view("ProjectStructure", None, module.id)
            self.assertTrue(response["ok"], response)
            delivered_ids = {node["id"] for node in response["data"]["nodes"]}
            self.assertIn(module.id, delivered_ids)
            self.assertGreater(len(delivered_ids), 1)
            context = controller.capture_assistant_context()
            for symbol_id in delivered_ids:
                symbol = snapshot.get_symbol(symbol_id)
                self.assertIsNotNone(symbol)
                key = ViewNodeKey(ViewKind.PROJECT_STRUCTURE, None,
                                  module.id, symbol_id)
                target = EntityRef("symbol", symbol_id)
                self.assertIn(target, context.context_map[key].targets)
                self.assertIn(symbol.source_anchor,
                              context.context_map[key].source_anchors)
                self.assertIn(key, context.navigation_facts[target].visible_locations)


if __name__ == "__main__":
    unittest.main()
