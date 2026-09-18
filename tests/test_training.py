"""Training events link static source evidence to independent entry points."""

import ast
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.analysis.project import analyze_project
from codeviz.analysis.training import build_training_analysis
from codeviz.core import Confidence, EntryPointKind
from codeviz.semantic import TrainingRole
from codeviz.semantic.pytorch import PyTorchModelRule


def analyze_files(files: dict[str, str]):
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        for relative, source in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        return analyze_project(root)


class TrainingAnalysisTests(unittest.TestCase):
    def test_train_and_evaluate_have_independent_flows_and_shared_model_definition(self):
        result = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, x): return x\n"
            ),
            "run.py": (
                "import torch\n"
                "import torch.nn as nn\n"
                "from model import Net\n"
                "def train(loader, epochs):\n"
                "    model = Net()\n"
                "    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)\n"
                "    criterion = nn.CrossEntropyLoss()\n"
                "    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, 1)\n"
                "    model.train()\n"
                "    for epoch in range(epochs):\n"
                "        for batch, target in loader:\n"
                "            optimizer.zero_grad()\n"
                "            prediction = model(batch)\n"
                "            loss = criterion(prediction, target)\n"
                "            loss.backward()\n"
                "            optimizer.step()\n"
                "        scheduler.step()\n"
                "    torch.save(model, 'checkpoint.pt')\n"
                "def evaluate():\n"
                "    model = Net()\n"
                "    model.eval()\n"
                "    with torch.no_grad():\n"
                "        prediction = model(x)\n"
            ),
        })
        training = getattr(result, "training", None)
        self.assertIsNotNone(training)
        entries = {item.name: item for item in training.entry_points}
        self.assertEqual({name: item.kind for name, item in entries.items()},
                         {"train": EntryPointKind.TRAIN, "evaluate": EntryPointKind.EVALUATE})
        model_id = next(item.symbol_id for item in result.model.definitions
                        if item.qualified_name == "model.Net")
        self.assertEqual(entries["train"].model_ids, (model_id,))
        self.assertEqual(entries["evaluate"].model_ids, (model_id,))
        events = {item.id: item for item in training.events}
        train_roles = [events[item].role for item in entries["train"].flow_node_ids]
        self.assertEqual(train_roles, [
            TrainingRole.TRAINING_MODE, TrainingRole.EPOCH_LOOP, TrainingRole.BATCH_LOOP,
            TrainingRole.CLEAR_GRADIENTS, TrainingRole.FORWARD_PASS,
            TrainingRole.LOSS_CALCULATION, TrainingRole.BACKWARD_PASS,
            TrainingRole.PARAMETER_UPDATE, TrainingRole.LR_UPDATE,
            TrainingRole.CHECKPOINT_SAVE,
        ])
        evaluate_roles = [events[item].role for item in entries["evaluate"].flow_node_ids]
        self.assertEqual(evaluate_roles, [
            TrainingRole.EVALUATION_MODE, TrainingRole.DISABLE_GRADIENT,
            TrainingRole.FORWARD_PASS,
        ])
        self.assertEqual(events[entries["train"].flow_node_ids[7]].confidence, Confidence.EXACT)
        self.assertTrue(all(item.source_anchor.file_path == "run.py" and item.evidence
                            for item in training.events))
        self.assertTrue(all(item.source_node_id in result.flow.graph.nodes
                            for item in training.events))
        ordered = [events[item] for item in entries["train"].flow_node_ids]
        self.assertEqual(getattr(ordered[2], "parent_loop_id", None), ordered[1].id)
        self.assertEqual(getattr(ordered[3], "parent_loop_id", None), ordered[2].id)
        self.assertEqual(getattr(ordered[8], "parent_loop_id", None), ordered[1].id)

    def test_local_torch_shadowing_never_yields_exact_framework_roles(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train(torch):\n"
            "    optimizer = torch.optim.SGD([])\n"
            "    optimizer.step()\n"
            "    with torch.no_grad(): pass\n"
        )})
        events = result.training.events
        self.assertFalse(any(item.role == TrainingRole.DISABLE_GRADIENT for item in events))
        step = next(item for item in events if item.role == TrainingRole.PARAMETER_UPDATE)
        self.assertEqual(step.confidence, Confidence.PROBABLE)

    def test_rebinding_downgrades_model_and_parameter_receivers_remain_probable(self):
        result = analyze_files({
            "model.py": "import torch.nn as nn\nclass Net(nn.Module): pass\n",
            "run.py": (
                "from model import Net\n"
                "def train(optimizer):\n"
                "    model = Net()\n"
                "    model = factory()\n"
                "    model.train()\n"
                "    optimizer.step()\n"
                "    worker.step()\n"
            ),
        })
        events = result.training.events
        self.assertEqual([(item.role, item.confidence) for item in events], [
            (TrainingRole.TRAINING_MODE, Confidence.PROBABLE),
            (TrainingRole.PARAMETER_UPDATE, Confidence.PROBABLE),
        ])

    def test_inference_and_export_are_distinct_from_evaluation(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def infer(model):\n"
            "    model.eval()\n"
            "    with torch.no_grad(): return model(x)\n"
            "def export(model, path):\n"
            "    torch.save(model, path)\n"
        )})
        entries = {item.name: item for item in result.training.entry_points}
        self.assertEqual(entries["infer"].kind, EntryPointKind.INFERENCE)
        self.assertEqual(entries["export"].kind, EntryPointKind.EXPORT)
        self.assertEqual({item.role for item in result.training.events if item.entry_point_id == entries["infer"].id},
                         {TrainingRole.EVALUATION_MODE, TrainingRole.DISABLE_GRADIENT,
                          TrainingRole.FORWARD_PASS})

    def test_bad_file_does_not_hide_good_training_entry(self):
        result = analyze_files({
            "bad.py": "def train(: pass\n",
            "good.py": "def train(optimizer):\n    optimizer.step()\n",
        })
        self.assertEqual([item.name for item in result.training.entry_points], ["train"])
        self.assertIn("SYNTAX_ERROR", {issue.code for issue in result.issues})

    def test_import_rule_error_is_isolated_to_one_file(self):
        original = PyTorchModelRule.import_bindings

        def failing_imports(rule, tree):
            if "break_training_rule" in ast.unparse(tree):
                raise RuntimeError("rule failed")
            return original(rule, tree)

        with patch.object(PyTorchModelRule, "import_bindings", failing_imports):
            result = analyze_files({
                "bad.py": "def train():\n    break_training_rule()\n",
                "good.py": "def train(optimizer):\n    optimizer.step()\n",
            })
        self.assertEqual([item.source_anchor.file_path for item in result.training.entry_points],
                         ["good.py"])
        self.assertIn("TRAINING_ERROR", {item.code for item in result.issues})

    def test_conditional_and_loop_rebinding_do_not_leave_false_exact_model(self):
        result = analyze_files({
            "model.py": "import torch.nn as nn\nclass Net(nn.Module): pass\n",
            "run.py": (
                "from model import Net\n"
                "def train(flag):\n"
                "    model = Net()\n"
                "    if flag: model = factory()\n"
                "    model.train()\n"
                "    for item in values: model = factory()\n"
                "    model.eval()\n"
            ),
        })
        modes = [item for item in result.training.events
                 if item.role in (TrainingRole.TRAINING_MODE, TrainingRole.EVALUATION_MODE)]
        self.assertEqual([item.confidence for item in modes],
                         [Confidence.PROBABLE, Confidence.PROBABLE])

    def test_try_handler_does_not_make_conditional_model_exact(self):
        result = analyze_files({
            "model.py": "import torch.nn as nn\nclass Net(nn.Module): pass\n",
            "run.py": (
                "from model import Net\n"
                "def train():\n"
                "    try: model = factory()\n"
                "    except Exception: model = Net()\n"
                "    model.train()\n"
            ),
        })
        mode = next(item for item in result.training.events
                    if item.role == TrainingRole.TRAINING_MODE)
        self.assertEqual(mode.confidence, Confidence.PROBABLE)

    def test_loop_target_and_with_as_shadow_optimizer_fact(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train():\n"
            "    optimizer = torch.optim.SGD([], lr=0.1)\n"
            "    for optimizer in others:\n"
            "        optimizer.step()\n"
            "    with context() as optimizer:\n"
            "        optimizer.step()\n"
        )})
        updates = [item for item in result.training.events
                   if item.role == TrainingRole.PARAMETER_UPDATE]
        self.assertFalse(any(item.confidence == Confidence.EXACT for item in updates))

    def test_named_expression_rebinding_downgrades_optimizer_fact(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train():\n"
            "    optimizer = torch.optim.SGD([], lr=0.1)\n"
            "    if (optimizer := factory()) is None: pass\n"
            "    optimizer.step()\n"
        )})
        updates = [item for item in result.training.events
                   if item.role == TrainingRole.PARAMETER_UPDATE]
        self.assertEqual([item.confidence for item in updates], [Confidence.PROBABLE])

    def test_delete_clears_optimizer_fact(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train():\n"
            "    optimizer = torch.optim.SGD([], lr=0.1)\n"
            "    del optimizer\n"
            "    optimizer.step()\n"
        )})
        self.assertFalse(any(item.role == TrainingRole.PARAMETER_UPDATE
                             for item in result.training.events))

    def test_unrelated_earlier_event_does_not_change_existing_event_id(self):
        base = "def evaluate(model):\n    model.eval()\n"
        inserted = "def evaluate(model):\n    for epoch in []: pass\n    model.eval()\n"
        first = analyze_files({"run.py": base}).training.events
        second = analyze_files({"run.py": inserted}).training.events
        eval_first = next(item for item in first if item.role == TrainingRole.EVALUATION_MODE)
        eval_second = next(item for item in second if item.role == TrainingRole.EVALUATION_MODE)
        self.assertEqual(eval_first.source_node_id, eval_second.source_node_id)
        self.assertEqual(eval_first.id, eval_second.id)

    def test_multiple_entries_reuse_project_reference_and_flow_indexes(self):
        class CountedList(list):
            iterations = 0

            def __iter__(self):
                self.iterations += 1
                return super().__iter__()

        class CountedDict(dict):
            scans = 0

            def values(self):
                self.scans += 1
                return super().values()

        source = "".join(f"def train_{name}(optimizer):\n    optimizer.step()\n"
                         for name in ("a", "b", "c", "d"))
        result = analyze_files({"run.py": source})
        references = CountedList(result.resolution.symbol_index.references)
        nodes = CountedDict(result.flow.graph.nodes)
        result.resolution.symbol_index.references = references
        result.flow.graph.nodes = nodes
        training = build_training_analysis(result.files, result.resolution,
                                           result.model, result.flow)
        self.assertEqual(len(training.entry_points), 4)
        self.assertLessEqual(references.iterations, 1)
        self.assertLessEqual(nodes.scans, 1)


if __name__ == "__main__":
    unittest.main()
