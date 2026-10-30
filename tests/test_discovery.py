import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from codeviz.analysis import discovery
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


class StrictDiscoveryTests(unittest.TestCase):
    def setUp(self):
        for name in ("discover_python_files_strict", "DiscoveryScanError"):
            self.assertTrue(hasattr(discovery, name), f"Missing B4 discovery API: {name}")
        self.scan = discovery.discover_python_files_strict
        self.error = discovery.DiscoveryScanError
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def rejected(self, code, **kwargs):
        with self.assertRaises(self.error) as caught:
            self.scan(self.root, **kwargs)
        self.assertEqual(caught.exception.code, code)
        messages = {
            "ROOT_UNVERIFIED": "Project root could not be verified.",
            "SCAN_INCOMPLETE": "Project source scan is incomplete.",
            "LIMIT_EXCEEDED": "Project source scan limit exceeded.",
        }
        self.assertEqual(str(caught.exception), messages[code])
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertNotIn(str(self.root), str(caught.exception))

    def test_strict_scan_default_literal_ignores_and_uppercase_suffix(self):
        for name in ("a.PY", "pkg/app.py", ".GIT/hidden.py", "generated/a.py",
                     "generated_more/a.py", "plain.txt"):
            write_file(self.root, name)
        self.assertEqual(self.scan(self.root, ("generated",)),
                         ("a.PY", "generated_more/a.py", "pkg/app.py"))

    def test_strict_scan_does_not_swallow_scandir_error(self):
        write_file(self.root, "safe.py")
        (self.root / "blocked").mkdir()
        native = os.scandir

        def guarded(directory):
            if Path(directory) == self.root / "blocked":
                raise PermissionError("PRIVATE-PATH-DO-NOT-LEAK")
            return native(directory)

        with patch.object(discovery.os, "scandir", side_effect=guarded):
            self.rejected("SCAN_INCOMPLETE")

    def test_strict_scan_iteration_and_entry_stat_errors_are_not_partial_success(self):
        write_file(self.root, "safe.py")
        with os.scandir(self.root) as entries:
            entry = next(entries)

        class BrokenScan:
            def __enter__(inner):
                return inner

            def __exit__(inner, *args):
                return False

            def __iter__(inner):
                yield entry
                raise PermissionError("PRIVATE-ITERATOR")

        bad_entry = SimpleNamespace(name="bad.py", path=str(self.root / "bad.py"))
        bad_entry.stat = lambda **kwargs: (_ for _ in ()).throw(OSError("PRIVATE-STAT"))
        for scanner in (BrokenScan(), ScanEntries((bad_entry,))):
            with self.subTest(scanner=type(scanner).__name__):
                with patch.object(discovery.os, "scandir", return_value=scanner):
                    self.rejected("SCAN_INCOMPLETE")

    def test_strict_scan_exact_entries_directories_and_files_limits(self):
        write_file(self.root, "pkg/a.py")
        write_file(self.root, "note.txt")
        self.assertEqual(self.scan(self.root, max_entries=3, max_directories=2, max_files=1),
                         ("pkg/a.py",))
        for kwargs in ({"max_entries": 2}, {"max_directories": 1}):
            with self.subTest(limits=kwargs):
                self.rejected("LIMIT_EXCEEDED", **kwargs)
        write_file(self.root, "b.py")
        self.rejected("LIMIT_EXCEEDED", max_files=1)

    def test_strict_scan_ignored_entries_still_count(self):
        write_file(self.root, ".git/ignored.py")
        write_file(self.root, "safe.py")
        self.assertEqual(self.scan(self.root, max_entries=2, max_directories=1), ("safe.py",))
        self.rejected("LIMIT_EXCEEDED", max_entries=1)

    def test_strict_scan_nonregular_reparse_and_case_alias_candidates(self):
        info = (self.root).lstat()
        base = dict(st_dev=info.st_dev, st_ino=info.st_ino, st_size=0,
                    st_mtime_ns=info.st_mtime_ns, st_ctime_ns=info.st_ctime_ns,
                    st_file_attributes=0)

        def candidate(name, mode, attributes=0):
            values = dict(base, st_mode=mode, st_file_attributes=attributes)
            return SimpleNamespace(name=name, path=str(self.root / name),
                                   stat=lambda **kwargs: SimpleNamespace(**values))

        for mode in (stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR):
            with self.subTest(mode=mode):
                with patch.object(discovery.os, "scandir",
                                  return_value=ScanEntries((candidate("bad.py", mode),))):
                    self.rejected("SCAN_INCOMPLETE")
        links = (candidate("link.py", stat.S_IFLNK),
                 candidate("junction", stat.S_IFDIR, 0x400))
        with patch.object(discovery.os, "scandir", return_value=ScanEntries(links)) as scan:
            self.assertEqual(self.scan(self.root), ())
            self.assertEqual(scan.call_count, 1)
        # Cross-platform conservative Windows alias rejection, even on case-sensitive disks.
        aliases = (candidate("A.py", stat.S_IFREG), candidate("a.py", stat.S_IFREG))
        for entries in (aliases, (candidate("bad:stream.py", stat.S_IFREG),),
                        (candidate("same.py", stat.S_IFREG),) * 2):
            with self.subTest(names=tuple(e.name for e in entries)):
                with patch.object(discovery.os, "scandir", return_value=ScanEntries(entries)):
                    self.rejected("SCAN_INCOMPLETE")

    def test_strict_scan_root_missing_file_link_and_zero_identity(self):
        native = Path.lstat
        info = self.root.lstat()
        values = {name: getattr(info, name) for name in
                  ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
        cases = ({"st_ino": 0}, {"st_dev": 0}, {"st_mode": stat.S_IFREG},
                 {"st_mode": stat.S_IFLNK}, {"st_file_attributes": 0x400})
        for changed in cases:
            with self.subTest(root=changed):
                def observe(path, *args, changed=changed, **kwargs):
                    if path == self.root:
                        return SimpleNamespace(**dict(values, **changed))
                    return native(path, *args, **kwargs)
                with patch.object(Path, "lstat", observe):
                    self.rejected("ROOT_UNVERIFIED")
        with self.assertRaises(self.error) as caught:
            self.scan(self.root / "missing")
        self.assertEqual(caught.exception.code, "ROOT_UNVERIFIED")

    def test_strict_scan_directory_state_change_is_incomplete(self):
        write_file(self.root, "safe.py")
        native = Path.lstat
        calls = 0

        def observe(path, *args, **kwargs):
            nonlocal calls
            info = native(path, *args, **kwargs)
            if path != self.root:
                return info
            calls += 1
            values = {name: getattr(info, name) for name in
                      ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
            values["st_mtime_ns"] += calls
            return SimpleNamespace(**values)

        with patch.object(Path, "lstat", observe):
            self.rejected("SCAN_INCOMPLETE")

    def test_strict_scan_checkpoint_cancel_and_timeout_propagate(self):
        from codeviz.assistant.contracts import AgentError

        write_file(self.root, "safe.py")
        for code in ("cancelled", "timeout"):
            with self.subTest(code=code):
                error = AgentError(code)
                with self.assertRaises(AgentError) as caught:
                    self.scan(self.root, checkpoint=lambda error=error: (_ for _ in ()).throw(error))
                self.assertIs(caught.exception, error)

    def test_strict_scan_real_symlink_is_not_followed(self):
        write_file(self.root, "safe.py")
        try:
            (self.root / "link.py").symlink_to(self.root / "safe.py")
            (self.root / "linked_dir").symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest("native symlink creation unavailable")
        self.assertEqual(self.scan(self.root), ("safe.py",))


class ScanEntries:
    """scandir context protocol for injected native observation failures."""

    def __init__(self, entries):
        self.entries = entries

    def __enter__(self):
        return iter(self.entries)

    def __exit__(self, *args):
        return False


if __name__ == "__main__":
    unittest.main()
