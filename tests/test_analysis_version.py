"""The final analysis facts invalidate v2 caches without discarding user state."""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.analysis.project import analyze_project
from codeviz.core import ViewKind
from codeviz.persistence.fingerprint import (
    ANALYSIS_VERSION,
    PARSER_VERSION,
    SEMANTIC_VERSION,
    fingerprint_project,
)
from codeviz.persistence.store import CacheStore, CacheVersionMismatch
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.views import ViewState


class AnalysisVersionTests(unittest.TestCase):
    def test_human_tasks_version_seven_keeps_semantic_and_parser_versions(self):
        self.assertEqual(ANALYSIS_VERSION, "7")
        self.assertEqual(SEMANTIC_VERSION, "3")
        self.assertEqual(PARSER_VERSION, f"python-{sys.version_info.major}.{sys.version_info.minor}")

    def test_v3_rejected_before_decode_preserves_cache_and_user_state_bytes(self):
        with TemporaryDirectory() as source_dir, TemporaryDirectory() as cache_dir:
            root = Path(source_dir)
            (root / "app.py").write_bytes(b"def main(): return 1\n")
            store = CacheStore(Path(cache_dir))
            analysis = analyze_project(root)
            node_id = next(iter(analysis.symbol_index.entries))
            store.save_analysis(root, analysis, fingerprint_project(root))
            state = ViewState(active_view=ViewKind.SYMBOL,
                              positions_by_view={"Symbol": {node_id: (12., 20.)}})
            notes = {node_id: "review note"}
            store.save_view_state(root, state)
            store.save_annotations(root, notes)
            path = store.analysis_path(root)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            payload["fingerprint"]["analysis_version"] = "3"
            payload["analysis"] = {"bad-analysis": "must not decode"}
            path.write_text(json.dumps(payload), encoding="utf-8")
            before = {item: item.read_bytes() for item in path.parent.glob("*.json")}
            with patch("codeviz.persistence.store.decode_analysis") as decode:
                with self.assertRaises(CacheVersionMismatch):
                    store.load_analysis(root)
                reopened = AppController(store)
                reopened.open_project(root)
                decode.assert_not_called()
            self.assertEqual(reopened.status, AnalysisStatus.OUTDATED)
            self.assertIsNone(reopened.analysis)
            self.assertIsNone(reopened.cache_issue)
            self.assertEqual(reopened.view_state, state)
            self.assertEqual(store.load_annotations(root), notes)
            self.assertEqual({item: item.read_bytes() for item in before}, before)

    def test_v2_versions_rejected_before_decode_and_user_state_retained(self):
        for key in ("analysis_version", "semantic_version", "parser_version"):
            with self.subTest(version=key), TemporaryDirectory() as source_dir, TemporaryDirectory() as cache_dir:
                root = Path(source_dir)
                (root / "app.py").write_text("def main(): return 1\n", encoding="utf-8")
                store = CacheStore(Path(cache_dir))
                store.save_analysis(root, analyze_project(root), fingerprint_project(root))
                state = ViewState(active_view=ViewKind.SYMBOL,
                                  positions_by_view={"Symbol": {"kept": (12., 20.)}})
                notes = {"kept": "review note"}
                store.save_view_state(root, state)
                store.save_annotations(root, notes)
                path = store.analysis_path(root)
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["fingerprint"][key] = "2"
                payload["analysis"] = {"v2-facts": "must not decode"}
                path.write_text(json.dumps(payload), encoding="utf-8")
                with patch("codeviz.persistence.store.decode_analysis") as decode:
                    with self.assertRaises(CacheVersionMismatch):
                        store.load_analysis(root)
                    reopened = AppController(store)
                    reopened.open_project(root)
                    decode.assert_not_called()
                self.assertEqual(reopened.status, AnalysisStatus.OUTDATED)
                self.assertIsNone(reopened.analysis)
                self.assertIsNone(reopened.cache_issue)
                self.assertEqual(reopened.view_state, state)
                self.assertEqual(store.load_annotations(root), notes)
