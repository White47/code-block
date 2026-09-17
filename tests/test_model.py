"""PyTorch model facts are static, source-backed, and rule-driven."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codeviz.analysis.model import build_project_model
from codeviz.analysis.project import analyze_project
from codeviz.core import Confidence
from codeviz.semantic import PyTorchModelRule


def analyze_files(files: dict[str, str]):
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        for relative, source in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        return analyze_project(root)


class ModelAnalysisTests(unittest.TestCase):
    def test_aliases_custom_module_and_common_layers(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\n"
            "from torch.nn import Module, Embedding\n"
            "class Block(Module):\n"
            "    def __init__(self):\n"
            "        self.embedding = Embedding(10, 4)\n"
            "        self.linear = nn.Linear(4, 4)\n"
            "    def forward(self, x):\n"
            "        return self.linear(self.embedding(x))\n"
            "class Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        self.block = Block()\n"
            "    def forward(self, x):\n"
            "        return self.block(x)\n"
        )})
        model = result.model
        self.assertEqual({item.qualified_name for item in model.definitions},
                         {"model.Block", "model.Net"})
        self.assertTrue(all(item.source_anchor.file_path == "model.py" and item.evidence
                            for item in model.definitions))
        instances = {item.qualified_name: item for item in model.instances}
        self.assertEqual(instances["model.Block.linear"].layer_type, "torch.nn.Linear")
        self.assertEqual(instances["model.Block.embedding"].layer_type, "torch.nn.Embedding")
        self.assertEqual(instances["model.Net.block"].target_definition_id,
                         next(item.symbol_id for item in model.definitions if item.qualified_name == "model.Block"))
        self.assertTrue(any(item.instance_id == instances["model.Net.block"].id
                            for item in model.invocations))
        self.assertTrue(all(item.call_site_id in result.flow.graph.nodes for item in model.invocations))

    def test_containers_order_repetition_and_no_false_chaining(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\n"
            "class Block(nn.Module):\n"
            "    def forward(self, x): return x\n"
            "class Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        self.seq = nn.Sequential(nn.Linear(2, 2), nn.ReLU())\n"
            "        self.layers = nn.ModuleList([Block() for _ in range(3)])\n"
            "        self.mixed = nn.ModuleList([nn.Linear(2, 2), nn.ReLU()])\n"
            "        self.same = nn.ModuleList([Block(), Block()])\n"
            "        self.named = nn.ModuleDict({'a': Block(), 'b': Block()})\n"
        )})
        instances = {item.name: item for item in result.model.instances}
        self.assertEqual(instances["seq"].container_kind, "Sequential")
        self.assertEqual([instances[next(item.name for item in result.model.instances if item.id == child)].layer_type
                          for child in instances["seq"].member_instance_ids],
                         ["torch.nn.Linear", "torch.nn.ReLU"])
        self.assertEqual(instances["layers"].container_kind, "ModuleList")
        self.assertEqual(instances["layers"].repeat_count, 3)
        self.assertEqual(len(instances["layers"].member_instance_ids), 1)
        template = next(item for item in result.model.instances
                        if item.id == instances["layers"].member_instance_ids[0])
        self.assertEqual(template.repeat_count, 3)
        self.assertEqual(template.confidence, Confidence.PROBABLE)
        self.assertEqual(instances["mixed"].repeat_count, 1)
        self.assertEqual(instances["same"].repeat_count, 2)
        self.assertEqual(instances["named"].repeat_count, 1)
        self.assertEqual(instances["named"].container_kind, "ModuleDict")
        self.assertEqual([item.name for item in result.model.instances
                          if item.id in instances["named"].member_instance_ids], ["named[a]", "named[b]"])
        self.assertFalse(any(item.shared_with_id for item in result.model.instances))

    def test_shared_alias_is_not_repeated_instance(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\n"
            "class Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        self.left = nn.Linear(2, 2)\n"
            "        self.right = self.left\n"
        )})
        instances = {item.name: item for item in result.model.instances}
        self.assertNotEqual(instances["left"].id, instances["right"].id)
        self.assertEqual(instances["right"].shared_with_id, instances["left"].id)
        self.assertIsNone(instances["left"].shared_with_id)
        self.assertEqual(instances["right"].repeat_count, 1)

    def test_unknown_and_unrelated_nn_do_not_invent_models(self):
        result = analyze_files({"model.py": (
            "class Fake:\n"
            "    def Module(self): pass\n"
            "nn = Fake()\n"
            "class NotModel(nn.Module): pass\n"
            "import torch.nn as torch_nn\n"
            "class Net(torch_nn.Module):\n"
            "    def __init__(self):\n"
            "        self.unknown = factory()\n"
            "        self.known = torch_nn.Conv2d(1, 2, 3)\n"
        )})
        self.assertEqual([item.qualified_name for item in result.model.definitions], ["model.Net"])
        self.assertEqual([item.name for item in result.model.instances], ["known"])

    def test_subclass_and_duplicate_instances_have_stable_distinct_ids(self):
        files = {"model.py": (
            "import torch.nn as nn\n"
            "class Base(nn.Module): pass\n"
            "class Child(Base):\n"
            "    def __init__(self):\n"
            "        self.a = nn.Linear(1, 1)\n"
            "        self.b = nn.Linear(1, 1)\n"
        )}
        first, second = analyze_files(files), analyze_files(files)
        self.assertEqual({item.qualified_name for item in first.model.definitions},
                         {"model.Base", "model.Child"})
        self.assertEqual([item.id for item in first.model.instances],
                         [item.id for item in second.model.instances])
        self.assertEqual(len({item.id for item in first.model.instances}), 2)
        self.assertTrue(all(item.confidence == Confidence.EXACT for item in first.model.instances))

    def test_bad_file_does_not_hide_valid_model_and_never_executes(self):
        result = analyze_files({
            "bad.py": "class Oops(: pass\n",
            "good.py": "raise RuntimeError('do not execute')\nimport torch.nn as nn\nclass Net(nn.Module): pass\n",
        })
        self.assertEqual([item.qualified_name for item in result.model.definitions], ["good.Net"])
        self.assertIn("SYNTAX_ERROR", {item.code for item in result.issues})

    def test_chained_assignment_marks_one_shared_constructor(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\n"
            "class Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        self.left = self.right = nn.Linear(2, 2)\n"
        )})
        instances = {item.name: item for item in result.model.instances}
        self.assertEqual(instances["right"].shared_with_id, instances["left"].id)

    def test_local_framework_alias_shadowing_does_not_claim_layer(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\n"
            "class Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        nn = unknown_factory()\n"
            "        self.layer = nn.Linear(2, 2)\n"
        )})
        self.assertEqual(result.model.instances, ())

    def test_dynamic_container_count_is_reported_without_fabricated_repeat(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\n"
            "class Block(nn.Module): pass\n"
            "class Net(nn.Module):\n"
            "    def __init__(self, n):\n"
            "        self.layers = nn.ModuleList([Block() for _ in range(n)])\n"
        )})
        container = next(item for item in result.model.instances if item.name == "layers")
        self.assertEqual(container.member_instance_ids, ())
        self.assertIsNone(container.repeat_count)
        self.assertIn("MODEL_CONTAINER_DYNAMIC", {issue.code for issue in result.issues})

    def test_project_local_torch_package_is_not_framework_evidence(self):
        result = analyze_files({
            "torch/__init__.py": "",
            "torch/nn.py": "class Module: pass\nclass Linear: pass\n",
            "model.py": "import torch.nn as nn\nclass Fake(nn.Module):\n    def __init__(self): self.x = nn.Linear()\n",
        })
        self.assertEqual(result.model.definitions, ())

    def test_loop_target_shadows_framework_alias(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\nclass Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        for nn in sources: pass\n"
            "        self.layer = nn.Linear(1, 1)\n"
        )})
        self.assertEqual(result.model.instances, ())

    def test_conditional_constructor_is_probable_and_not_a_unique_forward_target(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\nclass Net(nn.Module):\n"
            "    def __init__(self, flag):\n"
            "        if flag: self.layer = nn.Linear(1, 1)\n"
            "    def forward(self, x): return self.layer(x)\n"
        )})
        layer = next(item for item in result.model.instances if item.name == "layer")
        self.assertEqual(layer.confidence, Confidence.PROBABLE)
        self.assertFalse(any(item.instance_id == layer.id for item in result.model.invocations))

    def test_repeated_shared_member_is_not_structural_repetition(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\nclass Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        self.left = nn.Linear(1, 1)\n"
            "        self.layers = nn.ModuleList([self.left, self.left])\n"
        )})
        layers = next(item for item in result.model.instances if item.name == "layers")
        self.assertEqual(layers.repeat_count, 1)
        members = [item for item in result.model.instances if item.id in layers.member_instance_ids]
        self.assertEqual({item.shared_with_id for item in members},
                         {next(item.id for item in result.model.instances if item.name == "left")})

    def test_zero_repeat_and_shadowed_range(self):
        zero = analyze_files({"model.py": (
            "import torch.nn as nn\nclass Net(nn.Module):\n"
            "    def __init__(self): self.layers = nn.ModuleList([nn.Linear(1,1) for _ in range(0)])\n"
        )})
        layers = next(item for item in zero.model.instances if item.name == "layers")
        self.assertEqual(layers.repeat_count, 0)
        self.assertEqual(layers.member_instance_ids, ())
        shadowed = analyze_files({"model.py": (
            "import torch.nn as nn\nclass Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        range = lambda n: [1]\n"
            "        self.layers = nn.ModuleList([nn.Linear(1,1) for _ in range(3)])\n"
        )})
        self.assertIsNone(next(item for item in shadowed.model.instances
                               if item.name == "layers").repeat_count)

    def test_unknown_factory_called_in_forward_remains_unresolved(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\nclass Net(nn.Module):\n"
            "    def __init__(self): self.block = factory()\n"
            "    def forward(self, x): return self.block(x)\n"
        )})
        block = next(item for item in result.model.instances if item.name == "block")
        self.assertEqual(block.confidence, Confidence.UNRESOLVED)
        self.assertEqual(block.layer_type, "factory")
        self.assertTrue(any(item.instance_id == block.id and item.confidence == Confidence.UNRESOLVED
                            for item in result.model.invocations))

    def test_partial_moduledict_and_model_rule_failure_are_isolated(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\nclass Net(nn.Module):\n"
            "    def __init__(self): self.named = nn.ModuleDict({'a': nn.Linear(1,1), **extra})\n"
        )})
        named = next(item for item in result.model.instances if item.name == "named")
        self.assertEqual(named.confidence, Confidence.PROBABLE)
        self.assertIn("MODEL_CONTAINER_DYNAMIC", {issue.code for issue in result.issues})

        base = analyze_files({
            "bad.py": "import torch.nn as nn\nclass Bad(nn.Module): pass\n",
            "good.py": "import torch.nn as nn\nclass Good(nn.Module): pass\n",
        })

        class FailingRule(PyTorchModelRule):
            def import_bindings(self, tree):
                if any(getattr(item, "name", None) == "Bad" for item in tree.body):
                    raise RuntimeError("synthetic rule failure")
                return super().import_bindings(tree)

        model = build_project_model(base.files, base.resolution, base.flow, FailingRule())
        self.assertEqual([item.qualified_name for item in model.definitions], ["good.Good"])
        self.assertIn("MODEL_ERROR", {issue.code for issue in model.issues})

    def test_constructor_rule_exception_keeps_other_file_models(self):
        base = analyze_files({
            "bad.py": "import torch.nn as nn\nclass Bad(nn.Module):\n    def __init__(self): self.x = nn.Linear(1,1)\n",
            "good.py": "import torch.nn as nn\nclass Good(nn.Module):\n    def __init__(self): self.y = nn.ReLU()\n",
        })

        class FailingConstructorRule(PyTorchModelRule):
            def match_constructor(self, expression, bindings):
                if getattr(expression, "attr", None) == "Linear":
                    raise RuntimeError("synthetic constructor failure")
                return super().match_constructor(expression, bindings)

        model = build_project_model(base.files, base.resolution, base.flow,
                                    FailingConstructorRule())
        self.assertEqual([item.name for item in model.instances], ["y"])
        self.assertIn("MODEL_ERROR", {issue.code for issue in model.issues})

    def test_indexed_and_looped_modulelist_invocations_keep_candidates(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\n"
            "class Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        self.layers = nn.ModuleList([nn.Linear(1,1), nn.ReLU()])\n"
            "    def forward(self, x, i):\n"
            "        x = self.layers[0](x)\n"
            "        x = self.layers[i](x)\n"
            "        for layer in self.layers: x = layer(x)\n"
            "        return x\n"
        )})
        instances = {item.name: item for item in result.model.instances}
        calls = {item.source_anchor.start_line: item for item in result.model.invocations}
        self.assertEqual(calls[6].instance_id, instances["layers[0]"].id)
        self.assertEqual(calls[7].instance_id, instances["layers"].id)
        self.assertEqual(set(calls[7].possible_instance_ids),
                         {instances["layers[0]"].id, instances["layers[1]"].id})
        self.assertEqual(set(calls[8].possible_instance_ids),
                         {instances["layers[0]"].id, instances["layers[1]"].id})
        self.assertEqual(calls[7].confidence, Confidence.PROBABLE)

    def test_imported_project_module_subclass_and_instance(self):
        result = analyze_files({
            "base.py": "from torch.nn import Module\nclass Base(Module): pass\n",
            "model.py": (
                "from base import Base\n"
                "class Net(Base):\n"
                "    def __init__(self): self.block = Base()\n"
                "    def forward(self, x): return self.block(x)\n"
            ),
        })
        definitions = {item.qualified_name: item for item in result.model.definitions}
        self.assertEqual(set(definitions), {"base.Base", "model.Net"})
        block = next(item for item in result.model.instances if item.name == "block")
        self.assertEqual(block.target_definition_id, definitions["base.Base"].symbol_id)
        self.assertTrue(any(item.instance_id == block.id for item in result.model.invocations))

    def test_unresolved_container_member_keeps_static_index_positions(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\nclass Net(nn.Module):\n"
            "    def __init__(self): self.layers = nn.ModuleList([unknown_value, nn.ReLU()])\n"
            "    def forward(self, x):\n"
            "        x = self.layers[0](x)\n"
            "        return self.layers[1](x)\n"
        )})
        layers = next(item for item in result.model.instances if item.name == "layers")
        relu = next(item for item in result.model.instances if item.name == "layers[1]")
        self.assertEqual(layers.member_instance_ids, (None, relu.id))
        calls = {item.source_anchor.start_line: item for item in result.model.invocations}
        self.assertEqual(calls[5].confidence, Confidence.UNRESOLVED)
        self.assertNotEqual(calls[5].instance_id, relu.id)
        self.assertEqual(calls[6].instance_id, relu.id)

    def test_shared_comprehension_never_claims_distinct_instances(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\nclass Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        self.left = nn.Linear(1, 1)\n"
            "        self.layers = nn.ModuleList([self.left for _ in range(3)])\n"
        )})
        by_name = {item.name: item for item in result.model.instances}
        self.assertEqual(by_name["layers"].repeat_count, 1)
        self.assertEqual(by_name["layers[*]"].repeat_count, 1)
        self.assertEqual(by_name["layers[*]"].shared_with_id, by_name["left"].id)

    def test_top_level_control_bindings_shadow_torch_alias(self):
        files = {
            "loop.py": "import torch.nn as nn\nfor nn in values: pass\nclass Fake(nn.Module): pass\n",
            "branch.py": "import torch.nn as nn\nif flag: nn = Fake()\nclass FakeModel(nn.Module): pass\n",
            "with.py": "import torch.nn as nn\nwith context as nn: pass\nclass Other(nn.Module): pass\n",
        }
        result = analyze_files(files)
        self.assertEqual(result.model.definitions, ())

    def test_container_rule_failure_does_not_leave_orphan_children(self):
        base = analyze_files({"model.py": (
            "import torch.nn as nn\nclass Net(nn.Module):\n"
            "    def __init__(self): self.layers = nn.ModuleList([nn.Linear(1,1), nn.ReLU()])\n"
        )})

        class FailingSecondMember(PyTorchModelRule):
            def match_constructor(self, expression, bindings):
                if getattr(expression, "attr", None) == "ReLU":
                    raise RuntimeError("second member failed")
                return super().match_constructor(expression, bindings)

        model = build_project_model(base.files, base.resolution, base.flow, FailingSecondMember())
        self.assertEqual(model.instances, ())
        self.assertIn("MODEL_ERROR", {issue.code for issue in model.issues})


if __name__ == "__main__":
    unittest.main()
