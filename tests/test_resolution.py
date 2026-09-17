"""Project-level lexical and import resolution without executing source code."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codeviz.analysis.project import analyze_project
from codeviz.core import Confidence, NodeKind


def write_files(root: Path, files: dict[str, str]) -> None:
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def reference(result, name: str, scope: str, qualifier: str | None = None):
    matches = [
        item for item in result.symbol_index.find_references(name)
        if item.scope == scope and item.qualifier == qualifier
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one reference to {scope}.{name}, got {len(matches)}")
    return matches[0]


class ResolutionTests(unittest.TestCase):
    def test_lexical_shadowing_closures_and_class_scope(self):
        source = (
            "x = 0\n"
            "class Box:\n"
            "    x = 1\n"
            "    def method(self, x):\n"
            "        return x\n"
            "    def other(self):\n"
            "        return x\n"
            "def outer():\n"
            "    x = 2\n"
            "    def inner():\n"
            "        return x\n"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, {"model.py": source})
            result = analyze_project(root)

        definitions = {item.qualified_name: item for item in result.symbol_index.find_definitions("x")}
        self.assertEqual(reference(result, "x", "model.Box.method").resolved_definition_id,
                         definitions["model.Box.method.x"].id)
        self.assertEqual(reference(result, "x", "model.Box.other").resolved_definition_id,
                         definitions["model.x"].id)
        self.assertEqual(reference(result, "x", "model.outer.inner").resolved_definition_id,
                         definitions["model.outer.x"].id)

    def test_absolute_relative_and_dotted_local_imports(self):
        files = {
            "pkg/__init__.py": "",
            "pkg/util.py": "def helper():\n    return 1\n",
            "pkg/main.py": "from .util import helper as run\ndef go():\n    return run()\n",
            "train.py": (
                "from pkg.util import helper\n"
                "import pkg.util as u\n"
                "def use():\n"
                "    return helper() + u.helper()\n"
            ),
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, files)
            result = analyze_project(root)

        helper = next(item for item in result.symbol_index.find_definitions("helper")
                      if item.file_path == "pkg/util.py")
        self.assertEqual(reference(result, "run", "pkg.main.go").resolved_definition_id, helper.id)
        self.assertEqual(reference(result, "helper", "train.use").resolved_definition_id, helper.id)
        self.assertEqual(reference(result, "helper", "train.use", "u").resolved_definition_id, helper.id)

    def test_src_layout_import_uses_project_module(self):
        files = {
            "src/demo/__init__.py": "",
            "src/demo/util.py": "def helper():\n    return 1\n",
            "src/demo/app.py": "from demo.util import helper\nresult = helper()\n",
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, files)
            result = analyze_project(root)

        helper = next(item for item in result.symbol_index.find_definitions("helper")
                      if item.file_path == "src/demo/util.py")
        self.assertEqual(reference(result, "helper", "src.demo.app").resolved_definition_id, helper.id)

    def test_plain_dotted_import_binds_package_then_submodule(self):
        files = {
            "pkg/__init__.py": "",
            "pkg/util.py": "def helper():\n    return 1\n",
            "consumer.py": "import pkg.util\nresult = pkg.util.helper()\n",
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, files)
            result = analyze_project(root)

        helper = next(item for item in result.symbol_index.find_definitions("helper")
                      if item.file_path == "pkg/util.py")
        self.assertEqual(reference(result, "helper", "consumer", "pkg.util").resolved_definition_id,
                         helper.id)

    def test_reexport_and_circular_module_imports_with_real_definitions(self):
        files = {
            "pkg/__init__.py": "from .util import helper\n",
            "pkg/util.py": "def helper():\n    return 1\n",
            "a.py": "from b import beta\ndef alpha():\n    return beta()\n",
            "b.py": "from a import alpha\ndef beta():\n    return alpha()\n",
            "consumer.py": "from pkg import helper\nresult = helper()\n",
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, files)
            result = analyze_project(root)

        definitions = {entry.qualified_name: entry for entry in result.symbol_index.entries.values()}
        self.assertEqual(reference(result, "helper", "consumer").resolved_definition_id,
                         definitions["pkg.util.helper"].id)
        self.assertEqual(reference(result, "beta", "a.alpha").resolved_definition_id,
                         definitions["b.beta"].id)
        self.assertEqual(reference(result, "alpha", "b.beta").resolved_definition_id,
                         definitions["a.alpha"].id)
        self.assertNotIn("IMPORT_CYCLE", {issue.code for issue in result.issues})

    def test_external_import_and_dynamic_attribute_are_not_exact_local_targets(self):
        files = {
            "model.py": (
                "import torch.nn as nn\n"
                "def run(obj):\n"
                "    nn.Linear\n"
                "    obj.forward()\n"
                "    missing()\n"
            ),
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, files)
            result = analyze_project(root)

        self.assertIsNotNone(getattr(result, "resolution", None))
        linear = reference(result, "Linear", "model.run", "nn")
        self.assertEqual(getattr(linear, "confidence", None), Confidence.PROBABLE)
        self.assertIsNone(linear.resolved_definition_id)
        external = result.resolution.external_symbols
        self.assertTrue(any(node.qualified_name == "torch.nn.Linear"
                            and node.kind == NodeKind.EXTERNAL_SYMBOL
                            and node.source_anchors for node in external))
        for name, qualifier in (("forward", "obj"), ("missing", None)):
            unresolved = reference(result, name, "model.run", qualifier)
            self.assertIsNone(unresolved.resolved_definition_id)
            self.assertEqual(getattr(unresolved, "confidence", None), Confidence.UNRESOLVED)
            self.assertIsNotNone(getattr(unresolved, "unresolved_symbol_id", None))

    def test_duplicate_definitions_keep_possible_targets(self):
        source = (
            "if flag:\n"
            "    def step(): return 1\n"
            "else:\n"
            "    def step(): return 2\n"
            "step()\n"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, {"flow.py": source})
            result = analyze_project(root)

        step = reference(result, "step", "flow")
        self.assertIsNone(step.resolved_definition_id)
        self.assertEqual(getattr(step, "confidence", None), Confidence.PROBABLE)
        self.assertEqual(set(getattr(step, "possible_target_ids", ())),
                         {item.id for item in result.symbol_index.find_definitions("step")})

    def test_duplicate_function_bodies_keep_their_own_local_symbols(self):
        source = (
            "if flag:\n"
            "    def step():\n"
            "        local = 1\n"
            "        return local\n"
            "else:\n"
            "    def step():\n"
            "        local = 2\n"
            "        return local\n"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, {"flow.py": source})
            result = analyze_project(root)

        definitions = result.symbol_index.find_definitions("local")
        references = result.symbol_index.find_references("local")
        self.assertEqual(len(definitions), 2)
        self.assertEqual(len({item.id for item in definitions}), 2)
        self.assertEqual(len(references), 2)
        for item in references:
            self.assertEqual(item.confidence, Confidence.EXACT)
            self.assertIn(item.resolved_definition_id, {definition.id for definition in definitions})
            self.assertEqual(result.symbol_index.entries[item.resolved_definition_id].parent_symbol,
                             item.scope_symbol_id)

    def test_import_alias_cycle_and_missing_local_member_remain_unresolved(self):
        files = {
            "a.py": "from b import y as x\nresult = x\n",
            "b.py": "from a import x as y\n",
            "pkg/__init__.py": "",
            "consumer.py": "from pkg import missing\nresult = missing\n",
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, files)
            result = analyze_project(root)

        self.assertIsNone(reference(result, "x", "a").resolved_definition_id)
        self.assertIsNone(reference(result, "missing", "consumer").resolved_definition_id)
        self.assertIn("IMPORT_CYCLE", {issue.code for issue in result.issues})
        self.assertIn("IMPORT_UNRESOLVED", {issue.code for issue in result.issues})

    def test_star_import_reports_unsupported_binding_without_guessing(self):
        files = {
            "helper.py": "value = 1\n",
            "consumer.py": "from helper import *\nresult = value\n",
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, files)
            result = analyze_project(root)

        self.assertIsNone(reference(result, "value", "consumer").resolved_definition_id)
        self.assertIn("STAR_IMPORT_UNRESOLVED", {issue.code for issue in result.issues})

    def test_unused_imports_still_produce_external_or_missing_import_facts(self):
        files = {
            "pkg/__init__.py": "",
            "consumer.py": "import torch\nfrom pkg import absent\n",
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, files)
            result = analyze_project(root)

        self.assertIsNotNone(result.resolution)
        self.assertTrue(any(node.qualified_name == "torch" for node in result.resolution.external_symbols))
        self.assertIn("IMPORT_UNRESOLVED", {issue.code for issue in result.issues})

    def test_plain_dotted_import_checks_the_submodule_even_when_package_exists(self):
        files = {
            "pkg/__init__.py": "",
            "consumer.py": "import pkg.absent\n",
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, files)
            result = analyze_project(root)

        self.assertIn("IMPORT_UNRESOLVED", {issue.code for issue in result.issues})

    def test_global_and_nonlocal_declarations_redirect_bindings(self):
        source = (
            "value = 1\n"
            "def outer():\n"
            "    value = 2\n"
            "    def inner():\n"
            "        nonlocal value\n"
            "        return value\n"
            "def rewrite():\n"
            "    global value\n"
            "    value = 3\n"
            "    return value\n"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, {"scope.py": source})
            result = analyze_project(root)

        values = result.symbol_index.find_definitions("value")
        module_value = next(item for item in values if item.scope == "scope")
        outer_value = next(item for item in values if item.scope == "scope.outer")
        self.assertFalse(any(item.scope == "scope.rewrite" for item in values))
        self.assertEqual(reference(result, "value", "scope.outer.inner").resolved_definition_id,
                         outer_value.id)
        self.assertEqual(reference(result, "value", "scope.rewrite").resolved_definition_id,
                         module_value.id)


if __name__ == "__main__":
    unittest.main()
