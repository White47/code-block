"""Independent RED regressions for C3 Task 3 publication review blockers."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch

from codeviz.persistence.store import CacheStore
from codeviz.ui.assistant_context import PublicationError
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge


class _FailOnAbandonMirror:
    """Record real publication attempts; fail only a failed switch's cleanup."""

    def __init__(self):
        self.current = None
        self.observed = []
        self.armed = False

    def replace_current(self, identity):
        self.observed.append(identity)
        if self.armed and identity.root is None:
            raise OSError("mirror rejected abandoned switch")
        self.current = identity


class _FailOnIneligibleCompletionMirror:
    """Fail the second ineligible publication for a newly opened project."""

    def __init__(self, target: Path, when: str):
        self.target = target
        self.when = when
        self.current = None
        self.observed = []
        self.armed = False
        self.target_calls = 0

    def replace_current(self, identity):
        self.observed.append(identity)
        if self.armed and identity.root == self.target and not identity.eligible:
            self.target_calls += 1
            if self.target_calls == 2:
                if self.when == "after":
                    self.current = identity
                raise OSError("ineligible completion mirror failed")
        self.current = identity


class PublicationTask3ReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory(prefix="codeviz-task3-review-")
        self.addCleanup(temporary.cleanup)
        self.scope = Path(temporary.name).resolve()
        self.first = self.scope / "first"
        self.second = self.scope / "second"
        self.first.mkdir()
        self.second.mkdir()
        (self.first / "first.py").write_text("value = 1\n", encoding="utf-8")
        (self.second / "second.py").write_text("value = 2\n", encoding="utf-8")
        self.store = CacheStore(self.scope / "cache")
        self.controller = AppController(self.store)
        self.bridge = DesktopBridge(
            self.controller, self.store, PreferencesStore(self.scope / "preferences"))

    def success(self, response):
        self.assertTrue(response["ok"], response)
        json.dumps(response)
        return response["data"]

    def test_failed_source_binding_never_mixes_project_analysis_or_handles(self):
        self.success(self.bridge.open_project(str(self.first)))
        self.success(self.bridge.analyze())
        old_analysis = self.controller.analysis
        self.assertIsNotNone(old_analysis)
        self.assertTrue(self.controller.capture_assistant_context().eligible)

        # Both directories still exist: fail after selection/path validation.
        with patch.object(self.controller._source_editing, "bind_project",
                          side_effect=OSError("source binding failed")):
            response = self.bridge.open_project(str(self.second))
        self.assertFalse(response["ok"], response)
        json.dumps(response)

        context = self.controller.capture_assistant_context()
        self.assertFalse(context.eligible, "failed switch must not retain old AI eligibility")
        self.assertIsNone(context.snapshot)
        session = self.success(self.bridge.get_session())
        root = self.controller.project_root
        self.assertEqual(session["projectPath"], str(root) if root is not None else None)
        self.assertEqual(session["status"], self.controller.status.value)
        self.assertEqual(session["hasAnalysis"], self.controller.analysis is not None)
        self.assertEqual(
            (session["projectEpoch"], session["analysisGeneration"]),
            (context.project_epoch, context.analysis_generation))
        self.assertEqual(context.root, root)
        self.assertEqual(context.status, self.controller.status)

        if self.controller.analysis is old_analysis:
            # Retain the old ordinary result only together with its old project.
            self.assertEqual(root, self.first)
            self.assertIn(self.controller.status,
                          (AnalysisStatus.READY, AnalysisStatus.PARTIAL))
        else:
            # Alternatively a failed switch may leave a fully inert session.
            self.assertIsNone(self.controller.analysis)
            self.assertEqual(self.controller.status, AnalysisStatus.NOT_ANALYZED)

        self.assertEqual(self.bridge._project_explorer._root, root)
        self.assertEqual(self.controller._source_editing._root, root)
        if root is not None:
            listing = self.success(self.bridge.list_project_directory({
                "projectPath": str(root), "directory": "",
            }))
            self.assertEqual(listing["projectEpoch"], context.project_epoch)
            filename = "first.py" if root == self.first else "second.py"
            document = self.success(self.bridge.open_source_document(str(root), {
                "filePath": filename, "startLine": 1, "endLine": 1,
                "startColumn": None, "endColumn": None,
            }))
            self.assertTrue(document["revision"].startswith(context.project_epoch + "."))

    def test_settings_read_under_publication_contention_returns_envelope(self):
        self.success(self.bridge.open_project(str(self.first)))
        self.success(self.bridge.analyze())
        before = self.controller.capture_assistant_context()
        original = self.success(self.bridge.get_analysis_settings())
        shown = self.success(self.bridge.get_session())
        entered, release = Event(), Event()

        def hold_publication():
            with self.controller._assistant_publication_lock:
                entered.set()
                release.wait(5)

        holder = Thread(target=hold_publication, daemon=True)
        holder.start()
        try:
            self.assertTrue(entered.wait(2), "publication lock was not held")
            # A safe error is acceptable, but no raw PublicationError may escape
            # from the _call finally block or overwrite the ordinary result.
            response = self.bridge.get_analysis_settings()
            self.assertIsInstance(response, dict)
            json.dumps(response)
            self.assertIn("ok", response)
            if response["ok"]:
                self.assertEqual(response["data"], original)
            else:
                self.assertIn(response["error"]["code"],
                              ("NOT_READY", "INVALID_REQUEST", "INTERNAL_ERROR"))
            self.assertEqual(self.bridge._session_snapshot(), shown)
        finally:
            release.set()
            holder.join(2)
        self.assertFalse(holder.is_alive())
        self.assertIs(self.controller.capture_assistant_context(), before)
        self.assertEqual(self.success(self.bridge.get_analysis_settings()), original)

    def test_busy_session_fallback_does_not_capture_contended_publication(self):
        self.success(self.bridge.open_project(str(self.first)))
        self.success(self.bridge.analyze())
        expected = self.success(self.bridge.get_session())
        entered, release = Event(), Event()

        def hold_publication():
            with self.controller._assistant_publication_lock:
                entered.set()
                release.wait(5)

        holder = Thread(target=hold_publication, daemon=True)
        holder.start()
        try:
            self.assertTrue(entered.wait(2), "publication lock was not held")
            responses = []
            completed = Event()

            def request_session():
                responses.append(self.bridge.get_session())
                completed.set()

            with self.bridge._lock, patch.object(
                    self.controller, "capture_assistant_context",
                    side_effect=AssertionError("busy fallback captured publication")):
                worker = Thread(target=request_session, daemon=True)
                worker.start()
                self.assertTrue(completed.wait(2), "busy session waited for locks")
                worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(self.success(responses[0]), expected)
        finally:
            release.set()
            holder.join(2)
        self.assertFalse(holder.is_alive())

    def test_failed_explorer_binding_never_leaves_a_partially_switched_project(self):
        self.success(self.bridge.open_project(str(self.first)))
        self.success(self.bridge.analyze())
        old_analysis = self.controller.analysis

        with patch.object(self.bridge._project_explorer, "bind_project",
                          side_effect=OSError("explorer binding failed")):
            response = self.bridge.open_project(str(self.second))

        self.assertFalse(response["ok"], response)
        json.dumps(response)
        context = self.controller.capture_assistant_context()
        self.assertFalse(context.eligible)
        self.assertIsNone(context.snapshot)
        root = self.controller.project_root
        self.assertIn(root, (self.first, None), "failed switch must roll back or become inert")
        self.assertEqual(self.controller._source_editing._root, root)
        self.assertEqual(self.bridge._project_explorer._root, root)
        self.assertEqual(context.root, root)
        session = self.success(self.bridge.get_session())
        self.assertEqual(session["projectPath"], str(root) if root is not None else None)
        self.assertEqual((session["projectEpoch"], session["analysisGeneration"]),
                         (context.project_epoch, context.analysis_generation))
        if root == self.first:
            self.assertIs(self.controller.analysis, old_analysis)
        else:
            self.assertIsNone(self.controller.analysis)
            self.assertEqual(self.controller.status, AnalysisStatus.NOT_ANALYZED)

    def _assert_inert_failed_switch(self, response):
        self.assertIsInstance(response, dict)
        self.assertFalse(response["ok"], response)
        self.assertEqual(set(response), {"ok", "schemaVersion", "error"})
        self.assertEqual(set(response["error"]), {"code", "message"})
        self.assertIn(response["error"]["code"],
                      ("INVALID_REQUEST", "NOT_READY", "INTERNAL_ERROR"))
        self.assertIsInstance(response["error"]["message"], str)
        self.assertNotIn("rollback", response["error"]["message"].lower())
        json.dumps(response)

        context = self.controller.capture_assistant_context()
        self.assertFalse(context.eligible)
        self.assertIsNone(context.snapshot)
        self.assertIsNone(context.root)
        self.assertIsNone(self.controller.project_root)
        self.assertIsNone(self.controller.analysis)
        self.assertEqual(self.controller.status, AnalysisStatus.NOT_ANALYZED)
        self.assertIsNone(self.controller._source_editing._root)
        self.assertIsNone(self.bridge._project_explorer._root)
        session = self.success(self.bridge.get_session())
        self.assertIsNone(session["projectPath"])
        self.assertFalse(session["hasAnalysis"])
        self.assertEqual((session["projectEpoch"], session["analysisGeneration"]),
                         (context.project_epoch, context.analysis_generation))

    def test_failed_source_rollback_leaves_inert_session(self):
        self.success(self.bridge.open_project(str(self.first)))
        self.success(self.bridge.analyze())
        source_bind = self.controller._source_editing.bind_project
        explorer_bind = self.bridge._project_explorer.bind_project

        def bind_source(root, epoch=None):
            if root == self.first:
                raise OSError("source rollback failed")
            return source_bind(root, epoch)

        def bind_explorer(root, epoch=None):
            if root == self.second:
                explorer_bind(root, epoch)
                raise OSError("explorer bind failed after switch")
            return explorer_bind(root, epoch)

        with patch.object(self.controller._source_editing, "bind_project", bind_source), \
                patch.object(self.bridge._project_explorer, "bind_project", bind_explorer):
            response = self.bridge.open_project(str(self.second))
        self._assert_inert_failed_switch(response)

    def test_failed_explorer_rollback_leaves_inert_session(self):
        self.success(self.bridge.open_project(str(self.first)))
        self.success(self.bridge.analyze())
        explorer_bind = self.bridge._project_explorer.bind_project

        def bind_explorer(root, epoch=None):
            if root == self.first:
                raise OSError("explorer rollback failed")
            explorer_bind(root, epoch)
            raise OSError("explorer bind failed after switch")

        with patch.object(self.bridge._project_explorer, "bind_project", bind_explorer):
            response = self.bridge.open_project(str(self.second))
        self._assert_inert_failed_switch(response)

    def test_trusted_analysis_notification_capture_conflict_stops_before_parsing(self):
        self.success(self.bridge.open_project(str(self.first)))
        self.success(self.bridge.analyze())
        previous = self.controller.capture_assistant_context()
        old_analysis = self.controller.analysis
        capture = self.controller.capture_assistant_context

        def capture_except_during_notification():
            if self.controller.status is AnalysisStatus.ANALYZING:
                raise PublicationError("AI_ANALYSIS_BUSY")
            return capture()

        with patch.object(self.controller, "capture_assistant_context",
                          side_effect=capture_except_during_notification), \
                patch("codeviz.ui.controller.fingerprint_project") as fingerprint, \
                patch("codeviz.ui.controller.analyze_project") as parser:
            response = self.bridge.analyze()

        self.assertIsInstance(response, dict)
        self.assertFalse(response["ok"], response)
        self.assertIn(response["error"]["code"],
                      ("INVALID_REQUEST", "NOT_READY", "INTERNAL_ERROR"))
        json.dumps(response)
        fingerprint.assert_not_called()
        parser.assert_not_called()
        context = self.controller.capture_assistant_context()
        self.assertFalse(context.eligible)
        self.assertIsNone(context.snapshot)
        self.assertEqual(context.status, AnalysisStatus.FAILED)
        self.assertEqual(context.project_epoch, previous.project_epoch)
        self.assertEqual(context.analysis_generation, previous.analysis_generation + 1)
        self.assertIs(self.controller.analysis, old_analysis)
        self.assertEqual(self.controller.status, AnalysisStatus.FAILED)
        session = self.success(self.bridge.get_session())
        self.assertEqual(session["status"], AnalysisStatus.FAILED.value)
        self.assertEqual((session["projectEpoch"], session["analysisGeneration"]),
                         (context.project_epoch, context.analysis_generation))

    def test_explorer_failure_cannot_publish_cached_b_before_cleanup_mirror_failure(self):
        mirror = _FailOnAbandonMirror()
        self.controller = AppController(self.store, assistant_publication_mirror=mirror)
        self.bridge = DesktopBridge(
            self.controller, self.store, PreferencesStore(self.scope / "preferences"))

        # B has an accepted cache, so opening it would otherwise publish an
        # eligible snapshot before Explorer has confirmed the same project.
        self.success(self.bridge.open_project(str(self.second)))
        self.success(self.bridge.analyze())
        self.success(self.bridge.open_project(str(self.first)))
        self.success(self.bridge.analyze())
        old_analysis = self.controller.analysis
        before = self.controller.capture_assistant_context()
        self.assertTrue(before.eligible)
        self.assertEqual(mirror.current.root, self.first)
        mirror.observed.clear()
        mirror.armed = True
        bind = self.bridge._project_explorer.bind_project

        def fail_after_binding_b(root, epoch=None):
            bind(root, epoch)
            if root == self.second:
                raise OSError("explorer rejected B after binding")

        with patch.object(self.bridge._project_explorer, "bind_project",
                          side_effect=fail_after_binding_b):
            response = self.bridge.open_project(str(self.second))

        self.assertIsInstance(response, dict)
        self.assertFalse(response["ok"], response)
        self.assertEqual(set(response), {"ok", "schemaVersion", "error"})
        self.assertEqual(set(response["error"]), {"code", "message"})
        self.assertIn(response["error"]["code"],
                      ("INVALID_REQUEST", "NOT_READY", "INTERNAL_ERROR"))
        self.assertNotIn("mirror", response["error"]["message"].lower())
        json.dumps(response)
        self.assertTrue(mirror.observed, "switch never reached publication")
        self.assertFalse(any(identity.root == self.second and identity.eligible
                             for identity in mirror.observed),
                         "B became eligible before Explorer accepted it")
        self.assertFalse(mirror.current.eligible)

        context = self.controller.capture_assistant_context()
        self.assertFalse(context.eligible)
        self.assertIsNone(context.snapshot)
        root = self.controller.project_root
        self.assertIn(root, (self.first, None),
                      "failed switch must restore A or leave an inert session")
        self.assertEqual(self.controller._source_editing._root, root)
        self.assertEqual(self.bridge._project_explorer._root, root)
        self.assertEqual(context.root, root)
        session = self.success(self.bridge.get_session())
        self.assertEqual(session["projectPath"], str(root) if root is not None else None)
        self.assertEqual((session["projectEpoch"], session["analysisGeneration"]),
                         (context.project_epoch, context.analysis_generation))
        if root == self.first:
            self.assertIs(self.controller.analysis, old_analysis)
        else:
            self.assertIsNone(self.controller.analysis)
            self.assertEqual(self.controller.status, AnalysisStatus.NOT_ANALYZED)

    def test_source_bind_failure_with_cleanup_capture_contended_restores_a(self):
        self.success(self.bridge.open_project(str(self.first)))
        self.success(self.bridge.set_analysis_settings({"ignorePaths": ["keep/"]}))
        self.success(self.bridge.analyze())
        before = self.controller.capture_assistant_context()
        old_analysis = self.controller.analysis
        self.assertTrue(before.eligible)
        self.assertEqual(self.controller.ignore_paths, ("keep",))
        entered, release, completed = Event(), Event(), Event()
        holder_threads = []
        responses = []

        def hold_publication():
            with self.controller._assistant_publication_lock:
                entered.set()
                release.wait(5)

        def fail_source_binding(_root, _epoch=None):
            holder = Thread(target=hold_publication, daemon=True)
            holder_threads.append(holder)
            holder.start()
            if not entered.wait(2):
                raise RuntimeError("publication contention was not established")
            raise OSError("source binding failed after invalidation")

        def open_b():
            try:
                responses.append(self.bridge.open_project(str(self.second)))
            except BaseException as error:
                responses.append(error)
            finally:
                completed.set()

        original_capture = self.controller.capture_assistant_context

        def reject_second_capture():
            if entered.is_set() and not release.is_set():
                raise AssertionError("failure cleanup recaptured publication")
            return original_capture()

        with patch.object(self.controller._source_editing, "bind_project",
                          side_effect=fail_source_binding), patch.object(
                              self.controller, "capture_assistant_context",
                              side_effect=reject_second_capture):
            worker = Thread(target=open_b, daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(2), "publication lock was not held")
            finally:
                release.set()
                worker.join(2)
                for holder in holder_threads:
                    holder.join(2)
        self.assertTrue(completed.is_set(), "cleanup did not finish after publication lock release")
        self.assertFalse(worker.is_alive())
        self.assertTrue(all(not holder.is_alive() for holder in holder_threads))
        self.assertEqual(len(responses), 1)
        response = responses[0]
        self.assertIsInstance(response, dict, f"raw exception escaped: {response!r}")
        self.assertFalse(response["ok"], response)
        self.assertEqual(set(response), {"ok", "schemaVersion", "error"})
        self.assertEqual(set(response["error"]), {"code", "message"})
        self.assertIn(response["error"]["code"],
                      ("INVALID_REQUEST", "NOT_READY", "INTERNAL_ERROR"))
        json.dumps(response)

        context = self.controller.capture_assistant_context()
        self.assertFalse(context.eligible)
        self.assertIsNone(context.snapshot)
        self.assertEqual(context.root, self.first)
        self.assertEqual(context.ignore_paths, before.ignore_paths)
        self.assertEqual(context.project_epoch, before.project_epoch)
        self.assertEqual(context.analysis_generation, before.analysis_generation + 1)
        self.assertEqual(context.status, self.controller.status)
        self.assertEqual(self.controller.project_root, self.first)
        self.assertEqual(self.controller.ignore_paths, before.ignore_paths)
        self.assertIs(self.controller.analysis, old_analysis)
        self.assertEqual(self.controller._source_editing._root, self.first)
        self.assertEqual(self.bridge._project_explorer._root, self.first)
        session = self.success(self.bridge.get_session())
        self.assertEqual(session["projectPath"], str(self.first))
        self.assertEqual((session["projectEpoch"], session["analysisGeneration"]),
                         (context.project_epoch, context.analysis_generation))

    def test_first_open_source_bind_failure_restores_absent_epoch(self):
        before = self.controller.capture_assistant_context()
        self.assertIsNone(before.project_epoch)
        with patch.object(self.controller._source_editing, "bind_project",
                          side_effect=OSError("first source binding failed")):
            response = self.bridge.open_project(str(self.second))

        self.assertIsInstance(response, dict)
        self.assertFalse(response["ok"], response)
        self.assertEqual(set(response), {"ok", "schemaVersion", "error"})
        self.assertEqual(set(response["error"]), {"code", "message"})
        json.dumps(response)
        context = self.controller.capture_assistant_context()
        self.assertFalse(context.eligible)
        self.assertIsNone(context.snapshot)
        self.assertIsNone(context.root)
        self.assertIsNone(context.project_epoch,
                          "failed first open must not retain the rejected project's epoch")
        self.assertEqual(context.analysis_generation, 1)
        self.assertIsNone(self.controller.project_root)
        self.assertIsNone(self.controller.analysis)
        self.assertEqual(self.controller.status, AnalysisStatus.NOT_ANALYZED)
        self.assertIsNone(self.controller._source_editing._root)
        self.assertIsNone(self.bridge._project_explorer._root)
        session = self.success(self.bridge.get_session())
        self.assertIsNone(session["projectPath"])
        self.assertIsNone(session["projectEpoch"])
        self.assertEqual(session["analysisGeneration"], context.analysis_generation)

    def test_settings_commit_activates_new_policy_without_rebinding_project(self):
        (self.first / "keep.py").write_text("kept = 3\n", encoding="utf-8")
        self.success(self.bridge.open_project(str(self.first)))
        self.success(self.bridge.analyze())
        previous = self.controller.capture_assistant_context()
        old_analysis = self.controller.analysis
        old_epoch = previous.project_epoch
        self.assertTrue(previous.eligible)

        with patch.object(self.controller._source_editing, "bind_project",
                          side_effect=OSError("same-root source binding must not run")) as bind:
            response = self.bridge.set_analysis_settings({"ignorePaths": ["first.py"]})
            bind.assert_not_called()

        settings = self.success(response)
        self.assertEqual(settings["ignorePaths"], ["first.py"])
        key = self.bridge._project_settings_key(self.first)
        disk = json.loads(self.bridge._settings_path.read_text(encoding="utf-8"))
        self.assertEqual(disk["projects"][key]["ignorePaths"], ["first.py"])
        self.assertEqual(self.bridge._settings_by_project[key], ("first.py",))
        self.assertEqual(self.controller.ignore_paths, ("first.py",))
        self.assertIs(self.controller.analysis, old_analysis)
        self.assertEqual(self.controller.status, AnalysisStatus.OUTDATED)
        self.assertIn("analysis config changed", self.controller.outdated_reasons)

        context = self.controller.capture_assistant_context()
        self.assertFalse(context.eligible)
        self.assertIsNone(context.snapshot)
        self.assertEqual(context.root, self.first)
        self.assertEqual(context.ignore_paths, ("first.py",))
        self.assertEqual(context.project_epoch, old_epoch)
        self.assertEqual(context.analysis_generation, previous.analysis_generation + 1)
        self.assertEqual(context.status, AnalysisStatus.OUTDATED)
        self.assertEqual(self.controller._source_editing._root, self.first)
        self.assertEqual(self.controller._source_editing._epoch, old_epoch)
        self.assertEqual(self.bridge._project_explorer._root, self.first)
        self.assertEqual(self.bridge._project_explorer._epoch, old_epoch)
        session = self.success(self.bridge.get_session())
        self.assertEqual(session["projectPath"], str(self.first))
        self.assertEqual(session["status"], AnalysisStatus.OUTDATED.value)
        self.assertTrue(session["hasAnalysis"])
        self.assertEqual((session["projectEpoch"], session["analysisGeneration"]),
                         (old_epoch, context.analysis_generation))

        restarted_store = CacheStore(self.scope / "cache")
        restarted = DesktopBridge(
            AppController(restarted_store), restarted_store,
            PreferencesStore(self.scope / "preferences"))
        self.success(restarted.open_project(str(self.first)))
        self.assertEqual(self.success(restarted.get_analysis_settings())["ignorePaths"],
                         ["first.py"])

        self.success(self.bridge.analyze())
        self.assertEqual({file.relative_path for file in self.controller.analysis.files},
                         {"keep.py"})
        self.assertEqual(self.controller.ignore_paths, ("first.py",))

    def test_settings_replace_success_survives_temp_cleanup_failure(self):
        self.success(self.bridge.open_project(str(self.first)))
        self.success(self.bridge.analyze())
        previous = self.controller.capture_assistant_context()
        old_analysis = self.controller.analysis
        self.assertTrue(previous.eligible)
        original_unlink = Path.unlink
        cleanup_attempts = []

        def fail_settings_temp_cleanup(path, *args, **kwargs):
            if (path.parent == self.store.data_dir
                    and path.name.startswith(".analysis-settings-")
                    and kwargs.get("missing_ok") is True):
                cleanup_attempts.append(path)
                self.assertFalse(path.exists(), "settings replacement did not finish")
                raise OSError("settings temp cleanup failed after replace")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", new=fail_settings_temp_cleanup):
            response = self.bridge.set_analysis_settings({"ignorePaths": ["first.py"]})

        self.assertEqual(cleanup_attempts, [])
        self.assertEqual(self.success(response)["ignorePaths"], ["first.py"])
        key = self.bridge._project_settings_key(self.first)
        disk = json.loads(self.bridge._settings_path.read_text(encoding="utf-8"))
        self.assertEqual(disk["projects"][key]["ignorePaths"], ["first.py"])
        self.assertEqual(self.bridge._settings_by_project[key], ("first.py",))
        self.assertEqual(self.controller.ignore_paths, ("first.py",))
        self.assertIs(self.controller.analysis, old_analysis)
        self.assertEqual(self.controller.status, AnalysisStatus.OUTDATED)
        self.assertIn("analysis config changed", self.controller.outdated_reasons)

        context = self.controller.capture_assistant_context()
        self.assertFalse(context.eligible)
        self.assertIsNone(context.snapshot)
        self.assertEqual(context.root, self.first)
        self.assertEqual(context.ignore_paths, ("first.py",))
        self.assertEqual(context.project_epoch, previous.project_epoch)
        self.assertEqual(context.analysis_generation,
                         previous.analysis_generation + 1)
        self.assertEqual(context.status, AnalysisStatus.OUTDATED)

    def _assert_no_cache_open_survives_completion_mirror_failure(self, when: str) -> None:
        mirror = _FailOnIneligibleCompletionMirror(self.second, when)
        controller = AppController(self.store, assistant_publication_mirror=mirror)
        bridge = DesktopBridge(controller, self.store,
                               PreferencesStore(self.scope / "preferences"))
        self.success(bridge.open_project(str(self.first)))
        self.success(bridge.analyze())
        previous = controller.capture_assistant_context()
        self.assertTrue(previous.eligible)
        self.assertIsNone(self.store.load_analysis(self.second))
        mirror.observed.clear()
        mirror.armed = True

        response = bridge.open_project(str(self.second))
        opened = self.success(response)
        self.assertEqual(opened["projectPath"], str(self.second))
        self.assertEqual(opened["status"], AnalysisStatus.NOT_ANALYZED.value)
        self.assertFalse(opened["hasAnalysis"])
        self.assertEqual(mirror.target_calls, 2)
        self.assertEqual(len(mirror.observed), 2)
        invalidated, attempted = mirror.observed
        self.assertFalse(invalidated.eligible)
        self.assertFalse(attempted.eligible)
        self.assertEqual(invalidated.root, self.second)
        self.assertEqual(attempted.root, self.second)
        self.assertEqual(attempted.analysis_generation, invalidated.analysis_generation)
        self.assertGreater(attempted.publication_revision,
                           invalidated.publication_revision)

        context = controller.capture_assistant_context()
        self.assertFalse(context.eligible)
        self.assertIsNone(context.snapshot)
        self.assertEqual(context.root, self.second)
        self.assertEqual(context.status, AnalysisStatus.NOT_ANALYZED)
        self.assertEqual(context.analysis_generation, previous.analysis_generation + 1)
        self.assertNotEqual(context.project_epoch, previous.project_epoch)
        self.assertEqual(context.project_epoch, attempted.project_epoch)
        self.assertGreaterEqual(context.publication_revision,
                                attempted.publication_revision)
        self.assertIsNone(controller.analysis)
        self.assertEqual(controller.project_root, self.second)
        self.assertEqual(controller._source_editing._root, self.second)
        self.assertEqual(controller._source_editing._epoch, context.project_epoch)
        self.assertEqual(bridge._project_explorer._root, self.second)
        self.assertEqual(bridge._project_explorer._epoch, context.project_epoch)
        self.assertEqual((opened["projectEpoch"], opened["analysisGeneration"]),
                         (context.project_epoch, context.analysis_generation))
        session = self.success(bridge.get_session())
        self.assertEqual(session, opened)
        self.assertIsNotNone(mirror.current)
        self.assertFalse(mirror.current.eligible)
        self.assertEqual(mirror.current.root, self.second)
        self.assertEqual(mirror.current.project_epoch, context.project_epoch)
        self.assertEqual(mirror.current.analysis_generation, context.analysis_generation)
        self.assertLessEqual(mirror.current.publication_revision,
                             context.publication_revision)
        if when == "before":
            self.assertEqual(mirror.current.publication_revision,
                             invalidated.publication_revision)
        else:
            self.assertEqual(mirror.current.publication_revision,
                             attempted.publication_revision)

    def test_no_cache_open_survives_completion_mirror_failure_before_replacement(self):
        self._assert_no_cache_open_survives_completion_mirror_failure("before")

    def test_no_cache_open_survives_completion_mirror_failure_after_replacement(self):
        self._assert_no_cache_open_survives_completion_mirror_failure("after")


if __name__ == "__main__":
    unittest.main()
