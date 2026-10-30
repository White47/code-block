"""Check desktop packaging declarations without building or starting a GUI."""

import ast
import re
import tomllib
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class AssetReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []
        self.module_scripts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        for field in ("src", "href"):
            if field in attributes and tag != "a":
                self.references.append(attributes[field])
        if tag == "script" and attributes.get("type") == "module":
            self.module_scripts.append(attributes.get("src"))


class PackagingLayoutTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.configuration = tomllib.loads(
            (cls.root / "pyproject.toml").read_text(encoding="utf-8"))
        cls.spec = ast.parse(
            (cls.root / "scripts" / "codeviz.spec").read_text(encoding="utf-8"))

    def assignment(self, name):
        assignments = [node.value for node in self.spec.body
                       if isinstance(node, ast.Assign) and any(
                           isinstance(target, ast.Name) and target.id == name
                           for target in node.targets)]
        self.assertEqual(len(assignments), 1, name)
        return assignments[0]

    def keyword(self, call, name):
        self.assertIsInstance(call, ast.Call)
        values = [keyword.value for keyword in call.keywords
                  if keyword.arg == name]
        self.assertEqual(len(values), 1, name)
        return values[0]

    def assert_expression(self, actual, expected):
        self.assertEqual(ast.dump(actual), ast.dump(ast.parse(expected, mode="eval").body))

    def test_core_has_zero_runtime_dependencies_and_desktop_is_optional(self):
        project = self.configuration["project"]
        self.assertEqual(project["dependencies"], [])
        optional = project["optional-dependencies"]
        self.assertTrue(optional["desktop"])
        self.assertTrue(any(re.match(r"^pywebview(?:\s|[<>=!~\[]|$)", dependency,
                                     re.IGNORECASE)
                            for dependency in optional["desktop"]))
        self.assertTrue(any(re.match(r"^pyinstaller(?:\s|[<>=!~\[]|$)", dependency,
                                     re.IGNORECASE)
                            for dependency in optional["packaging"]))

    def test_desktop_and_tk_console_entry_points_are_preserved(self):
        scripts = self.configuration["project"]["scripts"]
        self.assertEqual(scripts["codeviz"], "codeviz.ui.desktop:main")
        self.assertEqual(scripts["codeviz-tk"], "codeviz.ui.tkapp:main")
        for entry in ("codeviz", "codeviz-tk"):
            with self.subTest(entry=entry):
                module, function = scripts[entry].split(":")
                path = self.root / "src" / Path(*module.split(".")).with_suffix(".py")
                tree = ast.parse(path.read_text(encoding="utf-8"))
                self.assertTrue(any(isinstance(node, ast.FunctionDef) and
                                    node.name == function for node in tree.body))

    def test_wheel_includes_frontend_assets_and_build_command(self):
        setuptools = self.configuration["tool"]["setuptools"]
        self.assertEqual(setuptools["cmdclass"]["build_py"],
                         "scripts.desktop_entry.BuildFrontendAssets")
        self.assertEqual(setuptools["package-dir"]["scripts"], "scripts")
        patterns = setuptools["package-data"]["codeviz.ui"]
        self.assertIn("assets/*", patterns)
        self.assertIn("assets/**/*", patterns)

    def test_frozen_spec_bundles_frontend_and_webview_assets(self):
        self.assert_expression(self.assignment("root"), "Path(SPECPATH).resolve().parent")
        self.assert_expression(self.assignment("frontend"), 'root / "frontend" / "dist"')
        analysis = self.assignment("a")
        self.assert_expression(analysis.func, "Analysis")
        self.assert_expression(analysis.args[0], '[str(root / "scripts" / "desktop_entry.py")]')
        self.assert_expression(self.keyword(analysis, "pathex"), '[str(root / "src")]')
        self.assert_expression(self.keyword(analysis, "datas"),
                               '[(str(frontend), "frontend/dist"), *webview_data, *ssl_data]')
        self.assert_expression(self.keyword(analysis, "binaries"), "webview_binaries")
        self.assert_expression(self.keyword(analysis, "hiddenimports"),
                               '["codeviz.ui.desktop", *webview_imports]')
        collection = [node for node in self.spec.body if isinstance(node, ast.Assign)
                      and isinstance(node.value, ast.Call)
                      and isinstance(node.value.func, ast.Name)
                      and node.value.func.id == "collect_all"]
        self.assertEqual(len(collection), 1)
        self.assert_expression(collection[0].value, 'collect_all("webview")')
        target = collection[0].targets[0]
        self.assertIsInstance(target, ast.Tuple)
        self.assertEqual([name.id for name in target.elts],
                         ["webview_data", "webview_binaries", "webview_imports"])
        exe = self.assignment("exe")
        self.assert_expression(exe.func, "EXE")
        self.assertIs(ast.literal_eval(self.keyword(exe, "console")), False)
        self.assertIs(ast.literal_eval(self.keyword(exe, "exclude_binaries")), True)
        collect = self.assignment("coll")
        self.assert_expression(collect.func, "COLLECT")
        self.assertTrue(any(ast.dump(argument) == ast.dump(ast.parse(
            "a.datas", mode="eval").body) for argument in collect.args))

    def test_existing_frontend_dist_references_only_present_local_assets(self):
        dist = self.root / "frontend" / "dist"
        if not dist.exists():
            self.skipTest("frontend/dist has not been built")
        index = dist / "index.html"
        self.assertTrue(index.is_file(), "Existing dist must contain index.html")
        html = index.read_text(encoding="utf-8")
        self.assertNotRegex(html, r"(?i)(?:https?:)?//|esm\.sh|unpkg|cdn\.")
        parser = AssetReferences()
        parser.feed(html)
        self.assertTrue(parser.module_scripts, "Missing bundled module script")
        self.assertTrue(all(parser.module_scripts), "Module scripts must reference bundled assets")
        self.assertTrue(parser.references, "Missing bundled assets")
        for reference in parser.references:
            with self.subTest(reference=reference):
                self.assertIsInstance(reference, str)
                url = urlsplit(reference)
                self.assertEqual(url.scheme, "")
                self.assertEqual(url.netloc, "")
                path = unquote(url.path)
                self.assertTrue(path, "Asset URL must contain a path")
                self.assertNotIn("\\", path)
                self.assertFalse(path.startswith("/"), "Assets must use relative URLs")
                relative = Path(path)
                self.assertEqual(relative.parts[0], "assets")
                self.assertNotIn("..", relative.parts)
                asset = (dist / relative).resolve()
                self.assertTrue(asset.is_relative_to((dist / "assets").resolve()))
                self.assertTrue(asset.is_file(), f"Missing local asset: {reference}")


if __name__ == "__main__":
    unittest.main()
