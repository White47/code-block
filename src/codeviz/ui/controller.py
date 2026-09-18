"""UI-independent project session and safe, read-only source navigation."""

import os
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from stat import S_ISREG

from codeviz.analysis.project import ProjectAnalysis, analyze_project
from codeviz.core import SourceAnchor, ViewKind, VisibleGraph
from codeviz.persistence.fingerprint import fingerprint_project, outdated_reasons
from codeviz.persistence.store import CacheError, CacheStore
from codeviz.source_handles import opened_file_path
from codeviz.views import ViewRequest, ViewState, build_visible_graph


class AnalysisStatus(StrEnum):
    NOT_ANALYZED = "NotAnalyzed"
    ANALYZING = "Analyzing"
    READY = "Ready"
    PARTIAL = "Partial"
    OUTDATED = "Outdated"
    FAILED = "Failed"


@dataclass(frozen=True, slots=True)
class SourceDocument:
    file_path: str
    text: str
    start_line: int
    end_line: int


class AppController:
    def __init__(self, cache_store: CacheStore | None = None,
                 ignore_paths: tuple[str, ...] = ()) -> None:
        self.cache_store = cache_store
        self.ignore_paths = ignore_paths
        self.project_root: Path | None = None
        self.analysis: ProjectAnalysis | None = None
        self.status = AnalysisStatus.NOT_ANALYZED
        self.view_state = ViewState()
        self.outdated_reasons: tuple[str, ...] = ()
        self.cache_issue: str | None = None

    def open_project(self, project_root: Path) -> None:
        if self.status == AnalysisStatus.ANALYZING:
            raise RuntimeError("cannot switch project during analysis")
        root = Path(project_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("project_root must be an existing directory")
        self.project_root = root
        self.analysis = None
        self.status = AnalysisStatus.NOT_ANALYZED
        self.view_state = ViewState()
        self.outdated_reasons = ()
        self.cache_issue = None
        if self.cache_store is None:
            return
        try:
            self.view_state = self.cache_store.load_view_state(root)
        except (CacheError, OSError, ValueError) as error:
            self.cache_issue = f"View state: {error}"
        try:
            cached = self.cache_store.load_analysis(root)
        except (CacheError, OSError, ValueError) as error:
            self.cache_issue = f"Analysis cache: {error}"
            cached = None
        if cached is not None:
            self.analysis = cached.analysis
            try:
                current = fingerprint_project(root, self.ignore_paths)
                self.outdated_reasons = outdated_reasons(cached.fingerprint, current)
            except (OSError, ValueError) as error:
                self.outdated_reasons = ("source could not be verified",)
                self.cache_issue = f"Source fingerprint: {error}"
            self.status = (AnalysisStatus.OUTDATED if self.outdated_reasons else
                           self._result_status(cached.analysis))
        try:
            self.cache_store.record_recent_project(root)
        except (CacheError, OSError, ValueError) as error:
            if self.cache_issue is None:
                self.cache_issue = f"Recent projects: {error}"

    @staticmethod
    def _result_status(result: ProjectAnalysis) -> AnalysisStatus:
        if (any(not file.was_parsed for file in result.files) or
                any(issue.code.endswith("_ERROR") for issue in result.issues)):
            return AnalysisStatus.PARTIAL
        return AnalysisStatus.READY

    def analyze(self) -> ProjectAnalysis:
        if self.project_root is None:
            raise RuntimeError("open a project before analyzing")
        if self.status == AnalysisStatus.ANALYZING:
            raise RuntimeError("analysis is already running")
        old_analysis = self.analysis
        self.status = AnalysisStatus.ANALYZING
        before = None
        if self.cache_store is not None:
            try:
                before = fingerprint_project(self.project_root, self.ignore_paths)
            except (OSError, ValueError) as error:
                self.cache_issue = f"Source fingerprint: {error}"
        try:
            result = analyze_project(self.project_root, self.ignore_paths)
        except Exception:
            self.analysis = old_analysis
            self.status = AnalysisStatus.FAILED
            raise
        self.analysis = result
        self.status = self._result_status(result)
        self.outdated_reasons = ()
        if self.cache_store is not None:
            try:
                after = fingerprint_project(self.project_root, self.ignore_paths)
                if before is None:
                    self.status = AnalysisStatus.PARTIAL
                elif before != after:
                    self.status = AnalysisStatus.OUTDATED
                    self.outdated_reasons = ("source changed during analysis",)
                else:
                    self.cache_store.save_analysis(self.project_root, result, after)
                    self.cache_issue = None
            except (CacheError, OSError, ValueError) as error:
                self.cache_issue = f"Analysis cache: {error}"
                self.status = AnalysisStatus.PARTIAL
        return result

    def recent_projects(self) -> tuple[Path, ...]:
        if self.cache_store is None:
            return ()
        try:
            return self.cache_store.recent_projects()
        except (CacheError, OSError, ValueError) as error:
            self.cache_issue = f"Recent projects: {error}"
            return ()

    def _persist_view_state(self) -> None:
        if self.cache_store is not None and self.project_root is not None:
            try:
                self.cache_store.save_view_state(self.project_root, self.view_state)
            except (CacheError, OSError, ValueError) as error:
                self.cache_issue = f"View state: {error}"

    def save_layout(self, active_view: ViewKind | None, selected_id: str | None,
                    open_tabs: tuple[str, ...],
                    graph_roots: dict[str, str | None]) -> None:
        self.view_state = replace(self.view_state, active_view=active_view,
                                  selected_id=selected_id, open_tabs=open_tabs,
                                  graph_roots=dict(graph_roots))
        self._persist_view_state()

    def view(self, kind: ViewKind, root_id: str | None = None,
             selected_id: str | None = None, *, hops: int = 1,
             direction: str = "both") -> VisibleGraph:
        if self.analysis is None:
            raise RuntimeError("analyze the project before opening a graph")
        request = ViewRequest(kind, root_id=root_id, selected_id=selected_id,
                              hops=hops, direction=direction)
        return build_visible_graph(self.analysis, request, self.view_state)

    def expand(self, node_id: str) -> None:
        if not node_id:
            raise ValueError("node ID cannot be empty")
        self.view_state = replace(self.view_state,
            expanded_ids=tuple(dict.fromkeys((*self.view_state.expanded_ids, node_id))),
            positions=dict(self.view_state.positions),
        )
        self._persist_view_state()

    def collapse(self, node_id: str) -> None:
        self.view_state = replace(self.view_state,
            expanded_ids=tuple(item for item in self.view_state.expanded_ids
                               if item != node_id),
            positions=dict(self.view_state.positions),
        )
        self._persist_view_state()

    def move_node(self, node_id: str, position: tuple[float, float]) -> None:
        positions = {**self.view_state.positions, node_id: position}
        self.view_state = replace(self.view_state, positions=positions)
        self._persist_view_state()

    def reset_positions(self) -> None:
        """Restore automatic positions for this project's graphs only."""
        self.view_state = replace(self.view_state, positions={})
        self._persist_view_state()

    def read_source(self, anchor: SourceAnchor, *, max_bytes: int = 5_000_000) -> SourceDocument:
        if self.project_root is None:
            raise RuntimeError("open a project before reading source")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        source_path = self.project_root.joinpath(*anchor.file_path.split("/"))
        resolved = source_path.resolve(strict=True)
        if not resolved.is_relative_to(self.project_root):
            raise ValueError("source file resolves outside the project")
        before = resolved.stat()
        if not S_ISREG(before.st_mode) or resolved.suffix.casefold() != ".py":
            raise ValueError("source anchor must reference a Python file")
        if before.st_size > max_bytes:
            raise ValueError("source file exceeds viewer size limit")
        with resolved.open("rb") as source:
            opened = os.fstat(source.fileno())
            if not opened_file_path(source).is_relative_to(self.project_root):
                raise ValueError("opened source file is outside the project")
            if (not before.st_ino or not opened.st_ino or
                    (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
                raise ValueError("source file changed between validation and open")
            if not S_ISREG(opened.st_mode) or opened.st_size > max_bytes:
                raise ValueError("source file exceeds viewer size limit")
            content = source.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise ValueError("source file exceeds viewer size limit")
        text = content.decode("utf-8", errors="replace")
        return SourceDocument(anchor.file_path, text, anchor.start_line,
                              anchor.end_line or anchor.start_line)
