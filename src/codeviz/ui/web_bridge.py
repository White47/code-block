"""Narrow, JSON-safe API exposed to the bundled pywebview frontend."""

import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import TYPE_CHECKING, Protocol, TypeVar, cast

from codeviz import navigation
from codeviz.analysis.discovery import _normalize_ignore
from codeviz.analysis.project import ProjectAnalysis
from codeviz.assistant import bridge_requests
from codeviz.assistant.issues import issue_for_code
from codeviz.core import SourceAnchor, ViewKind
from codeviz.persistence.store import CacheStore
from codeviz.search import search_project

from .assistant_context import PublicationError
from .controller import AnalysisStatus, AppController
from .preferences import PreferencesStore
from .presenter import build_inspector, build_overview, build_problems
from .project_explorer import ProjectExplorerService, ProjectFileError
from .source_editing import MAX_SOURCE_BYTES, SourceEditingError
from .web_contract import (
    JsonObject,
    JsonValue,
    assistant_error_payload,
    editable_source_payload,
    graph_payload,
    inspector_payload,
    overview_payload,
    preferences_payload,
    problem_payload,
    search_response_payload,
    session_payload,
    source_payload,
    success_payload,
    summary_layout_payload,
    view_state_payload,
)

if TYPE_CHECKING:
    from codeviz.assistant.service import _AssistantRuntime
from .web_contract import error_payload as contract_error_payload

_MAX_PATH_LENGTH = 32_768
_MAX_QUERY_LENGTH = 1_000
_MAX_STRING_LENGTH = 4_096
_MAX_COLLECTION_LENGTH = 256
_MAX_SETTINGS_BYTES = 1_100_000
_LEGACY_SETTINGS_ISSUE = (
    "Legacy version 1 ignore paths have no project identity and were not applied. "
    "Re-enter them explicitly for each project."
)
_DIRECTIONS = {"in": "upstream", "out": "downstream", "both": "both"}
_SOURCE_ANCHOR_FIELDS = frozenset({
    "filePath", "startLine", "startColumn", "endLine", "endColumn",
})
_LAYOUT_FIELDS = frozenset({
    "activeView", "selectedId", "openTabs", "graphRoots",
})
_PREFERENCES_FIELDS = frozenset({"language"})

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=JsonValue)


class ProjectWindow(Protocol):
    def create_file_dialog(self, dialog_type: int, *, directory: str,
                           allow_multiple: bool) -> tuple[str, ...] | list[str] | None: ...


def _ignore_paths(value: object) -> tuple[str, ...]:
    if type(value) is not list or len(value) > _MAX_COLLECTION_LENGTH:
        raise ValueError("ignorePaths must be a bounded list")
    return tuple(sorted({_normalize_ignore(_string(item, "ignorePaths item"))
                         for item in value}))


def _string(value: object, field: str, *, max_length: int = _MAX_STRING_LENGTH,
            allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    if (not allow_empty and not value) or len(value) > max_length or "\x00" in value:
        raise ValueError(f"{field} has an invalid length")
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field)


def _view_kind(value: object, field: str = "kind") -> ViewKind:
    text = _string(value, field)
    try:
        return ViewKind(text)
    except ValueError as error:
        raise ValueError(f"{field} is not a supported view kind") from error


def _point(value: object, field: str) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        value = {"x": value[0], "y": value[1]}
    if type(value) is not dict or set(value) != {"x", "y"}:
        raise ValueError(f"{field} must contain exactly x and y")
    x = value["x"]
    y = value["y"]
    if (isinstance(x, bool) or not isinstance(x, (int, float)) or
            isinstance(y, bool) or not isinstance(y, (int, float)) or
            not isfinite(x) or not isfinite(y)):
        raise ValueError(f"{field} coordinates must be finite numbers")
    return (float(x), float(y))


def _source_anchor(value: object) -> SourceAnchor:
    if type(value) is not dict or set(value) != _SOURCE_ANCHOR_FIELDS:
        raise ValueError("anchor has unexpected or missing fields")
    file_path = _string(value["filePath"], "anchor.filePath")
    start_line = value["startLine"]
    end_line = value["endLine"]
    start_column = value["startColumn"]
    end_column = value["endColumn"]
    if type(start_line) is not int or type(end_line) is not int:
        raise TypeError("anchor lines must be integers")
    for field, column in (("startColumn", start_column),
                          ("endColumn", end_column)):
        if column is not None and type(column) is not int:
            raise TypeError(f"anchor.{field} must be an integer or null")
    return SourceAnchor(file_path, start_line, start_column, end_line, end_column)


