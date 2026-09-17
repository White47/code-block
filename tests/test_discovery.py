import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codeviz.analysis.discovery import discover_python_files


def write_file(root: Path, relative_path: str, content: str = "pass\n") -> None:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


class DiscoveryTests(unittest.TestCase):
    def test_scans_python_files_and_respects_default_and_custom_ignores(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path in (
                "app/main.py", "scripts/train.py", ".venv/hidden.py",
                "node_modules/hidden.py", "build/hidden.py", "weights/hidden.py",
                "generated/hidden.py", "app/data.bin",
            ):
                write_file(root, path)

            result = discover_python_files(root, ignore_paths=("generated",))

            self.assertEqual(result, ("app/main.py", "scripts/train.py"))

    def test_rejects_ignore_paths_that_escape_the_project(self):
        with TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                discover_python_files(Path(temporary), ignore_paths=("../outside",))

    def test_skips_symlinked_python_files_outside_the_project(self):
        with TemporaryDirectory() as project, TemporaryDirectory() as outside:
            root = Path(project)
            write_file(root, "safe.py")
            external = Path(outside) / "external.py"
            external.write_text("pass\n", encoding="utf-8")
            try:
                (root / "linked.py").symlink_to(external)
            except OSError:
                self.skipTest("file symlinks are unavailable on this machine")

            self.assertEqual(discover_python_files(root), ("safe.py",))


if __name__ == "__main__":
    unittest.main()
