"""Check the frozen optional SDK closure without starting native windows."""

import ast
import importlib.util
import runpy
import ssl
import tempfile
import tomllib
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from codeviz.assistant.sdk_provider import _MODULES


class SDKPackagingTests(unittest.TestCase):
    def test_spec_preserves_runtime_ssl_policy_without_requiring_it_on_standard_python(self):
        spec = Path(__file__).resolve().parents[1] / 'scripts/codeviz.spec'
        hooks = types.ModuleType('PyInstaller.utils.hooks')
        hooks.collect_all = Mock(return_value=([], [], []))
        hooks.copy_metadata = Mock(return_value=[])
        modules = {'PyInstaller': types.ModuleType('PyInstaller'),
                   'PyInstaller.utils': types.ModuleType('PyInstaller.utils'),
                   'PyInstaller.utils.hooks': hooks}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'frontend/dist').mkdir(parents=True)
            (root / 'frontend/dist/index.html').write_text('owned fixture', encoding='utf-8')
            policy = root / 'codex-rejected-cas.pem'
            for present in (False, True):
                with self.subTest(runtime_policy=present):
                    if present:
                        policy.write_bytes(b'owned policy fixture')
                    analysis = Mock(return_value=SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[]))
                    with patch.dict('sys.modules', modules), patch.object(ssl, '__file__', str(root / 'ssl.py')):
                        runpy.run_path(str(spec), init_globals={'SPECPATH': str(root / 'scripts'),
                            'Analysis': analysis, 'PYZ': Mock(), 'EXE': Mock(), 'COLLECT': Mock()})
                    datas = analysis.call_args.kwargs['datas']
                    matches = [(source, target) for source, target in datas
                               if Path(source).name == policy.name]
                    self.assertEqual(matches, [(str(policy), '.')] if present else [])

    def test_build_preflight_rejects_foreign_native_module_origin(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts/build-desktop.ps1").read_text(encoding="utf-8")
        code = script.split("$dependencyCheck = @'", 1)[1].split("'@", 1)[0]
        original = importlib.util.find_spec

        def spec(name, *args, **kwargs):
            if name == "webview":
                return SimpleNamespace(origin=str(root / "build/foreign/webview/__init__.py"))
            return original(name, *args, **kwargs)

        with patch.object(importlib.util, "find_spec", side_effect=spec), self.assertRaisesRegex(
                RuntimeError, "outside"):
            exec(compile(code, "desktop-preflight", "exec"), {})

    def test_windows_native_interop_dependency_is_fixed(self):
        root = Path(__file__).resolve().parents[1]
        configuration = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        desktop = configuration["project"]["optional-dependencies"]["desktop"]
        self.assertIn("pythonnet==3.2.0; sys_platform == 'win32'", desktop)

    def test_spec_names_only_the_dynamic_sdk_entrypoints_and_distribution_metadata(self):
        spec = Path(__file__).resolve().parents[1] / "scripts" / "codeviz.spec"
        tree = ast.parse(spec.read_text(encoding="utf-8"))
        values = {node.targets[0].id: node.value for node in tree.body
                  if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
        self.assertIn("sdk_imports", values, "Frozen SDK dynamic imports are missing")
        self.assertEqual(set(ast.literal_eval(values["sdk_imports"])), set(_MODULES.values()))
        names = ast.literal_eval(values["sdk_distributions"])
        self.assertTrue({"pydantic-ai-slim", "openai", "httpx2", "httpcore2", "pydantic"} <= set(names))
        self.assertNotIn('collect_all("pydantic_ai")', spec.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
