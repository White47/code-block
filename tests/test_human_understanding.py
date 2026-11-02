"""Bounded, source-backed facts for human-readable project understanding."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.analysis.human_understanding import HumanOperationKind
from codeviz.analysis.project import analyze_project
from codeviz.analysis.symbols import index_parsed_file
from codeviz.core import Confidence


class HumanUnderstandingTests(unittest.TestCase):
    def test_index_failed_file_is_skipped_while_healthy_file_facts_survive(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.py").write_text("def broken():\n    return 0\n", encoding="utf-8")
            (root / "b.py").write_text(
                "def healthy():\n    \"\"\"保留健康事实。\"\"\"\n    return 1\n",
                encoding="utf-8",
            )
            original = index_parsed_file

            def fragile_index(parsed):
                if parsed.relative_path == "a.py":
                    raise RecursionError("nested source")
                return original(parsed)

            with patch("codeviz.analysis.project.index_parsed_file", side_effect=fragile_index):
                analysis = analyze_project(root)

        owner_names = {
            entry.id: entry.qualified_name for entry in analysis.symbol_index.entries.values()
        }
        self.assertEqual(
            [owner_names[fact.owner_symbol_id]
             for fact in analysis.human_understanding.author_statements],
            ["b.healthy"],
        )
        self.assertEqual(
            [owner_names[fact.owner_symbol_id]
             for fact in analysis.human_understanding.operations],
            ["b.healthy"],
        )

    def test_author_statements_keep_lexical_owner_and_probable_source_claim(self):
        source = (
            "def load_scores():\n"
            "    \"\"\"读取分数。第二句不应进入事实。\"\"\"\n"
            "    return []\n\n"
            "def summarize(scores):\n"
            "    \"\"\"统计数量与平均值。\"\"\"\n"
            "    return len(scores), sum(scores)\n\n"
            "def show_report(result):\n"
            "    \"\"\"展示结果。\"\"\"\n"
            "    print(result)\n"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "report.py").write_text(source, encoding="utf-8")
            analysis = analyze_project(root)

        self.assertTrue(hasattr(analysis, "human_understanding"),
                        "ProjectAnalysis must expose persisted human-understanding facts")
        statements = analysis.human_understanding.author_statements
        owner_names = {
            entry.id: entry.qualified_name for entry in analysis.symbol_index.entries.values()
        }
        self.assertEqual(
            [(owner_names[fact.owner_symbol_id], fact.text) for fact in statements],
            [
                ("report.load_scores", "读取分数。"),
                ("report.summarize", "统计数量与平均值。"),
                ("report.show_report", "展示结果。"),
            ],
        )
        self.assertTrue(all(fact.confidence is Confidence.PROBABLE for fact in statements))
        self.assertEqual([fact.source_anchor.start_line for fact in statements], [2, 6, 10])

    def test_explicit_returns_are_exact_syntax_facts_for_the_lexical_owner(self):
        source = (
            "def with_value():\n"
            "    return 1\n\n"
            "def without_value():\n"
            "    return\n"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "returns.py").write_text(source, encoding="utf-8")
            analysis = analyze_project(root)

        owner_names = {
            entry.id: entry.qualified_name for entry in analysis.symbol_index.entries.values()
        }
        returns = [fact for fact in analysis.human_understanding.operations
                   if fact.kind in (HumanOperationKind.RETURN_VALUE,
                                    HumanOperationKind.RETURN_EMPTY)]
        self.assertEqual(
            [(owner_names[fact.owner_symbol_id], fact.kind) for fact in returns],
            [
                ("returns.with_value", HumanOperationKind.RETURN_VALUE),
                ("returns.without_value", HumanOperationKind.RETURN_EMPTY),
            ],
        )
        self.assertTrue(all(fact.confidence is Confidence.EXACT for fact in returns))
        self.assertTrue(all(fact.callsite_id is None and fact.target_symbol_id is None
                            for fact in returns))

    def test_unique_external_builtins_create_source_backed_operation_facets(self):
        source = (
            "def report(values):\n"
            "    print(len(values), sum(values), int('1'))\n"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "facets.py").write_text(source, encoding="utf-8")
            analysis = analyze_project(root)

        operations = analysis.human_understanding.operations
        self.assertEqual(
            {fact.kind for fact in operations},
            {
                HumanOperationKind.STANDARD_OUTPUT,
                HumanOperationKind.COUNT,
                HumanOperationKind.SUM,
                HumanOperationKind.CONVERSION,
            },
        )
        self.assertTrue(all(fact.confidence is Confidence.PROBABLE for fact in operations))
        self.assertTrue(all(fact.callsite_id and fact.target_symbol_id for fact in operations))
        targets = {analysis.flow.graph.nodes[fact.target_symbol_id].qualified_name
                   for fact in operations}
        self.assertEqual(targets, {"builtins.print", "builtins.len", "builtins.sum", "builtins.int"})
        self.assertTrue(all(analysis.flow.graph.nodes[fact.callsite_id].source_anchors
                            == (fact.source_anchor,) for fact in operations))

    def test_conversion_whitelist_is_narrowly_limited_to_builtins_int(self):
        source = (
            "def convert(value):\n"
            "    int(value)\n"
            "    float(value)\n"
            "    str(value)\n"
            "    bool(value)\n"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "convert.py").write_text(source, encoding="utf-8")
            analysis = analyze_project(root)

        conversions = [fact for fact in analysis.human_understanding.operations
                       if fact.kind is HumanOperationKind.CONVERSION]
        self.assertEqual(len(conversions), 1)
        self.assertEqual(
            analysis.flow.graph.nodes[conversions[0].target_symbol_id].qualified_name,
            "builtins.int",
        )

    def test_project_defined_print_is_not_standard_output(self):
        source = (
            "def print(value):\n"
            "    return value\n\n"
            "def report(value):\n"
            "    print(value)\n"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "owned.py").write_text(source, encoding="utf-8")
            analysis = analyze_project(root)

        self.assertNotIn(
            HumanOperationKind.STANDARD_OUTPUT,
            {fact.kind for fact in analysis.human_understanding.operations},
        )

    def test_deferred_and_creation_expressions_use_scope_locations_owners(self):
        source = (
            "def outer():\n"
            "    callback = lambda value=len(()): int(value)\n"
            "    def inner():\n"
            "        print('nested')\n"
            "    return callback, inner\n"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scopes.py").write_text(source, encoding="utf-8")
            analysis = analyze_project(root)

        entries = analysis.symbol_index.entries
        operations = {
            analysis.flow.graph.nodes[fact.target_symbol_id].qualified_name: fact
            for fact in analysis.human_understanding.operations if fact.target_symbol_id
        }
        outer_id = next(entry.id for entry in entries.values()
                        if entry.qualified_name == "scopes.outer")
        inner_id = next(entry.id for entry in entries.values()
                        if entry.qualified_name == "scopes.outer.inner")
        lambda_id = next(entry.id for entry in entries.values()
                         if entry.metadata.get("scope_type") == "Lambda")
        self.assertEqual(operations["builtins.len"].owner_symbol_id, outer_id)
        self.assertEqual(operations["builtins.int"].owner_symbol_id, lambda_id)
        self.assertEqual(operations["builtins.print"].owner_symbol_id, inner_id)

    def test_global_fact_budget_reports_every_omitted_fact(self):
        source = "\n".join(
            f"def task_{index}():\n    \"\"\"任务 {index}。\"\"\"\n"
            for index in range(513)
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "many.py").write_text(source, encoding="utf-8")
            analysis = analyze_project(root)

        facts = analysis.human_understanding
        self.assertEqual(len(facts.author_statements) + len(facts.operations), 512)
        self.assertEqual(facts.omitted_fact_count, 1)
        self.assertEqual(
            [issue.code for issue in analysis.issues].count("UNDERSTANDING_FACTS_TRUNCATED"),
            1,
        )

    def test_author_statement_keeps_only_a_bounded_first_meaningful_line(self):
        private_tail = "PRIVATE_DOCSTRING_TAIL"
        source = (
            "def task():\n"
            "    \"\"\"\n"
            f"    {'读' * 100}\n"
            f"    {private_tail}\n"
            "    \"\"\"\n"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bounded.py").write_text(source, encoding="utf-8")
            analysis = analyze_project(root)

        statement = analysis.human_understanding.author_statements[0]
        self.assertLessEqual(len(statement.text.encode("utf-8")), 240)
        self.assertNotIn(private_tail, statement.text)
        self.assertTrue(statement.truncated)

    def test_author_statement_does_not_split_dotted_identifier_or_decimal(self):
        source = (
            "def array_scores():\n"
            "    \"\"\"Return a numpy.ndarray with scores.\"\"\"\n\n"
            "def threshold():\n"
            "    \"\"\"Use threshold 3.14 for scores.\"\"\"\n"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs.py").write_text(source, encoding="utf-8")
            statements = analyze_project(root).human_understanding.author_statements

        self.assertEqual(
            [statement.text for statement in statements],
            ["Return a numpy.ndarray with scores.", "Use threshold 3.14 for scores."],
        )

    def test_fact_ids_are_deterministic_for_the_same_relative_source(self):
        source = "def task(values):\n    \"\"\"统计值。\"\"\"\n    return sum(values)\n"
        ids = []
        for _ in range(2):
            with TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "stable.py").write_text(source, encoding="utf-8")
                facts = analyze_project(root).human_understanding
                ids.append(tuple(fact.id for fact in (*facts.author_statements, *facts.operations)))

        self.assertEqual(ids[0], ids[1])


if __name__ == "__main__":
    unittest.main()