class DesktopBridge:
    """Own desktop services and convert every pywebview call to an envelope."""

    def __init__(
        self,
        controller: AppController | None = None,
        cache_store: CacheStore | None = None,
        preferences_store: PreferencesStore | None = None,
    ) -> None:
        if cache_store is None and controller is not None:
            cache_store = controller.cache_store
        self._cache_store = cache_store or CacheStore()
        if controller is None:
            controller = AppController(self._cache_store)
        elif controller.cache_store is None:
            controller.cache_store = self._cache_store
        elif controller.cache_store is not self._cache_store:
            raise ValueError("controller and bridge must share one cache store")
        self._controller = controller
        self._preferences_store = preferences_store or PreferencesStore(
            self._cache_store.data_dir)
        self._lock = RLock()
        self._project_explorer = ProjectExplorerService()
        self._recent_projects_snapshot: tuple[Path, ...] = ()
        self._window: ProjectWindow | None = None
        self._folder_dialog_type = 20
        self._close_handler: Callable[[str, bool], None] | None = None
        self._assistant_cleanup: Callable[[], None] | None = None
        self._assistant_runtime: _AssistantRuntime | None = None
        self._settings_path = self._cache_store.data_dir / "analysis-settings.json"
        self._settings_issue: str | None = None
        self._settings_by_project: dict[str, tuple[str, ...]] = {}
        self._legacy_ignore_paths: tuple[str, ...] | None = None
        self._load_analysis_settings()
        if self._controller.project_root is None:
            self._controller.ignore_paths = ()
        else:
            self._open_project(self._controller.project_root)
        self._snapshot: tuple[Path | None, AnalysisStatus, tuple[str, ...],
                              str | None, tuple[Path, ...], bool, str | None, int | None]
        self._publish_session()

    def _publish_session(self, status: AnalysisStatus | None = None) -> None:
        identity = self._controller.capture_assistant_context()
        display = (self._controller.project_root,
                   status or self._controller.status,
                   self._controller.outdated_reasons,
                   self._controller.cache_issue,
                   self._recent_projects_snapshot,
                   self._controller.analysis is not None)
        self._snapshot = (*display, identity.project_epoch, identity.analysis_generation)

    def _bind_window(self, window: ProjectWindow, folder_dialog_type: int) -> None:
        # Private: pywebview exposes public methods of js_api automatically.
        self._window = window
        self._folder_dialog_type = folder_dialog_type

    def _bind_close_handler(self, handler: Callable[[str, bool], None]) -> None:
        self._close_handler = handler

    def _bind_assistant_cleanup(self, cleanup: Callable[[], None]) -> None:
        if not callable(cleanup):
            raise ValueError("Invalid assistant cleanup handler.")
        self._assistant_cleanup = cleanup

    def _bind_assistant_runtime(self, runtime: "_AssistantRuntime") -> None:
        # Construction must pair the controller mirror and runtime. Never swap a
        # live controller's mirror after publication/admission has begun.
        if (self._controller._assistant_publication_mirror is not runtime
                or self._assistant_runtime is not None and self._assistant_runtime is not runtime):
            raise ValueError("Assistant runtime and controller must be paired.")
        context = self._controller.capture_assistant_context()
        from .assistant_context import PublicationIdentity
        runtime._replace_publication(PublicationIdentity(
            context.publication_revision, context.project_epoch, context.analysis_generation,
            context.project_key, context.root, context.ignore_paths, context.status, context.eligible),
            context.snapshot)
        self._assistant_runtime = runtime
        self._bind_assistant_cleanup(runtime.prepare_close)

    def _finish_assistant_close(self) -> None:
        runtime = self._assistant_runtime
        if runtime is not None:
            runtime.shutdown()

    @staticmethod
    def _assistant_execute(operation: Callable[[], object]) -> JsonObject:
        try:
            # success_payload validates/clones JSON-native values at runtime.
            return success_payload(cast(JsonValue, operation()))
        except Exception as error:
            # Only a closed code/message survives. Do not route assistant
            # exceptions through the ordinary diagnostic logger.
            return assistant_error_payload(issue_for_code(getattr(error, "code", None)).code)

    def _prepare_assistant_close(self) -> None:
        cleanup = self._assistant_cleanup
        if cleanup is not None:
            cleanup()

    def _assistant_unavailable(self, parser: Callable[[object], object], request: object) -> JsonObject:
        """Base-only boundary: no optional SDK, project queue or diagnostic logging.

        The composed runtime remains gate-off until its integration acceptance;
        the ordinary offline desktop must keep an honest, usable failure path.
        """
        try:
            parser(request)
        except bridge_requests.BridgeRequestError:
            return assistant_error_payload("AI_INVALID_INPUT")
        except Exception:
            return assistant_error_payload("AI_PROTOCOL_INCOMPATIBLE")
        return assistant_error_payload("AI_DEPENDENCY_MISSING")

    def assistant_get_settings(self) -> JsonObject:
        if self._assistant_runtime is not None:
            return self._assistant_execute(self._assistant_runtime.get_settings)
        issue = issue_for_code("AI_DEPENDENCY_MISSING")
        return success_payload({"available": False, "enabled": False, "baseUrl": "", "model": "",
                                "keySet": False, "configRevision": 0,
                                "issue": {"code": issue.code, "message": issue.message}})

    def assistant_configure(self, request: object) -> JsonObject:
        runtime = self._assistant_runtime
        if runtime is not None:
            return self._assistant_execute(lambda: runtime.configure(bridge_requests.parse_configure(request)))
        return self._assistant_unavailable(bridge_requests.parse_configure, request)

    def assistant_test_connection(self, request: object) -> JsonObject:
        runtime = self._assistant_runtime
        if runtime is not None:
            return self._assistant_execute(lambda: runtime.test_connection(
                bridge_requests.parse_connection(request)))
        return self._assistant_unavailable(bridge_requests.parse_connection, request)

    def assistant_get_state(self, request: object) -> JsonObject:
        runtime = self._assistant_runtime
        if runtime is not None:
            def operation() -> object:
                parsed = bridge_requests.parse_project(request)
                context = self._controller.capture_assistant_context()
                return runtime.get_state(parsed, context)
            return self._assistant_execute(operation)
        return self._assistant_unavailable(bridge_requests.parse_project, request)

    def assistant_get_consent_candidates(self, request: object) -> JsonObject:
        runtime = self._assistant_runtime
        if runtime is not None:
            def operation() -> object:
                parsed = bridge_requests.parse_consent_candidates(request)
                context = self._controller.capture_assistant_context()
                return runtime.get_consent_candidates(parsed, context)
            return self._assistant_execute(operation)
        return self._assistant_unavailable(bridge_requests.parse_consent_candidates, request)

    def assistant_grant_consent(self, request: object) -> JsonObject:
        runtime = self._assistant_runtime
        if runtime is not None:
            def operation() -> object:
                parsed = bridge_requests.parse_consent(request)
                context = self._controller.capture_assistant_context()
                return runtime.grant_consent(parsed, context)
            return self._assistant_execute(operation)
        return self._assistant_unavailable(bridge_requests.parse_consent, request)

    def assistant_revoke_consent(self, request: object) -> JsonObject:
        runtime = self._assistant_runtime
        if runtime is not None:
            return self._assistant_execute(lambda: runtime.revoke_consent(
                bridge_requests.parse_project(request).project_epoch))
        return self._assistant_unavailable(bridge_requests.parse_project, request)

    def assistant_new_conversation(self, request: object) -> JsonObject:
        runtime = self._assistant_runtime
        if runtime is not None:
            def operation() -> object:
                parsed = bridge_requests.parse_project(request)
                context = self._controller.capture_assistant_context()
                return runtime.new_conversation(parsed, context)
            return self._assistant_execute(operation)
        return self._assistant_unavailable(bridge_requests.parse_project, request)

    def assistant_clear_conversation(self, request: object) -> JsonObject:
        runtime = self._assistant_runtime
        if runtime is not None:
            def operation() -> object:
                parsed = bridge_requests.parse_clear(request)
                context = self._controller.capture_assistant_context()
                return runtime.clear_conversation(parsed, context)
            return self._assistant_execute(operation)
        return self._assistant_unavailable(bridge_requests.parse_clear, request)

    def assistant_start(self, request: object) -> JsonObject:
        runtime = self._assistant_runtime
        if runtime is not None:
            def operation() -> object:
                parsed = bridge_requests.parse_start(request)
                context = self._controller.capture_assistant_context()
                return runtime.start(parsed, context)
            return self._assistant_execute(operation)
        return self._assistant_unavailable(bridge_requests.parse_start, request)

    def assistant_poll(self, request: object) -> JsonObject:
        runtime = self._assistant_runtime
        if runtime is not None:
            return self._assistant_execute(lambda: runtime.poll(bridge_requests.parse_poll(request)))
        return self._assistant_unavailable(bridge_requests.parse_poll, request)

    def assistant_cancel(self, request: object) -> JsonObject:
        runtime = self._assistant_runtime
        if runtime is not None:
            return self._assistant_execute(lambda: runtime.cancel(bridge_requests.parse_cancel(request)))
        return self._assistant_unavailable(bridge_requests.parse_cancel, request)

    def assistant_resolve_evidence(self, request: object) -> JsonObject:
        runtime = self._assistant_runtime
        if runtime is not None:
            def operation() -> object:
                parsed = bridge_requests.parse_resolve(request)
                context = self._controller.capture_assistant_context()
                return runtime.resolve_evidence(parsed, context)
            return self._assistant_execute(operation)
        return self._assistant_unavailable(bridge_requests.parse_resolve, request)

    def approve_close(self, request_id: object) -> JsonObject:
        return self._close_response(request_id, True)

    def cancel_close(self, request_id: object) -> JsonObject:
        return self._close_response(request_id, False)

    def _close_response(self, request_id: object, approve: bool) -> JsonObject:
        def operation() -> JsonObject:
            token = _string(request_id, "requestId", max_length=128)
            if self._close_handler is None:
                raise ValueError("no native close request")
            self._close_handler(token, approve)
            return {"requestId": token}

        return self._invoke(operation, invalid_message="The close request is no longer valid.")

    def _load_analysis_settings(self) -> None:
        try:
            if not self._settings_path.exists():
                return
            if self._settings_path.is_symlink():
                raise ValueError("linked settings file")
            with self._settings_path.open("rb") as source:
                content = source.read(_MAX_SETTINGS_BYTES + 1)
            if len(content) > _MAX_SETTINGS_BYTES:
                raise ValueError("oversized settings file")
            payload = json.loads(content)
            if type(payload) is not dict or type(payload.get("version")) is not int:
                raise ValueError("invalid settings version")
            if payload["version"] == 1 and set(payload) == {"version", "ignorePaths"}:
                self._legacy_ignore_paths = _ignore_paths(payload["ignorePaths"])
                self._settings_issue = _LEGACY_SETTINGS_ISSUE
                return
            if (payload["version"] != 2 or set(payload) not in (
                    {"version", "projects"}, {"version", "projects", "legacyIgnorePaths"})):
                raise ValueError("invalid project settings schema")
            projects = payload["projects"]
            if type(projects) is not dict or len(projects) > _MAX_COLLECTION_LENGTH:
                raise ValueError("invalid project settings map")
            parsed: dict[str, tuple[str, ...]] = {}
            for key, settings in projects.items():
                if (type(key) is not str or len(key) != 64 or
                        any(char not in "0123456789abcdef" for char in key) or
                        type(settings) is not dict or set(settings) != {"ignorePaths"}):
                    raise ValueError("invalid project settings record")
                parsed[key] = _ignore_paths(settings["ignorePaths"])
            legacy = (_ignore_paths(payload["legacyIgnorePaths"])
                      if "legacyIgnorePaths" in payload else None)
            self._settings_by_project = parsed
            self._legacy_ignore_paths = legacy
            if legacy is not None:
                self._settings_issue = _LEGACY_SETTINGS_ISSUE
        except (OSError, ValueError, TypeError, RecursionError):
            self._settings_issue = "Analysis settings could not be loaded."

    @staticmethod
    def _project_settings_key(root: Path) -> str:
        return sha256(os.path.normcase(str(root.resolve())).encode("utf-8")).hexdigest()

    def _open_project(self, path: Path) -> None:
        # Resolve/validate first so a failed selection cannot change active rules.
        root = path.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("project path must be a directory")
        ignored = self._settings_by_project.get(self._project_settings_key(root), ())
        before = self._controller.capture_assistant_context()
        try:
            self._controller.open_project(
                root, ignore_paths=ignored, _defer_assistant_commit=True)
        except Exception:
            # The controller has not admitted the switch if publication is unchanged.
            if self._controller._assistant_context is before:
                raise
            if self._controller.project_root == root:
                try:
                    identity = self._controller.capture_assistant_context()
                    if identity.project_epoch is None:
                        raise RuntimeError("project identity unavailable")
                    self._project_explorer.bind_project(root, identity.project_epoch)
                except Exception:
                    try:
                        self._controller._abandon_project_switch()
                    finally:
                        self._project_explorer.unbind_project()
                    raise ValueError("project path cannot be opened") from None
            elif self._controller.project_root is None:
                self._project_explorer.unbind_project()
            raise
        try:
            identity = self._controller.capture_assistant_context()
            if identity.project_epoch is None:
                raise RuntimeError("project identity unavailable")
            self._project_explorer.bind_project(root, identity.project_epoch)
        except Exception:
            try:
                self._controller._abandon_project_switch()
            finally:
                self._project_explorer.unbind_project()
            raise ValueError("project path cannot be opened") from None
        self._sanitize_view_state()
        self._controller._assistant_finalize_project_open(identity)

    def _settings(self) -> JsonObject:
        return {"ignorePaths": list(self._controller.ignore_paths),
                "issue": self._settings_issue}

    def get_analysis_settings(self) -> JsonObject:
        return self._call(self._settings,
                          invalid_message="The analysis settings request is invalid.")

    def set_analysis_settings(self, payload: object) -> JsonObject:
        def operation() -> JsonObject:
            root = self._controller.project_root
            if root is None:
                raise RuntimeError("open a project before changing analysis settings")
            if type(payload) is not dict or set(payload) != {"ignorePaths"}:
                raise ValueError("settings have unexpected or missing fields")
            ignored = _ignore_paths(payload["ignorePaths"])
            key = self._project_settings_key(root)
            projects = {**self._settings_by_project, key: ignored}
            if len(projects) > _MAX_COLLECTION_LENGTH:
                raise ValueError("project settings limit reached")
            document: JsonObject = {
                "version": 2,
                "projects": {project_key: {"ignorePaths": list(paths)}
                             for project_key, paths in projects.items()},
            }
            if self._legacy_ignore_paths is not None:
                document["legacyIgnorePaths"] = list(self._legacy_ignore_paths)
            content = json.dumps(document,
                                 ensure_ascii=False).encode("utf-8")
            if len(content) > _MAX_SETTINGS_BYTES:
                raise ValueError("analysis settings exceed size limit")
            if self._cache_store.data_dir.resolve().is_relative_to(root):
                raise ValueError("application data must be outside the analyzed project")
            changed = ignored != self._controller.ignore_paths
            pending = None
            if changed:
                pending = self._controller._assistant_invalidate(
                    status=(AnalysisStatus.OUTDATED if self._controller.analysis is not None
                            else AnalysisStatus.NOT_ANALYZED),
                    root=root, ignore_paths=self._controller.ignore_paths)
            temporary: Path | None = None
            try:
                self._cache_store.data_dir.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(mode="wb", dir=self._cache_store.data_dir,
                                        prefix=".analysis-settings-", delete=False) as output:
                    temporary = Path(output.name)
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self._settings_path)
                # Replacement owns the settings now; there is no temp file to clean up.
                temporary = None
            except Exception:
                if pending is not None:
                    self._controller._assistant_complete(pending)
                raise
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            self._settings_by_project = projects
            self._settings_issue = (_LEGACY_SETTINGS_ISSUE
                                    if self._legacy_ignore_paths is not None else None)
            if pending is not None:
                self._controller._activate_saved_analysis_settings(ignored, pending)
            return self._settings()

        return self._write_call(operation,
                                invalid_message="The analysis settings update is invalid.")

    def choose_project(self) -> JsonObject:
        def operation() -> JsonObject:
            if self._window is None:
                raise RuntimeError("desktop window is not available")
            paths = self._window.create_file_dialog(
                self._folder_dialog_type,
                directory=str(self._controller.project_root or Path.home()),
                allow_multiple=False)
            if paths:
                path = _string(paths[0], "path", max_length=_MAX_PATH_LENGTH)
                try:
                    self._open_project(Path(path))
                except OSError:
                    raise ValueError("project path cannot be opened") from None
            return self._session()

        return self._write_call(operation,
                                invalid_message="The project selection is invalid.")

    def get_cache_info(self) -> JsonObject:
        def operation() -> JsonObject:
            root = self._controller.project_root
            path = self._cache_store.analysis_path(root) if root is not None else None
            return {"dataDir": str(self._cache_store.data_dir.resolve()),
                    "analysisPath": str(path) if path is not None else None,
                    "exists": path.is_file() if path is not None else False,
                    "status": self._controller.status.value,
                    "outdatedReasons": list(self._controller.outdated_reasons),
                    "cacheIssue": self._controller.cache_issue}

        return self._call(operation, invalid_message="The cache request is invalid.")

    @staticmethod
    def _invoke(operation: Callable[[], T], *,
                invalid_message: str) -> JsonObject:
        try:
            return success_payload(operation())
        except SourceEditingError as error:
            return contract_error_payload(error)
        except RuntimeError as error:
            return contract_error_payload(error)
        except (TypeError, ValueError) as error:
            return contract_error_payload(error, safe_message=invalid_message)
        except Exception as error:
            logger.exception("Desktop bridge call failed")
            return contract_error_payload(error)

    def _call(self, operation: Callable[[], T], *,
              invalid_message: str) -> JsonObject:
        with self._lock:
            response = self._invoke(operation, invalid_message=invalid_message)
            try:
                self._publish_session()
            except PublicationError:
                # A display refresh may race publication without changing the
                # ordinary response or replacing the last coherent snapshot.
                pass
            return response

    def _write_call(self, operation: Callable[[], T], *,
                    invalid_message: str) -> JsonObject:
        if (self._snapshot[1] is AnalysisStatus.ANALYZING and
                self._controller.status is AnalysisStatus.ANALYZING):
            return contract_error_payload(
                RuntimeError("state cannot change during analysis"))

        def guarded() -> T:
            if self._controller.status is AnalysisStatus.ANALYZING:
                raise RuntimeError("state cannot change during analysis")
            return operation()

        return self._call(guarded, invalid_message=invalid_message)

    def _session(self) -> JsonObject:
        recent_projects = self._controller.recent_projects()
        self._recent_projects_snapshot = recent_projects
        self._publish_session()
        return self._session_snapshot()

    def _session_snapshot(self) -> JsonObject:
        (root, status, reasons, issue, recent, has_analysis,
         epoch, generation) = self._snapshot
        payload = session_payload(root, status, reasons, issue, recent, has_analysis)
        payload["projectEpoch"] = epoch
        payload["analysisGeneration"] = generation
        payload["fileSystemCaseSensitive"] = sys.platform != "win32"
        return payload

    def _analysis(self) -> ProjectAnalysis:
        if self._controller.analysis is None:
            raise RuntimeError("analyze the project before requesting data")
        return self._controller.analysis

    def get_session(self) -> JsonObject:
        acquired = self._lock.acquire(blocking=False)
        try:
            operation = self._session if acquired else self._session_snapshot
            return self._invoke(
                operation, invalid_message="The session request is invalid.")
        finally:
            if acquired:
                self._lock.release()

    def open_project(self, path: object) -> JsonObject:
        def operation() -> JsonObject:
            project_path = _string(path, "path", max_length=_MAX_PATH_LENGTH)
            try:
                self._open_project(Path(project_path))
            except OSError:
                raise ValueError("project path cannot be opened") from None
            return self._session()

        return self._write_call(
            operation, invalid_message="The project path is invalid.")

    def analyze(self) -> JsonObject:
        def operation() -> JsonObject:
            if self._controller.project_root is None:
                raise RuntimeError("open a project before analyzing")
            self._controller.analyze(before_analysis=self._publish_session)
            self._sanitize_view_state()
            return self._session()

        return self._call(
            operation, invalid_message="The analysis request is invalid.")

    def get_overview(self) -> JsonObject:
        def operation() -> JsonObject:
            analysis = self._analysis()
            if self._controller.project_root is None:
                raise RuntimeError("open a project before requesting the overview")
            return overview_payload(build_overview(
                analysis, self._controller.project_root))

        return self._call(
            operation, invalid_message="The overview request is invalid.")

    def get_view(self, kind: object, root_id: object = None,
                 selected_id: object = None, hops: object = 1,
                 direction: object = "both", structure_depth: object = 1) -> JsonObject:
        def operation() -> JsonObject:
            parsed_kind = _view_kind(kind)
            root = _optional_string(root_id, "root_id")
            selected = _optional_string(selected_id, "selected_id")
            if type(structure_depth) is not int or not 1 <= structure_depth <= 5:
                raise ValueError("structure_depth must be an integer from 1 to 5")
            if type(hops) is not int or not 1 <= hops <= 5:
                raise ValueError("hops must be an integer from 1 to 5")
            if type(direction) is not str or direction not in (*_DIRECTIONS, "upstream", "downstream"):
                raise ValueError("direction must be in, out, or both")
            try:
                expected = self._controller.capture_assistant_context()
            except PublicationError:
                expected = None
            except Exception:
                logger.exception("Delivered graph context capture failed")
                expected = None
            graph = self._controller.view(
                parsed_kind, root, selected, hops=hops,
                direction=_DIRECTIONS.get(direction, direction), structure_depth=structure_depth)
            if "direction" in graph.query_context:
                graph = replace(graph, query_context={**graph.query_context, "direction": direction})
            payload = graph_payload(graph)
            if expected is not None:
                try:
                    self._controller._assistant_publish_view(expected, graph, parsed_kind, root)
                except Exception:
                    logger.exception("Delivered graph context projection failed")
            return payload

        return self._call(
            operation, invalid_message="The graph request is invalid.")

    def get_detail_view(self, kind: object, root_id: object,
                        source_node_id: object) -> JsonObject:
        def operation() -> JsonObject:
            parsed_kind = _view_kind(kind)
            root = _optional_string(root_id, "root_id")
            source = _string(source_node_id, "source_node_id")
            try:
                expected = self._controller.capture_assistant_context()
            except PublicationError:
                expected = None
            except Exception:
                logger.exception("Delivered detail context capture failed")
                expected = None
            graph = self._controller.detail_view(parsed_kind, root, source)
            payload = graph_payload(graph)
            if expected is not None:
                try:
                    self._controller._assistant_publish_view(
                        expected, graph, parsed_kind, root, source)
                except Exception:
                    logger.exception("Delivered detail context projection failed")
            return payload

        return self._call(
            operation, invalid_message="The detail graph request is invalid.")

    def get_inspector(self, kind: object, root_id: object, selected_id: object,
                      detail_source_id: object = None, structure_depth: object = 1) -> JsonObject:
        def operation() -> JsonObject:
            parsed_kind = _view_kind(kind)
            if type(structure_depth) is not int or not 1 <= structure_depth <= 5:
                raise ValueError("structure_depth must be an integer from 1 to 5")
            root = _optional_string(root_id, "root_id")
            selected = _string(selected_id, "selected_id")
            detail_source = _optional_string(detail_source_id, "detail_source_id")
            view = (self._controller.detail_view(parsed_kind, root, detail_source)
                    if detail_source is not None else
                    self._controller.view(parsed_kind, root, selected, structure_depth=structure_depth))
            if selected not in {node.id for node in view.nodes}:
                raise ValueError("selected node is not available in this view")
            return inspector_payload(build_inspector(view, selected, self._analysis()))

        return self._call(operation, invalid_message="The inspector request is invalid.")

    def get_view_state(self) -> JsonObject:
        def operation() -> JsonObject:
            self._sanitize_view_state()
            return view_state_payload(self._controller.view_state)

        return self._call(operation,
                          invalid_message="The view state request is invalid.")

    def get_summary_layout(self, context: object) -> JsonObject:
        return self._call(
            lambda: summary_layout_payload(self._controller.get_summary_layout(context)),
            invalid_message="The summary layout request is invalid.")

    def move_summary_node(self, context: object, layout_token: object,
                          node_id: object, position: object) -> JsonObject:
        return self._write_call(
            lambda: summary_layout_payload(self._controller.move_summary_node(
                context, layout_token, node_id, position)),
            invalid_message="The summary node position request is invalid.")

    def save_summary_viewport(self, context: object, layout_token: object,
                              viewport: object) -> JsonObject:
        return self._write_call(
            lambda: summary_layout_payload(self._controller.save_summary_viewport(
                context, layout_token, viewport)),
            invalid_message="The summary viewport request is invalid.")

    def _sanitize_view_state(self) -> None:
        # Without analysis, defer identity checks until analyze/get_view_state.
        # Annotations are a separate record and must never be purged here.
        if self._controller.analysis is None:
            return
        previous = self._controller.view_state
        self._controller.sanitize_view_state()
        if previous != self._controller.view_state:
            self._controller._persist_view_state()

    def search(self, query: object, max_results: object = 100,
               current_view: object = None) -> JsonObject:
        def operation() -> JsonObject:
            text = _string(query, "query", max_length=_MAX_QUERY_LENGTH,
                           allow_empty=True)
            if type(max_results) is not int or not 1 <= max_results <= 100:
                raise ValueError("max_results must be an integer from 1 to 100")
            if current_view is not None:
                raise ValueError("current_view is not supported by this bridge version")
            analysis = self._analysis()
            response = search_project(analysis, text, max_results=max_results)
            targets = tuple(navigation.plan_reveal(analysis, result)
                            for result in response.results)
            return search_response_payload(response, targets)

        return self._call(
            operation, invalid_message="The search request is invalid.")

    def read_source(self, anchor: object) -> JsonObject:
        return self._call(
            lambda: source_payload(self._controller.read_source(
                _source_anchor(anchor))),
            invalid_message="The source request is invalid.",
        )

    def open_source_document(self, project_path: object, anchor: object) -> JsonObject:
        def operation() -> JsonObject:
            return editable_source_payload(self._controller.open_source_document(
                _string(project_path, "projectPath", max_length=_MAX_PATH_LENGTH),
                _source_anchor(anchor)))

        return self._call(operation, invalid_message="The source document request is invalid.")

    @staticmethod
    def _explorer_request(request: object, required: set[str],
                          optional: set[str] | None = None) -> dict[str, object]:
        if (type(request) is not dict or not required.issubset(request)
                or not set(request).issubset(required | (optional or set()))):
            raise ValueError("file request has unexpected or missing fields")
        return request

    @staticmethod
    def _explorer_project(request: dict[str, object]) -> str:
        return _string(request["projectPath"], "projectPath", max_length=_MAX_PATH_LENGTH)

    def list_project_directory(self, request: object) -> JsonObject:
        def operation() -> JsonObject:
            payload = self._explorer_request(request, {"projectPath", "directory"},
                                             {"projectEpoch", "cursor"})
            return self._project_explorer.list_directory(
                self._explorer_project(payload),
                _string(payload["directory"], "directory", allow_empty=True),
                _optional_string(payload.get("projectEpoch"), "projectEpoch"),
                _optional_string(payload.get("cursor"), "cursor"))

        return self._call(operation, invalid_message="The directory request is invalid.")

    def _project_files_changed(self, affected_paths: tuple[str, ...]) -> None:
        self._controller._source_editing.invalidate_paths(affected_paths)
        if self._controller.analysis is not None:
            self._controller.status = AnalysisStatus.OUTDATED
            self._controller.outdated_reasons = tuple(dict.fromkeys(
                (*self._controller.outdated_reasons, "project files changed; reanalysis required")))

    def _file_mutation(self, operation: Callable[[], JsonObject]) -> JsonObject:
        try:
            result = operation()
        except ProjectFileError as error:
            if error.affected_paths:
                self._project_files_changed(error.affected_paths)
            raise
        affected = result["affectedPaths"]
        if type(affected) is not list or any(type(path) is not str for path in affected):
            raise RuntimeError("invalid file service result")
        self._project_files_changed(tuple(str(path) for path in affected))
        return result

    def create_project_entry(self, request: object) -> JsonObject:
        def operation() -> JsonObject:
            payload = self._explorer_request(request, {
                "projectPath", "projectEpoch", "parent", "parentRevision", "name", "kind"})
            return self._file_mutation(lambda: self._project_explorer.create_entry(
                self._explorer_project(payload), _string(payload["projectEpoch"], "projectEpoch"),
                _string(payload["parent"], "parent", allow_empty=True),
                _string(payload["parentRevision"], "parentRevision", max_length=128),
                _string(payload["name"], "name", allow_empty=True), _string(payload["kind"], "kind"),
                before_mutation=self._controller._assistant_invalidate_for_file_mutation))

        return self._write_call(operation, invalid_message="The create request is invalid.")

    def prepare_project_entry_operation(self, request: object) -> JsonObject:
        def operation() -> JsonObject:
            payload = self._explorer_request(request, {
                "projectPath", "projectEpoch", "path", "revision", "operation"},
                {"newName", "destinationDirectory", "destinationRevision"})
            action = _string(payload["operation"], "operation")
            fields = {"rename": {"newName"}, "delete": set(),
                      "move": {"destinationDirectory", "destinationRevision"}}
            if (action not in fields or set(payload) - {
                    "projectPath", "projectEpoch", "path", "revision", "operation"}
                    != fields[action]):
                raise ValueError("unexpected or missing operation target")
            return self._project_explorer.prepare_operation(
                self._explorer_project(payload), _string(payload["projectEpoch"], "projectEpoch"),
                _string(payload["path"], "path"),
                _string(payload["revision"], "revision", max_length=128), action,
                _string(payload["newName"], "newName", allow_empty=True)
                if "newName" in payload else None,
                _string(payload["destinationDirectory"], "destinationDirectory", allow_empty=True)
                if action == "move" else None,
                _string(payload["destinationRevision"], "destinationRevision", max_length=128)
                if action == "move" else None)

        return self._write_call(operation, invalid_message="The file operation request is invalid.")

    def apply_project_entry_operation(self, request: object) -> JsonObject:
        def operation() -> JsonObject:
            payload = self._explorer_request(request, {"projectPath", "projectEpoch", "operationToken"})
            return self._file_mutation(lambda: self._project_explorer.apply_operation(
                self._explorer_project(payload), _string(payload["projectEpoch"], "projectEpoch"),
                _string(payload["operationToken"], "operationToken", max_length=128),
                before_mutation=self._controller._assistant_invalidate_for_file_mutation))

        return self._write_call(operation, invalid_message="The file operation token is invalid.")

    def save_source_document(self, request: object) -> JsonObject:
        def operation() -> JsonObject:
            if (type(request) is not dict
                    or set(request) != {"projectPath", "filePath", "text", "revision"}):
                raise ValueError("save request has unexpected or missing fields")
            text = _string(request["text"], "text", max_length=MAX_SOURCE_BYTES, allow_empty=True)
            try:
                # Validate Unicode here; the service bounds the preserved encoding's bytes.
                text.encode("utf-8", errors="strict")
            except UnicodeError:
                raise ValueError("invalid source text") from None
            return editable_source_payload(self._controller.save_source_document(
                _string(request["projectPath"], "projectPath", max_length=_MAX_PATH_LENGTH),
                _string(request["filePath"], "filePath"), text,
                _string(request["revision"], "revision", max_length=128)))

        return self._write_call(operation, invalid_message="The source save request is invalid.")

    def get_preferences(self) -> JsonObject:
        def operation() -> JsonObject:
            language = self._preferences_store.load()
            return preferences_payload(language, self._preferences_store.issue)

        return self._call(
            operation, invalid_message="The preferences request is invalid.")

    def set_language(self, language: object) -> JsonObject:
        return self._write_call(
            lambda: self._save_language(language),
            invalid_message="The language is invalid.")

    def _save_language(self, language: object) -> JsonObject:
        parsed = _string(language, "language", max_length=16)
        self._preferences_store.save(parsed)
        return preferences_payload(parsed, self._preferences_store.issue)

    def set_preferences(self, payload: object) -> JsonObject:
        def operation() -> JsonObject:
            if type(payload) is not dict or set(payload) != _PREFERENCES_FIELDS:
                raise ValueError("preferences have unexpected or missing fields")
            return self._save_language(payload["language"])

        return self._write_call(
            operation, invalid_message="The preferences update is invalid.")

    def save_layout(self, payload: object) -> JsonObject:
        def operation() -> JsonObject:
            if type(payload) is not dict or set(payload) != _LAYOUT_FIELDS:
                raise ValueError("layout has unexpected or missing fields")
            active_value = payload["activeView"]
            active = None if active_value is None else _view_kind(
                active_value, "activeView")
            selected = _optional_string(payload["selectedId"], "selectedId")
            tabs_value = payload["openTabs"]
            if type(tabs_value) is not list or len(tabs_value) > _MAX_COLLECTION_LENGTH:
                raise ValueError("openTabs must be a bounded list")
            tabs = tuple(_string(tab, "openTabs item") for tab in tabs_value)
            roots_value = payload["graphRoots"]
            if type(roots_value) is not dict or len(roots_value) > len(ViewKind):
                raise ValueError("graphRoots must be a bounded object")
            roots: dict[str, str | None] = {}
            for key, value in roots_value.items():
                kind = _view_kind(key, "graphRoots key")
                roots[kind.value] = _optional_string(value, "graphRoots value")
                if roots[kind.value] is not None:
                    self._controller.view(kind, roots[kind.value],
                        structure_depth=self._controller.current_structure_depth(kind, roots[kind.value]),
                        _remember_depth=False)
            if selected is not None and not self._controller.valid_identity(
                    selected, active, roots.get(active.value) if active else None):
                raise ValueError("unknown selected node ID")
            self._controller.save_layout(active, selected, tabs, roots)
            return view_state_payload(self._controller.view_state)

        return self._write_call(
            operation, invalid_message="The layout request is invalid.")

    def move_node(self, node_id: object, position: object,
                  view_kind: object = None) -> JsonObject:
        def operation() -> JsonObject:
            kind = None if view_kind is None else _view_kind(view_kind, "view_kind")
            parsed_position = _point(position, "position")
            self._controller.move_node(
                self._node_id(node_id, kind), parsed_position, kind)
            return view_state_payload(self._controller.view_state)

        return self._write_call(
            operation, invalid_message="The node position request is invalid.")

    def save_viewport(self, kind: object, zoom: object, pan: object) -> JsonObject:
        def operation() -> JsonObject:
            if (isinstance(zoom, bool) or not isinstance(zoom, (int, float)) or
                    not isfinite(zoom) or zoom <= 0):
                raise ValueError("zoom must be a positive finite number")
            self._controller.save_viewport(
                _view_kind(kind), float(zoom), _point(pan, "pan"))
            return view_state_payload(self._controller.view_state)

        return self._write_call(
            operation, invalid_message="The viewport request is invalid.")

    def expand(self, node_id: object) -> JsonObject:
        def operation() -> JsonObject:
            self._controller.expand(self._node_id(node_id))
            return view_state_payload(self._controller.view_state)

        return self._write_call(
            operation, invalid_message="The expand request is invalid.")

    def collapse(self, node_id: object) -> JsonObject:
        def operation() -> JsonObject:
            self._controller.collapse(self._node_id(node_id))
            return view_state_payload(self._controller.view_state)

        return self._write_call(
            operation, invalid_message="The collapse request is invalid.")

    def open_detail_island(self, kind: object, source_node_id: object,
                           position: object = None) -> JsonObject:
        def operation() -> JsonObject:
            parsed_position = None if position is None else _point(position, "position")
            parsed_kind = _view_kind(kind)
            source = self._node_id(source_node_id, parsed_kind)
            self._controller.detail_view(parsed_kind,
                                         self._controller.view_state.graph_roots.get(parsed_kind.value), source)
            self._controller.open_detail_island(
                parsed_kind, source,
                parsed_position)
            return view_state_payload(self._controller.view_state)

        return self._write_call(
            operation, invalid_message="The detail island request is invalid.")

    def _analysis_node_ids(self) -> set[str]:
        analysis = self._analysis()
        known = set(analysis.symbol_index.entries)
        if analysis.flow is not None:
            known.update(analysis.flow.graph.nodes)
            known.update(node.id for node in analysis.flow.data_nodes)
        if analysis.model is not None:
            known.update(definition.symbol_id for definition in analysis.model.definitions)
            known.update(instance.id for instance in analysis.model.instances)
            known.update(invocation.id for invocation in analysis.model.invocations)
        if analysis.training is not None:
            known.update(entry.id for entry in analysis.training.entry_points)
            known.update(event.id for event in analysis.training.events)
            known.update(join.id for join in analysis.training.joins)
        if analysis.semantic is not None:
            known.update(component.id for component in analysis.semantic.graph.components)
        return known

    def _known_node_ids(self, roots: dict[str, str | None]) -> set[str]:
        known = self._analysis_node_ids()
        for kind in ViewKind:
            try:
                view = self._controller.view(kind, roots.get(kind.value))
            except ValueError:
                continue
            known.update(node.id for node in view.nodes)
        return known

    def _node_id(self, value: object, kind: ViewKind | None = None) -> str:
        node_id = _string(value, "node_id")
        kind = kind or self._controller.view_state.active_view
        if not self._controller.valid_identity(node_id, kind,
                self._controller.view_state.graph_roots.get(kind.value) if kind else None):
            raise ValueError("unknown node ID")
        state = self._controller.view_state
        stored = set(state.positions) | set(state.expanded_ids)
        for positions in state.positions_by_view.values():
            stored.update(positions)
        if node_id not in stored and len(stored) >= 1000:
            raise ValueError("node state limit reached")
        return node_id

    def move_detail_island(self, island_id: object,
                           position: object) -> JsonObject:
        def operation() -> JsonObject:
            self._controller.move_detail_island(
                _string(island_id, "island_id"), _point(position, "position"))
            return view_state_payload(self._controller.view_state)

        return self._write_call(
            operation, invalid_message="The detail island position is invalid.")

    def close_detail_island(self, island_id: object) -> JsonObject:
        def operation() -> JsonObject:
            self._controller.close_detail_island(_string(island_id, "island_id"))
            return view_state_payload(self._controller.view_state)

        return self._write_call(
            operation, invalid_message="The detail island ID is invalid.")

    def get_problems(self) -> JsonObject:
        def operation() -> list[JsonValue]:
            problems = build_problems(self._analysis(), self._controller.view_issues)
            return [problem_payload(problem) for problem in problems]

        return self._call(
            operation, invalid_message="The problems request is invalid.")
