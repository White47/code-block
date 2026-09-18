"""Desktop controller keeps source access read-only and project-scoped."""

import builtins
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.core import SourceAnchor, ViewKind
from codeviz.ui.controller import AnalysisStatus, AppController


class ControllerTests(unittest.TestCase):
    def test_open_project_rejects_switch_during_analysis(self):
        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            controller = AppController()
            controller.open_project(Path(first))
            original_root = controller.project_root
            controller.status = AnalysisStatus.ANALYZING
            with self.assertRaises(RuntimeError):
                controller.open_project(Path(second))
            self.assertEqual(controller.project_root, original_root)
            self.assertEqual(controller.status, AnalysisStatus.ANALYZING)

    def test_open_requires_explicit_analysis_and_new_project_clears_old_facts(self):
        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            first_root, second_root = Path(first), Path(second)
            (first_root / "run.py").write_text(
                "def train(optimizer):\n    optimizer.step()\n", encoding="utf-8")
            (second_root / "other.py").write_text("answer = 42\n", encoding="utf-8")
            controller = AppController()
            controller.open_project(first_root)
            self.assertIsNone(controller.analysis)
            self.assertEqual(controller.status, AnalysisStatus.NOT_ANALYZED)
            with self.assertRaises(RuntimeError):
                controller.view(ViewKind.EXECUTION)
            analysis = controller.analyze()
            self.assertIs(analysis, controller.analysis)
            self.assertEqual(controller.status, AnalysisStatus.READY)
            self.assertTrue(controller.view(ViewKind.EXECUTION).nodes)
            controller.open_project(second_root)
            self.assertIsNone(controller.analysis)
            self.assertEqual(controller.status, AnalysisStatus.NOT_ANALYZED)

    def test_partial_analysis_keeps_available_result(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "good.py").write_text("value = 1\n", encoding="utf-8")
            (root / "bad.py").write_text("def broken(:\n", encoding="utf-8")
            controller = AppController()
            controller.open_project(root)
            result = controller.analyze()
            self.assertEqual(controller.status, AnalysisStatus.PARTIAL)
            self.assertIs(result, controller.analysis)
            self.assertTrue(any(item.relative_path == "good.py" and item.tree is not None
                                for item in result.files))

    def test_failed_analysis_has_no_stale_result(self):
        with TemporaryDirectory() as temporary:
            controller = AppController()
            controller.open_project(Path(temporary))
            with patch("codeviz.ui.controller.analyze_project", side_effect=OSError("scan failed")):
                with self.assertRaisesRegex(OSError, "scan failed"):
                    controller.analyze()
            self.assertEqual(controller.status, AnalysisStatus.FAILED)
            self.assertIsNone(controller.analysis)

    def test_source_navigation_returns_exact_lines_and_never_writes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "train.py"
            original = "def train():\n    loss = 1\n    return loss\n"
            source.write_text(original, encoding="utf-8")
            controller = AppController()
            controller.open_project(root)
            document = controller.read_source(SourceAnchor("train.py", 2, end_line=3))
            self.assertEqual(document.file_path, "train.py")
            self.assertEqual(document.start_line, 2)
            self.assertEqual(document.end_line, 3)
            self.assertIn("loss = 1", document.text)
            self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_source_navigation_rejects_symlink_escape(self):
        with TemporaryDirectory() as project, TemporaryDirectory() as outside:
            root = Path(project)
            target = Path(outside) / "secret.py"
            target.write_text("secret = 1\n", encoding="utf-8")
            link = root / "linked.py"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlink creation unavailable in this environment")
            controller = AppController()
            controller.open_project(root)
            with self.assertRaises(ValueError):
                controller.read_source(SourceAnchor("linked.py", 1))

    def test_source_navigation_rejects_file_changed_between_check_and_open(self):
        with TemporaryDirectory() as project, TemporaryDirectory() as outside:
            root = Path(project)
            (root / "public.py").write_text("public = 1\n", encoding="utf-8")
            secret = Path(outside) / "secret.py"
            secret.write_text("secret = 1\n", encoding="utf-8")
            controller = AppController()
            controller.open_project(root)

            def diverted_open(_path, *args, **kwargs):
                return builtins.open(secret, *args, **kwargs)

            with patch.object(Path, "open", diverted_open):
                with self.assertRaises(ValueError):
                    controller.read_source(SourceAnchor("public.py", 1))

    def test_open_handle_path_rejects_stale_resolve_and_stat(self):
        with TemporaryDirectory() as project, TemporaryDirectory() as outside:
            root = Path(project)
            public = root / "public.py"
            public.write_text("public = 1\n", encoding="utf-8")
            secret = Path(outside) / "secret.py"
            secret.write_text("secret = 1\n", encoding="utf-8")
            secret_stat = secret.stat()
            original_stat = Path.stat
            controller = AppController()
            controller.open_project(root)

            def stale_stat(path, *args, **kwargs):
                return secret_stat if path == public else original_stat(path, *args, **kwargs)

            def diverted_open(_path, *args, **kwargs):
                return builtins.open(secret, *args, **kwargs)

            with patch.object(Path, "stat", stale_stat), patch.object(Path, "open", diverted_open):
                with self.assertRaises(ValueError):
                    controller.read_source(SourceAnchor("public.py", 1))

    def test_expanding_view_does_not_change_analysis_facts(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text(
                "def train(loss, optimizer):\n"
                "    for epoch in range(2):\n"
                "        loss.backward()\n"
                "        optimizer.step()\n", encoding="utf-8")
            controller = AppController()
            controller.open_project(root)
            analysis = controller.analyze()
            original = analysis.semantic.graph
            collapsed = controller.view(ViewKind.EXECUTION)
            loop = next(node for node in collapsed.nodes if node.display_name == "EpochLoop")
            controller.expand(loop.id)
            expanded = controller.view(ViewKind.EXECUTION)
            self.assertGreater(len(expanded.nodes), len(collapsed.nodes))
            self.assertEqual(analysis.semantic.graph, original)
            controller.collapse(loop.id)
            self.assertEqual(controller.view(ViewKind.EXECUTION).nodes, collapsed.nodes)
            controller.move_node(loop.id, (90.0, 120.0))
            self.assertEqual(controller.view(ViewKind.EXECUTION).layout_hints[loop.id],
                             (90.0, 120.0))

    def test_reset_positions_keeps_analysis_and_navigation_state(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_text("def train(): return 1\n", encoding="utf-8")
            controller = AppController()
            controller.open_project(root)
            analysis = controller.analyze()
            controller.move_node("custom-node", (220.0, 100.0))
            controller.save_layout(ViewKind.ARCHITECTURE, None,
                                   ("graph:Architecture",), {})
            controller.reset_positions()
            self.assertEqual(controller.view_state.positions, {})
            self.assertEqual(controller.view_state.open_tabs, ("graph:Architecture",))
            self.assertIs(controller.analysis, analysis)



if __name__ == "__main__":
    unittest.main()
