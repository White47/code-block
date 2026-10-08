"""Independent public acceptance for static work caps; target code never runs."""

import json
import unittest
from collections import Counter
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.analysis import def_use, resolution, value_candidates
from codeviz.analysis.project import analyze_project
from codeviz.core import Confidence, EdgeType, NodeKind
from codeviz.persistence.codec import decode_analysis, encode_analysis


def nested_body(depth, indent=4):
    lines = [" " * indent + "item = raw"]
    for level in range(depth):
        lines.append(" " * (indent + level * 4) + f"for row_{level} in rows:")
    lines.append(" " * (indent + depth * 4) + f"item = row_{depth - 1}")
    lines.append(" " * indent + "return item")
    return "\n".join(lines) + "\n"


def resetting_candidate_loops(depth):
    """Each local loop converges, yet nested revisits multiply without a work cap."""
    lines = ["def f(): return 1", "def g(): return 2", "def stress(rows):"]
    for level in range(depth):
        indent = " " * (4 + level * 4)
        lines.extend((indent + "x = f", indent + f"for row_{level} in rows:"))
    lines.append(" " * (4 + depth * 4) + "x = g")
    lines.append("    x()")
    lines.extend(("def sibling():", "    fn = f", "    fn()"))
    return "\n".join(lines) + "\n"


class AnalysisWorkBudgetAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def analyze(self, sources):
        for relative, source in sources.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        result = analyze_project(self.root)
        self.assertFalse([issue for issue in result.issues if issue.code.endswith("_ERROR")])
        return result

    def work_constant(self, module, word):
        # Inspect actual loaded source constants; never patch a made-up name
        # with create=True (that would let a missing work cap fake a pass).
        names = [name for name, value in vars(module).items()
                 if name.isupper() and "WORK" in name and word in name
                 and type(value) is int and value > 0]
        self.assertEqual(len(names), 1,
                         f"{module.__name__} needs one real {word} work budget, found {names}")
        return names[0]

    def anchored_issues(self, result, code, file_path):
        issues = [issue for issue in result.issues if issue.code == code
                  and issue.file_path == file_path]
        self.assertTrue(issues, f"missing source-anchored {code} in {file_path}")
        for issue in issues:
            self.assertIsNotNone(issue.source_anchor)
            self.assertEqual(issue.source_anchor.file_path, file_path)
            lines = (self.root / file_path).read_text(encoding="utf-8").splitlines()
            self.assertTrue(1 <= issue.source_anchor.start_line <= len(lines))
            self.assertTrue(lines[issue.source_anchor.start_line - 1].strip())
        return issues

    def calls(self, result, file_path, name):
        graph = result.flow.graph
        sites = {node.id for node in graph.nodes.values()
                 if node.kind == NodeKind.CALL_SITE and node.name == name
                 and node.file_path == file_path}
        edges = [edge for edge in graph.edges if edge.type == EdgeType.CALLS and edge.source in sites]
        self.assertEqual(len(edges), 1)
        return edges[0]

    def valid_return(self, result, file_path, function):
        owner = next(entry for entry in result.symbol_index.entries.values()
                     if entry.file_path == file_path and entry.name == function
                     and entry.kind == NodeKind.FUNCTION)
        graph = result.flow.graph
        edges = [edge for edge in graph.edges if edge.type == EdgeType.READS
                 and edge.metadata.get("generic_data")
                 and edge.source_anchor.file_path == file_path
                 and graph.nodes[edge.target].name == "copy"
                 and owner.source_anchor.start_line <= edge.source_anchor.start_line
                 <= owner.source_anchor.end_line]
        self.assertEqual(len(edges), 1, f"healthy return must remain analyzed: {function}")
        edge = edges[0]
        self.assertFalse(edge.metadata.get("unknown"))
        self.assertFalse(edge.metadata.get("truncated"))
        origins = edge.metadata.get("reaching_definition_ids", [])
        self.assertEqual(len(origins), 1)
        anchor = graph.nodes[origins[0]].source_anchors[0]
        self.assertEqual(anchor.file_path, file_path)
        self.assertEqual((self.root / file_path).read_text(encoding="utf-8").splitlines()
                         [anchor.start_line - 1].strip(), "copy = raw")

    def cache_preserved(self, result):
        encoded = encode_analysis(result)
        restored = decode_analysis(json.loads(json.dumps(encoded)))
        self.assertTrue(all(parsed.tree is None for parsed in restored.files))
        self.assertEqual(encode_analysis(restored), encoded)
        self.assertEqual(restored.issues, result.issues)
        self.assertEqual(restored.flow.graph.nodes, result.flow.graph.nodes)
        self.assertEqual(restored.flow.graph.edges, result.flow.graph.edges)
        return restored

    def test_candidate_nested_reset_loops_have_cumulative_scope_cap_and_isolation(self):
        original_expr = value_candidates.ValueCandidates._expr
        # Acceptance cap is deliberately small. Only patch an actual source
        # constant once it exists; absence must not silently install a fake cap.
        budget = 256
        constants = [name for name, value in vars(value_candidates).items()
                     if name.isupper() and "SCOPE" in name and "WORK" in name
                     and type(value) is int and value > 0]
        scope_local = bool(constants)
        if not constants:
            # Architecture permits a file-only cap; later same-file scopes then
            # must explicitly remain partial instead of pretending to be complete.
            constants = [name for name, value in vars(value_candidates).items()
                         if name.isupper() and "FILE" in name and "WORK" in name
                         and type(value) is int and value > 0]
        for depth in (8, 10, 12):
            visits = Counter()

            def counted_expr(visitor, node, scope, *args, _visits=visits, **kwargs):
                owner = visitor.index.entries[scope].qualified_name
                _visits[(visitor.parsed.relative_path, owner)] += 1
                return original_expr(visitor, node, scope, *args, **kwargs)

            budget_patch = (patch.object(value_candidates, constants[0], budget)
                            if len(constants) == 1 else nullcontext())
            with self.subTest(depth=depth), budget_patch, \
                    patch.object(value_candidates.ValueCandidates, "_expr", counted_expr):
                result = self.analyze({"deep.py": resetting_candidate_loops(depth),
                                       "safe.py": "def healthy(): return 1\n"
                                                  "def next_file():\n    fn = healthy\n    fn()\n"})
                stress_visits = visits[("deep.py", "deep.stress")]
                self.assertGreater(stress_visits, 0, "observer must count real candidate traversal")
                self.assertLessEqual(
                    stress_visits, budget + 1,
                    f"depth={depth}: {stress_visits} actual _expr visits; per-loop iteration "
                    "limits alone do not cap cumulative scope work",
                )
                self.assertEqual(len(constants), 1, "missing real cumulative candidate work budget")
                issues = [issue for issue in result.issues
                          if issue.file_path == "deep.py" and issue.code.startswith("VALUE_")
                          and "WORK" in issue.code and "BUDGET" in issue.code
                          and issue.code.endswith("_PARTIAL")]
                self.assertTrue(issues, "cumulative traversal exhaustion needs source-anchored Partial")
                stress = next(entry for entry in result.symbol_index.entries.values()
                              if entry.qualified_name == "deep.stress")
                for issue in issues:
                    self.anchored_issues(result, issue.code, "deep.py")
                    self.assertTrue(stress.source_anchor.start_line <= issue.source_anchor.start_line
                                    <= stress.source_anchor.end_line)
                edge = self.calls(result, "deep.py", "x")
                self.assertTrue(edge.metadata.get("unknown"))
                self.assertTrue(edge.metadata.get("truncated"))
                self.assertNotEqual(edge.confidence, Confidence.EXACT)
                self.assertTrue(all(target in result.flow.graph.nodes for target in edge.possible_targets))
                for file_path, expected in (("deep.py", "deep.f"), ("safe.py", "safe.healthy")):
                    call = self.calls(result, file_path, "fn")
                    if file_path == "deep.py" and not scope_local:
                        self.assertTrue(call.metadata.get("unknown"))
                        self.assertTrue(call.metadata.get("truncated"))
                        self.assertNotEqual(call.confidence, Confidence.EXACT)
                    else:
                        self.assertFalse(call.metadata.get("unknown"))
                        self.assertFalse(call.metadata.get("truncated"))
                        self.assertEqual({result.flow.graph.nodes[target].qualified_name
                                          for target in call.possible_targets}, {expected})
                restored = self.cache_preserved(result)
                self.assertTrue(self.calls(restored, "deep.py", "x").metadata.get("truncated"))

    def test_nine_layer_four_class_dag_has_explicit_bounded_partial(self):
        name = self.work_constant(value_candidates, "METHOD")
        source = "class Base:\n    def run(self): return 1\n"
        parents = ["Base"]
        for layer in range(9):
            children = [f"Layer{layer}_{column}" for column in range(4)]
            source += "".join(f"class {child}({', '.join(parents)}): pass\n" for child in children)
            parents = children
        source += f"def overloaded():\n    return {parents[0]}().run()\n"
        source += "def healthy(): return 1\ndef sibling():\n    fn = healthy\n    fn()\n"
        with patch.object(value_candidates, name, 4):
            result = self.analyze({"dag.py": source, "safe.py": "def healthy(): return 1\nhealthy()\n"})
        issues = self.anchored_issues(result, "VALUE_INHERIT_WORK_BUDGET_PARTIAL", "dag.py")
        edge = self.calls(result, "dag.py", "run")
        self.assertTrue(edge.metadata.get("unknown"))
        self.assertTrue(edge.metadata.get("truncated"))
        self.assertNotEqual(edge.confidence, Confidence.EXACT)
        self.assertTrue(any(issue.source_anchor.start_line == edge.source_anchor.start_line for issue in issues))
        sibling = self.calls(result, "dag.py", "fn")
        self.assertFalse(sibling.metadata.get("truncated"))
        names = {result.flow.graph.nodes[target].qualified_name for target in sibling.possible_targets}
        self.assertEqual(names, {"dag.healthy"})
        self.assertFalse(self.calls(result, "safe.py", "healthy").metadata.get("truncated"))
        self.cache_preserved(result)

    def test_candidate_file_work_exhaustion_does_not_fallback_exact_in_later_scope(self):
        original_expr = value_candidates.ValueCandidates._expr
        visits = Counter()
        budget = 256
        constants = [name for name, value in vars(value_candidates).items()
                     if name.isupper() and "FILE" in name and "WORK" in name
                     and type(value) is int and value > 0]

        def counted_expr(visitor, node, scope, *args, **kwargs):
            visits[visitor.parsed.relative_path] += 1
            return original_expr(visitor, node, scope, *args, **kwargs)

        # Raise any actual scope cap so this probe isolates cumulative file work.
        scope_constants = [name for name, value in vars(value_candidates).items()
                           if name.isupper() and "SCOPE" in name and "WORK" in name
                           and type(value) is int and value > 0]
        file_patch = (patch.object(value_candidates, constants[0], budget)
                      if len(constants) == 1 else nullcontext())
        scope_patch = (patch.object(value_candidates, scope_constants[0], budget * 8)
                       if len(scope_constants) == 1 else nullcontext())
        source = resetting_candidate_loops(10)
        # An already-discovered callable must survive as a possible target when
        # its scope later exhausts work; it must not become a fake Exact fact.
        source = source.replace("def stress(rows):\n",
                                "def stress(rows):\n    known = f\n    known()\n")
        with file_patch, scope_patch, \
                patch.object(value_candidates.ValueCandidates, "_expr", counted_expr):
            result = self.analyze({"deep.py": source,
                                   "safe.py": "def healthy(): return 1\n"
                                              "def next_file():\n    fn = healthy\n    fn()\n"})
        self.assertGreater(visits["deep.py"], 0)
        scopes = sum(entry.file_path == "deep.py" and entry.definition
                     and entry.kind in (NodeKind.MODULE, NodeKind.FUNCTION, NodeKind.METHOD)
                     for entry in result.symbol_index.entries.values())
        self.assertLessEqual(visits["deep.py"], budget + scopes,
                             f"{visits['deep.py']} real expression visits: cumulative file work must be capped; "
                             "allow at most one failed admission per indexed lexical scope")
        self.assertEqual(len(constants), 1, "missing real candidate file work budget")
        issues = [issue for issue in result.issues if issue.file_path == "deep.py"
                  and issue.code.startswith("VALUE_") and "FILE" in issue.code
                  and "WORK" in issue.code and issue.code.endswith("_PARTIAL")]
        self.assertTrue(issues, "file-work truncation must carry source-anchored Partial")
        for issue in issues:
            self.anchored_issues(result, issue.code, "deep.py")
        known = self.calls(result, "deep.py", "known")
        self.assertIn("deep.f", {result.flow.graph.nodes[target].qualified_name
                                 for target in known.possible_targets})
        for name in ("known", "x", "fn"):
            edge = self.calls(result, "deep.py", name)
            self.assertTrue(edge.metadata.get("unknown"), f"{name} must not hide incomplete value work")
            self.assertTrue(edge.metadata.get("truncated"))
            self.assertNotEqual(edge.confidence, Confidence.EXACT)
        healthy = self.calls(result, "safe.py", "fn")
        self.assertFalse(healthy.metadata.get("unknown"))
        self.assertFalse(healthy.metadata.get("truncated"))
        self.assertEqual({result.flow.graph.nodes[target].qualified_name
                          for target in healthy.possible_targets}, {"safe.healthy"})
        restored = self.cache_preserved(result)
        self.assertTrue(self.calls(restored, "deep.py", "fn").metadata.get("unknown"))

    def test_cyclic_reexport_dag_budget_preserves_unknown_and_other_file(self):
        name = self.work_constant(resolution, "IMPORT")
        sources = {"leaf.py": "from layer3_0 import worker\ndef worker(): return 1\n"}
        parents = ["leaf"]
        for layer in range(4):
            children = [f"layer{layer}_{column}" for column in range(3)]
            for child in children:
                sources[child + ".py"] = "".join(f"from {parent} import worker\n" for parent in parents)
            parents = children
        sources["app.py"] = f"import {parents[0]}\ndef overloaded():\n    {parents[0]}.worker()\n"
        sources["safe.py"] = "def healthy(): return 1\nhealthy()\n"
        with patch.object(resolution, name, 4):
            result = self.analyze(sources)
        budget_issues = [issue for issue in result.issues if "IMPORT" in issue.code
                         and "WORK" in issue.code and "BUDGET" in issue.code]
        self.assertTrue(budget_issues, "re-export work exhaustion needs explicit budget diagnostics")
        for issue in budget_issues:
            self.anchored_issues(result, issue.code, issue.file_path)
        edge = self.calls(result, "app.py", "worker")
        self.assertTrue(edge.metadata.get("unknown"))
        self.assertTrue(edge.metadata.get("truncated"))
        self.assertNotEqual(edge.confidence, Confidence.EXACT)
        self.assertFalse(self.calls(result, "safe.py", "healthy").metadata.get("truncated"))
        self.cache_preserved(result)

    def test_nested_for_depths_have_scope_partial_without_poisoning_siblings(self):
        self.work_constant(def_use, "SCOPE")
        for depth in (12, 15, 18):
            with self.subTest(depth=depth), patch.object(def_use, "MAX_SCOPE_WORK", 48):
                source = "def overloaded(rows, raw):\n" + nested_body(depth)
                source += "def sibling(raw):\n    copy = raw\n    return copy\n"
                result = self.analyze({"deep.py": source,
                                       "safe.py": "def healthy(raw):\n    copy = raw\n    return copy\n"})
                issues = self.anchored_issues(result, "GENERIC_DATA_WORK_BUDGET_PARTIAL", "deep.py")
                self.assertTrue(all(1 < issue.source_anchor.start_line < depth + 5 for issue in issues))
                retained = [edge for edge in result.flow.graph.edges
                            if edge.metadata.get("generic_data")
                            and edge.source_anchor.file_path == "deep.py"
                            and edge.source_anchor.start_line < depth + 5]
                self.assertTrue(retained, "retain discovered facts rather than dropping the scope")
                self.assertTrue(any(edge.metadata.get("unknown") and edge.metadata.get("truncated")
                                    for edge in retained))
                self.valid_return(result, "deep.py", "sibling")
                self.valid_return(result, "safe.py", "healthy")
                restored = self.cache_preserved(result)
                self.valid_return(restored, "deep.py", "sibling")

    def test_exhausted_module_scope_still_analyzes_same_file_function(self):
        self.work_constant(def_use, "SCOPE")
        body = nested_body(12, indent=0).removesuffix("return item\n")
        source = "raw = rows = []\n" + body
        source += "def sibling(raw):\n    copy = raw\n    return copy\n"
        with patch.object(def_use, "MAX_SCOPE_WORK", 48):
            result = self.analyze({"deep.py": source,
                                   "safe.py": "def healthy(raw):\n    copy = raw\n    return copy\n"})
        self.anchored_issues(result, "GENERIC_DATA_WORK_BUDGET_PARTIAL", "deep.py")
        module = next(node for node in result.flow.graph.nodes.values()
                      if node.kind == NodeKind.MODULE and node.file_path == "deep.py")
        self.assertTrue(module.metadata.get("generic_def_use_work_truncated"))
        self.assertEqual(module.metadata.get("generic_def_use_work_visits"), 48)
        self.valid_return(result, "deep.py", "sibling")
        self.valid_return(result, "safe.py", "healthy")
        self.valid_return(self.cache_preserved(result), "deep.py", "sibling")

    def test_file_budget_marks_omitted_sibling_but_resets_for_other_file(self):
        self.work_constant(def_use, "FILE")
        with patch.object(def_use, "MAX_SCOPE_WORK", 128), patch.object(def_use, "MAX_FILE_WORK", 32):
            source = "def overloaded(rows, raw):\n" + nested_body(12)
            source += "def sibling(raw):\n    copy = raw\n    return copy\n"
            result = self.analyze({"deep.py": source,
                                   "safe.py": "def healthy(raw):\n    copy = raw\n    return copy\n"})
        self.anchored_issues(result, "GENERIC_DATA_FILE_WORK_BUDGET_PARTIAL", "deep.py")
        graph = result.flow.graph
        module = next(node for node in graph.nodes.values()
                      if node.kind == NodeKind.MODULE and node.file_path == "deep.py")
        self.assertTrue(module.metadata.get("generic_def_use_file_work_truncated"))
        self.assertLessEqual(module.metadata.get("generic_def_use_file_work_visits", 33), 32)
        sibling = next(node for node in graph.nodes.values()
                       if node.kind == NodeKind.FUNCTION and node.qualified_name == "deep.sibling")
        self.assertTrue(sibling.metadata.get("generic_def_use_omitted"))
        self.assertTrue(sibling.metadata.get("generic_def_use_work_truncated"))
        self.assertTrue(any(issue.code == "GENERIC_DATA_FILE_WORK_BUDGET_PARTIAL"
                            and issue.source_anchor.file_path == "deep.py"
                            and sibling.source_anchors[0].start_line <= issue.source_anchor.start_line
                            <= sibling.source_anchors[0].end_line
                            for issue in result.issues), "omitted sibling needs its own source diagnostic")
        self.assertTrue(any(node.metadata.get("unknown") and node.metadata.get("truncated")
                            for node in graph.nodes.values()
                            if node.metadata.get("owner_scope_id") == sibling.id))
        self.valid_return(result, "safe.py", "healthy")
        self.valid_return(self.cache_preserved(result), "safe.py", "healthy")


if __name__ == "__main__":
    unittest.main()
