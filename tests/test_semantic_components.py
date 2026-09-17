"""Semantic graph keeps code provenance and static uncertainty."""

import unittest

from tests.test_training import analyze_files

from codeviz.core import Confidence, EdgeType


class SemanticComponentTests(unittest.TestCase):
    def test_model_instances_and_entries_are_distinct_source_backed_components(self):
        result = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.left = nn.Linear(2, 2)\n"
                "        self.right = self.left\n"
                "    def forward(self, x): return self.left(x)\n"
            ),
            "run.py": (
                "from model import Net\n"
                "def train(optimizer):\n"
                "    model = Net()\n"
                "    model.train()\n"
                "    for epoch in range(2):\n"
                "        for batch in loader:\n"
                "            out = model(batch)\n"
                "            optimizer.step()\n"
                "def evaluate():\n"
                "    model = Net()\n"
                "    model.eval()\n"
            ),
        })
        graph = result.semantic.graph
        components = {item.id: item for item in graph.components}
        definition = result.model.definitions[0]
        self.assertIn(definition.symbol_id, graph.roots)
        self.assertEqual(components[definition.symbol_id].code_symbol_ids,
                         (definition.symbol_id,))
        self.assertEqual(len([item for item in graph.components if item.role == "ModelDefinition"]), 1)
        for instance in result.model.instances:
            component = components[instance.id]
            self.assertEqual(component.role, "ModuleInstance")
            self.assertEqual(component.metadata["shared_with_id"], instance.shared_with_id)
            self.assertEqual(component.source_anchors, (instance.source_anchor,))
        for entry in result.training.entry_points:
            self.assertIn(entry.id, graph.roots)
            self.assertEqual(components[entry.id].role, "EntryPoint")
        for event in result.training.events:
            self.assertIn(event.id, components)
            self.assertIn(event.source_anchor, components[event.id].source_anchors)
        self.assertTrue(all(item.evidence and item.source_anchors for item in graph.relations))
        self.assertTrue(any(item.type == EdgeType.CONTAINS and item.source == definition.symbol_id
                            for item in graph.relations))
        self.assertTrue(any(item.type == EdgeType.LOOP_BACK and item.confidence == Confidence.PROBABLE
                            for item in graph.relations))
        self.assertEqual(graph, analyze_files({
            "model.py": (
                "import torch.nn as nn\nclass Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.left = nn.Linear(2, 2)\n        self.right = self.left\n"
                "    def forward(self, x): return self.left(x)\n"
            ),
            "run.py": (
                "from model import Net\ndef train(optimizer):\n    model = Net()\n"
                "    model.train()\n    for epoch in range(2):\n"
                "        for batch in loader:\n            out = model(batch)\n"
                "            optimizer.step()\ndef evaluate():\n    model = Net()\n"
                "    model.eval()\n"
            ),
        }).semantic.graph)

    def test_gradient_relation_is_only_a_probable_static_pattern(self):
        result = analyze_files({"run.py": (
            "def train(loss, optimizer):\n"
            "    loss.backward()\n"
            "    optimizer.step()\n"
        )})
        gradients = [item for item in result.semantic.graph.relations
                     if item.type == EdgeType.GRADIENT_FLOW]
        self.assertEqual(len(gradients), 1)
        self.assertEqual(gradients[0].confidence, Confidence.PROBABLE)
        self.assertIn("static", " ".join(gradients[0].evidence))

    def test_attention_candidate_needs_qkv_score_and_softmax(self):
        result = analyze_files({"model.py": (
            "import torch\nimport torch.nn as nn\n"
            "class TokenMixer(nn.Module):\n"
            "    def forward(self, x):\n"
            "        q = self.query(x)\n        k = self.key(x)\n"
            "        v = self.value(x)\n"
            "        score = q @ k.transpose(-2, -1)\n"
            "        weight = torch.softmax(score, dim=-1)\n"
            "        return weight @ v\n"
            "class Projection(nn.Module):\n"
            "    def forward(self, x):\n"
            "        q = self.query(x)\n        k = self.key(x)\n"
            "        v = self.value(x)\n        return q + k + v\n"
            "class Distractor(nn.Module):\n"
            "    def forward(self, x):\n"
            "        q = self.query(x)\n        k = self.key(x)\n"
            "        v = self.value(x)\n"
            "        score = x @ x\n"
            "        weight = torch.softmax(score, dim=-1)\n"
            "        return weight + q + k + v\n"
        )})
        candidates = [item for item in result.semantic.graph.components
                      if item.role == "AttentionLikeCandidate"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].code_name, "TokenMixer")
        self.assertEqual(candidates[0].confidence, Confidence.PROBABLE)
        self.assertTrue(candidates[0].evidence)

    def test_redefined_attention_class_does_not_crash_semantic_graph(self):
        attention_class = (
            "class Mixer(nn.Module):\n"
            "    def forward(self, x):\n"
            "        q = self.query(x)\n        k = self.key(x)\n"
            "        v = self.value(x)\n"
            "        score = q @ k.transpose(-2, -1)\n"
            "        weight = torch.softmax(score, dim=-1)\n"
            "        return weight @ v\n"
        )
        result = analyze_files({"model.py": "import torch\nimport torch.nn as nn\n"
                                + attention_class + attention_class})
        self.assertEqual(len([item for item in result.semantic.graph.components
                              if item.role == "AttentionLikeCandidate"]), 2)


if __name__ == "__main__":
    unittest.main()
