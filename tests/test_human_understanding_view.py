"""Projection from persisted H1 facts into bounded responsibility cards."""

import json
import unittest
from dataclasses import replace

from tests.test_training import analyze_files

import codeviz.views as views
from codeviz.analysis.human_understanding import HumanOperationKind, _fact_id
from codeviz.analysis.project import ProjectAnalysis
from codeviz.analysis.symbols import SymbolIndex
from codeviz.core import Confidence, EdgeType, EntryPoint, EntryPointKind, NodeKind
from codeviz.persistence.codec import decode_analysis, encode_analysis


class HumanUnderstandingViewTests(unittest.TestCase):
    def test_author_statement_becomes_probable_source_labeled_responsibility(self):
        analysis = analyze_files({
            "app.py": (
                "def main():\n"
                "    \"\"\"Load scores unchanged.\"\"\"\n"
                "    return []\n"
            ),
        })
        self.assertTrue(hasattr(views, "build_human_understanding"),
                        "views must export the approved H2 projection")

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.status, "source_backed")
        self.assertEqual(len(result.responsibilities), 1)
        card = result.responsibilities[0]
        owner = analysis.symbol_index.entries[card.owner_symbol_id]
        author = analysis.human_understanding.author_statements[0]
        self.assertEqual(card.code_name, "app.main")
        self.assertIs(card.owner_kind, NodeKind.FUNCTION)
        self.assertEqual(card.responsibility, "Load scores unchanged.")
        self.assertEqual(card.basis, "source_author_statement")
        self.assertIs(card.confidence, Confidence.PROBABLE)
        self.assertEqual(card.author_fact_id, author.id)
        self.assertFalse(card.author_truncated)
        self.assertEqual(card.source_anchor, owner.source_anchor)

    def test_operation_only_owner_remains_unknown_without_name_or_count_purpose(self):
        analysis = analyze_files({
            "calc.py": (
                "def calculate(values):\n"
                "    print(len(values))\n"
                "    sum(values)\n"
                "    return\n"
            ),
        })

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.responsibilities), 1)
        card = result.responsibilities[0]
        self.assertEqual(card.code_name, "calc.calculate")
        self.assertIsNone(card.responsibility)
        self.assertEqual(card.basis, "unknown")
        self.assertIs(card.confidence, Confidence.UNRESOLVED)
        self.assertIsNone(card.author_fact_id)
        self.assertEqual(
            {facet.kind for facet in card.operations},
            {
                HumanOperationKind.RETURN_EMPTY,
                HumanOperationKind.STANDARD_OUTPUT,
                HumanOperationKind.COUNT,
                HumanOperationKind.SUM,
            },
        )
        self.assertTrue(all(facet.source_location_count == 1 for facet in card.operations))

    def test_cold_and_astless_warm_projection_are_equal(self):
        analysis = analyze_files({
            "app.py": (
                "def main(values):\n"
                "    \"\"\"Report values.\"\"\"\n"
                "    print(len(values))\n"
                "    return sum(values)\n"
            ),
        })

        restored = decode_analysis(json.loads(json.dumps(encode_analysis(analysis))))

        self.assertTrue(all(parsed.tree is None for parsed in restored.files))
        self.assertEqual(
            views.build_human_understanding(restored),
            views.build_human_understanding(analysis),
        )

    def test_entry_and_parent_rank_before_other_authors_without_calls_ranking(self):
        analysis = analyze_files({
            "app.py": (
                "\"\"\"Module responsibility.\"\"\"\n"
                "def other():\n"
                "    \"\"\"Other responsibility.\"\"\"\n"
                "def main():\n"
                "    \"\"\"Entry responsibility.\"\"\"\n"
                "    other()\n"
                "def effects():\n"
                "    print('effect')\n"
            ),
        })

        first = views.build_human_understanding(analysis)
        analysis.flow.graph.edges.reverse()
        second = views.build_human_understanding(analysis)

        self.assertEqual(
            [card.code_name for card in first.responsibilities],
            ["app.main", "app", "app.other", "app.effects"],
        )
        self.assertEqual(second, first)

    def test_operation_facets_count_locations_cap_refs_and_keep_weakest_confidence(self):
        calls = "".join(f"    print({index})\n" for index in range(10))
        analysis = analyze_files({"output.py": f"def emit():\n{calls}"})
        operations = analysis.human_understanding.operations
        upgraded = replace(operations[0], confidence=Confidence.EXACT)
        # This synthetic mixed-strength fixture must keep its source witnesses consistent.
        reference_indices = [
            position for position, reference in enumerate(analysis.symbol_index.references)
            if reference.scope_symbol_id == upgraded.owner_symbol_id
            and reference.source_anchor.file_path == upgraded.source_anchor.file_path
            and reference.source_anchor.start_line == upgraded.source_anchor.start_line
            and reference.source_anchor.start_column == upgraded.source_anchor.start_column
        ]
        self.assertEqual(len(reference_indices), 1)
        position = reference_indices[0]
        analysis.symbol_index.references[position] = replace(
            analysis.symbol_index.references[position], confidence=Confidence.EXACT,
            resolved_definition_id=upgraded.target_symbol_id, possible_target_ids=(),
            unresolved_symbol_id=None, reason=None,
        )
        self.assertIsNotNone(analysis.flow)
        edge_indices = [
            position for position, edge in enumerate(analysis.flow.graph.edges)
            if edge.type is EdgeType.CALLS
            and edge.source == upgraded.callsite_id and edge.target == upgraded.target_symbol_id
        ]
        self.assertEqual(len(edge_indices), 1)
        position = edge_indices[0]
        analysis.flow.graph.edges[position] = replace(
            analysis.flow.graph.edges[position], confidence=Confidence.EXACT, possible_targets=(),
        )
        analysis = replace(
            analysis,
            human_understanding=replace(
                analysis.human_understanding,
                operations=(upgraded, *operations[1:]),
            ),
        )

        result = views.build_human_understanding(analysis)

        facet = result.responsibilities[0].operations[0]
        self.assertIs(facet.kind, HumanOperationKind.STANDARD_OUTPUT)
        self.assertIs(facet.confidence, Confidence.PROBABLE)
        self.assertEqual(facet.source_location_count, 10)
        self.assertEqual(len(facet.fact_ids), 8)
        self.assertEqual(len(facet.source_anchors), 8)

    def test_operation_confidence_upgrade_without_source_witness_is_discarded(self):
        analysis = analyze_files({"output.py": "def emit():\n    print(1)\n    print(2)\n"})
        operations = analysis.human_understanding.operations
        upgraded = replace(operations[0], confidence=Confidence.EXACT)
        analysis = replace(analysis, human_understanding=replace(
            analysis.human_understanding, operations=(upgraded, *operations[1:]),
        ))

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.discarded_fact_count, 1)
        facet = result.responsibilities[0].operations[0]
        self.assertEqual(facet.source_location_count, 1)
        self.assertIs(facet.confidence, Confidence.PROBABLE)
        self.assertNotIn(upgraded.id, facet.fact_ids)

    def test_conflicting_author_statements_fail_closed_without_losing_operations(self):
        analysis = analyze_files({
            "app.py": "def task():\n    \"\"\"First claim.\"\"\"\n    return 1\n",
        })
        first = analysis.human_understanding.author_statements[0]
        return_fact = analysis.human_understanding.operations[0]
        second = replace(
            first,
            id=_fact_id(first.owner_symbol_id, "author_statement", return_fact.source_anchor),
            text="Conflicting claim.",
            source_anchor=return_fact.source_anchor,
        )
        analysis = replace(
            analysis,
            human_understanding=replace(
                analysis.human_understanding,
                author_statements=(first, second),
            ),
        )

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.discarded_fact_count, 2)
        card = result.responsibilities[0]
        self.assertIsNone(card.responsibility)
        self.assertEqual(card.basis, "unknown")
        self.assertIs(card.confidence, Confidence.UNRESOLVED)
        self.assertIsNone(card.author_fact_id)
        self.assertEqual(
            {facet.kind for facet in card.operations},
            {HumanOperationKind.RETURN_VALUE},
        )

    def test_empty_legacy_analysis_projects_to_unknown(self):
        result = views.build_human_understanding(ProjectAnalysis((), SymbolIndex(), ()))

        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.responsibilities, ())
        self.assertEqual(result.omitted_responsibility_count, 0)
        self.assertEqual(result.omitted_fact_count, 0)
        self.assertEqual(result.discarded_fact_count, 0)
        self.assertIsNotNone(result.architecture)
        self.assertEqual(result.architecture.id, "project-architecture")
        self.assertEqual(result.flows, ())
        self.assertIsNone(result.default_flow_id)
        self.assertEqual(result.omitted_flow_count, 0)

    def test_foreign_owner_fact_is_discarded_without_a_card(self):
        analysis = analyze_files({
            "app.py": "def task():\n    \"\"\"Owned claim.\"\"\"\n",
        })
        statement = analysis.human_understanding.author_statements[0]
        foreign_id = "node_foreign"
        forged = replace(
            statement,
            id=_fact_id(foreign_id, "author_statement", statement.source_anchor),
            owner_symbol_id=foreign_id,
        )
        analysis = replace(
            analysis,
            human_understanding=replace(
                analysis.human_understanding,
                author_statements=(forged,),
            ),
        )

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.responsibilities, ())
        self.assertEqual(result.discarded_fact_count, 1)

    def test_fact_outside_current_owner_anchor_is_discarded(self):
        analysis = analyze_files({
            "app.py": (
                "module_value = 1\n"
                "def task():\n"
                "    \"\"\"Owned claim.\"\"\"\n"
            ),
        })
        statement = analysis.human_understanding.author_statements[0]
        outside = replace(statement.source_anchor, start_line=1, end_line=1)
        forged = replace(
            statement,
            id=_fact_id(statement.owner_symbol_id, "author_statement", outside),
            source_anchor=outside,
        )
        analysis = replace(
            analysis,
            human_understanding=replace(
                analysis.human_understanding,
                author_statements=(forged,),
            ),
        )

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.responsibilities, ())
        self.assertEqual(result.discarded_fact_count, 1)

    def test_nested_syntax_facts_cannot_be_rebound_to_outer_owner(self):
        analysis = analyze_files({
            "app.py": (
                "def outer():\n"
                "    def inner():\n"
                "        \"\"\"Inner claim.\"\"\"\n"
                "        return 1\n"
            ),
        })
        outer = next(entry for entry in analysis.symbol_index.entries.values()
                     if entry.qualified_name == "app.outer")
        statement = analysis.human_understanding.author_statements[0]
        operation = analysis.human_understanding.operations[0]
        forged_statement = replace(
            statement,
            id=_fact_id(outer.id, "author_statement", statement.source_anchor),
            owner_symbol_id=outer.id,
        )
        forged_operation = replace(
            operation,
            id=_fact_id(
                outer.id, "operation", operation.source_anchor,
                operation.kind.value, operation.target_symbol_id,
            ),
            owner_symbol_id=outer.id,
        )
        analysis = replace(
            analysis,
            human_understanding=replace(
                analysis.human_understanding,
                author_statements=(forged_statement,),
                operations=(forged_operation,),
            ),
        )

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.responsibilities, ())
        self.assertEqual(result.discarded_fact_count, 2)

    def test_nested_call_fact_cannot_be_rebound_to_outer_owner(self):
        analysis = analyze_files({
            "app.py": (
                "def outer():\n"
                "    def inner():\n"
                "        print('inner')\n"
            ),
        })
        outer = next(entry for entry in analysis.symbol_index.entries.values()
                     if entry.qualified_name == "app.outer")
        operation = analysis.human_understanding.operations[0]
        forged = replace(
            operation,
            id=_fact_id(
                outer.id, "operation", operation.source_anchor,
                operation.kind.value, operation.target_symbol_id,
            ),
            owner_symbol_id=outer.id,
        )
        analysis = replace(
            analysis,
            human_understanding=replace(
                analysis.human_understanding,
                operations=(forged,),
            ),
        )

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.responsibilities, ())
        self.assertEqual(result.discarded_fact_count, 1)

    def test_default_expression_call_keeps_reference_scope_owner(self):
        analysis = analyze_files({
            "app.py": (
                "def outer():\n"
                "    def inner(value=int('1')):\n"
                "        return value\n"
            ),
        })

        result = views.build_human_understanding(analysis)

        conversion_card = next(
            card for card in result.responsibilities
            if any(facet.kind is HumanOperationKind.CONVERSION for facet in card.operations)
        )
        self.assertEqual(conversion_card.code_name, "app.outer")
        self.assertEqual(result.discarded_fact_count, 0)

    def test_lexical_scope_operation_is_not_projected_as_a_responsibility(self):
        analysis = analyze_files({"app.py": "callback = lambda: int('1')\n"})
        self.assertEqual(len(analysis.human_understanding.operations), 1)

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.responsibilities, ())
        self.assertEqual(result.discarded_fact_count, 1)

    def test_persisted_training_entry_id_receives_root_rank(self):
        analysis = analyze_files({
            "train.py": (
                "def other():\n"
                "    \"\"\"Other responsibility.\"\"\"\n"
                "def train():\n"
                "    \"\"\"Training responsibility.\"\"\"\n"
            ),
        })
        train = next(entry for entry in analysis.symbol_index.entries.values()
                     if entry.qualified_name == "train.train")
        analysis = replace(
            analysis,
            training=replace(
                analysis.training,
                entry_points=(EntryPoint(
                    train.id, train.name, EntryPointKind.TRAIN, train.source_anchor,
                ),),
            ),
        )

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.responsibilities[0].code_name, "train.train")

    def test_stale_main_name_anchor_does_not_rank_root(self):
        analysis = analyze_files({
            "app.py": (
                "def other():\n"
                "    \"\"\"Other responsibility.\"\"\"\n"
                "def main():\n"
                "    \"\"\"Main responsibility.\"\"\"\n"
            ),
        })
        other = next(entry for entry in analysis.symbol_index.entries.values()
                     if entry.qualified_name == "app.other")
        candidate = next(item for item in analysis.generic_entries
                         if item.detection_kind == "MainName")
        analysis = replace(
            analysis,
            generic_entries=(replace(candidate, source_anchor=other.source_anchor),),
        )

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.responsibilities[0].code_name, "app.other")

    def test_stale_training_entry_anchor_does_not_rank_root(self):
        analysis = analyze_files({
            "train.py": (
                "def other():\n"
                "    \"\"\"Other responsibility.\"\"\"\n"
                "def train():\n"
                "    \"\"\"Training responsibility.\"\"\"\n"
            ),
        })
        other = next(entry for entry in analysis.symbol_index.entries.values()
                     if entry.qualified_name == "train.other")
        train = next(entry for entry in analysis.symbol_index.entries.values()
                     if entry.qualified_name == "train.train")
        analysis = replace(
            analysis,
            training=replace(
                analysis.training,
                entry_points=(EntryPoint(
                    train.id, train.name, EntryPointKind.TRAIN, other.source_anchor,
                ),),
            ),
        )

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.responsibilities[0].code_name, "train.other")

    def test_valid_generic_entry_kinds_keep_their_root_rank(self):
        analyses = (
            ("MainName", analyze_files({
                "a.py": "def other():\n    \"\"\"Other responsibility.\"\"\"\n",
                "z.py": "def main():\n    \"\"\"Main responsibility.\"\"\"\n",
            })),
            ("MainModule", analyze_files({
                "a.py": "def other():\n    \"\"\"Other responsibility.\"\"\"\n",
                "z/__main__.py": "\"\"\"Main module responsibility.\"\"\"\n",
            })),
            ("MainGuard", analyze_files({
                "a.py": "def other():\n    \"\"\"Other responsibility.\"\"\"\n",
                "z.py": (
                    "def launch():\n"
                    "    \"\"\"Launch responsibility.\"\"\"\n"
                    "if '__main__' == __name__:\n"
                    "    launch()\n"
                ),
            })),
        )

        for detection_kind, analysis in analyses:
            with self.subTest(detection_kind=detection_kind):
                candidate = next(item for item in analysis.generic_entries
                                 if item.detection_kind == detection_kind)
                result = views.build_human_understanding(analysis)
                self.assertEqual(
                    result.responsibilities[0].owner_symbol_id,
                    candidate.root_symbol_id,
                )

    def test_main_guard_ranks_a_root_after_another_true_branch_statement(self):
        analysis = analyze_files({
            "a.py": 'def other():\n    """Other responsibility."""\n',
            "z.py": (
                'def prepare():\n    pass\n'
                'def launch():\n    """Launch responsibility."""\n'
                'if __name__ == "__main__":\n'
                '    prepare()\n'
                '    launch()\n'
            ),
        })
        launch = next(item for item in analysis.symbol_index.entries.values()
                      if item.name == "launch")
        self.assertTrue(any(item.root_symbol_id == launch.id
                            and item.detection_kind == "MainGuard"
                            for item in analysis.generic_entries))

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.responsibilities[0].owner_symbol_id, launch.id)

    def test_main_guard_does_not_rank_a_root_called_only_in_false_branch(self):
        analysis = analyze_files({
            "a.py": 'def other():\n    """Other responsibility."""\n',
            "z.py": (
                'def launch():\n    """Launch responsibility."""\n'
                'if __name__ == "__main__":\n'
                '    pass\n'
                'else:\n'
                '    launch()\n'
            ),
        })
        launch = next(item for item in analysis.symbol_index.entries.values()
                      if item.name == "launch")
        guard = next(item for item in analysis.generic_entries
                     if item.detection_kind == "MainGuard")
        analysis = replace(
            analysis,
            generic_entries=(replace(guard, root_symbol_id=launch.id),),
        )

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.responsibilities[0].code_name, "a.other")

    def test_main_guard_does_not_rank_a_root_called_only_after_guard(self):
        analysis = analyze_files({
            "a.py": 'def other():\n    """Other responsibility."""\n',
            "z.py": (
                'def launch():\n    """Launch responsibility."""\n'
                'if __name__ == "__main__":\n'
                '    pass\n'
                'launch()\n'
            ),
        })
        launch = next(item for item in analysis.symbol_index.entries.values()
                      if item.name == "launch")
        guard = next(item for item in analysis.generic_entries
                     if item.detection_kind == "MainGuard")
        analysis = replace(
            analysis,
            generic_entries=(replace(guard, root_symbol_id=launch.id),),
        )

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.responsibilities[0].code_name, "a.other")

    def test_responsibility_budget_keeps_twelve_and_reports_other_owners(self):
        source = "\n".join(
            f"def task_{index:02d}():\n    \"\"\"Responsibility {index}.\"\"\"\n"
            for index in range(14)
        )
        analysis = analyze_files({"many.py": source})

        result = views.build_human_understanding(analysis)

        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.responsibilities), 12)
        self.assertEqual(result.omitted_responsibility_count, 2)
        self.assertEqual(result.omitted_fact_count, 0)
        self.assertEqual(result.discarded_fact_count, 0)


if __name__ == "__main__":
    unittest.main()
