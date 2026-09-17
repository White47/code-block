"""Semantic relations are traceable and join validated components."""

import unittest

import codeviz.core as core


class SemanticGraphTests(unittest.TestCase):
    def test_relation_requires_provenance_and_registered_endpoints(self):
        relation_type = getattr(core, "SemanticRelation", None)
        graph_type = getattr(core, "SemanticGraph", None)
        self.assertIsNotNone(relation_type)
        self.assertIsNotNone(graph_type)
        anchor = core.SourceAnchor("train.py", 3)
        left = core.SemanticComponent("left", "Model", source_anchors=(anchor,))
        right = core.SemanticComponent("right", "Forward", source_anchors=(anchor,))
        relation = relation_type("left", "right", core.EdgeType.CONTAINS,
                                 core.Confidence.EXACT, ("model contains forward",), (anchor,))
        graph = graph_type((left, right), (relation,), ("left",))
        self.assertEqual(graph.roots, ("left",))
        with self.assertRaises(ValueError):
            graph_type((left,), (relation,))
        with self.assertRaises(ValueError):
            graph_type((core.SemanticComponent("orphan", "Orphan",
                                                child_component_ids=("missing",)),))
        with self.assertRaises(ValueError):
            relation_type("left", "right", core.EdgeType.CONTAINS,
                          core.Confidence.EXACT, (), (anchor,))


if __name__ == "__main__":
    unittest.main()
