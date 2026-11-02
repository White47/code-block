"""Source-backed parameter sharing stays distinct from module aliases."""

import unittest

from tests.test_model import analyze_files

from codeviz.core import Confidence


class ModelParameterSharingTests(unittest.TestCase):
    def test_weight_assignment_between_registered_instances_is_recorded(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.embedding = nn.Embedding(8, 4)\n"
                "        self.head = nn.Linear(4, 8, bias=False)\n"
                "        self.embedding.weight = self.head.weight\n"
            ),
        })

        sharing, = analysis.model.parameter_sharings
        instances = {item.name: item for item in analysis.model.instances}
        owner = next(item for item in analysis.symbol_index.entries.values()
                     if item.qualified_name == "model.Net.__init__")
        definition = analysis.model.definitions[0]
        self.assertEqual(sharing.model_definition_id, definition.symbol_id)
        self.assertEqual(sharing.owner_symbol_id, owner.id)
        self.assertEqual(sharing.assigned_instance_id, instances["embedding"].id)
        self.assertEqual(sharing.assigned_attribute, "weight")
        self.assertEqual(sharing.source_instance_id, instances["head"].id)
        self.assertEqual(sharing.source_attribute, "weight")
        self.assertEqual(sharing.source_anchor.start_line, 6)
        self.assertIs(sharing.confidence, Confidence.EXACT)
        self.assertIsNone(instances["embedding"].shared_with_id)
        self.assertIsNone(instances["head"].shared_with_id)

    def test_module_alias_does_not_become_parameter_sharing(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.left = nn.Linear(2, 2)\n"
                "        self.right = self.left\n"
            ),
        })

        self.assertEqual(analysis.model.parameter_sharings, ())
        right = next(item for item in analysis.model.instances if item.name == "right")
        self.assertIsNotNone(right.shared_with_id)

    def test_cross_attribute_alias_is_an_unresolved_gap(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.left = nn.Linear(2, 2)\n"
                "        self.right = nn.Linear(2, 2)\n"
                "        self.left.bias = self.right.weight\n"
            ),
        })

        self.assertEqual(analysis.model.parameter_sharings, ())
        self.assertIn("MODEL_PARAMETER_SHARING_UNRESOLVED",
                      {item.code for item in analysis.model.issues})

    def test_disabled_or_conditional_parameter_aliases_are_gaps_not_facts(self):
        disabled = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.left = nn.Linear(2, 2, bias=False)\n"
                "        self.right = nn.Linear(2, 2, bias=False)\n"
                "        self.left.bias = self.right.bias\n"
            ),
        })
        conditional = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def __init__(self, flag):\n"
                "        self.left = nn.Linear(2, 2)\n"
                "        self.right = nn.Linear(2, 2)\n"
                "        if flag:\n"
                "            self.left.weight = self.right.weight\n"
            ),
        })

        self.assertEqual(disabled.model.parameter_sharings, ())
        self.assertEqual(conditional.model.parameter_sharings, ())
        self.assertIn("MODEL_PARAMETER_SHARING_UNRESOLVED",
                      {item.code for item in disabled.model.issues})
        self.assertIn("MODEL_PARAMETER_SHARING_UNRESOLVED",
                      {item.code for item in conditional.model.issues})


if __name__ == "__main__":
    unittest.main()
