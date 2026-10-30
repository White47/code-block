import os
import subprocess
import sys
import textwrap
import unittest
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from codeviz import source_handles


class SourceHandleTests(unittest.TestCase):
    def setUp(self):
        for name in ("SourceRead", "SourceReadError", "read_source_bytes"):
            self.assertTrue(hasattr(source_handles, name), f"missing safe-reading API: {name}")
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / "app.py"
        self.path.write_bytes(b"x = 1\n")

    def read(self, relative="app.py", limit=100):
        return source_handles.read_source_bytes(self.root, relative, max_bytes=limit)

    def assert_code(self, code, operation):
        with self.assertRaises(source_handles.SourceReadError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)
        self.assertIsInstance(caught.exception, ValueError)
        self.assertIsInstance(caught.exception.message, str)
        self.assertEqual(str(caught.exception), caught.exception.message)
        self.assertNotIn(str(self.root), str(caught.exception))
        return caught.exception

    def test_native_exact_bytes_empty_and_limit(self):
        for raw in (b"", b"# coding: latin-1\r\nname = '\xe9'\r\n", b"a" * 131073):
            with self.subTest(length=len(raw)):
                self.path.write_bytes(raw)
                result = self.read(limit=max(1, len(raw)))
                self.assertIsInstance(result, source_handles.SourceRead)
                self.assertEqual(result.raw_bytes, raw)
                self.assertEqual(result.content_hash, sha256(raw).hexdigest())

    def test_result_is_frozen_and_slotted(self):
        result = self.read()
        self.assertFalse(hasattr(result, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            result.raw_bytes = b"changed"
        with self.assertRaises(FrozenInstanceError):
            result.content_hash = "changed"

    def test_invalid_paths_are_rejected_before_read(self):
        for relative in ("", ".", "./app.py", "a/../app.py", "a//app.py", "a/",
                         "/app.py", "C:/app.py", "C:app.py", "//host/share/app.py",
                         "a\\app.py", "app.py\0", "a/./app.py"):
            with self.subTest(relative=relative):
                self.assert_code("INVALID_SOURCE_PATH", lambda relative=relative: self.read(relative))

    @unittest.skipUnless(sys.platform == "win32", "native Windows path rules")
    def test_windows_ads_devices_and_trailing_aliases(self):
        for relative in ("app.py:stream", "dir:stream/app.py", "CON.py", "aux/app.py",
                         "NUL", "COM1.txt", "LPT9.py", "app.py.", "dir /app.py"):
            with self.subTest(relative=relative):
                self.assert_code("INVALID_SOURCE_PATH", lambda relative=relative: self.read(relative))

    def test_limits_require_strict_positive_integer(self):
        for limit in (0, -1, True, False, 1.5, "10", None):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError) as caught:
                    self.read(limit=limit)
                self.assertNotIsInstance(caught.exception, source_handles.SourceReadError)

    def test_oversize_file_has_no_partial_result(self):
        error = self.assert_code("FILE_TOO_LARGE", lambda: self.read(limit=5))
        self.assertFalse(hasattr(error, "raw_bytes"))
        self.assertFalse(hasattr(error, "content_hash"))

    def test_directory_is_not_regular_file(self):
        (self.root / "directory").mkdir()
        self.assert_code("NOT_REGULAR_FILE", lambda: self.read("directory"))

    def test_read_error_message_is_fixed_across_paths(self):
        messages = []
        for relative in ("private-name.py", "another-secret.py"):
            error = self.assert_code("READ_ERROR", lambda relative=relative: self.read(relative))
            self.assertNotIn(relative, str(error))
            messages.append(error.message)
        self.assertEqual(*messages)

    def test_unsupported_platform_fails_closed(self):
        with patch.object(source_handles.sys, "platform", "unsupported-test-platform"):
            self.assert_code("UNSUPPORTED_PLATFORM", self.read)

    def test_exact_handle_path_required_even_inside_root(self):
        other = self.root / "other.py"
        other.write_bytes(b"x = 2\n")
        with patch.object(source_handles, "opened_file_path", return_value=other):
            self.assert_code("SOURCE_VERIFICATION_FAILED", self.read)

    def test_verification_failure_is_fixed_and_closes_stream(self):
        descriptors = []

        def fail(source):
            descriptors.append(source.fileno())
            raise OSError(f"private OS detail {self.root}")

        with patch.object(source_handles, "opened_file_path", side_effect=fail):
            error = self.assert_code("SOURCE_VERIFICATION_FAILED", self.read)
        self.assertNotIn("private OS detail", str(error))
        self.assertTrue(descriptors)
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_handle_is_verified_before_and_after_read_and_closed(self):
        original = source_handles.opened_file_path
        descriptors = []

        def observe(source):
            descriptors.append(source.fileno())
            return original(source)

        with patch.object(source_handles, "opened_file_path", side_effect=observe):
            self.read()
        self.assertGreaterEqual(len(descriptors), 2)
        for descriptor in set(descriptors):
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_symlink_leaf_directory_and_root(self):
        target = self.root / "target"
        target.mkdir()
        (target / "app.py").write_bytes(b"x = 1\n")
        try:
            (self.root / "leaf.py").symlink_to(self.path)
            (self.root / "linked").symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"native symlink creation unavailable: {error}")
        for relative in ("leaf.py", "linked/app.py"):
            with self.subTest(relative=relative):
                self.assert_code("SYMLINK_UNSUPPORTED", lambda relative=relative: self.read(relative))
        self.assert_code("SYMLINK_UNSUPPORTED", lambda: source_handles.read_source_bytes(
            self.root / "linked", "app.py", max_bytes=100))

    def test_hard_link_is_allowed(self):
        try:
            os.link(self.path, self.root / "hard.py")
        except OSError as error:
            self.skipTest(f"native hard links unavailable: {error}")
        self.assertEqual(self.read("hard.py").raw_bytes, self.path.read_bytes())

    @unittest.skipUnless(sys.platform == "win32", "requires native Windows sharing semantics")
    def test_windows_read_handle_blocks_write_delete_and_releases_after_read(self):
        original = source_handles.opened_file_path
        observed = []

        def verify_sharing(source):
            actual = original(source)
            # Real operations exercise CreateFile sharing while the read handle lives.
            with self.assertRaises(PermissionError):
                with self.path.open("r+b") as writer:
                    writer.write(b"changed")
            with self.assertRaises(PermissionError):
                self.path.unlink()
            with self.path.open("rb") as reader:
                self.assertEqual(reader.read(), b"x = 1\n")
            observed.append(source.fileno())
            return actual

        with patch.object(source_handles, "opened_file_path", side_effect=verify_sharing):
            result = self.read()
        self.assertTrue(observed, "sharing must be checked while the actual handle is open")
        self.assertEqual(result.raw_bytes, b"x = 1\n")
        with self.path.open("r+b") as writer:
            writer.write(b"x = 2\n")
        self.path.unlink()
        self.assertFalse(self.path.exists())

    @unittest.skipUnless(sys.platform == "win32", "requires native Windows birth-time metadata")
    def test_windows_birth_time_and_handle_change_time_are_checked_separately(self):
        before = self.path.lstat()
        with self.path.open("rb") as source:
            native = os.fstat(source.fileno())
        if any(not hasattr(info, name) for info in (before, native)
               for name in ("st_birthtime", "st_birthtime_ns")):
            self.skipTest("native Windows stat/fstat birth-time fields unavailable")
        raw = self.path.read_bytes()
        original = os.fstat
        original_lstat = Path.lstat
        offset_ns = before.st_ctime_ns + 1_000_000_000
        for case in ("stable-offset", "handle-ctime-change", "birth-mismatch", "pathname-ctime-change"):
            with self.subTest(case=case):
                calls = 0
                leaf_calls = 0
                descriptors = []

                def metadata(fd, case=case, descriptors=descriptors):
                    nonlocal calls
                    actual = original(fd)
                    descriptors.append(fd)
                    values = {name: getattr(actual, name) for name in dir(actual)
                              if name.startswith("st_")}
                    calls += 1
                    ctime_ns = offset_ns
                    if case == "handle-ctime-change" and calls > 1:
                        ctime_ns += 1_000_000_000
                    values.update(st_ctime_ns=ctime_ns, st_ctime=ctime_ns / 1_000_000_000)
                    if case == "birth-mismatch":
                        birth_ns = actual.st_birthtime_ns + 1_000_000_000
                        values.update(st_birthtime_ns=birth_ns,
                                      st_birthtime=birth_ns / 1_000_000_000)
                    return SimpleNamespace(**values)

                def pathname_metadata(path, *, case=case):
                    nonlocal leaf_calls
                    actual = original_lstat(path)
                    if case != "pathname-ctime-change" or path != self.path:
                        return actual
                    leaf_calls += 1
                    values = {name: getattr(actual, name) for name in dir(actual)
                              if name.startswith("st_")}
                    if leaf_calls > 1:
                        ctime_ns = actual.st_ctime_ns + 1_000_000_000
                        values.update(st_ctime_ns=ctime_ns, st_ctime=ctime_ns / 1_000_000_000)
                    return SimpleNamespace(**values)

                with (patch.object(os, "fstat", side_effect=metadata),
                      patch.object(Path, "lstat", new=pathname_metadata)):
                    if case == "stable-offset":
                        result = self.read()
                        self.assertEqual(result.raw_bytes, raw)
                        self.assertEqual(result.content_hash, sha256(raw).hexdigest())
                    else:
                        self.assert_code("SOURCE_CHANGED", self.read)
                self.assertGreaterEqual(calls, 1 if case in (
                    "birth-mismatch", "pathname-ctime-change") else 2)
                if case == "pathname-ctime-change":
                    self.assertGreaterEqual(leaf_calls, 2)
                    for descriptor in set(descriptors):
                        with self.assertRaises(OSError):
                            os.fstat(descriptor)

    def test_chunk_requests_are_bounded_and_growth_closes_stream(self):
        original = os.fdopen
        for case in ("bounded", "growth-over-limit"):
            with self.subTest(case=case):
                raw = b"x" * 131073 if case == "bounded" else b"x = 1\n"
                self.path.write_bytes(raw)
                limit = len(raw)
                streams = []
                requests = []
                test = self

                class ObservedStream:
                    def __init__(self, stream, test=test, limit=limit, requests=requests, case=case):
                        self.stream = stream
                        self.total = 0
                        self.test = test
                        self.limit = limit
                        self.requests = requests
                        self.case = case

                    def __enter__(self):
                        self.stream.__enter__()
                        return self

                    def __exit__(self, *args):
                        return self.stream.__exit__(*args)

                    def fileno(self):
                        return self.stream.fileno()

                    def read(self, size):
                        self.test.assertGreater(size, 0)
                        self.test.assertLessEqual(size, self.limit - self.total + 1)
                        self.requests.append(size)
                        chunk = self.stream.read(size)
                        if self.case == "growth-over-limit" and self.total == 0:
                            chunk += b"!"
                        self.total += len(chunk)
                        return chunk

                def observe(*args, streams=streams, wrapper=ObservedStream, **kwargs):
                    stream = original(*args, **kwargs)
                    streams.append(stream)
                    return wrapper(stream)

                with patch.object(os, "fdopen", side_effect=observe):
                    if case == "bounded":
                        result = self.read(limit=limit)
                        self.assertEqual(result.raw_bytes, raw)
                        self.assertEqual(result.content_hash, sha256(raw).hexdigest())
                    else:
                        error = self.assert_code("FILE_TOO_LARGE", lambda limit=limit: self.read(limit=limit))
                        self.assertFalse(hasattr(error, "raw_bytes"))
                        self.assertFalse(hasattr(error, "content_hash"))
                self.assertTrue(requests)
                if case == "bounded":
                    self.assertGreater(len(requests), 2, "source must be read in bounded chunks to EOF")
                self.assertTrue(streams)
                self.assertTrue(all(stream.closed for stream in streams))

    @unittest.skipUnless(sys.platform == "win32", "requires native Windows handle transfer")
    def test_windows_crt_transfer_failure_closes_native_handle(self):
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        file_type = kernel.GetFileType
        file_type.argtypes = (wintypes.HANDLE,)
        file_type.restype = wintypes.DWORD
        handles = []

        def fail_transfer(handle, flags):
            handles.append(handle)
            self.assertEqual(file_type(handle), 1, "actual CreateFile handle must be a disk file")
            raise OSError(f"private CRT failure {self.path}")

        with patch.object(msvcrt, "open_osfhandle", side_effect=fail_transfer):
            error = self.assert_code("READ_ERROR", self.read)
        self.assertNotIn("private CRT failure", str(error))
        self.assertFalse(hasattr(error, "raw_bytes"))
        self.assertFalse(hasattr(error, "content_hash"))
        self.assertEqual(len(handles), 1)
        ctypes.set_last_error(0)
        self.assertEqual(file_type(handles[0]), 0)
        self.assertEqual(ctypes.get_last_error(), 6, "transferred native handle must be closed")

    def test_zero_identity_fails_closed(self):
        original = os.fstat

        def unidentified(fd):
            actual = original(fd)
            values = {name: getattr(actual, name) for name in dir(actual)
                      if name.startswith("st_")}
            values["st_ino"] = 0
            return SimpleNamespace(**values)

        with patch.object(os, "fstat", side_effect=unidentified):
            self.assert_code("SOURCE_VERIFICATION_FAILED", self.read)

    @unittest.skipUnless(sys.platform == "linux", "requires native Linux mutation semantics")
    def test_size_and_timestamp_mutation_during_read(self):
        original = source_handles.opened_file_path
        for mutation in ("growth", "truncate", "same-size"):
            with self.subTest(mutation=mutation):
                self.path.write_bytes(b"x = 1\n")
                calls = 0

                def mutate(source, mutation=mutation):
                    nonlocal calls
                    actual = original(source)
                    calls += 1
                    if calls == 1:
                        self.path.write_bytes({"growth": b"x = 100\n", "truncate": b"",
                                               "same-size": b"x = 2\n"}[mutation])
                        stamp = self.path.stat().st_mtime_ns + 1_000_000_000
                        os.utime(self.path, ns=(stamp, stamp))
                    return actual

                with patch.object(source_handles, "opened_file_path", side_effect=mutate):
                    self.assert_code("SOURCE_CHANGED", self.read)

    @unittest.skipUnless(sys.platform == "linux", "requires native Linux rename semantics")
    def test_root_ancestor_and_leaf_replacement(self):
        original = source_handles.opened_file_path
        for component in ("root", "ancestor", "leaf"):
            with self.subTest(component=component), TemporaryDirectory() as temporary:
                root = Path(temporary) / "project"
                directory = root / "pkg"
                directory.mkdir(parents=True)
                leaf = directory / "app.py"
                leaf.write_bytes(b"x = 1\n")
                calls = 0

                def replace(source, root=root, directory=directory, leaf=leaf, component=component):
                    nonlocal calls
                    actual = original(source)
                    calls += 1
                    if calls == 1:
                        victim = {"root": root, "ancestor": directory, "leaf": leaf}[component]
                        victim.rename(victim.with_name(victim.name + ".old"))
                        if component != "leaf":
                            directory.mkdir(parents=True, exist_ok=True)
                        leaf.write_bytes(b"x = 1\n")
                    return actual

                with patch.object(source_handles, "opened_file_path", side_effect=replace):
                    self.assert_code("SOURCE_CHANGED", lambda root=root: source_handles.read_source_bytes(
                        root, "pkg/app.py", max_bytes=100))

    @unittest.skipUnless(sys.platform == "linux", "requires native Linux; not verified on Windows")
    def test_fifo_replacement_cannot_hang_open(self):
        # Child owns its temporary tree; parent timeout kills and reaps it.
        script = textwrap.dedent('''
            import os
            from pathlib import Path
            from unittest.mock import patch
            from codeviz import source_handles
            root = Path(__import__('sys').argv[1])
            leaf = root / 'app.py'
            leaf.write_bytes(b'x = 1\\n')
            original = os.open
            def swap(path, flags, *args, **kwargs):
                if str(path) in ('app.py', str(leaf)):
                    leaf.unlink()
                    os.mkfifo(leaf)
                return original(path, flags, *args, **kwargs)
            with patch.object(os, 'open', side_effect=swap):
                try:
                    source_handles.read_source_bytes(root, 'app.py', max_bytes=100)
                except source_handles.SourceReadError as error:
                    assert error.code == 'NOT_REGULAR_FILE', error.code
                else:
                    raise AssertionError('FIFO read succeeded')
        ''')
        completed = subprocess.run([sys.executable, "-B", "-c", script, str(self.root)],
                                   capture_output=True, text=True, timeout=3)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
