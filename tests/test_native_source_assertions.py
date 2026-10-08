"""Monaco assertions must bind visible decoration, number and exact source text."""

import ast
import importlib
import importlib.util
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock


class NativeSourceAssertionTests(unittest.TestCase):
    def setUp(self):
        self.helper()

    def helper(self):
        self.assertIsNotNone(importlib.util.find_spec("scripts.native_source_assertions"),
                             "Monaco native source assertion helper is missing")
        return importlib.import_module("scripts.native_source_assertions")

    def snapshot(self):
        return {"filePath": "helpers.py", "range": "L900–902", "editorVisible": True,
                "viewportHeight": 200, "lineHeight": 20, "renderedLineCount": 12,
                "decorations": [{"top": 40, "visible": True}],
                "numbers": [{"top": 40, "text": "900", "visible": True}],
                "lines": [{"top": 40, "text": "    value\u00a0=\u00a01", "visible": True}]}

    def validate(self, snapshot):
        return self.helper().assert_source_snapshot(
            snapshot, "helpers.py", 900, 902, "    value = 1")

    def test_virtualized_far_anchor_matches_exact_line_with_nbsp_normalization(self):
        self.validate(self.snapshot())

    def test_wrong_file_or_range_is_rejected(self):
        self.helper()
        for field, value in (("filePath", "other.py"), ("range", "L899–902")):
            snapshot = self.snapshot()
            snapshot[field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                self.validate(snapshot)

    def test_snippet_on_different_rendered_line_cannot_satisfy_anchor(self):
        snapshot = self.snapshot()
        snapshot["lines"] = [{"top": 20, "text": "    value = 1", "visible": True},
                             {"top": 40, "text": "    value = 2", "visible": True}]
        with self.assertRaises(AssertionError):
            self.validate(snapshot)

    def test_decoration_on_wrong_line_cannot_satisfy_anchor(self):
        snapshot = self.snapshot()
        snapshot["decorations"][0]["top"] = 20
        with self.assertRaises(AssertionError):
            self.validate(snapshot)

    def test_missing_decoration_line_number_or_text_is_rejected(self):
        self.helper()
        for field in ("decorations", "numbers", "lines"):
            snapshot = self.snapshot()
            snapshot[field] = []
            with self.subTest(field=field), self.assertRaises(AssertionError):
                self.validate(snapshot)

    def test_hidden_editor_or_anchor_nodes_are_rejected(self):
        self.helper()
        base = self.snapshot()
        hidden = deepcopy(base)
        hidden["editorVisible"] = False
        with self.assertRaises(AssertionError):
            self.validate(hidden)
        for field in ("decorations", "numbers", "lines"):
            hidden = deepcopy(base)
            hidden[field][0]["visible"] = False
            with self.subTest(field=field), self.assertRaises(AssertionError):
                self.validate(hidden)

    def test_substring_does_not_replace_exact_snippet_check(self):
        snapshot = self.snapshot()
        snapshot["lines"][0]["text"] += " # unexpected"
        with self.assertRaises(AssertionError):
            self.validate(snapshot)

    def test_wrong_line_number_with_right_text_and_decoration_is_rejected(self):
        snapshot = self.snapshot()
        snapshot["numbers"][0]["text"] = "901"
        with self.assertRaises(AssertionError):
            self.validate(snapshot)

    def test_unbounded_dom_rendering_is_rejected(self):
        snapshot = self.snapshot()
        snapshot["renderedLineCount"] = 1000
        with self.assertRaises(AssertionError):
            self.validate(snapshot)

    def test_mvp_native_zero_alert_checks_are_scoped_to_application(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "verify_native_desktop.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        checks = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "to_have_count" and len(node.args) == 1
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value == 0):
                selectors = [child.args[0].value for child in ast.walk(node.func.value)
                             if isinstance(child, ast.Call)
                             and isinstance(child.func, ast.Attribute)
                             and child.func.attr == "locator" and child.args
                             and isinstance(child.args[0], ast.Constant)
                             and isinstance(child.args[0].value, str)
                             and 'role="alert"' in child.args[0].value]
                checks.extend(selectors)
        self.assertEqual(checks, ['.desktop-shell [role="alert"]'] * 2,
                         "Keep both application alert zero-count checks; exclude Monaco globals by scope")

    def test_mvp_native_uncaught_pageerror_assertion_is_retained(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "verify_native_desktop.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        self.assertTrue(any(isinstance(node, ast.Assert)
                            and isinstance(node.test, ast.UnaryOp)
                            and isinstance(node.test.op, ast.Not)
                            and isinstance(node.test.operand, ast.Name)
                            and node.test.operand.id == "errors" for node in ast.walk(tree)))
        self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "on" and node.args
                            and isinstance(node.args[0], ast.Constant)
                            and node.args[0].value == "pageerror" for node in ast.walk(tree)))

    def screenshot_helper(self):
        helper = getattr(self.helper(), "capture_screenshot", None)
        self.assertTrue(callable(helper), "Bounded native screenshot helper is missing")
        return helper

    def test_screenshot_timeout_recovers_once_with_front_before_each_attempt(self):
        class ScreenshotTimeout(Exception):
            pass

        page = Mock()
        page.screenshot.side_effect = [ScreenshotTimeout("first timeout"), b"image"]
        result = self.screenshot_helper()(
            page, path="evidence.png", full_page=True, timeout_type=ScreenshotTimeout)
        self.assertEqual(result, b"image")
        self.assertEqual([call[0] for call in page.mock_calls],
                         ["bring_to_front", "screenshot", "bring_to_front", "screenshot"])
        self.assertEqual(page.screenshot.call_count, 2)
        for call in page.screenshot.call_args_list:
            self.assertEqual(call.kwargs, {"path": "evidence.png", "full_page": True,
                                           "timeout": 10_000})

    def test_screenshot_second_timeout_is_raised_without_third_attempt(self):
        class ScreenshotTimeout(Exception):
            pass

        final = ScreenshotTimeout("final timeout")
        page = Mock()
        page.screenshot.side_effect = [ScreenshotTimeout("first timeout"), final]
        helper = self.screenshot_helper()
        with self.assertRaises(ScreenshotTimeout) as caught:
            helper(page, path="evidence.png", full_page=True, timeout_type=ScreenshotTimeout)
        self.assertIs(caught.exception, final)
        self.assertEqual(page.screenshot.call_count, 2)
        self.assertEqual(page.bring_to_front.call_count, 2)

    def test_screenshot_other_error_propagates_without_retry(self):
        class ScreenshotTimeout(Exception):
            pass

        failure = RuntimeError("disconnected")
        page = Mock()
        page.screenshot.side_effect = failure
        helper = self.screenshot_helper()
        with self.assertRaises(RuntimeError) as caught:
            helper(page, path="evidence.png", full_page=True, timeout_type=ScreenshotTimeout)
        self.assertIs(caught.exception, failure)
        self.assertEqual(page.screenshot.call_count, 1)
        self.assertEqual(page.bring_to_front.call_count, 1)
