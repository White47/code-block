"""Project-level lexical and import resolution without executing source code."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.analysis.project import analyze_project
from codeviz.analysis.resolution import ImportTargetQuery
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
    def test_reexport_dag_query_bounds_work_without_losing_complete_fact(self):
        files = {"root.py": "def run(): pass\n"}
        parents = ["root"]
        for layer in range(9):
            names = [f"m{layer}_{position}" for position in range(4)]
            for name in names:
                files[name + ".py"] = "".join(f"from {parent} import run\n" for parent in parents)
            parents = names
        files["app.py"] = f"from {parents[0]} import run\nrun()\n"
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, files)
            result = analyze_project(root)
        query = ImportTargetQuery(result.files, result.resolution)
        entry = next(entry for entry in result.symbol_index.entries.values()
                     if entry.file_path == "app.py" and entry.name == "run")
        visits = 0
        original = query.resolver._import_target

        def counted(*args, **kwargs):
            nonlocal visits
            visits += 1
            return original(*args, **kwargs)

        with patch.object(query.resolver, "_import_target", counted):
            fact = query.targets(entry)
        aliases = sum("imported_from" in entry.metadata for entry in result.symbol_index.entries.values())
        self.assertLessEqual(visits, 4 * aliases + 1, "lookup count must scale with the DAG, not its paths")
        self.assertLessEqual(query.resolver.import_work, 4096)
        self.assertEqual(len(fact.ids), 1)
        self.assertIsNone(fact.issue_code)

    def test_import_work_budget_preserves_known_fact_and_later_local_definition(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, {"root.py": "def run(): pass\nfrom tail import run\n",
                               "tail.py": "def run(): pass\n",
                               "app.py": "from root import run\ndef healthy(): pass\nhealthy()\n"})
            result = analyze_project(root)
        query = ImportTargetQuery(result.files, result.resolution)
        entry = next(entry for entry in result.symbol_index.entries.values()
                     if entry.file_path == "app.py" and entry.name == "run")
        with patch("codeviz.analysis.resolution.MAX_IMPORT_WORK", 1, create=True):
            partial = query.targets(entry)
            repeated = query.targets(entry)
            healthy = query.reference_targets(reference(result, "healthy", "app"))
        self.assertTrue(partial.ids)
        self.assertEqual(partial.issue_code, "IMPORT_WORK_BUDGET_PARTIAL")
        self.assertEqual(partial.confidence, Confidence.PROBABLE)
        self.assertEqual(repeated.ids, partial.ids, "budget exhaustion must not discard known partial IDs")
        self.assertEqual(repeated.issue_code, "IMPORT_WORK_BUDGET_PARTIAL")
        self.assertEqual(healthy.confidence, Confidence.EXACT)
        self.assertIsNone(healthy.issue_code)

    def test_import_work_budget_is_shared_across_queries(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {f"m{i}.py": "def run(): pass\n" for i in range(3)}
            files["app.py"] = "".join(f"from m{i} import run as f{i}\n" for i in range(3))
            write_files(root, files)
            result = analyze_project(root)
        query = ImportTargetQuery(result.files, result.resolution)
        entries = sorted((entry for entry in result.symbol_index.entries.values()
                          if entry.file_path == "app.py" and "imported_from" in entry.metadata),
                         key=lambda entry: entry.name)
        with patch("codeviz.analysis.resolution.MAX_IMPORT_WORK", 2, create=True):
            facts = [query.targets(entry) for entry in entries]
        self.assertIsNone(facts[0].issue_code)
        self.assertIsNone(facts[1].issue_code)
        self.assertEqual(facts[2].issue_code, "IMPORT_WORK_BUDGET_PARTIAL")

    def reference_fact(self, result, ref):
        query = ImportTargetQuery(result.files, result.resolution)
        lookup = getattr(query, "reference_targets", None)
        self.assertTrue(callable(lookup), "shared query must expose Name/Attribute reference facts")
        before = tuple(result.symbol_index.references)
        fact = lookup(ref)
        self.assertEqual(tuple(result.symbol_index.references), before)
        return fact

    def test_member_lookup_keeps_partial_import_cycle_issue(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, {
                "a.py": "from b import Base\nclass Base: pass\n",
                "b.py": "from a import Base\n",
                "app.py": "import b\nb.Base\n",
            })
            result = analyze_project(root)
        query = ImportTargetQuery(result.files, result.resolution)
        ref = reference(result, "Base", "app", "b")
        fact = query.resolver._qualified(ref)
        self.assertTrue(fact.ids)
        self.assertEqual(fact.issue_code, "IMPORT_CYCLE")
        self.assertEqual(fact.confidence, Confidence.PROBABLE)

    def test_unified_reference_facts_keep_name_and_attribute_partial(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, {
                "a.py": "from b import Base\nclass Base: pass\n",
                "b.py": "from a import Base\n",
                "app.py": "from b import Base\nimport b\nBase\nb.Base\n"
                          "def build():\n    global Base\n    Base\n",
            })
            result = analyze_project(root)
        target = next(entry.id for entry in result.symbol_index.entries.values()
                      if entry.qualified_name == "a.Base" and entry.kind == NodeKind.CLASS)
        for scope, qualifier in (("app", None), ("app", "b"), ("app.build", None)):
            with self.subTest(scope=scope, qualifier=qualifier):
                fact = self.reference_fact(result, reference(result, "Base", scope, qualifier))
                self.assertEqual(fact.ids, (target,))
                self.assertEqual(fact.issue_code, "IMPORT_CYCLE")
                self.assertEqual(fact.confidence, Confidence.PROBABLE)

    def test_unified_reference_facts_keep_complete_probable_and_exact(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, {
                "bases.py": "class Base: pass\nclass Base: pass\nclass Unique: pass\n",
                "app.py": "from bases import Base\nimport bases\nBase\nbases.Base\nbases.Unique\n"
                          "def build():\n    class Base: pass\n    Base\n",
            })
            result = analyze_project(root)
        for qualifier in (None, "bases"):
            fact = self.reference_fact(result, reference(result, "Base", "app", qualifier))
            self.assertEqual(len(fact.ids), 2)
            self.assertEqual(fact.confidence, Confidence.PROBABLE)
            self.assertIsNone(fact.issue_code)
        for name, scope, qualifier in (("Unique", "app", "bases"), ("Base", "app.build", None)):
            fact = self.reference_fact(result, reference(result, name, scope, qualifier))
            self.assertEqual(len(fact.ids), 1)
            self.assertEqual(fact.confidence, Confidence.EXACT)
            self.assertIsNone(fact.issue_code)

    def test_member_chain_keeps_partial_receiver_issue(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_files(root, {
                "real.py": "class Base: pass\n",
                "a.py": "from b import container\nimport real as container\n",
                "b.py": "from a import container\n",
                "app.py": "from b import container\ncontainer.Base\ncontainer.missing\n",
            })
            result = analyze_project(root)
        fact = self.reference_fact(result, reference(result, "Base", "app", "container"))
        self.assertTrue(fact.ids)
        self.assertEqual(fact.issue_code, "IMPORT_CYCLE")
        self.assertEqual(fact.confidence, Confidence.PROBABLE)
        missing = self.reference_fact(result, reference(result, "missing", "app", "container"))
        self.assertFalse(missing.ids)
        self.assertEqual(missing.issue_code, "IMPORT_CYCLE")

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
