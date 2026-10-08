"""Public native session availability is derived from real retained analysis."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codeviz.core import NodeKind
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge


class ProjectRecoverySessionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.root = base / "project"
        self.root.mkdir()
        self.source = self.root / "app.py"
        # Own-written static input, never imported or executed.
        self.source.write_text("def helper(value):\n    return value\n", encoding="utf-8")
        self.cache = CacheStore(base / "cache")
        self.controller = AppController(self.cache)
        self.bridge = DesktopBridge(
            self.controller, self.cache, PreferencesStore(base / "preferences"))

    def data(self, response):
        self.assertTrue(response["ok"], response)
        return response["data"]

    def test_missing_cache_has_no_analysis_then_analysis_is_available(self):
        opened = self.data(self.bridge.open_project(str(self.root)))
        self.assertEqual(opened["status"], "NotAnalyzed")
        self.assertIs(opened.get("hasAnalysis"), False)
        analyzed = self.data(self.bridge.analyze())
        self.assertIsNotNone(self.controller.analysis)
        self.assertIs(analyzed.get("hasAnalysis"), True)
        self.assertIs(self.data(self.bridge.get_session()).get("hasAnalysis"), True)

    def test_version_mismatch_outdated_has_no_analysis_until_reanalysis(self):
        self.data(self.bridge.open_project(str(self.root)))
        self.data(self.bridge.analyze())
        path = self.cache.analysis_path(self.root)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["fingerprint"]["analysis_version"] = "obsolete-recovery-test-version"
        path.write_text(json.dumps(payload), encoding="utf-8")
        opened = self.data(self.bridge.open_project(str(self.root)))
        self.assertEqual(opened["status"], "Outdated")
        self.assertIsNone(self.controller.analysis)
        self.assertTrue(opened["outdatedReasons"])
        self.assertIs(opened.get("hasAnalysis"), False)
        self.assertIs(self.data(self.bridge.get_session()).get("hasAnalysis"), False)
        analyzed = self.data(self.bridge.analyze())
        self.assertIs(analyzed.get("hasAnalysis"), True)

    def test_source_changed_outdated_retains_queryable_cached_analysis(self):
        self.data(self.bridge.open_project(str(self.root)))
        self.data(self.bridge.analyze())
        self.source.write_text(
            "def helper(value):\n    return value\n\ndef added():\n    return 2\n",
            encoding="utf-8")
        opened = self.data(self.bridge.open_project(str(self.root)))
        self.assertEqual(opened["status"], "Outdated")
        self.assertIsNotNone(self.controller.analysis)
        self.assertIs(opened.get("hasAnalysis"), True)
        self.assertIs(self.data(self.bridge.get_session()).get("hasAnalysis"), True)
        self.data(self.bridge.get_overview())
        self.data(self.bridge.get_problems())
        definitions = [entry for entry in self.controller.analysis.symbol_index.find_definitions("helper")
                       if entry.kind is NodeKind.FUNCTION]
        self.assertEqual(len(definitions), 1)
        helper = definitions[0]
        self.assertEqual(helper.source_anchor.file_path, "app.py")
        graph = self.data(self.bridge.get_view("Symbol", helper.id, helper.id))
        self.assertIn(helper.id, {node["id"] for node in graph["nodes"]})
