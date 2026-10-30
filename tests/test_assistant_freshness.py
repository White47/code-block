"""B4 complete local identity verification; synthetic facts except native smoke tests."""

import importlib
import importlib.util
import os
import stat
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from codeviz.analysis.project import ProjectAnalysis
from codeviz.analysis.python import ParsedFile
from codeviz.analysis.symbols import SymbolIndex
from codeviz.assistant.budget import BudgetLedger, BudgetReservation
from codeviz.assistant.contracts import AgentError, RequestScope
from codeviz.assistant.snapshot import SnapshotBinding, build_snapshot
from codeviz.source_handles import SourceRead, SourceReadError, read_source_bytes


def cached_file(path, raw=b"x = 1\n", *, parsed=True, known=True, lines=1):
    return ParsedFile(path, path.removesuffix(".py"), None, lines,
                      parsed_success=parsed, content_hash=sha256(raw).hexdigest() if known else None)


class InventoryAccessorTests(unittest.TestCase):
    def test_full_inventory_1001_sorted_immutable_and_no_io(self):
        # Deliberately housed here while B3's snapshot test is under independent review.
        binding = SnapshotBinding("E:/trusted/project" if os.name == "nt" else "/trusted/project",
                                  (), "epoch-b4", 1)
        files = tuple(cached_file(f"f{i:04}.py") for i in reversed(range(1001)))
        snapshot = build_snapshot(ProjectAnalysis(files, SymbolIndex(), ()), binding=binding)
        self.assertTrue(hasattr(type(snapshot), "file_inventory"), "Missing B4 file_inventory")
        with patch("os.scandir", side_effect=AssertionError("inventory performed I/O")), \
                patch.object(Path, "stat", side_effect=AssertionError("inventory stat")), \
                patch.object(Path, "lstat", side_effect=AssertionError("inventory lstat")), \
                patch("builtins.open", side_effect=AssertionError("inventory open")):
            inventory = snapshot.file_inventory
            self.assertIs(inventory, snapshot.file_inventory)
            self.assertIs(type(inventory), tuple)
            self.assertEqual(len(inventory), 1001)
            self.assertEqual(tuple(f.relative_path for f in inventory),
                             tuple(f"f{i:04}.py" for i in range(1001)))
            self.assertEqual(len(snapshot.files(limit=1000)), 1000)
        with self.assertRaises(FrozenInstanceError):
            inventory[0].content_hash = "a" * 64
        with self.assertRaises((AttributeError, TypeError)):
            snapshot.file_inventory = ()


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        name = "codeviz.assistant.freshness"
        self.assertIsNotNone(importlib.util.find_spec(name), f"Missing B4 API: {name}")
        self.api = importlib.import_module(name)
        for export in ("FreshnessStatus", "FreshnessReason", "FreshnessLimits",
                       "FreshnessReport", "verify_snapshot_freshness"):
            self.assertTrue(hasattr(self.api, export), f"Missing B4 freshness API: {export}")
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.binding = SnapshotBinding(str(self.root), (), "epoch-b4", 1)
        self.scope = RequestScope("epoch-b4", 1, "request-b4")
        self.raw = b"x = 1\n"
        (self.root / "app.py").write_bytes(self.raw)
        self.snapshot = self.build((cached_file("app.py"),))

    def build(self, files, *, binding=None):
        return build_snapshot(ProjectAnalysis(tuple(files), SymbolIndex(), ()),
                              binding=binding or self.binding)

    def verify(self, snapshot=None, **kwargs):
        return self.api.verify_snapshot_freshness(
            self.snapshot if snapshot is None else snapshot, scope=self.scope, **kwargs)

    def not_fresh(self, report, *reasons):
        self.assertIs(report.status, self.api.FreshnessStatus.NOT_FRESH)
        self.assertTrue(report.reasons)
        for reason in reasons:
            self.assertIn(getattr(self.api.FreshnessReason, reason), report.reasons)
        ordered = tuple(r for r in self.api.FreshnessReason if r in report.reasons)
        self.assertEqual(report.reasons, ordered)
        self.assertEqual(report.observed_hashes, tuple(sorted(set(report.observed_hashes))))

    def test_native_unchanged_raw_identity_and_report_observation_interval(self):
        clock = iter((10, 20))
        report = self.verify(clock_ns=lambda: next(clock))
        self.assertIs(report.status, self.api.FreshnessStatus.FRESH)
        self.assertEqual(report.reasons, ())
        self.assertTrue(report.scan_complete)
        self.assertEqual((report.started_ns, report.checked_ns), (10, 20))
        self.assertEqual(report.binding, self.binding)
        self.assertEqual(report.scope, self.scope)
        self.assertEqual(report.observed_hashes, (("app.py", sha256(self.raw).hexdigest()),))
        self.assertFalse({"raw_bytes", "source", "text"} & {f.name for f in fields(report)})
        with self.assertRaises(FrozenInstanceError):
            report.scan_complete = False
        (self.root / "app.py").write_bytes(b"x = 2\n")
        self.assertIs(report.status, self.api.FreshnessStatus.FRESH)
        self.not_fresh(self.verify(), "CONTENT_CHANGED")

    def test_native_same_size_restored_mtime_still_changes_hash(self):
        target = self.root / "app.py"
        before = target.stat()
        target.write_bytes(b"x = 2\n")
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.not_fresh(self.verify(), "CONTENT_CHANGED")

    def test_native_added_removed_renamed_sources_change_full_inventory(self):
        for change in ("add", "remove", "rename"):
            with self.subTest(change=change):
                target = self.root / "app.py"
                target.write_bytes(self.raw)
                if change == "add":
                    (self.root / "new.py").write_bytes(b"pass\n")
                elif change == "remove":
                    target.unlink()
                else:
                    target.rename(self.root / "renamed.py")
                self.not_fresh(self.verify(), "INVENTORY_CHANGED")
                for name in ("new.py", "renamed.py"):
                    (self.root / name).unlink(missing_ok=True)

    def test_stable_ignored_and_nonpython_entries_do_not_change_inventory(self):
        (self.root / ".git").mkdir()
        (self.root / ".git" / "new.py").write_bytes(b"pass\n")
        (self.root / "note.txt").write_bytes(b"local metadata")
        report = self.verify()
        self.assertIs(report.status, self.api.FreshnessStatus.FRESH)

    def test_full_1001_inventory_checks_last_file_not_query_page(self):
        paths = tuple(f"f{i:04}.py" for i in range(1001))
        snapshot = self.build(tuple(cached_file(p) for p in paths))
        calls = []

        def read(root, relative, *, max_bytes):
            calls.append(relative)
            raw = b"x = 2\n" if relative == paths[-1] else self.raw
            return SourceRead(raw, sha256(raw).hexdigest())

        with patch.object(self.api, "discover_python_files_strict", return_value=paths) as scan, \
                patch.object(self.api, "read_source_bytes", side_effect=read), \
                patch.object(type(snapshot), "files", side_effect=AssertionError("paged query")):
            report = self.verify(snapshot)
        self.not_fresh(report, "CONTENT_CHANGED")
        self.assertEqual(calls, list(paths))
        self.assertEqual(scan.call_count, 2)

    def test_unknown_failed_cached_and_protected_identity_groups(self):
        for parsed, known, path, reason in (
            (True, False, "app.py", "IDENTITY_MISSING"),
            (False, False, "app.py", "IDENTITY_MISSING"),
            (False, True, "app.py", None),
            (True, True, "app.py", None),
            (True, True, "secrets.py", "PROTECTED_FILE"),
        ):
            with self.subTest(parsed=parsed, known=known, path=path):
                snapshot = self.build((cached_file(path, parsed=parsed, known=known),))
                with patch.object(self.api, "discover_python_files_strict", return_value=(path,)) as scan, \
                        patch.object(self.api, "read_source_bytes",
                                          return_value=SourceRead(self.raw, sha256(self.raw).hexdigest())) as read, \
                        patch("ast.parse", side_effect=AssertionError("freshness reparsed")), \
                        patch("codeviz.persistence.fingerprint.fingerprint_project",
                              side_effect=AssertionError("saved fingerprint used")):
                    report = self.verify(snapshot)
                if reason:
                    self.not_fresh(report, reason)
                    read.assert_not_called()
                else:
                    self.assertIs(report.status, self.api.FreshnessStatus.FRESH)
                    self.assertEqual(read.call_count, 1)
                    self.assertTrue(snapshot.get_file(path).was_parsed is parsed)
                self.assertEqual(scan.call_count, 2)

    def test_failed_known_hash_is_still_compared(self):
        snapshot = self.build((cached_file("app.py", parsed=False),))
        (self.root / "app.py").write_bytes(b"x = 2\n")
        self.not_fresh(self.verify(snapshot), "CONTENT_CHANGED")

    def test_unconsented_other_file_is_locally_verified(self):
        (self.root / "other.py").write_bytes(self.raw)
        snapshot = self.build((cached_file("app.py"), cached_file("other.py")))
        (self.root / "other.py").write_bytes(b"x = 2\n")
        self.not_fresh(self.verify(snapshot), "CONTENT_CHANGED")

    def test_scope_mismatch_has_no_filesystem_io(self):
        for scope in (replace(self.scope, project_epoch="other"),
                      replace(self.scope, analysis_generation=2)):
            with self.subTest(scope=scope):
                with patch.object(Path, "lstat", side_effect=AssertionError("unauthorized stat")), \
                        patch.object(self.api, "read_source_bytes", side_effect=AssertionError("read")), \
                        patch.object(self.api, "discover_python_files_strict",
                                          side_effect=AssertionError("scan")):
                    report = self.api.verify_snapshot_freshness(self.snapshot, scope=scope)
                self.not_fresh(report, "SCOPE_MISMATCH")

    def test_two_complete_scans_each_receive_exact_caps(self):
        limits = self.api.FreshnessLimits(max_entries=7, max_directories=3, max_files=2)
        with patch.object(self.api, "discover_python_files_strict", return_value=("app.py",)) as scan:
            report = self.verify(limits=limits)
        self.assertIs(report.status, self.api.FreshnessStatus.FRESH)
        self.assertEqual(scan.call_count, 2)
        for call in scan.call_args_list:
            self.assertEqual(call.kwargs["max_entries"], 7)
            self.assertEqual(call.kwargs["max_directories"], 3)
            self.assertEqual(call.kwargs["max_files"], 2)

    def test_checked_clock_follows_second_scan_and_final_root_observation(self):
        events = []
        native = Path.lstat
        scans = 0
        ticks = iter((10, 20))

        def observe(path, *args, **kwargs):
            if path == self.root:
                events.append("root")
            return native(path, *args, **kwargs)

        def scan(*args, **kwargs):
            nonlocal scans
            scans += 1
            events.append(f"scan-{scans}")
            return ("app.py",)

        def clock():
            tick = next(ticks)
            events.append(f"clock-{tick}")
            return tick

        with patch.object(Path, "lstat", observe), \
                patch.object(self.api, "discover_python_files_strict", side_effect=scan), \
                patch.object(self.api, "read_source_bytes",
                                  return_value=SourceRead(self.raw, sha256(self.raw).hexdigest())):
            report = self.verify(clock_ns=clock)
        self.assertIs(report.status, self.api.FreshnessStatus.FRESH)
        self.assertEqual(events[0], "clock-10")
        self.assertEqual(events[-1], "clock-20")
        self.assertEqual(scans, 2)
        final_root = max(i for i, event in enumerate(events) if event == "root")
        self.assertGreater(final_root, events.index("scan-2"))
        self.assertLess(final_root, events.index("clock-20"))

    def test_freshness_limits_keep_fixed_hard_caps_and_strict_positive_integers(self):
        defaults = self.api.FreshnessLimits()
        expected = dict(max_entries=200_000, max_directories=20_000, max_files=200_000,
                        max_file_bytes=5_000_000, max_total_bytes=50_000_000)
        policy = importlib.import_module("codeviz.assistant.source_policy")
        for name, cap in expected.items():
            self.assertEqual(getattr(defaults, name), cap)
            for value in (0, -1, True, 1.0, cap + 1):
                with self.subTest(field=name, value=value):
                    with self.assertRaises(policy.SourceExcerptError) as caught:
                        self.api.FreshnessLimits(**{name: value})
                    self.assertEqual(caught.exception.code, "invalid_input")

    def test_scan_a_or_b_errors_never_return_fresh_or_partial_complete(self):
        from codeviz.analysis import discovery

        self.assertTrue(hasattr(discovery, "DiscoveryScanError"), "Missing strict scan error")
        for position in (0, 1):
            for code, reason in (("SCAN_INCOMPLETE", "SCAN_INCOMPLETE"),
                                 ("LIMIT_EXCEEDED", "LIMIT_EXCEEDED"),
                                 ("ROOT_UNVERIFIED", "ROOT_UNVERIFIED")):
                with self.subTest(scan=position, code=code):
                    results = [("app.py",), ("app.py",)]
                    results[position] = discovery.DiscoveryScanError(code)
                    with patch.object(self.api, "discover_python_files_strict", side_effect=results):
                        report = self.verify()
                    self.not_fresh(report, reason)
                    self.assertFalse(report.scan_complete)

    def test_inventory_changes_between_scans_and_known_content_failure_still_scan_b(self):
        for second in (("app.py", "new.py"), (), ("renamed.py",)):
            with self.subTest(second=second):
                with patch.object(self.api, "discover_python_files_strict",
                                  side_effect=[("app.py",), second]) as scan:
                    report = self.verify()
                self.not_fresh(report, "INVENTORY_CHANGED")
                self.assertEqual(scan.call_count, 2)
        (self.root / "app.py").write_bytes(b"x = 2\n")
        with patch.object(self.api, "discover_python_files_strict", return_value=("app.py",)) as scan:
            self.not_fresh(self.verify(), "CONTENT_CHANGED")
        self.assertEqual(scan.call_count, 2)

    def test_checkpoint_tree_change_between_reads_and_final_scan_is_detected(self):
        changed = False

        def read(root, relative, *, max_bytes):
            nonlocal changed
            result = read_source_bytes(root, relative, max_bytes=max_bytes)
            changed = True
            return result

        def checkpoint():
            if changed:
                (self.root / "new.py").write_bytes(b"pass\n")

        with patch.object(self.api, "read_source_bytes", side_effect=read):
            report = self.verify(checkpoint=checkpoint)
        self.not_fresh(report)
        self.assertTrue(set(report.reasons) & {self.api.FreshnessReason.INVENTORY_CHANGED,
                                             self.api.FreshnessReason.SCAN_INCOMPLETE})

    def test_total_byte_exact_boundary_remaining_allowance_and_no_extra_read(self):
        paths = ("a.py", "b.py", "c.py")
        raw = b"pass\n"
        for total, contents, expected_reads, fresh in (
            (10, (raw, raw), (5, 5), True),
            (8, (raw, raw, raw), (5, 3), False),
            (5, (raw, raw), (5,), False),
        ):
            with self.subTest(total=total):
                selected = paths[:len(contents)]
                snapshot = self.build(tuple(cached_file(p, r) for p, r in
                                            zip(selected, contents, strict=True)))
                caps = []

                def read(root, relative, *, max_bytes, caps=caps):
                    caps.append(max_bytes)
                    if len(raw) > max_bytes:
                        raise SourceReadError("FILE_TOO_LARGE") from None
                    return SourceRead(raw, sha256(raw).hexdigest())

                limits = self.api.FreshnessLimits(max_file_bytes=5, max_total_bytes=total)
                with patch.object(self.api, "discover_python_files_strict", return_value=selected) as scan, \
                        patch.object(self.api, "read_source_bytes", side_effect=read):
                    report = self.verify(snapshot, limits=limits)
                self.assertEqual(tuple(caps), expected_reads)
                self.assertEqual(scan.call_count, 2)
                if fresh:
                    self.assertIs(report.status, self.api.FreshnessStatus.FRESH)
                else:
                    self.not_fresh(report, "LIMIT_EXCEEDED")
                    self.assertTrue(report.scan_complete)

    def test_read_failures_single_file_cap_and_raw_hash_structure(self):
        failures = ((SourceReadError("READ_ERROR"), "FILE_UNVERIFIED"),
                    (SourceReadError("FILE_TOO_LARGE"), "FILE_UNVERIFIED"),
                    (SourceReadError("SOURCE_CHANGED"), "CONTENT_CHANGED"),
                    (SourceRead(self.raw, "a" * 64), "FILE_UNVERIFIED"))
        for result, reason in failures:
            with self.subTest(result=result):
                kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with patch.object(self.api, "read_source_bytes", **kwargs), \
                        patch.object(self.api, "discover_python_files_strict", return_value=("app.py",)) as scan:
                    report = self.verify()
                self.not_fresh(report, reason)
                self.assertEqual(scan.call_count, 2)
                self.assertEqual(report.observed_hashes, ())

    def test_empty_inventory_requires_root_and_two_scans(self):
        (self.root / "app.py").unlink()
        snapshot = self.build(())
        with patch.object(self.api, "discover_python_files_strict", return_value=()) as scan:
            report = self.verify(snapshot)
        self.assertIs(report.status, self.api.FreshnessStatus.FRESH)
        self.assertEqual(scan.call_count, 2)
        self.root.rmdir()
        self.not_fresh(self.verify(snapshot), "ROOT_UNVERIFIED")

    def test_final_root_identity_change_cannot_be_fresh(self):
        native = Path.lstat
        root_reads = 0

        def observe(path, *args, **kwargs):
            nonlocal root_reads
            info = native(path, *args, **kwargs)
            if path != self.root:
                return info
            root_reads += 1
            values = {name: getattr(info, name) for name in
                      ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
            if root_reads > 1:
                values["st_ino"] += 1
            return SimpleNamespace(**values)

        with patch.object(Path, "lstat", observe), \
                patch.object(self.api, "discover_python_files_strict", return_value=("app.py",)), \
                patch.object(self.api, "read_source_bytes",
                                  return_value=SourceRead(self.raw, sha256(self.raw).hexdigest())):
            self.not_fresh(self.verify(), "ROOT_UNVERIFIED")

    def test_root_link_reparse_zero_identity_and_noncanonical_binding_are_unverified(self):
        native = Path.lstat
        info = native(self.root)
        values = {name: getattr(info, name) for name in
                  ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
        for change in ({"st_mode": stat.S_IFLNK}, {"st_file_attributes": 0x400},
                       {"st_ino": 0}, {"st_dev": 0}):
            with self.subTest(change=change):
                def observe(path, *args, change=change, **kwargs):
                    if path == self.root:
                        return SimpleNamespace(**dict(values, **change))
                    return native(path, *args, **kwargs)
                with patch.object(Path, "lstat", observe), \
                        patch.object(self.api, "read_source_bytes", side_effect=AssertionError("unsafe root read")):
                    report = self.verify()
                self.not_fresh(report, "ROOT_UNVERIFIED")
                self.assertFalse(report.scan_complete)
        binding = replace(self.binding, project_root=str(self.root) + os.sep + ".")
        snapshot = self.build((cached_file("app.py"),), binding=binding)
        self.not_fresh(self.verify(snapshot), "ROOT_UNVERIFIED")

    def test_cancel_deadline_checkpoint_propagates_without_charging(self):
        for code in ("cancelled", "timeout"):
            with self.subTest(code=code):
                now = [0.0]
                ledger = BudgetLedger(clock=lambda now=now: now[0])
                if code == "cancelled":
                    ledger.cancel()
                else:
                    now[0] = 90.0
                before = ledger.usage
                with self.assertRaises(AgentError) as caught:
                    self.verify(checkpoint=lambda ledger=ledger: ledger.reserve(BudgetReservation()))
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(ledger.usage, before)

    def test_illegal_and_backwards_clocks_fail_verification(self):
        policy = importlib.import_module("codeviz.assistant.source_policy")
        for values in ((True, 20), (-1, 20), (1.5, 20), (20, 10), (10, "private")):
            with self.subTest(values=values):
                clock = iter(values)
                with self.assertRaises(policy.SourceExcerptError) as caught:
                    self.verify(clock_ns=lambda clock=clock: next(clock))
                self.assertEqual(caught.exception.code, "verification_failed")
                self.assertEqual(str(caught.exception), "Source verification could not be completed.")


if __name__ == "__main__":
    unittest.main()
