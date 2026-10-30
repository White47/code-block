"""A small ordinary-Python project for future fake-provider acceptance."""

import unittest
from pathlib import Path

from codeviz.analysis.project import analyze_project
from codeviz.core import Confidence, EdgeType, EntityRole

PROJECT = Path(__file__).parent / "fixtures" / "assistant_project"


class AssistantOfflineFixtureTests(unittest.TestCase):
    def test_general_python_path_is_source_backed_without_executing_project(self):
        self.assertTrue((PROJECT / "app.py").is_file())
        analysis = analyze_project(PROJECT)
        self.assertIsNotNone(analysis.flow)
        graph = analysis.flow.graph
        definitions = {node.qualified_name: node for node in graph.nodes.values()
                       if node.role == EntityRole.DEFINITION}
        expected = {"app.process_record", "helpers.normalize_record",
                    "helpers.resolve_conflict", "helpers.persist_record"}
        self.assertTrue(expected <= definitions.keys())
        calls = [edge for edge in graph.edges if edge.type == EdgeType.CALLS]
        self.assertTrue(any(edge.target == definitions["helpers.normalize_record"].id
                            and edge.confidence == Confidence.EXACT
                            and edge.source_anchor is not None for edge in calls))
        self.assertTrue(any(edge.type == EdgeType.BRANCH_TRUE for edge in graph.edges))


if __name__ == "__main__":
    unittest.main()
