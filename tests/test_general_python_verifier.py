"""Verifier self-tests: temporary sources only, never downloaded corpora."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_general_python.py"


class GeneralPythonVerifierTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            SCRIPT.is_file(), "verification harness has not been implemented"
        )
        spec = importlib.util.spec_from_file_location("general_python_verifier", SCRIPT)
        self.verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.verifier)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "app.py").write_text(
            'raise RuntimeError("TARGET MUST NEVER EXECUTE")\n'
            "def helper(value):\n    return value + 1\n"
            "def main():\n    value = helper(1)\n    return value\n"
            'if __name__ == "__main__": main()\n',
            encoding="utf-8",
        )

    def verify(self, **options):
        return self.verifier.verify_project(
            self.root, expected_symbols=(("app.py", "main"),), **options
        )

    def test_default_projects_use_exact_pinned_source_archive_paths(self):
        self.assertEqual(self.verifier.DEFAULT_PROJECTS, (
            "work/general-python-corpus/click/click-06b2a678741131fd577ce170e23e5ca0aeba0309/src",
            "work/general-python-corpus/requests/requests-611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60/src",
            "work/general-python-corpus/httpx/httpx-b5addb64f0161ff6bfe94c124ef76f6a1fba5254",
            "tests/fixtures/mvp_project",
        ))

    def test_default_cli_preserves_pinned_roots_and_their_symbol_profiles(self):
        roots = (
            "work/general-python-corpus/click/click-06b2a678741131fd577ce170e23e5ca0aeba0309/src",
            "work/general-python-corpus/requests/requests-611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60/src",
            "work/general-python-corpus/httpx/httpx-b5addb64f0161ff6bfe94c124ef76f6a1fba5254",
            "tests/fixtures/mvp_project",
        )
        profiles = (
            (("click/core.py", "click.core.Command.invoke"),
             ("click/decorators.py", "click.decorators.pass_context")),
            (("requests/sessions.py", "requests.sessions.Session.request"),
             ("requests/api.py", "requests.api.request")),
            (("httpx/_client.py", "httpx._client.Client.send"),
             ("httpx/_api.py", "httpx._api.request")),
            (("model.py", "Classifier.forward"), ("run.py", "train_epoch")),
        )
        # Routing check only; never read or execute downloaded source in this test.
        with patch.object(self.verifier, "verify_project", return_value={"status": "verified"}) as verify, \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(self.verifier.main([]), 0)
        self.assertEqual(len(verify.call_args_list), 4)
        for index, call in enumerate(verify.call_args_list):
            with self.subTest(index=index):
                self.assertEqual(call.args[0], self.verifier.REPO_ROOT / roots[index])
                self.assertEqual(call.kwargs["expected_symbols"], profiles[index])
                self.assertEqual(call.kwargs["expected_issues"],
                                 ("SYNTAX_ERROR",) if index == 3 else ())
                self.assertEqual(call.kwargs.get("package_name"),
                                 ("click", "requests", "httpx", None)[index])

    def test_package_parent_keeps_relative_imports_and_excludes_other_top_level_sources(self):
        (self.root / "app.py").unlink()
        package = self.root / "ordinary"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "helper.py").write_text("def helper(): return 1\n", encoding="utf-8")
        (package / "api.py").write_text(
            "from .helper import helper\ndef main(): return helper()\n", encoding="utf-8")
        (self.root / "outside.py").write_text("def broken(:\n", encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
        options = {"package_name": "ordinary", "expected_symbols": (("ordinary/api.py", "ordinary.api.main"),)}
        try:
            result = self.verifier.verify_project(self.root, **options)
        except TypeError as error:
            self.fail(f"harness must support an explicit package context: {error}")
        self.assertEqual(result["status"], "verified", result["checks"])
        self.assertEqual(result["metrics"]["files_total"], 3)
        self.assertNotIn("SYNTAX_ERROR", result["metrics"]["issues_by_code"])
        self.assertNotIn("IMPORT_UNRESOLVED", result["metrics"]["issues_by_code"])
        self.assertEqual(result["scope"]["module_prefix"], "ordinary")
        self.assertEqual(result["scope"]["ignore_paths"], ["outside.py", "tests"])
        self.assertTrue(all(item["name"].startswith("ordinary.") for item in result["symbol_samples"]))
        self.assertEqual(result["fingerprints"]["before"], result["fingerprints"]["after"])

    def test_unique_implementation_profile_coexists_with_legitimate_overload_definitions(self):
        (self.root / "app.py").write_text(
            "from typing import overload\n"
            "@overload\ndef overloaded(value: str) -> str: ...\n"
            "@overload\ndef overloaded(value: int) -> int: ...\n"
            "def overloaded(value): return value\n"
            "def actual_impl(value): return value\n", encoding="utf-8")
        result = self.verifier.verify_project(self.root, expected_symbols=(("app.py", "actual_impl"),))
        self.assertEqual(result["status"], "verified", result["checks"])
        self.assertEqual(result["metrics"]["overview_functions"], 4)

    def test_static_source_evidence_and_cache_round_trip(self):
        result = self.verify(max_nodes=4, max_edges=3, sample_size=2)
        self.assertEqual(result["status"], "verified", result["checks"])
        self.assertEqual(result["metrics"]["files_parsed"], 1)
        self.assertGreaterEqual(result["metrics"]["symbol_count"], 3)
        self.assertGreater(result["metrics"]["generic_entries"], 0)
        self.assertEqual(result["metrics"]["overview_functions"], 2)
        self.assertEqual(
            result["fingerprints"]["before"], result["fingerprints"]["after"]
        )
        self.assertTrue(result["cache"]["no_ast"])
        self.assertTrue(result["cache"]["facts_equal"])
        self.assertTrue(result["cache"]["views_equal"])
        self.assertFalse(result["full_graph_returned"])
        self.assertFalse(result["runtime_trace"])
        self.assertEqual(result["mode"], "static")
        self.assertFalse(result["feature_complete"])
        self.assertGreaterEqual(result["analysis_elapsed_seconds"], 0)
        self.assertEqual(
            result["expected_symbols"][0]["source_anchor"]["file_path"], "app.py"
        )
        self.assertEqual(
            result["expected_symbols"][0]["source_anchor"]["start_line"], 4
        )
        for graph in result["flow_graphs"]:
            self.assertLessEqual(graph["nodes"], 4)
            self.assertLessEqual(graph["edges"], 3)
            self.assertTrue(graph["source_backed"])

    def test_missing_expected_symbol_is_failed_not_count_success(self):
        result = self.verifier.verify_project(
            self.root, expected_symbols=(("app.py", "absent"),)
        )
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["expected_symbols"][0]["found"])

    def test_diagnostics_checked_by_code_with_source_anchor(self):
        (self.root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
        result = self.verify(expected_issues=("SYNTAX_ERROR",))
        self.assertEqual(result["status"], "verified", result["checks"])
        self.assertEqual(result["metrics"]["issues_by_code"]["SYNTAX_ERROR"], 1)
        check = next(
            item
            for item in result["checks"]
            if item["name"] == "diagnostic:SYNTAX_ERROR"
        )
        self.assertEqual(check["status"], "verified")

    def test_missing_expected_diagnostic_fails(self):
        self.assertEqual(
            self.verify(expected_issues=("SYNTAX_ERROR",))["status"], "failed"
        )

    def test_no_expected_symbols_is_explicitly_unverified(self):
        result = self.verifier.verify_project(self.root)
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["feature_complete"])

    def test_samples_are_repeatable_and_bounded(self):
        first = self.verify(sample_size=1, max_nodes=1, max_edges=1)
        second = self.verify(sample_size=1, max_nodes=1, max_edges=1)
        self.assertEqual(first["flow_graphs"], second["flow_graphs"])
        self.assertEqual(len(first["flow_graphs"]), 1)
        self.assertLessEqual(len(first["symbol_samples"]), 1)
        self.assertEqual(first["status"], "verified", first["checks"])

    def test_fingerprint_detects_content_and_membership_changes(self):
        before = self.verifier.source_fingerprint(self.root)
        (self.root / "extra.txt").write_text(
            "also protect non-Python files", encoding="utf-8"
        )
        after = self.verifier.source_fingerprint(self.root)
        self.assertNotEqual(before, after)
        (self.root / "app.py").write_text("changed = 1\n", encoding="utf-8")
        self.assertNotEqual(after, self.verifier.source_fingerprint(self.root))

    def test_missing_project_and_invalid_budgets(self):
        self.assertEqual(
            self.verifier.verify_project(self.root / "absent")["status"], "unverified"
        )
        for keyword in ("sample_size", "max_nodes", "max_edges"):
            with self.subTest(keyword=keyword), self.assertRaises(ValueError):
                self.verify(**{keyword: 0})

    def test_cli_explicit_project_emits_only_json_and_no_target_writes(self):
        before = self.verifier.source_fingerprint(self.root)
        process = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPT),
                "--project",
                str(self.root),
                "--expect",
                "app.py::main",
                "--sample-size",
                "1",
                "--max-nodes",
                "3",
                "--max-edges",
                "2",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        payload = json.loads(process.stdout)
        self.assertEqual(len(payload["projects"]), 1)
        self.assertEqual(payload["projects"][0]["status"], "verified")
        self.assertEqual(process.stderr, "")
        self.assertEqual(before, self.verifier.source_fingerprint(self.root))

    def test_cli_failure_and_unverified_exit_codes(self):
        for expectation, code, status in (
            ("app.py::absent", 1, "failed"),
            (None, 2, "unverified"),
        ):
            args = [sys.executable, "-B", str(SCRIPT), "--project", str(self.root)]
            if expectation:
                args.extend(("--expect", expectation))
            process = subprocess.run(args, capture_output=True, text=True, check=False)
            self.assertEqual(process.returncode, code, process.stdout + process.stderr)
            self.assertEqual(
                json.loads(process.stdout)["projects"][0]["status"], status
            )

    def test_cli_output_writes_same_json_and_preserves_target(self):
        before = self.verifier.source_fingerprint(self.root)
        with tempfile.TemporaryDirectory() as reports:
            output = Path(reports) / "nested" / "报告.json"
            process = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--project", str(self.root),
                 "--expect", "app.py::main", "--output", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            self.assertTrue(output.is_file(), "--output must persist the full report")
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved, json.loads(process.stdout))
            self.assertEqual(saved["projects"][0]["status"], "verified")
            self.assertTrue(saved["projects"][0]["cache"]["no_ast"])
            self.assertFalse(saved["feature_complete"])
            self.assertEqual(process.stderr, "")
        self.assertEqual(before, self.verifier.source_fingerprint(self.root))

    def test_cli_output_persists_failed_and_unverified_reports_with_exit_codes(self):
        with tempfile.TemporaryDirectory() as reports:
            for expectation, code, status in (
                ("app.py::absent", 1, "failed"), (None, 2, "unverified"),
            ):
                with self.subTest(status=status):
                    output = Path(reports) / (status + ".json")
                    args = [sys.executable, "-B", str(SCRIPT), "--project", str(self.root),
                            "--output", str(output)]
                    if expectation:
                        args.extend(("--expect", expectation))
                    process = subprocess.run(args, capture_output=True, text=True, check=False)
                    self.assertTrue(output.is_file(), "non-success evidence must also be saved")
                    saved = json.loads(output.read_text(encoding="utf-8"))
                    self.assertEqual(process.returncode, code, process.stdout + process.stderr)
                    self.assertEqual(saved, json.loads(process.stdout))
                    self.assertEqual(saved["projects"][0]["status"], status)

    def test_cli_output_rejects_any_location_inside_target_before_analysis(self):
        before = self.verifier.source_fingerprint(self.root)
        for output in (self.root / "app.py", self.root / "reports" / "result.json"):
            with self.subTest(output=output), \
                    patch.object(self.verifier, "verify_project") as verify, \
                    patch("sys.stderr", new_callable=io.StringIO) as errors:
                with self.assertRaises(SystemExit) as raised:
                    self.verifier.main(["--project", str(self.root), "--output", str(output)])
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("outside analyzed project roots", errors.getvalue())
                verify.assert_not_called()
        self.assertEqual(before, self.verifier.source_fingerprint(self.root))

    def test_cli_output_write_error_is_nonzero_without_losing_stdout_evidence(self):
        with tempfile.TemporaryDirectory() as reports:
            # Existing directory is not a writable report file; no permissions tricks.
            process = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--project", str(self.root),
                 "--expect", "app.py::main", "--output", reports],
                capture_output=True, text=True, check=False,
            )
            self.assertTrue(process.stdout.strip(), "analysis evidence must survive output failure")
            self.assertEqual(json.loads(process.stdout)["projects"][0]["status"], "verified")
            self.assertEqual(process.returncode, 1)
            self.assertIn("cannot write report", process.stderr)
            self.assertNotIn("Traceback", process.stderr)

    def test_cli_output_hardlink_replaces_report_entry_without_changing_source(self):
        source = self.root / "app.py"
        content = source.read_bytes()
        identity = (source.stat().st_dev, source.stat().st_ino)
        with tempfile.TemporaryDirectory() as reports:
            output = Path(reports) / "report.json"
            os.link(source, output)
            self.assertTrue(os.path.samefile(source, output))
            before = self.verifier.source_fingerprint(self.root)
            process = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--project", str(self.root),
                 "--expect", "app.py::main", "--output", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(source.read_bytes(), content, "report must never write through a source hardlink")
            self.assertEqual((source.stat().st_dev, source.stat().st_ino), identity)
            self.assertEqual(before, self.verifier.source_fingerprint(self.root))
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            self.assertFalse(os.path.samefile(source, output))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), json.loads(process.stdout))
            self.assertEqual(list(Path(reports).iterdir()), [output])

    def test_cli_output_replace_failure_preserves_previous_report_and_removes_only_temp(self):
        before = self.verifier.source_fingerprint(self.root)
        with tempfile.TemporaryDirectory() as reports:
            output = Path(reports) / "report.json"
            original = b'{"previous": true}\n'
            output.write_bytes(original)
            with patch.object(self.verifier.os, "replace", side_effect=OSError("replace denied")), \
                    patch("sys.stdout", new_callable=io.StringIO) as stdout, \
                    patch("sys.stderr", new_callable=io.StringIO) as stderr:
                code = self.verifier.main(["--project", str(self.root), "--expect", "app.py::main",
                                           "--output", str(output)])
            self.assertEqual(code, 1)
            self.assertEqual(output.read_bytes(), original, "failed publication must not truncate the old report")
            self.assertEqual(list(Path(reports).iterdir()), [output])
            self.assertEqual(json.loads(stdout.getvalue())["projects"][0]["status"], "verified")
            self.assertIn("cannot write report", stderr.getvalue())
        self.assertEqual(before, self.verifier.source_fingerprint(self.root))

    def test_cli_output_rechecks_source_after_report_publication(self):
        source = self.root / "app.py"
        real_replace = os.replace

        def replace_then_change_source(temporary, output):
            real_replace(temporary, output)
            stat = source.stat()
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

        with tempfile.TemporaryDirectory() as reports, \
                patch.object(self.verifier.os, "replace", side_effect=replace_then_change_source), \
                patch("sys.stdout", new_callable=io.StringIO), \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            code = self.verifier.main(["--project", str(self.root), "--expect", "app.py::main",
                                       "--output", str(Path(reports) / "report.json")])
        self.assertEqual(code, 1, "post-publication mutation must not return verified exit 0")
        self.assertIn("target changed after report output", stderr.getvalue())

    def test_unavailable_analysis_or_function_flow_never_fake_passes(self):
        with patch.object(
            self.verifier, "load_api", side_effect=ImportError("Task1 pending")
        ):
            result = self.verify()
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(
            result["fingerprints"]["before"], result["fingerprints"]["after"]
        )
        api = self.verifier.load_api()
        api.ViewKind = SimpleNamespace()
        with patch.object(self.verifier, "load_api", return_value=api):
            result = self.verify()
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["flow_graphs"], [])
        self.assertEqual(result["cache"], {})

    def test_detects_target_mutation_during_analysis(self):
        api = self.verifier.load_api()
        real_analyze = api.analyze_project

        def mutating_analysis(root):
            result = real_analyze(root)
            (root / "unexpected.txt").write_text("unexpected write", encoding="utf-8")
            return result

        api.analyze_project = mutating_analysis
        with patch.object(self.verifier, "load_api", return_value=api):
            result = self.verify()
        self.assertEqual(result["status"], "failed")
        self.assertNotEqual(
            result["fingerprints"]["before"], result["fingerprints"]["after"]
        )

    def test_partial_cfg_diagnostic_is_visible_in_sampled_graph(self):
        (self.root / "app.py").write_text(
            "async def main(callback):\n    return await callback()\n", encoding="utf-8"
        )
        result = self.verify(expected_issues=("CFG_PARTIAL",), sample_size=1)
        self.assertEqual(result["status"], "verified", result["checks"])
        self.assertGreater(result["flow_graphs"][0]["issues_by_code"]["CFG_PARTIAL"], 0)

    def test_fingerprint_read_failure_returns_unverified_evidence(self):
        with patch.object(
            self.verifier,
            "source_fingerprint",
            side_effect=OSError("cannot read target"),
        ):
            try:
                result = self.verify()
            except OSError as error:
                self.fail(
                    f"read failure must be JSON evidence, not an uncaught exception: {error}"
                )
        self.assertEqual(result["status"], "unverified")
        self.assertTrue(
            any(item["name"] == "source_fingerprint" for item in result["checks"])
        )

    def test_missing_generic_main_evidence_cannot_pass_on_counts(self):
        api = self.verifier.load_api()
        real_analyze = api.analyze_project
        api.analyze_project = lambda root: replace(
            real_analyze(root), generic_entries=()
        )
        with patch.object(self.verifier, "load_api", return_value=api):
            result = self.verify()
        self.assertEqual(result["status"], "failed", result["checks"])


class MLNativeSelectorRegressionTests(unittest.TestCase):
    def test_overview_choices_are_scoped_to_model_and_entry_cards(self):
        path = SCRIPT.with_name("verify_native_desktop.py")
        spec = importlib.util.spec_from_file_location("ml_native_selectors", path)
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        choose = getattr(native, "overview_choice", None)
        self.assertIsNotNone(choose, "ML choices must be scoped away from generic catalogs")
        for category, name, valid_heading, excluded_heading in (
            ("models", "model.Classifier", "模型4", "类"),
            ("entries", "train_epoch", "入口2", "函数与方法11"),
        ):
            with self.subTest(category=category):
                # Selector-routing unit check only; live native golden checks
                # still verify the actual browser, graph and source interactions.
                page = Mock()
                cards, headings, selected, rows, choice = (Mock() for _ in range(5))
                page.locator.side_effect = [cards, headings]
                headings.filter.return_value = headings
                cards.filter.return_value = selected
                selected.locator.return_value = rows
                rows.filter.return_value = choice
                with patch.object(native, "expect") as expect:
                    self.assertIs(choose(page, category, name), choice)
                self.assertEqual([call.args[0] for call in page.locator.call_args_list],
                                 [".overview-card", ".card-heading"])
                pattern = headings.filter.call_args.kwargs["has_text"]
                self.assertIsNotNone(pattern.fullmatch(valid_heading))
                self.assertIsNone(pattern.fullmatch(excluded_heading))
                cards.filter.assert_called_once_with(has=headings)
                selected.locator.assert_called_once_with(".entity-row")
                rows.filter.assert_called_once_with(has_text=name)
                expect.assert_any_call(choice)
                expect.return_value.to_have_count.assert_called_with(1)


class NativeVerifierRegressionTests(unittest.TestCase):
    def setUp(self):
        path = SCRIPT.with_name("verify_general_python_native.py")
        spec = importlib.util.spec_from_file_location("native_verifier_regression", path)
        self.native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.native)
        self.project = SCRIPT.parents[1] / "tests/fixtures/general_python_project"
        self.page = Mock()
        self.runner = self.native.NativeAcceptance(self.page, self.project, None, {}, None)

    def test_session_waits_for_completed_bridge_response_after_stale_none(self):
        self.page.evaluate.side_effect = [
            {"ok": True, "data": {"projectPath": None, "status": "NotAnalyzed"}},
            {"ok": True, "data": {"projectPath": str(self.project), "status": "NotAnalyzed"}},
        ]
        session = self.runner.session_for(self.project)
        self.assertEqual(session["projectPath"], str(self.project))
        self.assertEqual(self.page.evaluate.call_count, 2)
        self.page.wait_for_function.assert_not_called()
        self.page.wait_for_timeout.assert_called_once_with(100)

    def test_analyzed_session_waits_for_matching_project_and_finished_status(self):
        self.page.evaluate.side_effect = [
            {"ok": True, "data": {"projectPath": str(self.project.parent / "mvp_project"), "status": "Ready"}},
            {"ok": True, "data": {"projectPath": str(self.project), "status": "Analyzing"}},
            {"ok": True, "data": {"projectPath": str(self.project), "status": "Partial"}},
        ]
        self.assertEqual(self.runner.session_for(self.project, analyzed=True)["status"], "Partial")
        self.assertEqual(self.page.evaluate.call_count, 3)
        self.page.wait_for_function.assert_not_called()

    def test_repeat_run_uses_other_seeded_recent_project_for_cache_reopen(self):
        choose = getattr(self.native, "cache_switch_project", None)
        self.assertIsNotNone(choose, "cache reopen must choose a different seeded recent on repeat runs")
        previous = str(self.project.parent / "mvp_project")
        session = {"projectPath": str(self.project),
                   "recentProjects": [str(self.project), previous]}
        self.assertEqual(choose(session, self.project), previous)


class NativeVerifierOptionalDependencyTests(unittest.TestCase):
    """Pure helpers must work even when native browser automation is unavailable."""

    def run_without_playwright(self, script_name, body, *arguments):
        # A fresh interpreter cannot reuse this suite's imports or the developer's
        # installed Playwright. The finder raises rather than supplying a fake package.
        bootstrap = '''
import importlib.abc
import importlib.util
import sys

class BlockPlaywright(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "playwright" or fullname.startswith("playwright."):
            raise ModuleNotFoundError(
                "Playwright deliberately unavailable in isolated regression: " + fullname,
                name=fullname,
            )

assert not any(name == "playwright" or name.startswith("playwright.") for name in sys.modules)
sys.meta_path.insert(0, BlockPlaywright())
spec = importlib.util.spec_from_file_location("isolated_native_verifier", sys.argv[1])
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
print("NATIVE_IMPORT_OK", flush=True)
'''
        return subprocess.run(
            [sys.executable, "-B", "-c", bootstrap + body,
             str(SCRIPT.with_name(script_name)), *map(str, arguments)],
            capture_output=True, text=True, check=False, timeout=20,
        )

    def test_native_scripts_dynamically_import_without_playwright(self):
        for name in ("verify_native_desktop.py", "verify_general_python_native.py"):
            with self.subTest(script=name):
                process = self.run_without_playwright(name, '''
assert callable(native.main)
assert not any(name == "playwright" or name.startswith("playwright.") for name in sys.modules)
print("PURE_IMPORT_OK")
''')
                self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                self.assertIn("PURE_IMPORT_OK", process.stdout)
                self.assertEqual(process.stderr, "")

    def test_overview_choice_routes_models_and_entries_without_playwright(self):
        process = self.run_without_playwright("verify_native_desktop.py", '''
from unittest.mock import Mock, patch

for category, name, valid_heading, excluded_heading in (
    ("models", "model.Classifier", "Models 4", "Classes 4"),
    ("entries", "train_epoch", "Entry points 2", "Functions and methods 11"),
):
    page = Mock()
    cards, headings, selected, rows, choice = (Mock() for _ in range(5))
    page.locator.side_effect = [cards, headings]
    headings.filter.return_value = headings
    cards.filter.return_value = selected
    selected.locator.return_value = rows
    rows.filter.return_value = choice
    # Only the browser assertion boundary is mocked, never the missing package.
    with patch.object(native, "expect", create=True) as expect:
        assert native.overview_choice(page, category, name) is choice
        expect.assert_any_call(choice)
        expect.return_value.to_have_count.assert_called_with(1)
    assert [call.args[0] for call in page.locator.call_args_list] == [".overview-card", ".card-heading"]
    pattern = headings.filter.call_args.kwargs["has_text"]
    assert pattern.fullmatch(valid_heading)
    assert not pattern.fullmatch(excluded_heading)
    cards.filter.assert_called_once_with(has=headings)
    selected.locator.assert_called_once_with(".entity-row")
    rows.filter.assert_called_once_with(has_text=name)
assert not any(name == "playwright" or name.startswith("playwright.") for name in sys.modules)
print("OVERVIEW_CHOICE_OK")
''')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn("OVERVIEW_CHOICE_OK", process.stdout)
        self.assertEqual(process.stderr, "")

    def test_actual_native_main_reports_missing_playwright_after_successful_import(self):
        with tempfile.TemporaryDirectory() as output:
            process = self.run_without_playwright("verify_native_desktop.py", '''
sys.argv = [sys.argv[1], "--output", sys.argv[2]]
native.main()
print("UNEXPECTED_NATIVE_SUCCESS")
''', output)
        # The marker distinguishes a genuine runtime dependency error from the
        # top-level import failure that broke CI before main could be called.
        self.assertIn("NATIVE_IMPORT_OK", process.stdout, process.stderr)
        self.assertNotEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn("playwright", process.stderr.lower())
        self.assertNotIn("NameError", process.stderr)
        self.assertNotIn("UNEXPECTED_NATIVE_SUCCESS", process.stdout)


if __name__ == "__main__":
    unittest.main()
