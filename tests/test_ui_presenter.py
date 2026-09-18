"""UI presentation facts stay separate from analysis and Tk widgets."""

import unittest
from pathlib import Path

from tests.test_training import analyze_files

from codeviz.core import ViewKind, VisibleGraph, VisibleNode
from codeviz.ui.presenter import build_inspector, build_overview, build_problems
from codeviz.views import ViewRequest, build_visible_graph


class PresenterTests(unittest.TestCase):
    def test_overview_keeps_models_and_entries_separate_with_real_health(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, x): return x\n"
            ),
            "run.py": (
                "def train(optimizer):\n"
                "    optimizer.step()\n"
                "def evaluate(model):\n"
                "    model.eval()\n"
            ),
        })
        overview = build_overview(analysis, Path("example-project"))
        self.assertEqual(overview.project_name, "example-project")
        self.assertEqual(overview.language, "Python")
        self.assertEqual(overview.framework, "PyTorch")
        self.assertEqual({item.id for item in overview.models},
                         {item.symbol_id for item in analysis.model.definitions})
        self.assertEqual({item.id for item in overview.entries},
                         {item.id for item in analysis.training.entry_points})
        self.assertEqual(overview.health.files_parsed, 2)
        self.assertEqual(overview.health.files_total, 2)
        self.assertGreaterEqual(overview.health.probable, 1)

    def test_inspector_keeps_real_name_evidence_relations_and_source(self):
        analysis = analyze_files({"run.py": (
            "def train(loss, optimizer):\n"
            "    loss.backward()\n"
            "    optimizer.step()\n"
        )})
        view = build_visible_graph(analysis, ViewRequest(ViewKind.EXECUTION))
        selected = next(node for node in view.nodes if node.display_name == "ParameterUpdate")
        inspector = build_inspector(view, selected.id, analysis)
        self.assertEqual(inspector.code_name, "optimizer.step")
        self.assertTrue(inspector.evidence)
        self.assertTrue(inspector.relations)
        self.assertEqual(inspector.source_anchors[0].file_path, "run.py")
        self.assertEqual(inspector.source_anchors[0].start_line, 3)
        self.assertTrue(inspector.can_open_source)

    def test_aggregation_and_problems_never_invent_source(self):
        analysis = analyze_files({"bad.py": "def broken(:\n"})
        aggregation = VisibleNode("aggregate", "12 more", member_ids=("a", "b"))
        view = VisibleGraph(ViewKind.ARCHITECTURE, (aggregation,))
        inspector = build_inspector(view, aggregation.id, analysis)
        self.assertFalse(inspector.can_open_source)
        self.assertEqual(inspector.source_anchors, ())
        self.assertEqual(inspector.member_ids, ("a", "b"))
        problems = build_problems(analysis)
        self.assertTrue(any(item.file_path == "bad.py" for item in problems))


if __name__ == "__main__":
    unittest.main()
