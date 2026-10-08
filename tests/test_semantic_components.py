"""Semantic graph keeps code provenance and static uncertainty."""

import unittest
from dataclasses import replace

from tests.test_training import analyze_files

from codeviz.analysis.model import ModelAnalysis
from codeviz.analysis.training import TrainingAnalysis
from codeviz.core import Confidence, EdgeType
from codeviz.semantic.components import build_semantic_analysis


def can_reach(edges: set[tuple[str, str]], source: str, target: str) -> bool:
    frontier = [source]
    visited: set[str] = set()
    while frontier:
        current = frontier.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        frontier.extend(next_id for start, next_id in edges if start == current)
    return False


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
            "import torch\n"
            "def train(loss, optimizer):\n"
            "    criterion = torch.nn.MSELoss()\n"
            "    loss.backward()\n"
            "    optimizer.step()\n"
        )})
        gradients = [item for item in result.semantic.graph.relations
                     if item.type == EdgeType.GRADIENT_FLOW]
        self.assertEqual(len(gradients), 1)
        self.assertEqual(gradients[0].confidence, Confidence.PROBABLE)
        self.assertIn("static", " ".join(gradients[0].evidence))

    def test_loop_back_uses_final_frontier_after_branch_join(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train(optimizer, flag):\n"
            "    criterion = torch.nn.MSELoss()\n"
            "    for epoch in range(2):\n"
            "        if flag:\n"
            "            optimizer.step()\n"
            "        optimizer.zero_grad()\n"
        )})
        events = {event.role.value: event.id for event in result.training.events}
        loop_id = events["EpochLoop"]
        clear_id = events["ClearGradients"]
        joins = {join.id for join in result.training.joins}
        back_edges = {(edge.source, edge.target)
                      for edge in result.semantic.graph.relations
                      if edge.type == EdgeType.LOOP_BACK}
        self.assertEqual(back_edges, {(clear_id, loop_id)})
        self.assertTrue(all((join_id, loop_id) not in back_edges for join_id in joins))

    def test_mutually_exclusive_if_branches_have_no_flow_or_gradient_edge(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train(loss, optimizer, flag):\n"
            "    criterion = torch.nn.MSELoss()\n"
            "    if flag:\n"
            "        loss.backward()\n"
            "    else:\n"
            "        optimizer.step()\n"
        )})
        events = {item.role.value: item for item in result.training.events}
        backward = events["BackwardPass"].id
        update = events["ParameterUpdate"].id
        self.assertFalse(any({relation.source, relation.target} == {backward, update}
                             and relation.type in (EdgeType.GRADIENT_FLOW,
                                                   EdgeType.CONTROL_FLOW)
                             for relation in result.semantic.graph.relations))

    def test_conditional_expression_branches_are_not_a_gradient_sequence(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train(loss, optimizer, flag):\n"
            "    criterion = torch.nn.MSELoss()\n"
            "    loss.backward() if flag else optimizer.step()\n"
        )})
        roles = {event.role.value: event.id for event in result.training.events}
        self.assertFalse(any({relation.source, relation.target} ==
                             {roles["BackwardPass"], roles["ParameterUpdate"]}
                             and relation.type in (EdgeType.CONTROL_FLOW,
                                                   EdgeType.GRADIENT_FLOW)
                             for relation in result.semantic.graph.relations))

    def test_both_if_branches_rejoin_following_event(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train(loss, optimizer, flag):\n"
            "    if flag:\n"
            "        loss.backward()\n"
            "    else:\n"
            "        optimizer.step()\n"
            "    torch.save({}, 'checkpoint.pt')\n"
        )})
        roles = {event.role.value: event.id for event in result.training.events}
        flow = {(relation.source, relation.target)
                for relation in result.semantic.graph.relations
                if relation.type == EdgeType.CONTROL_FLOW}
        self.assertTrue(can_reach(flow, roles["BackwardPass"], roles["CheckpointSave"]))
        self.assertTrue(can_reach(flow, roles["ParameterUpdate"], roles["CheckpointSave"]))

    def test_nested_optional_branch_keeps_bypass_to_join(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train(loss, optimizer, scheduler, outer, inner):\n"
            "    if outer:\n"
            "        loss.backward()\n"
            "        if inner: optimizer.step()\n"
            "    else:\n"
            "        scheduler.step()\n"
            "    torch.save({}, 'checkpoint.pt')\n"
        )})
        roles = {event.role.value: event.id for event in result.training.events}
        flow = {(relation.source, relation.target)
                for relation in result.semantic.graph.relations
                if relation.type == EdgeType.CONTROL_FLOW}
        self.assertTrue(can_reach(flow, roles["BackwardPass"], roles["CheckpointSave"]))
        self.assertTrue(can_reach(flow, roles["ParameterUpdate"], roles["CheckpointSave"]))
        self.assertTrue(can_reach(flow, roles["LRUpdate"], roles["CheckpointSave"]))

    def test_full_two_branch_coverage_has_no_direct_bypass(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train(model, loss, optimizer, flag):\n"
            "    model.train()\n"
            "    if flag: loss.backward()\n"
            "    else: optimizer.step()\n"
            "    torch.save({}, 'checkpoint.pt')\n"
        )})
        roles = {event.role.value: event.id for event in result.training.events}
        flow = {(relation.source, relation.target)
                for relation in result.semantic.graph.relations
                if relation.type == EdgeType.CONTROL_FLOW}
        self.assertNotIn((roles["TrainingMode"], roles["CheckpointSave"]), flow)

    def test_consecutive_complete_branches_do_not_leak_old_exits(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train(model, loss, optimizer, scheduler, first, second):\n"
            "    if first: loss.backward()\n"
            "    else: optimizer.step()\n"
            "    if second: model.train()\n"
            "    else: scheduler.step()\n"
            "    torch.save({}, 'checkpoint.pt')\n"
        )})
        roles = {event.role.value: event.id for event in result.training.events}
        flow = {(relation.source, relation.target)
                for relation in result.semantic.graph.relations
                if relation.type == EdgeType.CONTROL_FLOW}
        for role in ("BackwardPass", "ParameterUpdate"):
            self.assertNotIn((roles[role], roles["CheckpointSave"]), flow)
        for role in ("TrainingMode", "LRUpdate"):
            self.assertTrue(can_reach(flow, roles[role], roles["CheckpointSave"]))

    def test_many_optional_branches_keep_control_relation_count_linear(self):
        count = 80
        source = ("import torch\ndef train(optimizer, flags):\n"
                  "    criterion = torch.nn.MSELoss()\n") + "".join(
            f"    if flags[{index}]: optimizer.step()\n" for index in range(count)
        )
        result = analyze_files({"run.py": source})
        relations = [item for item in result.semantic.graph.relations
                     if item.type == EdgeType.CONTROL_FLOW]
        self.assertEqual(len(result.training.events), count)
        self.assertLessEqual(len(relations), count * 5)
        self.assertTrue(any(item.role == "ControlJoin"
                            for item in result.semantic.graph.components))

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

    def test_model_parent_lookup_does_not_probe_every_pair_of_instances(self):
        class CountedMembers(tuple):
            checks = 0

            def __contains__(self, value):
                CountedMembers.checks += 1
                return super().__contains__(value)

        assignments = "".join(f"        self.layer_{index} = nn.Linear(2, 2)\n"
                              for index in range(40))
        result = analyze_files({"model.py": (
            "import torch.nn as nn\nclass Net(nn.Module):\n"
            "    def __init__(self):\n" + assignments
        )})
        model = ModelAnalysis(result.model.definitions,
                              tuple(replace(item, member_instance_ids=CountedMembers(
                                  item.member_instance_ids)) for item in result.model.instances),
                              result.model.invocations, result.model.issues)
        graph = build_semantic_analysis(result.files, model, result.training).graph
        self.assertEqual(len([item for item in graph.components if item.role == "ModuleInstance"]), 40)
        self.assertLessEqual(CountedMembers.checks, 40)

    def test_event_parent_lookup_does_not_rescan_all_events(self):
        class CountedEvent:
            reads = 0

            def __init__(self, event):
                self.event = event

            def __getattr__(self, name):
                if name == "parent_loop_id":
                    CountedEvent.reads += 1
                return getattr(self.event, name)

        source = ("import torch\ndef train(optimizer):\n"
                  "    criterion = torch.nn.MSELoss()\n") + "    optimizer.step()\n" * 40
        result = analyze_files({"run.py": source})
        training = TrainingAnalysis(result.training.entry_points,
                                    tuple(CountedEvent(item) for item in result.training.events))
        graph = build_semantic_analysis(result.files, result.model, training).graph
        self.assertEqual(len([item for item in graph.components
                              if item.role == "ParameterUpdate"]), 40)
        self.assertLessEqual(CountedEvent.reads, 200)


if __name__ == "__main__":
    unittest.main()
