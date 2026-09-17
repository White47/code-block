import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.analysis.project import analyze_project
from codeviz.analysis.python import parse_python_file
from codeviz.analysis.symbols import index_parsed_file
from codeviz.core import NodeKind


def parse_source(root: Path, relative_path: str, source: str):
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return parse_python_file(root, relative_path)


class SymbolIndexTests(unittest.TestCase):
    def test_indexes_class_method_parameters_attributes_and_raw_references(self):
        source = (
            "import torch.nn as nn\n"
            "class Encoder(nn.Module):\n"
            "    def forward(self, x):\n"
            "        hidden = x\n"
            "        self.output = hidden\n"
            "        return self.output\n"
        )
        with TemporaryDirectory() as temporary:
            parsed = parse_source(Path(temporary), "model.py", source)
            index = index_parsed_file(parsed)

        self.assertGreater(len(index.entries), 0)
        qualified = {entry.qualified_name: entry for entry in index.entries.values()}
        self.assertEqual(qualified["model.Encoder"].kind, NodeKind.CLASS)
        self.assertEqual(qualified["model.Encoder.forward"].kind, NodeKind.METHOD)
        self.assertEqual(qualified["model.Encoder.forward.x"].kind, NodeKind.PARAMETER)
        self.assertEqual(qualified["model.Encoder.forward.hidden"].kind, NodeKind.VARIABLE)
        self.assertEqual(qualified["model.Encoder.output"].kind, NodeKind.ATTRIBUTE)
        self.assertEqual(qualified["model.Encoder.forward"].parent_symbol, qualified["model.Encoder"].id)
        self.assertEqual(qualified["model.Encoder.forward"].source_anchor.start_line, 3)
        self.assertEqual(qualified["model.nn"].metadata["imported_from"], "torch.nn")
        self.assertTrue(any(ref.name == "x" and ref.scope == "model.Encoder.forward" for ref in index.references))
        self.assertTrue(all(ref.resolved_definition_id is None for ref in index.references))

    def test_same_named_locals_are_distinct_and_branches_keep_function_scope(self):
        source = (
            "def train(loader):\n"
            "    for batch in loader:\n"
            "        if batch:\n"
            "            loss = batch\n"
            "    return loss\n"
            "def validate(loader):\n"
            "    loss = loader\n"
            "    return loss\n"
        )
        with TemporaryDirectory() as temporary:
            index = index_parsed_file(parse_source(Path(temporary), "train.py", source))

        losses = index.find_definitions("loss")
        self.assertEqual(len(losses), 2)
        self.assertEqual({entry.qualified_name for entry in losses}, {"train.train.loss", "train.validate.loss"})
        self.assertEqual(len({entry.id for entry in losses}), 2)
        self.assertEqual(index.find_definitions("batch")[0].scope, "train.train")
        self.assertEqual(len(index.find_references("loss")), 2)

    def test_indexes_async_function_and_import_alias(self):
        source = "from collections import deque as Queue\nasync def run(value):\n    return Queue([value])\n"
        with TemporaryDirectory() as temporary:
            index = index_parsed_file(parse_source(Path(temporary), "worker.py", source))

        self.assertGreater(len(index.entries), 0)
        definitions = {entry.qualified_name: entry for entry in index.entries.values()}
        self.assertEqual(definitions["worker.run"].kind, NodeKind.FUNCTION)
        self.assertEqual(definitions["worker.Queue"].metadata["imported_from"], "collections.deque")
        self.assertEqual(definitions["worker.run.value"].kind, NodeKind.PARAMETER)

    def test_duplicate_definition_names_keep_distinct_ids(self):
        source = "if flag:\n    def step(): pass\nelse:\n    def step(): pass\n"
        with TemporaryDirectory() as temporary:
            index = index_parsed_file(parse_source(Path(temporary), "flow.py", source))

        definitions = index.find_definitions("step")
        self.assertEqual(len(definitions), 2)
        self.assertEqual(len({entry.id for entry in definitions}), 2)

    def test_package_init_uses_package_module_name(self):
        with TemporaryDirectory() as temporary:
            parsed = parse_source(Path(temporary), "pkg/__init__.py", "value = 1\n")
            index = index_parsed_file(parsed)

        self.assertEqual(parsed.module_name, "pkg")
        self.assertEqual(len(index.find_definitions("value")), 1)
        self.assertEqual(index.find_definitions("value")[0].qualified_name, "pkg.value")

    def test_import_rebinding_does_not_keep_stale_import_metadata(self):
        source = "from collections import deque as Queue\nQueue = list\n"
        with TemporaryDirectory() as temporary:
            index = index_parsed_file(parse_source(Path(temporary), "worker.py", source))

        definitions = index.find_definitions("Queue")
        self.assertEqual(len(definitions), 2)
        self.assertNotEqual(definitions[0].id, definitions[1].id)
        self.assertEqual(definitions[0].metadata["imported_from"], "collections.deque")
        self.assertNotIn("imported_from", definitions[1].metadata)

    def test_symbol_ids_survive_line_insertion_before_definitions(self):
        source = "class Encoder:\n    def forward(self, x):\n        return x\n"
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = index_parsed_file(parse_source(root, "model.py", source))
            shifted = index_parsed_file(parse_source(root, "model.py", "\n\n" + source))

        for name in ("Encoder", "forward", "x"):
            self.assertEqual(original.find_definitions(name)[0].id,
                             shifted.find_definitions(name)[0].id)


class ProjectAnalysisTests(unittest.TestCase):
    def test_bad_file_does_not_hide_symbols_from_good_file(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            parse_source(root, "good.py", "def train():\n    return 1\n")
            parse_source(root, "bad.py", "def broken(:\n")

            result = analyze_project(root)

            self.assertEqual(len(result.files), 2)
            self.assertEqual({item.relative_path for item in result.files}, {"good.py", "bad.py"})
            self.assertEqual([issue.code for issue in result.issues], ["SYNTAX_ERROR"])
            self.assertEqual(result.symbol_index.find_definitions("train")[0].file_path, "good.py")

    def test_user_ignore_paths_apply_to_analysis(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            parse_source(root, "src/kept.py", "x = 1\n")
            parse_source(root, "generated/ignored.py", "y = 1\n")

            result = analyze_project(root, ignore_paths=("generated",))

            self.assertEqual(tuple(item.relative_path for item in result.files), ("src/kept.py",))

    def test_index_failure_is_isolated_to_its_file(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            parse_source(root, "a.py", "broken = 1\n")
            parse_source(root, "b.py", "healthy = 2\n")
            original = index_parsed_file

            def fragile_index(parsed):
                if parsed.relative_path == "a.py":
                    raise RecursionError("nested source")
                return original(parsed)

            with patch("codeviz.analysis.project.index_parsed_file", side_effect=fragile_index):
                result = analyze_project(root)

        self.assertEqual([issue.code for issue in result.issues], ["INDEX_ERROR"])
        self.assertEqual(result.issues[0].file_path, "a.py")
        self.assertEqual(len(result.symbol_index.find_definitions("healthy")), 1)
        self.assertEqual(result.symbol_index.find_definitions("broken"), ())


if __name__ == "__main__":
    unittest.main()
