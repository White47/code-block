"""Task1 contracts beyond the independently owned acceptance tests."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.test_training import analyze_files

from codeviz.core import Confidence, NodeKind, ViewKind
from codeviz.persistence.fingerprint import ANALYSIS_VERSION
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.presenter import build_inspector, build_overview
from codeviz.ui.web_contract import overview_payload
from codeviz.views import ViewRequest, build_visible_graph


class Task1Contracts(unittest.TestCase):
    def test_deferred_lambda_walrus_in_eager_comprehension_preserves_outer_optimizer(self):
        for expression in (
            '[lambda: (optimizer := None) for item in []]',
            '[lambda: [(optimizer := None) for nested in []] for item in []]',
            '[(lambda: (optimizer := None) for nested in []) for item in []]',
        ):
            with self.subTest(expression=expression):
                result = analyze_files({"app.py": (
                    'import torch\ndef train():\n'
                    '    optimizer = torch.optim.SGD([])\n'
                    f'    callbacks = {expression}\n'
                    '    optimizer.step()\n'
                )})
                updates = [event for event in result.training.events
                           if event.role.value == "ParameterUpdate"]
                self.assertEqual(len(updates), 1,
                                 "deferred lexical walrus must not clear the enclosing receiver")
                self.assertEqual(updates[0].source_anchor.start_line, 5)
                self.assertEqual(updates[0].confidence, Confidence.EXACT)
                self.assertIsNotNone(updates[0].source_node_id)

    def test_immediate_lambda_default_walrus_clears_outer_optimizer(self):
        result = analyze_files({"app.py": (
            'import torch\ndef train():\n'
            '    optimizer = torch.optim.SGD([])\n'
            '    callback = lambda value=(optimizer := None): value\n'
            '    optimizer.step()\n'
        )})
        self.assertFalse(any(event.role.value == "ParameterUpdate"
                             for event in result.training.events))

    def test_first_iterable_creation_walrus_does_not_preserve_false_exact_receiver(self):
        # AST parsing accepts this iterable walrus; the lexical analyzer reports
        # its invalid Python binding. Training must not retain a false Exact fact.
        result = analyze_files({"app.py": (
            'import torch\ndef train():\n'
            '    optimizer = torch.optim.SGD([])\n'
            '    gen = (item for item in reset((optimizer := None)))\n'
            '    optimizer.step()\n'
        )})
        self.assertFalse(any(event.role.value == "ParameterUpdate"
                             for event in result.training.events))
        self.assertTrue(any(issue.code == "WALRUS_BINDING_UNRESOLVED" for issue in result.issues))

    def test_eager_comprehension_creation_defaults_and_walrus_invalidate_outer_optimizer(self):
        for expression in (
            '[(optimizer := None) for item in []]',
            '[lambda value=(optimizer := None): value for item in []]',
            '[[lambda value=(optimizer := None): value for nested in []] for item in []]',
        ):
            with self.subTest(expression=expression):
                result = analyze_files({"app.py": (
                    'import torch\ndef train():\n'
                    '    optimizer = torch.optim.SGD([])\n'
                    f'    callbacks = {expression}\n'
                    '    optimizer.step()\n'
                )})
                self.assertFalse(any(event.role.value == "ParameterUpdate"
                                     for event in result.training.events))

    def test_framework_import_alone_does_not_prove_training_names(self):
        result = analyze_files({"app.py": (
            'import torch\ndef export_csv(): return 1\n'
            'def validate_json(): return 1\ndef infer_type(): return 1\n'
            'def train_data(): return 1\n'
        )})
        self.assertEqual(result.training.entry_points, ())

    def test_parameter_role_and_step_name_without_framework_are_not_ml(self):
        for imported in ('', 'import torch\n'):
            with self.subTest(imported=imported):
                result = analyze_files({"app.py": imported +
                                       'def train(optimizer): optimizer.step()\n'})
                self.assertEqual(result.training.entry_points, ())
                self.assertEqual(result.training.events, ())

    def test_framework_bound_receiver_and_explicit_checkpoint_remain_training(self):
        result = analyze_files({"app.py": (
            'import torch\ndef train():\n'
            '    optimizer = torch.optim.SGD([])\n    optimizer.step()\n'
            'def export_model(model): torch.save(model, "weights")\n'
        )})
        self.assertEqual({entry.name for entry in result.training.entry_points},
                         {"train", "export_model"})
        self.assertTrue(result.training.events)

    def test_generator_expression_cfg_limitation_is_source_backed_partial(self):
        result = analyze_files({"app.py": 'def f(items):\n    return (x for x in items)\n'})
        self.assertEqual(AppController._result_status(result), AnalysisStatus.PARTIAL)
        self.assertTrue(any(issue.code == "CFG_PARTIAL" and issue.source_anchor
                            and issue.source_anchor.start_line == 2 for issue in result.issues))

    def test_training_implicit_bodies_do_not_leak_outer_receiver_events(self):
        result = analyze_files({"app.py": (
            'import torch\ndef train():\n'
            '    optimizer = torch.optim.SGD([])\n'
            '    callback = lambda: optimizer.step()\n'
            '    gen = (optimizer.step() for item in [])\n'
            '    eager = [optimizer.step() for optimizer in []]\n'
        )})
        self.assertEqual(result.training.events, (),
                         "implicit lexical bodies must not invent outer training events")
        self.assertTrue(any(issue.code == "TRAINING_SCOPE_PARTIAL" and issue.source_anchor
                            for issue in result.issues))

    def test_training_keeps_outer_defaults_and_first_iterable_callpoints(self):
        result = analyze_files({"app.py": (
            'import torch\ndef train():\n'
            '    optimizer = torch.optim.SGD([])\n'
            '    callback = lambda value=optimizer.step(): optimizer.step()\n'
            '    gen = (optimizer.step() for item in optimizer.step())\n'
            '    eager = [optimizer.step() for optimizer in optimizer.step()]\n'
            '    optimizer.step()\n'
        )})
        events = result.training.events
        self.assertEqual([event.source_anchor.start_line for event in events], [4, 5, 6, 7])
        self.assertTrue(all(event.source_node_id in result.flow.graph.nodes for event in events))
        root = result.training.entry_points[0].id
        self.assertTrue(all(result.flow.graph.nodes[event.source_node_id].metadata[
            "scope_symbol_id"] == root for event in events))

    def test_every_ml_name_requires_relevant_evidence_not_unrelated_model(self):
        result = analyze_files({
            "model.py": 'import torch.nn as nn\nclass Net(nn.Module): pass\n',
            "plain.py": ('def train_data(): return 1\n'
                         'def validate_json(): return 1\n'
                         'def infer_type(): return 1\n'
                         'def export_csv(): return 1\n'),
            "train.py": 'for epoch in []: pass\n',
        })
        self.assertEqual(result.training.entry_points, (),
                         "unrelated model definitions and loop names are not ML evidence")

    def test_entry_evidence_is_structural_and_main_name_only_probable(self):
        result = analyze_files({
            "app.py": 'def main(): return 1\nif "__main__" == __name__:\n    main()\n',
            "library.py": 'def main(): return 2\ndef utility(): return 3\n',
            "__main__.py": 'value = 1\n',
        })
        candidates = getattr(result, "generic_entries", ())
        self.assertTrue(candidates, "generic entries must be persisted facts")
        by_file = {item.source_anchor.file_path: item for item in candidates}
        self.assertEqual(by_file["app.py"].confidence, Confidence.EXACT)
        self.assertEqual(by_file["library.py"].confidence, Confidence.PROBABLE)
        self.assertEqual(by_file["__main__.py"].confidence, Confidence.EXACT)
        self.assertTrue(all(item.evidence for item in candidates))
        utility = result.symbol_index.find_definitions("utility")[0]
        self.assertNotIn(utility.id, {item.root_symbol_id for item in candidates})

    def test_overview_dispatch_and_bounded_source_backed_details(self):
        result = analyze_files({"app.py": (
            'def helper(): return 9\ndef main():\n'
            '    x = helper()\n    return x\n'
            'if __name__ == "__main__": main()\n'
        )})
        payload = overview_payload(build_overview(result, Path("project")))
        self.assertIn("functions", payload)
        self.assertEqual(payload["entries"][0]["viewKind"], "FunctionFlow")
        kind = ViewKind("FunctionFlow")
        main = result.symbol_index.find_definitions("main")[0]
        helper = result.symbol_index.find_definitions("helper")[0]
        view = build_visible_graph(result, ViewRequest(kind, main.id))
        self.assertNotIn(helper.id, {item.id for item in view.nodes})
        self.assertFalse(any(anchor.start_line == 1 for node in view.nodes
                             for anchor in node.source_anchors))
        call = next(node for node in view.nodes if node.role == NodeKind.CALL_SITE.value)
        inspector = build_inspector(view, call.id, result)
        self.assertTrue(inspector.can_open_source)
        controller = AppController()
        controller.analysis = result
        detail = controller.detail_view(kind, main.id, call.id)
        self.assertIn(call.id, {node.id for node in detail.nodes})
        bounded = build_visible_graph(result, ViewRequest(kind, main.id, max_nodes=2,
                                                        max_edges=1))
        self.assertLessEqual(len(bounded.nodes), 2)
        self.assertLessEqual(len(bounded.edges), 1)
        self.assertGreater(bounded.hidden_counts["nodes"], 0)

    def test_cfg_approximations_are_partial_for_match_async_and_generator(self):
        for source in (
            'def f(x):\n    match x:\n        case 1: return 1\n',
            'async def f(x):\n    return await x()\n',
            'def f():\n    yield 1\n',
        ):
            with self.subTest(source=source):
                result = analyze_files({"app.py": source})
                self.assertEqual(AppController._result_status(result), AnalysisStatus.PARTIAL)
                self.assertTrue(any(issue.code.startswith("CFG_") and issue.source_anchor
                                    for issue in result.issues))

    def test_old_cache_version_is_checked_before_strict_decode_preserving_state(self):
        with TemporaryDirectory() as source_dir, TemporaryDirectory() as cache_dir:
            root = Path(source_dir)
            (root / "app.py").write_text('def main(): return 1\n', encoding="utf-8")
            store = CacheStore(Path(cache_dir))
            first = AppController(store)
            first.open_project(root)
            first.analyze()
            first.save_layout(ViewKind.SYMBOL, "stale", ("Overview", "Symbol"),
                              {"Symbol": "stale"})
            first.move_node("kept", (12., 20.), ViewKind.SYMBOL)
            store.save_annotations(root, {"kept": "note"})
            path = store.analysis_path(root)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["fingerprint"]["analysis_version"] = str(int(ANALYSIS_VERSION) - 1)
            payload["analysis"] = {"old-schema": "must not decode"}
            path.write_text(json.dumps(payload), encoding="utf-8")
            reopened = AppController(store)
            reopened.open_project(root)
            self.assertEqual(reopened.status, AnalysisStatus.OUTDATED)
            self.assertIsNone(reopened.analysis)
            self.assertIsNone(reopened.cache_issue)
            self.assertTrue(reopened.outdated_reasons)
            self.assertEqual(reopened.view_state.positions_for(ViewKind.SYMBOL)["kept"],
                             (12., 20.))
            self.assertEqual(store.load_annotations(root), {"kept": "note"})
            reopened.analyze()
            self.assertIsNone(reopened.view_state.selected_id)
            self.assertIsNone(reopened.view_state.graph_roots["Symbol"])
