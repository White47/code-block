"""Reveal routes are chosen from source-backed IDs, not guessed names."""

import unittest

from tests.test_training import analyze_files

from codeviz.core import ViewKind
from codeviz.navigation import plan_reveal
from codeviz.search import search_project
from codeviz.views import ViewRequest, build_visible_graph


class NavigationTests(unittest.TestCase):
    def test_model_training_and_ordinary_symbol_routes(self):
        analysis = analyze_files({
            "model.py": "import torch.nn as nn\nclass Net(nn.Module):\n"
                        "    def forward(self, x): return x\n",
            "run.py": "import torch\ndef train(loss, optimizer):\n"
                      "    criterion = torch.nn.MSELoss()\n"
                      "    loss.backward()\n    optimizer.step()\n"
                      "def helper(): return 1\n",
        })
        model = next(item for item in search_project(analysis, "Net").results
                     if item.source_kind == "symbol" and item.name == "Net")
        backward = search_project(analysis, "反向传播").results[0]
        helper = next(item for item in search_project(analysis, "helper").results
                      if item.source_kind == "symbol")
        self.assertEqual(plan_reveal(analysis, model).view_kind, ViewKind.ARCHITECTURE)
        self.assertEqual(plan_reveal(analysis, backward).view_kind, ViewKind.EXECUTION)
        self.assertEqual(plan_reveal(analysis, helper).view_kind, ViewKind.SYMBOL)
        current = build_visible_graph(analysis, ViewRequest(ViewKind.ARCHITECTURE))
        self.assertEqual(plan_reveal(analysis, model, current).view_kind,
                         ViewKind.ARCHITECTURE)
        code_only = build_visible_graph(analysis, ViewRequest(
            ViewKind.SYMBOL, selected_id=model.id))
        self.assertEqual(plan_reveal(analysis, model, code_only).view_kind,
                         ViewKind.ARCHITECTURE)

    def test_critical_variable_routes_to_data_context(self):
        analysis = analyze_files({"run.py": "def train(x):\n    loss = x\n    return loss\n"})
        variable = next(item for item in search_project(analysis, "loss").results
                        if item.source_kind == "symbol" and item.name == "loss")
        self.assertEqual(plan_reveal(analysis, variable).view_kind, ViewKind.DATA_FLOW)

    def test_model_child_semantic_result_routes_to_own_model(self):
        analysis = analyze_files({"model.py": (
            "import torch.nn as nn\n"
            "class Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        self.layer = nn.Linear(2, 2)\n"
            "    def forward(self, x): return self.layer(x)\n")})
        child = next(item for item in search_project(analysis, "layer").results
                     if item.source_kind == "semantic" and item.kind == "ModuleInstance")
        target = plan_reveal(analysis, child)
        self.assertEqual(target.view_kind, ViewKind.ARCHITECTURE)
        self.assertEqual(target.root_id, analysis.model.definitions[0].symbol_id)
        self.assertEqual(target.selected_id, child.id)
