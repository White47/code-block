"""Full code graph facts built from syntax and conservative resolution."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.analysis.call_cycles import find_call_cycles
from codeviz.analysis.flow import _FlowVisitor
from codeviz.analysis.project import analyze_project
from codeviz.core import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    Confidence,
    DataRole,
    EdgeType,
    EntityRole,
    NodeKind,
    SourceAnchor,
)


def analyze_files(files: dict[str, str]):
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        for relative, source in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        return analyze_project(root)


class FlowGraphTests(unittest.TestCase):
    def test_full_graph_contains_definitions_and_distinct_resolved_call_sites(self):
        result = analyze_files({"app.py": (
            "def helper(): return 1\n"
            "def run():\n"
            "    return helper() + helper()\n"
        )})
        self.assertIsNotNone(getattr(result, "flow", None))
        graph = result.flow.graph
        helper = next(node for node in graph.nodes.values()
                      if node.qualified_name == "app.helper" and node.role == EntityRole.DEFINITION)
        run = next(node for node in graph.nodes.values()
                   if node.qualified_name == "app.run" and node.role == EntityRole.DEFINITION)
        sites = [node for node in graph.nodes.values() if node.kind == NodeKind.CALL_SITE]
        self.assertEqual(len(sites), 2)
        self.assertEqual(len({site.id for site in sites}), 2)
        self.assertTrue(all(site.role == EntityRole.INVOCATION for site in sites))
        self.assertTrue(all(any(edge.type == EdgeType.CONTAINS and edge.target == site.id
                                and graph.nodes[edge.source].kind == NodeKind.FLOW_STEP
                                for edge in graph.edges) for site in sites))
        self.assertTrue(any(edge.type == EdgeType.CONTAINS and edge.source == run.id
                            and graph.nodes[edge.target].kind == NodeKind.FLOW_STEP
                            for edge in graph.edges))
        calls = [edge for edge in graph.edges if edge.type == EdgeType.CALLS]
        self.assertEqual({edge.source for edge in calls}, {site.id for site in sites})
        self.assertTrue(all(edge.target == helper.id and edge.confidence == Confidence.EXACT
                            and edge.source_anchor is not None for edge in calls))

    def test_ambiguous_external_and_dynamic_calls_never_claim_exact_target(self):
        result = analyze_files({"app.py": (
            "import torch\n"
            "if flag:\n"
            "    def choose(): return 1\n"
            "else:\n"
            "    def choose(): return 2\n"
            "def run(obj):\n"
            "    choose()\n"
            "    torch.randn(1)\n"
            "    obj.forward()\n"
        )})
        graph = result.flow.graph
        calls = {graph.nodes[edge.source].name: edge for edge in graph.edges
                 if edge.type == EdgeType.CALLS}
        self.assertEqual(calls["choose"].confidence, Confidence.PROBABLE)
        self.assertEqual(len(calls["choose"].possible_targets), 2)
        self.assertEqual(calls["randn"].confidence, Confidence.PROBABLE)
        self.assertEqual(graph.nodes[calls["randn"].target].kind, NodeKind.EXTERNAL_SYMBOL)
        self.assertEqual(calls["forward"].confidence, Confidence.UNRESOLVED)
        self.assertEqual(graph.nodes[calls["forward"].target].kind, NodeKind.UNRESOLVED_SYMBOL)
        self.assertFalse(any(edge.confidence == Confidence.EXACT for edge in calls.values()))

    def test_nested_branch_loop_and_return_have_distinct_control_edges(self):
        result = analyze_files({"app.py": (
            "def run(items):\n"
            "    total = 0\n"
            "    for item in items:\n"
            "        if item:\n"
            "            total = total + item\n"
            "        else:\n"
            "            continue\n"
            "    return total\n"
        )})
        graph = result.flow.graph
        self.assertTrue(any(node.kind == NodeKind.CONTROL_BLOCK for node in graph.nodes.values()))
        loop = next(node for node in graph.nodes.values()
                    if node.kind == NodeKind.CONTROL_BLOCK and node.name == "For")
        branch = next(node for node in graph.nodes.values()
                      if node.kind == NodeKind.CONTROL_BLOCK and node.name == "If")
        initial = next(node for node in graph.nodes.values()
                       if node.kind == NodeKind.FLOW_STEP and node.source_anchors[0].start_line == 2)
        true_step = next(node for node in graph.nodes.values()
                         if node.kind == NodeKind.FLOW_STEP and node.source_anchors[0].start_line == 5)
        continue_step = next(node for node in graph.nodes.values()
                             if node.kind == NodeKind.FLOW_STEP and node.name == "Continue")
        returned = next(node for node in graph.nodes.values()
                        if node.kind == NodeKind.FLOW_STEP and node.name == "Return")
        edges = {(edge.source, edge.target, edge.type) for edge in graph.edges}
        self.assertIn((initial.id, loop.id, EdgeType.CONTROL_FLOW), edges)
        self.assertIn((loop.id, branch.id, EdgeType.BRANCH_TRUE), edges)
        self.assertIn((loop.id, returned.id, EdgeType.BRANCH_FALSE), edges)
        self.assertIn((branch.id, true_step.id, EdgeType.BRANCH_TRUE), edges)
        self.assertIn((branch.id, continue_step.id, EdgeType.BRANCH_FALSE), edges)
        self.assertIn((true_step.id, loop.id, EdgeType.LOOP_BACK), edges)
        self.assertIn((continue_step.id, loop.id, EdgeType.LOOP_BACK), edges)
        self.assertFalse(any(edge.type == EdgeType.CONTROL_FLOW and edge.source == returned.id
                             for edge in graph.edges))

    def test_critical_data_has_static_read_write_flow_but_tmp_stays_generic(self):
        result = analyze_files({"train.py": (
            "def train(input, target):\n"
            "    prediction = input\n"
            "    tmp = 1\n"
            "    loss = prediction + target\n"
            "    return loss\n"
        )})
        self.assertEqual({node.name: node.role for node in result.flow.data_nodes}, {
            "input": DataRole.INPUT, "target": DataRole.TARGET,
            "prediction": DataRole.PREDICTION, "loss": DataRole.LOSS,
        })
        self.assertTrue(all(node.confidence == Confidence.PROBABLE
                            for node in result.flow.data_nodes))
        graph = result.flow.graph
        data = {node.name: node for node in graph.nodes.values() if node.kind == NodeKind.DATA}
        self.assertNotIn("tmp", data)
        edges = {(edge.source, edge.target, edge.type) for edge in graph.edges}
        self.assertIn((data["input"].id, data["prediction"].id, EdgeType.DATA_FLOW), edges)
        self.assertIn((data["prediction"].id, data["loss"].id, EdgeType.DATA_FLOW), edges)
        self.assertIn((data["target"].id, data["loss"].id, EdgeType.DATA_FLOW), edges)
        assignment = next(node for node in graph.nodes.values()
                          if node.kind == NodeKind.FLOW_STEP and node.source_anchors[0].start_line == 4)
        returned = next(node for node in graph.nodes.values()
                        if node.kind == NodeKind.FLOW_STEP and node.name == "Return")
        self.assertIn((assignment.id, data["prediction"].id, EdgeType.READS), edges)
        self.assertIn((assignment.id, data["loss"].id, EdgeType.WRITES), edges)
        self.assertIn((returned.id, data["loss"].id, EdgeType.READS), edges)
        self.assertFalse(any(edge.type == EdgeType.GRADIENT_FLOW for edge in graph.edges))

    def test_recursive_and_mutual_call_cycles_are_finite_groups(self):
        result = analyze_files({"cycle.py": (
            "def first(): return second()\n"
            "def second(): return first()\n"
            "def recur(): return recur()\n"
        )})
        graph = result.flow.graph
        names = [{graph.nodes[node_id].name for node_id in cycle}
                 for cycle in result.flow.call_cycles]
        self.assertIn({"first", "second"}, names)
        self.assertIn({"recur"}, names)
        self.assertEqual(len(names), 2)

    def test_calling_a_parameter_is_not_an_exact_function_call(self):
        result = analyze_files({"dynamic.py": "def run(fn):\n    return fn()\n"})
        graph = result.flow.graph
        calls = [edge for edge in graph.edges if edge.type == EdgeType.CALLS]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].confidence, Confidence.UNRESOLVED)
        self.assertEqual(graph.nodes[calls[0].target].kind, NodeKind.UNRESOLVED_SYMBOL)
        self.assertIn("DYNAMIC_CALL_UNRESOLVED", {issue.code for issue in result.issues})

    def test_one_file_flow_failure_preserves_other_file_graph(self):
        original_visit = _FlowVisitor.visit

        def fail_one_file(visitor, node):
            if visitor.parsed.relative_path == "bad.py":
                raise RecursionError("synthetic deep tree")
            return original_visit(visitor, node)

        with patch.object(_FlowVisitor, "visit", fail_one_file):
            result = analyze_files({
                "bad.py": "def ignored(): return 1\n",
                "good.py": "def helper(): return 1\nresult = helper()\n",
            })
        self.assertIsNotNone(result.flow)
        self.assertIn("FLOW_ERROR", {issue.code for issue in result.issues})
        self.assertTrue(any(node.kind == NodeKind.CALL_SITE and node.file_path == "good.py"
                            for node in result.flow.graph.nodes.values()))

    def test_import_alias_named_loss_is_not_a_loss_data_node(self):
        result = analyze_files({
            "helper.py": "def loss(): return 1\n",
            "train.py": "from helper import loss\nvalue = loss()\n",
        })
        self.assertFalse(any(node.name == "loss" and node.source_anchor.file_path == "train.py"
                             for node in result.flow.data_nodes))

    def test_cross_file_call_is_traceable_and_full_graph_round_trips(self):
        result = analyze_files({
            "pkg/__init__.py": "",
            "pkg/helper.py": "def calculate(): return 1\n",
            "app.py": "from pkg.helper import calculate\nvalue = calculate()\n",
        })
        graph = result.flow.graph
        call = next(edge for edge in graph.edges if edge.type == EdgeType.CALLS)
        self.assertEqual(graph.nodes[call.target].qualified_name, "pkg.helper.calculate")
        self.assertEqual(call.source_anchor.file_path, "app.py")
        self.assertEqual(call.source_anchor.start_line, 2)
        restored = CodeGraph.from_dict(json.loads(json.dumps(graph.to_dict())))
        self.assertEqual(len(restored.nodes), len(graph.nodes))
        self.assertEqual(len(restored.edges), len(graph.edges))

    def test_analysis_never_executes_analyzed_module(self):
        result = analyze_files({"danger.py": (
            "raise RuntimeError('must never execute')\n"
            "def helper(): return 1\n"
        )})
        self.assertTrue(any(node.kind == NodeKind.FLOW_STEP and node.name == "Raise"
                            for node in result.flow.graph.nodes.values()))

    def test_self_method_call_is_not_exact_when_subclass_can_override(self):
        result = analyze_files({"inherit.py": (
            "class Base:\n"
            "    def f(self): return 1\n"
            "    def run(self): return self.f()\n"
            "class Sub(Base):\n"
            "    def f(self): return 2\n"
        )})
        graph = result.flow.graph
        call = next(edge for edge in graph.edges if edge.type == EdgeType.CALLS
                    and graph.nodes[edge.source].name == "f")
        self.assertNotEqual(call.confidence, Confidence.EXACT)
        self.assertIn("DYNAMIC_DISPATCH_UNRESOLVED", {issue.code for issue in result.issues})

    def test_long_acyclic_call_chain_does_not_exhaust_python_stack(self):
        graph = CodeGraph()
        for number in range(1100):
            graph.add_node(CodeNode(
                id=f"fn{number}", kind=NodeKind.FUNCTION, name=f"fn{number}",
                qualified_name=f"chain.fn{number}", scope="chain", language="python",
                source_anchors=(SourceAnchor("chain.py", number + 1),),
            ))
        for number in range(1099):
            site_id = f"site{number}"
            graph.add_node(CodeNode(
                id=site_id, kind=NodeKind.CALL_SITE, name="call",
                qualified_name=f"chain.site{number}", scope="chain", language="python",
                source_anchors=(SourceAnchor("chain.py", number + 1),),
                metadata={"scope_symbol_id": f"fn{number}"},
            ))
            graph.add_edge(CodeEdge(f"fn{number}", site_id, EdgeType.CONTAINS))
            graph.add_edge(CodeEdge(site_id, f"fn{number + 1}", EdgeType.CALLS))
        self.assertEqual(find_call_cycles(graph), ())

    def test_branch_call_sites_are_contained_by_their_own_flow_steps(self):
        result = analyze_files({"branch.py": (
            "def a(): pass\n"
            "def b(): pass\n"
            "def run(flag):\n"
            "    if flag:\n"
            "        a()\n"
            "    else:\n"
            "        b()\n"
        )})
        graph = result.flow.graph
        containment = {(edge.source, edge.target) for edge in graph.edges
                       if edge.type == EdgeType.CONTAINS}
        for line, name in ((5, "a"), (7, "b")):
            step = next(node for node in graph.nodes.values()
                        if node.kind == NodeKind.FLOW_STEP
                        and node.source_anchors[0].start_line == line)
            site = next(node for node in graph.nodes.values()
                        if node.kind == NodeKind.CALL_SITE and node.name == name)
            self.assertIn((step.id, site.id), containment)

    def test_with_and_try_do_not_hide_nested_control_and_data_steps(self):
        result = analyze_files({"blocks.py": (
            "def run(input):\n"
            "    with guard():\n"
            "        if input:\n"
            "            prediction = input\n"
            "    try:\n"
            "        loss = prediction\n"
            "    except Exception:\n"
            "        loss = input\n"
            "    return loss\n"
        )})
        graph = result.flow.graph
        by_line = {node.source_anchors[0].start_line: node for node in graph.nodes.values()
                   if node.kind in (NodeKind.FLOW_STEP, NodeKind.CONTROL_BLOCK)}
        self.assertIn(3, by_line)
        self.assertIn(4, by_line)
        self.assertIn(6, by_line)
        self.assertIn(8, by_line)
        edges = {(edge.source, edge.target, edge.type) for edge in graph.edges}
        self.assertIn((by_line[2].id, by_line[3].id, EdgeType.CONTROL_FLOW), edges)
        self.assertIn((by_line[5].id, by_line[6].id, EdgeType.BRANCH_TRUE), edges)
        self.assertIn((by_line[5].id, by_line[8].id, EdgeType.BRANCH_FALSE), edges)
        loss = next(node for node in graph.nodes.values()
                    if node.kind == NodeKind.DATA and node.name == "loss")
        self.assertIn((by_line[6].id, loss.id, EdgeType.WRITES), edges)

    def test_definition_time_calls_belong_to_enclosing_scope(self):
        result = analyze_files({"define.py": (
            "def mark(): return lambda item: item\n"
            "def factory(): return 1\n"
            "@mark()\n"
            "def target(arg=factory()): return arg\n"
            "class Child(factory()): pass\n"
        )})
        graph = result.flow.graph
        sites = [node for node in graph.nodes.values() if node.kind == NodeKind.CALL_SITE]
        self.assertEqual({site.source_anchors[0].start_line for site in sites}, {3, 4, 5})
        self.assertTrue(all(site.scope == "define" for site in sites))
        self.assertEqual(len([edge for edge in graph.edges if edge.type == EdgeType.CALLS]), 3)

    def test_self_attribute_critical_data_has_read_and_write_evidence(self):
        result = analyze_files({"trainer.py": (
            "class Trainer:\n"
            "    def run(self, input):\n"
            "        self.loss = input\n"
            "        return self.loss\n"
        )})
        graph = result.flow.graph
        loss = next(node for node in graph.nodes.values()
                    if node.kind == NodeKind.DATA and node.name == "loss")
        write_step = next(node for node in graph.nodes.values()
                          if node.kind == NodeKind.FLOW_STEP
                          and node.source_anchors[0].start_line == 3)
        read_step = next(node for node in graph.nodes.values()
                         if node.kind == NodeKind.FLOW_STEP
                         and node.source_anchors[0].start_line == 4)
        edges = {(edge.source, edge.target, edge.type) for edge in graph.edges}
        self.assertIn((write_step.id, loss.id, EdgeType.WRITES), edges)
        self.assertIn((read_step.id, loss.id, EdgeType.READS), edges)

    def test_hidden_temporary_preserves_critical_data_dependency(self):
        result = analyze_files({"chain.py": (
            "def run(input):\n"
            "    tmp = input\n"
            "    prediction = tmp\n"
        )})
        graph = result.flow.graph
        data = {node.name: node for node in graph.nodes.values() if node.kind == NodeKind.DATA}
        self.assertNotIn("tmp", data)
        self.assertTrue(any(edge.type == EdgeType.DATA_FLOW
                            and edge.source == data["input"].id
                            and edge.target == data["prediction"].id
                            for edge in graph.edges))

    def test_overwritten_temporary_does_not_retain_old_critical_origin(self):
        result = analyze_files({"chain.py": (
            "def run(input):\n"
            "    tmp = input\n"
            "    tmp = 0\n"
            "    prediction = tmp\n"
        )})
        graph = result.flow.graph
        data = {node.name: node for node in graph.nodes.values() if node.kind == NodeKind.DATA}
        self.assertFalse(any(edge.type == EdgeType.DATA_FLOW
                             and edge.source == data["input"].id
                             and edge.target == data["prediction"].id
                             for edge in graph.edges))


if __name__ == "__main__":
    unittest.main()
