"""Reaching definitions are lexical static facts, independent of value analysis."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from tests.test_training import analyze_files

from codeviz.analysis.def_use import MAX_SCOPE_WORK, _DefUse
from codeviz.core import CodeGraph, EdgeType, NodeKind, ViewKind
from codeviz.navigation import plan_reveal
from codeviz.search import search_project
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.views import ViewRequest, build_visible_graph
from codeviz.views.data import critical_data_slice


class GenericDefUseTests(unittest.TestCase):
    def analyze(self, body):
        result = analyze_files({"app.py": body})
        self.assertFalse([issue for issue in result.issues if issue.code.endswith("_ERROR")])
        return result

    def read(self, result, line, name):
        graph = result.flow.graph
        matches = [edge for edge in graph.edges
                   if edge.type == EdgeType.READS and edge.metadata.get("generic_data")
                   and edge.source_anchor.start_line == line
                   and graph.nodes[edge.target].name == name]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def origins(self, result, edge):
        return {result.flow.graph.nodes[node].source_anchors[0].start_line
                for node in edge.metadata["reaching_definition_ids"]}

    def test_sequential_assignment_kills_previous_definition(self):
        result = self.analyze("def f(left, right):\n    item = left\n    item = right\n    return item\n")
        self.assertEqual(self.origins(result, self.read(result, 4, "item")), {3})

    def test_branch_union_and_missing_branch_unbound(self):
        result = self.analyze("def f(flag, left, right):\n    if flag:\n        item = left\n    else:\n        item = right\n    return item\n")
        self.assertEqual(self.origins(result, self.read(result, 6, "item")), {3, 5})
        result = self.analyze("def f(flag, raw):\n    if flag:\n        item = raw\n    return item\n")
        self.assertTrue(self.read(result, 4, "item").metadata["may_unbound"])

    def test_terminated_branch_does_not_reach_normal_successor(self):
        result = self.analyze("def f(flag, raw):\n    item = raw\n    if flag:\n        item = 0\n        return item\n    return item\n")
        self.assertEqual(self.origins(result, self.read(result, 6, "item")), {2})

    def test_loop_retains_zero_and_iteration_origins(self):
        result = self.analyze("def f(rows, raw):\n    item = raw\n    for row in rows:\n        item = row\n    return item\n")
        self.assertEqual(self.origins(result, self.read(result, 5, "item")), {2, 4})

    def test_annotation_without_value_is_not_a_definition(self):
        result = self.analyze("def f():\n    item: int\n    return item\n")
        edge = self.read(result, 3, "item")
        self.assertEqual(edge.metadata["reaching_definition_ids"], [])
        self.assertTrue(edge.metadata["may_unbound"])

    def test_deferred_lambda_body_does_not_read_at_creation(self):
        result = self.analyze("def f(raw):\n    thunk = lambda: raw\n    return thunk\n")
        graph = result.flow.graph
        self.assertFalse([edge for edge in graph.edges if edge.type == EdgeType.READS
                          and edge.metadata.get("generic_data")
                          and edge.source_anchor.start_line == 2
                          and graph.nodes[edge.target].name == "raw"])
        self.assertTrue(any(node.metadata.get("generic_data") for node in graph.nodes.values()))

    def test_defaults_and_generator_first_iter_are_creation_reads(self):
        result = self.analyze("def f(rows, raw):\n    thunk = lambda value=raw: value\n    stream = (raw for row in rows)\n    return stream\n")
        self.read(result, 2, "raw")
        self.read(result, 3, "rows")
        self.assertFalse([edge for edge in result.flow.graph.edges
                          if edge.type == EdgeType.READS and edge.metadata.get("generic_data")
                          and edge.source_anchor.start_line == 3
                          and result.flow.graph.nodes[edge.target].name == "raw"])

    def test_generic_nodes_do_not_enter_critical_slice_or_returned_list(self):
        result = self.analyze("def train(raw):\n    rows = raw\n    loss = rows\n    return loss\n")
        root = next(entry.id for entry in result.symbol_index.entries.values()
                    if entry.qualified_name == "app.train")
        selected = critical_data_slice(result, (root,), ViewKind.EXECUTION)
        self.assertEqual({node.name for node in selected.nodes}, {"loss"})
        self.assertEqual({node.name for node in result.flow.data_nodes}, {"loss"})
        self.assertTrue(any(node.metadata.get("generic_data") for node in result.flow.graph.nodes.values()))

    def test_navigation_cached_graph_and_budgets(self):
        result = self.analyze("def f(path):\n    rows = path\n    result = rows\n    return result\n")
        selected = next(item for item in search_project(result, "rows").results
                        if item.source_kind == "symbol" and item.name == "rows")
        self.assertEqual(plan_reveal(result, selected).view_kind, ViewKind.DATA_FLOW)
        graph = CodeGraph.from_dict(result.flow.graph.to_dict())
        cached = replace(result, flow=replace(result.flow, graph=graph),
                         files=tuple(replace(file, tree=None) for file in result.files))
        view = build_visible_graph(cached, ViewRequest(ViewKind.DATA_FLOW,
                                   selected_id=selected.id, hops=5, max_nodes=3, max_edges=2))
        self.assertLessEqual(len(view.nodes), 3)
        self.assertLessEqual(len(view.edges), 2)
        self.assertTrue(view.edges)

    def test_no_heap_write_claim(self):
        result = self.analyze("def f(obj, rows):\n    obj.value = rows\n    rows[0] = obj\n    return rows\n")
        writes = [edge for edge in result.flow.graph.edges
                  if edge.type == EdgeType.WRITES and edge.metadata.get("generic_data")]
        self.assertEqual(writes, [])
        self.assertTrue(any(node.kind == NodeKind.DATA and node.metadata.get("generic_data")
                            for node in result.flow.graph.nodes.values()))

    def test_eager_comprehension_walrus_retains_empty_iteration_state(self):
        result = self.analyze("def f(rows, raw):\n    item = raw\n    collected = [(item := row) for row in rows]\n    return item\n")
        self.assertEqual(self.origins(result, self.read(result, 4, "item")), {2, 3})
        result = self.analyze("def f(rows):\n    collected = [(item := row) for row in rows]\n    return item\n")
        self.assertTrue(self.read(result, 3, "item").metadata["may_unbound"])

    def test_loop_break_bypasses_else_and_terminating_path_stays_excluded(self):
        result = self.analyze("def f(rows, raw):\n    item = raw\n    for row in rows:\n        item = row\n        break\n    else:\n        item = 0\n    return item\n")
        self.assertEqual(self.origins(result, self.read(result, 8, "item")), {4, 7})

    def test_deleted_binding_is_unbound(self):
        result = self.analyze("def f(raw):\n    item = raw\n    del item\n    return item\n")
        self.assertTrue(self.read(result, 4, "item").metadata["may_unbound"])
        self.assertEqual(self.origins(result, self.read(result, 4, "item")), set())

    def test_function_defaults_read_parent_but_body_is_separate(self):
        result = self.analyze("def f(raw):\n    item = raw\n    def inner(value=item):\n        item = 0\n        return value\n    return item\n")
        self.assertEqual(self.origins(result, self.read(result, 3, "item")), {2})
        self.assertEqual(self.origins(result, self.read(result, 6, "item")), {2})

    def test_occurrence_ids_ignore_blank_lines_and_preserve_critical_display(self):
        source = "def f(input):\n    loss = input\n    return loss\n"
        first = self.analyze(source)
        second = self.analyze("\n\n" + source)
        for result in (first, second):
            graph = result.flow.graph
            critical = next(node for node in result.flow.data_nodes if node.name == "loss")
            self.assertEqual(graph.nodes[critical.id].name, "loss")
            self.assertEqual(len([node for node in graph.nodes.values()
                                  if node.kind == NodeKind.DATA and node.name == "loss"]), 1)
        self.assertEqual({node.id for node in first.flow.graph.nodes.values()
                          if node.metadata.get("generic_data")},
                         {node.id for node in second.flow.graph.nodes.values()
                          if node.metadata.get("generic_data")})

    def test_short_circuit_and_conditional_walrus_keep_possible_definitions(self):
        result = self.analyze("def f(flag, left, right):\n    item = left\n    flag and (item := right)\n    return item\n")
        self.assertEqual(self.origins(result, self.read(result, 4, "item")), {2, 3})
        result = self.analyze("def f(flag, left, right):\n    result = (item := left) if flag else (item := right)\n    return item\n")
        edge = self.read(result, 3, "item")
        self.assertEqual(len(edge.metadata["reaching_definition_ids"]), 2)
        self.assertFalse(edge.metadata["may_unbound"])

    def test_while_test_assignment_runs_even_for_zero_iterations(self):
        result = self.analyze("def f(raw):\n    while (item := raw):\n        pass\n    return item\n")
        self.assertFalse(self.read(result, 4, "item").metadata["may_unbound"])

    def test_exception_handler_includes_writes_before_a_terminating_raise(self):
        result = self.analyze("def f(raw):\n    item = 0\n    try:\n        item = raw\n        raise ValueError()\n    except ValueError:\n        return item\n")
        self.assertIn(4, self.origins(result, self.read(result, 7, "item")))

    def test_finally_reads_include_returning_branch_without_leaking_after_try(self):
        result = self.analyze("def f(flag, raw):\n    item = raw\n    try:\n        if flag:\n            item = 0\n            return item\n    finally:\n        print(item)\n    return item\n")
        self.assertEqual(self.origins(result, self.read(result, 8, "item")), {2, 5})
        self.assertEqual(self.origins(result, self.read(result, 9, "item")), {2})

    def test_critical_read_sites_are_enriched_without_duplicate_edges(self):
        result = self.analyze("def f(inputs):\n    thunk = lambda value=inputs: value\n    return inputs\n")
        reads = [edge for edge in result.flow.graph.edges
                 if edge.type == EdgeType.READS and edge.source_anchor.start_line == 2]
        self.assertEqual(len(reads), 1)
        self.assertEqual(len(reads[0].metadata["reaching_definition_ids"]), 1)

    def test_invalid_nonlocal_write_does_not_fall_back_to_module(self):
        result = self.analyze("item = 0\ndef outer():\n    def inner():\n        nonlocal item\n        result = [(item := row) for row in []]\n        return item\n")
        writes = [edge for edge in result.flow.graph.edges
                  if edge.type == EdgeType.WRITES and edge.metadata.get("generic_data")
                  and edge.source_anchor.start_line == 5
                  and result.flow.graph.nodes[edge.target].name == "item"]
        self.assertEqual(writes, [])

    def test_finally_write_reaches_loop_break_successor(self):
        result = self.analyze("def f(rows, raw):\n    item = raw\n    for row in rows:\n        try:\n            item = row\n            break\n        finally:\n            item = 0\n    return item\n")
        self.assertEqual(self.origins(result, self.read(result, 9, "item")), {2, 8})

    def test_upstream_projection_excludes_killed_origin_in_cached_graph(self):
        result = self.analyze("def f(left, right):\n    item = left\n    item = right\n    result = item\n    return result\n")
        selected = next(entry for entry in result.symbol_index.entries.values()
                        if entry.qualified_name == "app.f.result")
        cached = replace(result, flow=replace(result.flow, graph=CodeGraph.from_dict(
                         result.flow.graph.to_dict())),
                         files=tuple(replace(file, tree=None) for file in result.files))
        for analysis in (result, cached):
            view = build_visible_graph(analysis, ViewRequest(ViewKind.DATA_FLOW,
                                       selected_id=selected.id, direction="upstream", hops=5))
            names = {node.display_name for node in view.nodes}
            self.assertIn("right", names)
            self.assertNotIn("left", names)
            self.assertNotIn(2, {anchor.start_line for node in view.nodes
                                 for anchor in node.source_anchors})

    def test_recursive_delete_targets_kill_reaching_definitions(self):
        for target in ("(item,)", "[item]", "(item, [other])"):
            result = self.analyze(f"def f(raw):\n    item = raw\n    other = raw\n    del {target}\n    return item\n")
            edge = self.read(result, 5, "item")
            self.assertTrue(edge.metadata["may_unbound"])
            self.assertEqual(edge.metadata["reaching_definition_ids"], [])

    def test_match_and_exception_bindings_have_explicit_source_backed_partial(self):
        sources = (
            "def f(source):\n    match source:\n        case captured:\n            return captured\n",
            "def f():\n    try:\n        raise ValueError()\n    except ValueError as error:\n        return error\n",
        )
        for source in sources:
            result = self.analyze(source)
            issues = [issue for issue in result.issues if issue.code == "GENERIC_DATA_BINDING_PARTIAL"]
            self.assertTrue(issues)
            self.assertEqual(AppController._result_status(result), AnalysisStatus.PARTIAL)
            self.assertTrue(all(issue.source_anchor and issue.source_anchor.file_path == "app.py"
                                for issue in issues))
            names = ("captured", "error")
            reads = [edge for edge in result.flow.graph.edges if edge.type == EdgeType.READS
                     and edge.metadata.get("generic_data")
                     and result.flow.graph.nodes[edge.target].name in names]
            self.assertTrue(reads)
            self.assertTrue(all(edge.metadata["unknown"] for edge in reads))

    def test_project_call_reports_no_cross_function_propagation(self):
        result = self.analyze("def identity(value):\n    return value\ndef main(source):\n    out = identity(source)\n    return out\n")
        issues = [issue for issue in result.issues if issue.code == "GENERIC_DATA_CALL_PARTIAL"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].source_anchor.start_line, 4)
        self.assertIn("interprocedural", issues[0].message)
        self.assertTrue(self.read(result, 5, "out").metadata["unknown"])
        self.assertEqual(AppController._result_status(result), AnalysisStatus.PARTIAL)

    def test_reaching_definition_sets_have_budget_unknown_and_diagnostic(self):
        source = "def f(flag, raw):\n    item = raw\n" + "".join(
            f"    if flag == {position}:\n        item = {position}\n" for position in range(40)
        ) + "    return item\n"
        result = self.analyze(source)
        edge = self.read(result, 83, "item")
        self.assertLessEqual(len(edge.metadata["reaching_definition_ids"]), 32)
        self.assertTrue(edge.metadata["truncated"])
        self.assertTrue(edge.metadata["unknown"])
        issues = [issue for issue in result.issues if issue.code == "GENERIC_DATA_BUDGET_PARTIAL"]
        self.assertTrue(issues)
        self.assertTrue(all(issue.source_anchor and issue.source_anchor.file_path == "app.py"
                            for issue in issues))
        self.assertEqual(AppController._result_status(result), AnalysisStatus.PARTIAL)

    def test_call_result_unknown_reaches_named_expression_binding(self):
        result = self.analyze("def identity(value):\n    return value\ndef main(source):\n    if (out := identity(source)):\n        pass\n    return out\n")
        edge = self.read(result, 6, "out")
        self.assertTrue(edge.metadata["reaching_definition_ids"])
        self.assertTrue(edge.metadata["unknown"])
        self.assertTrue(any(issue.code == "GENERIC_DATA_CALL_PARTIAL"
                            and issue.source_anchor.start_line == 4 for issue in result.issues))

    def test_possible_call_targets_report_interprocedural_partial(self):
        result = self.analyze("def main(flag, source):\n    if flag:\n        def choose(value): return value\n    else:\n        def choose(value): return 0\n    out = choose(source)\n    return out\n")
        reference = next(ref for ref in result.symbol_index.references
                         if ref.name == "choose" and ref.source_anchor.start_line == 6)
        self.assertIsNone(reference.resolved_definition_id)
        self.assertEqual(len(reference.possible_target_ids), 2)
        self.assertTrue(self.read(result, 7, "out").metadata["unknown"])
        self.assertTrue(self.read(result, 7, "out").metadata["reaching_definition_ids"])
        issues = [issue for issue in result.issues if issue.code == "GENERIC_DATA_CALL_PARTIAL"]
        self.assertTrue(any(issue.source_anchor.start_line == 6 for issue in issues))
        self.assertEqual(AppController._result_status(result), AnalysisStatus.PARTIAL)

    def test_edge_budget_reports_more_without_node_truncation(self):
        result = self.analyze("def f(raw):\n    item = raw\n    result = item\n    return result\n")
        selected = next(entry.id for entry in result.symbol_index.entries.values()
                        if entry.qualified_name == "app.f.result")
        view = build_visible_graph(result, ViewRequest(ViewKind.DATA_FLOW,
                                   selected_id=selected, hops=5, max_nodes=100, max_edges=1))
        self.assertEqual(view.hidden_counts["nodes"], 0)
        self.assertGreater(view.hidden_counts["edges"], 0)
        self.assertEqual(len(view.edges), 1)
        self.assertTrue(view.query_context["has_more"])
        self.assertTrue(view.query_context["budget_truncated"])

    def test_nested_loop_work_budget_is_scope_local(self):
        original = _DefUse.statement
        for depth in (12, 8, 4):
            source = "def expensive(rows, raw):\n    item = raw\n"
            source += "".join("    " * (level + 1) + f"for row{level} in rows:\n"
                              for level in range(depth))
            source += "    " * (depth + 1) + "item = raw\n"
            source += "    return item\ndef sibling(raw):\n    kept = raw\n    return kept\n"
            visits = []

            def counted(visitor, node, scope, env, recorded_visits=visits):
                if visitor.index.entries[scope].qualified_name == "app.expensive":
                    recorded_visits.append(node)
                return original(visitor, node, scope, env)

            # Instrument real traversal, without replacing any semantic work.
            with patch.object(_DefUse, "statement", counted):
                result = analyze_files({"app.py": source,
                                        "other.py": "def sibling(raw):\n    kept = raw\n    return kept\n"})
            self.assertLessEqual(len(visits), MAX_SCOPE_WORK + 1)
            self.assertFalse([issue for issue in result.issues if issue.code.endswith("_ERROR")])
            if depth == 4:
                continue
            issues = [issue for issue in result.issues
                      if issue.code == "GENERIC_DATA_WORK_BUDGET_PARTIAL"]
            self.assertTrue(issues)
            self.assertTrue(all(issue.source_anchor and issue.source_anchor.file_path == "app.py"
                                for issue in issues))
            graph = result.flow.graph
            owner_node = next(node for node in graph.nodes.values()
                              if node.qualified_name == "app.expensive")
            self.assertEqual(owner_node.metadata["generic_def_use_work_visits"], MAX_SCOPE_WORK)
            self.assertTrue(owner_node.metadata["generic_def_use_work_truncated"])
            reads = [edge for edge in graph.edges if edge.type == EdgeType.READS
                     and edge.metadata.get("generic_data")]
            affected = [edge for edge in reads if graph.nodes[edge.source].scope == "app.expensive"]
            self.assertTrue(affected)
            self.assertTrue(all(edge.metadata["unknown"] for edge in affected))
            self.assertTrue(any(edge.metadata["reaching_definition_ids"] for edge in affected))
            for owner in ("app.sibling", "other.sibling"):
                sibling = [edge for edge in reads if graph.nodes[edge.source].scope == owner]
                self.assertTrue(sibling)
                self.assertTrue(all(not edge.metadata["unknown"] for edge in sibling))
            self.assertEqual(AppController._result_status(result), AnalysisStatus.PARTIAL)

    def test_finally_shared_occurrence_is_explicitly_partial_not_precise(self):
        result = self.analyze("def f(flag, left, right):\n    item = left\n    try:\n        if flag:\n            item = right\n            return item\n    finally:\n        out = item\n    final = out\n    return final\n")
        self.assertTrue(self.read(result, 10, "final").metadata["reaching_definition_ids"])
        self.assertTrue(self.read(result, 10, "final").metadata["unknown"])
        issues = [issue for issue in result.issues if issue.code == "GENERIC_DATA_FINALLY_PARTIAL"]
        self.assertTrue(issues)
        self.assertEqual(issues[0].source_anchor.start_line, 8)
        self.assertEqual(AppController._result_status(result), AnalysisStatus.PARTIAL)
        selected = next(entry.id for entry in result.symbol_index.entries.values()
                        if entry.qualified_name == "app.f.final")
        view = build_visible_graph(result, ViewRequest(ViewKind.DATA_FLOW, selected_id=selected,
                                   direction="upstream", hops=5, max_nodes=100, max_edges=100))
        self.assertTrue({"left", "right"}.issubset({node.display_name for node in view.nodes}))

    def test_low_module_budget_does_not_omit_sibling_or_next_file(self):
        source = "".join(f"item{position} = {position}\n" for position in range(20))
        source += "def sibling(raw):\n    kept = raw\n    return kept\n"
        with patch("codeviz.analysis.def_use.MAX_SCOPE_WORK", 12):
            result = analyze_files({"app.py": source,
                                    "other.py": "def sibling(raw):\n    kept = raw\n    return kept\n"})
        issues = [issue for issue in result.issues if issue.code == "GENERIC_DATA_WORK_BUDGET_PARTIAL"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].source_anchor.file_path, "app.py")
        graph = result.flow.graph
        for owner in ("app.sibling", "other.sibling"):
            reads = [edge for edge in graph.edges if edge.type == EdgeType.READS
                     and edge.metadata.get("generic_data") and graph.nodes[edge.source].scope == owner]
            self.assertTrue(reads)
            self.assertTrue(all(edge.metadata["reaching_definition_ids"]
                                and not edge.metadata["unknown"] for edge in reads))

    def test_finally_pending_loop_exit_also_reports_shared_context_partial(self):
        result = self.analyze("def f(rows, left, right):\n    item = left\n    for row in rows:\n        try:\n            item = right\n            break\n        finally:\n            out = item\n    return out\n")
        self.assertTrue(any(issue.code == "GENERIC_DATA_FINALLY_PARTIAL"
                            and issue.source_anchor.start_line == 8 for issue in result.issues))
        self.assertTrue(self.read(result, 9, "out").metadata["unknown"])

    def test_file_work_budget_marks_omitted_scopes_and_keeps_next_file(self):
        source = "".join(f"def f{position}(raw):\n    item = raw\n    return item\n"
                         for position in range(10))
        with patch("codeviz.analysis.def_use.MAX_FILE_WORK", 12, create=True):
            result = analyze_files({"app.py": source,
                                    "other.py": "def sibling(raw):\n    kept = raw\n    return kept\n"})
        issues = [issue for issue in result.issues if issue.code == "GENERIC_DATA_FILE_WORK_BUDGET_PARTIAL"]
        self.assertTrue(issues)
        self.assertTrue(all(issue.source_anchor and issue.source_anchor.file_path == "app.py"
                            for issue in issues))
        graph = result.flow.graph
        module = next(node for node in graph.nodes.values() if node.qualified_name == "app"
                      and node.kind == NodeKind.MODULE)
        self.assertLessEqual(module.metadata["generic_def_use_file_work_visits"], 12)
        omitted = next(node for node in graph.nodes.values() if node.qualified_name == "app.f9")
        self.assertTrue(omitted.metadata["generic_def_use_omitted"])
        bindings = [node for node in graph.nodes.values() if node.metadata.get("generic_data")
                    and node.metadata.get("owner_scope_id") == omitted.id]
        self.assertTrue(bindings)
        self.assertTrue(all(node.metadata["unknown"] for node in bindings))
        reads = [edge for edge in graph.edges if edge.type == EdgeType.READS
                 and edge.metadata.get("generic_data") and graph.nodes[edge.source].scope == "other.sibling"]
        self.assertTrue(reads)
        self.assertTrue(all(not edge.metadata["unknown"] for edge in reads))
        self.assertEqual(AppController._result_status(result), AnalysisStatus.PARTIAL)
