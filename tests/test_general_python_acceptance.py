"""General Python acceptance through real, non-executing project analysis."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from codeviz.analysis.project import analyze_project
from codeviz.core import EdgeType, EntryPointKind, NodeKind, SourceAnchor, ViewKind
from codeviz.persistence.codec import decode_analysis, encode_analysis
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.presenter import build_overview
from codeviz.views import ViewRequest, build_visible_graph


class GeneralPythonAcceptanceTests(unittest.TestCase):
    def analyze(self, source):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "app.py").write_text(dedent(source).lstrip("\n"), encoding="utf-8")
        result = analyze_project(root)
        self.assertTrue(all(file.was_parsed for file in result.files))
        self.assertFalse(any(issue.code == "INDEX_ERROR" for issue in result.issues))
        return result, root

    def function(self, result, name):
        matches = [item for item in result.symbol_index.find_definitions(name)
                   if item.kind == NodeKind.FUNCTION]
        self.assertEqual(len(matches), 1, f"expected function definition {name}")
        return matches[0]

    def function_flow(self, result, symbol_id):
        kind = getattr(ViewKind, "FUNCTION_FLOW", None)
        self.assertIsNotNone(kind, "ViewKind.FUNCTION_FLOW must exist for ordinary functions")
        self.assertEqual(kind, ViewKind("FunctionFlow"))
        try:
            view = build_visible_graph(result, ViewRequest(kind, root_id=symbol_id))
        except ValueError as error:
            self.fail(f"FunctionFlow must accept a function symbol ID: {error}")
        self.assertIn(symbol_id, {node.id for node in view.nodes})
        self.assertGreater(len(view.nodes), 1, "FunctionFlow must include the function body")
        self.assertTrue(any(edge.type == EdgeType.CONTROL_FLOW for edge in view.edges))
        self.assertTrue(all(isinstance(anchor, SourceAnchor)
                            for node in view.nodes for anchor in node.source_anchors))
        self.assertTrue(all(node.source_anchors or node.member_ids for node in view.nodes))
        return view

    def test_guarded_main_is_an_overview_entry_with_a_function_flow_root(self):
        result, root = self.analyze('''
            def main():
                value = 1
                return value
            if __name__ == "__main__":
                main()
        ''')
        main = self.function(result, "main")
        overview = build_overview(result, root)
        self.assertTrue(overview.entries, "ordinary main guard must produce an Overview entry")
        # Generic candidates identify the code root independently of training IDs.
        entries = [item for item in getattr(result, "generic_entries", ())
                   if getattr(item, "root_symbol_id", None) == main.id]
        self.assertTrue(entries, "generic main entry must carry its root_symbol_id")
        self.assertIn(entries[0].id, {item.id for item in overview.entries})
        self.assertEqual(entries[0].source_anchor.file_path, "app.py")
        self.function_flow(result, main.id)

    def test_no_entry_library_catalogs_ordinary_functions(self):
        result, root = self.analyze('''
            def normalize(value):
                result = value.strip()
                return result
            def combine(left, right):
                joined = left + right
                return joined
        ''')
        overview = build_overview(result, root)
        functions = getattr(overview, "functions", ())
        expected = {self.function(result, name).id for name in ("normalize", "combine")}
        self.assertEqual({item.id for item in functions}, expected,
                         "Overview.functions must expose a library without an entry")
        self.assertEqual(overview.entries, ())
        self.assertTrue(all(isinstance(item.source_anchor, SourceAnchor) for item in functions))

    def test_function_symbol_id_opens_function_flow_without_an_entry(self):
        result, _ = self.analyze('''
            def normalize(value):
                stripped = value.strip()
                return stripped
        ''')
        self.function_flow(result, self.function(result, "normalize").id)

    def test_export_csv_without_ml_evidence_is_not_a_training_export(self):
        result, _ = self.analyze('''
            def export_csv(rows):
                text = ",".join(rows)
                return text
        ''')
        self.assertIsNotNone(result.training)
        exports = [item for item in result.training.entry_points
                   if item.kind == EntryPointKind.EXPORT]
        self.assertEqual(exports, [], "export_csv name alone is not ML Export evidence")

    def test_try_finally_cfg_limitation_is_source_backed_and_partial(self):
        result, _ = self.analyze('''
            def cleanup():
                return None
            def process(value):
                try:
                    return value
                finally:
                    cleanup()
        ''')
        issues = [issue for issue in result.issues
                  if issue.source_anchor is not None
                  and issue.source_anchor.file_path == "app.py"
                  and 4 <= issue.source_anchor.start_line <= 7
                  and any(word in (issue.code + " " + issue.message).lower()
                          for word in ("try", "finally", "control", "cfg"))]
        self.assertTrue(issues, "simplified try/finally CFG must report its source limitation")
        self.assertEqual(AppController._result_status(result), AnalysisStatus.PARTIAL,
                         "CFG limitations must make analysis Partial")

    def test_function_flow_excludes_uncalled_nested_function_body(self):
        result, _ = self.analyze('''
            def outer(value):
                def inner():
                    hidden = 99
                    return hidden
                visible = value
                return visible
        ''')
        view = self.function_flow(result, self.function(result, "outer").id)
        body_lines = {anchor.start_line for node in view.nodes
                      for anchor in node.source_anchors}
        self.assertIn(5, body_lines)
        self.assertIn(6, body_lines)
        self.assertTrue({3, 4}.isdisjoint(body_lines), "nested body is not outer execution")

    def test_cached_function_flow_and_catalog_work_without_ast(self):
        result, root = self.analyze('''
            def normalize(value):
                stripped = value.strip()
                return stripped
        ''')
        symbol = self.function(result, "normalize")
        before = self.function_flow(result, symbol.id)
        restored = decode_analysis(json.loads(json.dumps(encode_analysis(result))))
        self.assertTrue(all(file.tree is None for file in restored.files))
        self.assertEqual(self.function_flow(restored, symbol.id), before)
        restored_functions = getattr(build_overview(restored, root), "functions", ())
        self.assertEqual({item.id for item in restored_functions}, {symbol.id})
        self.assertEqual(restored_functions, getattr(build_overview(result, root), "functions", ()))


if __name__ == "__main__":
    unittest.main()
