"""Summary layout isolation, stale writes, and legacy state preservation."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.core import ViewKind
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.summary_layout import SummaryGeometry, SummaryLayoutIndex
from codeviz.ui.web_bridge import DesktopBridge
from codeviz.views.state import ViewState


class SummaryLayoutTests(unittest.TestCase):
    def test_bridge_uses_current_summary_and_preserves_it_through_sanitization(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            root.mkdir()
            (root / "main.py").write_text('def greet():\n    """Show a greeting."""\n    print("hello")\n\nif __name__ == "__main__":\n    greet()\n', encoding="utf-8")
            cache = CacheStore(Path(temporary) / "cache")
            controller = AppController(cache)
            bridge = DesktopBridge(controller, cache, PreferencesStore(Path(temporary) / "settings"))
            self.assertTrue(bridge.open_project(str(root))["ok"])
            self.assertTrue(bridge.analyze()["ok"])
            overview = bridge.get_overview()
            self.assertTrue(overview["ok"])
            architecture = overview["data"]["understanding"]["architecture"]
            self.assertTrue(architecture["areas"])
            context = {"kind": "architecture", "id": architecture["id"]}
            layout = bridge.get_summary_layout(context)
            self.assertTrue(layout["ok"])
            token = layout["data"]["layoutToken"]
            node = architecture["areas"][0]["id"]
            self.assertTrue(bridge.move_summary_node(context, token, node, {"x": 20, "y": 30})["ok"])
            before = controller.view_state
            controller.sanitize_view_state()
            self.assertEqual(controller.view_state.positions_by_view, before.positions_by_view)
            self.assertFalse(bridge.move_summary_node(context, token, "foreign", {"x": 2, "y": 3})["ok"])
            self.assertEqual(controller.view_state, before)
            with patch("codeviz.ui.controller.ANALYSIS_VERSION", "future-version", create=True):
                self.assertFalse(bridge.move_summary_node(context, token, node, {"x": 2, "y": 3})["ok"])
                self.assertEqual(controller.view_state, before)
            warm = AppController(cache)
            warm.open_project(root)
            self.assertEqual(warm.get_summary_layout(context)["positions"], {node: [20, 30]})

    def test_isolates_contexts_and_rejects_stale_or_foreign_writes(self):
        first = SummaryGeometry("task_flow", "a", ("same",), "geometry-a")
        second = SummaryGeometry("task_flow", "b", ("same",), "geometry-b")
        index = SummaryLayoutIndex("project-a", "source-v1", (first, second))
        a, b = {"kind": "task_flow", "id": "a"}, {"kind": "task_flow", "id": "b"}
        state = ViewState(positions_by_view={"Overview": {"legacy-node": (1, 2)}})
        token = index.payload(state, a)["layoutToken"]
        moved = index.move(state, a, token, "same", {"x": 80, "y": 120})
        self.assertEqual(index.payload(moved, a)["positions"], {"same": [80, 120]})
        self.assertEqual(index.payload(moved, b)["positions"], {})
        self.assertEqual(moved.positions_for(ViewKind.OVERVIEW)["legacy-node"], (1, 2))
        stale = SummaryLayoutIndex("project-a", "source-v2", (first, second))
        for current, context, node, point in (
            (stale, a, "same", {"x": 1, "y": 2}),
            (index, b, "same", {"x": 1, "y": 2}),
            (index, a, "foreign", {"x": 1, "y": 2}),
            (index, a, "same", {"x": float("nan"), "y": 2}),
            (index, a, "same", {"x": 1e7, "y": 2}),
        ):
            with self.subTest(context=context, node=node, point=point), self.assertRaises(ValueError):
                current.move(moved, context, token, node, point)
        self.assertEqual(index.payload(moved, a)["positions"], {"same": [80, 120]})
        cleaned = SummaryLayoutIndex("project-a", "source-v2", (second,))
        saved = cleaned.move(moved, b, cleaned.payload(moved, b)["layoutToken"], "same", {"x": 4, "y": 5})
        self.assertEqual(len(saved.positions_for(ViewKind.OVERVIEW)), 2)
        viewport = index.save_viewport(moved, a, token, {"zoom": 0.8, "pan": [10, 20]})
        self.assertEqual(index.payload(viewport, b)["viewport"], {"zoom": 0.8, "pan": [10, 20]})
        self.assertEqual(viewport.positions_by_view, moved.positions_by_view)
        dirty = ViewState(positions_by_view={"Overview": {"legacy-node": (1, 2), "summary:v1:obsolete": (3, 4)}})
        camera_saved = index.save_viewport(dirty, a, token, {"zoom": 1, "pan": [0, 0]})
        self.assertEqual(camera_saved.positions_by_view["Overview"], {"legacy-node": (1, 2)})


if __name__ == "__main__":
    unittest.main()
