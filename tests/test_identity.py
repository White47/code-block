import unittest

from codeviz.core import EntityRole, NodeKind, SourceAnchor, stable_node_id


class SourceAnchorTests(unittest.TestCase):
    def test_accepts_relative_source_range(self):
        anchor = SourceAnchor("models/encoder.py", 12, 3, 15, 8)

        self.assertEqual(anchor.file_path, "models/encoder.py")
        self.assertEqual(anchor.start_line, 12)
        self.assertEqual(anchor.end_column, 8)

    def test_rejects_path_outside_project_and_reversed_range(self):
        for path in ("../secrets.py", "/tmp/model.py", "C:\\model.py"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                SourceAnchor(path, 1)

        with self.assertRaises(ValueError):
            SourceAnchor("model.py", 4, 0, 3, 0)


class StableIdTests(unittest.TestCase):
    def test_definition_id_is_deterministic_and_line_independent(self):
        first = stable_node_id("models/encoder.py", "Encoder.forward", NodeKind.METHOD, "Encoder")
        second = stable_node_id("models/encoder.py", "Encoder.forward", NodeKind.METHOD, "Encoder")

        self.assertEqual(first, second)
        self.assertNotIn(":12", first)

    def test_multiple_invocations_in_same_scope_have_distinct_ids(self):
        first = stable_node_id(
            "train.py", "train.model", NodeKind.FUNCTION, "train",
            role=EntityRole.INVOCATION, occurrence_key="body/call:model:0",
        )
        second = stable_node_id(
            "train.py", "train.model", NodeKind.FUNCTION, "train",
            role=EntityRole.INVOCATION, occurrence_key="body/call:model:1",
        )

        self.assertNotEqual(first, second)
        self.assertEqual(first, stable_node_id(
            "train.py", "train.model", NodeKind.FUNCTION, "train",
            role=EntityRole.INVOCATION, occurrence_key="body/call:model:0",
        ))

    def test_occurrence_key_is_required_for_instances_and_invocations(self):
        for role in (EntityRole.INSTANCE, EntityRole.INVOCATION):
            with self.subTest(role=role), self.assertRaises(ValueError):
                stable_node_id("model.py", "Block", NodeKind.CLASS, "Model", role=role)

    def test_duplicate_definitions_can_use_a_structural_occurrence_key(self):
        first = stable_node_id(
            "model.py", "make.Block", NodeKind.CLASS, "make",
            occurrence_key="if-branch/Block:0",
        )
        second = stable_node_id(
            "model.py", "make.Block", NodeKind.CLASS, "make",
            occurrence_key="else-branch/Block:0",
        )

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
