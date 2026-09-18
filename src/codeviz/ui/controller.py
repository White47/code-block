"""UI-independent project session and safe, read-only source navigation."""

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from stat import S_ISREG
from typing import BinaryIO

from codeviz.analysis.project import ProjectAnalysis, analyze_project
from codeviz.core import SourceAnchor, ViewKind, VisibleGraph
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


def _opened_file_path(source: BinaryIO) -> Path:
    """Resolve the opened handle, not the name that may have changed before open."""
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        api = ctypes.windll.kernel32.GetFinalPathNameByHandleW
        api.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
        api.restype = wintypes.DWORD
        size = 260
        while size <= 32768:
            buffer = ctypes.create_unicode_buffer(size)
            length = api(msvcrt.get_osfhandle(source.fileno()), buffer, size, 0)
            if length == 0:
                raise OSError(ctypes.get_last_error(), "cannot verify opened source path")
            if length < size:
                raw = buffer.value
                if raw.startswith("\\\\?\\UNC\\"):
                    return Path("\\\\" + raw[8:])
                if raw.startswith("\\\\?\\"):
                    return Path(raw[4:])
                raise ValueError("opened source path is not a DOS path")
            size = length + 1
        raise ValueError("opened source path is too long to verify")
    descriptor = Path("/proc/self/fd") / str(source.fileno())
    if not descriptor.exists():
        raise ValueError("opened source path cannot be verified on this platform")
    return descriptor.resolve(strict=True)


class AppController:
    def __init__(self) -> None:
        self.project_root: Path | None = None
        self.analysis: ProjectAnalysis | None = None
        self.status = AnalysisStatus.NOT_ANALYZED
        self.view_state = ViewState()

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

    def analyze(self) -> ProjectAnalysis:
        if self.project_root is None:
            raise RuntimeError("open a project before analyzing")
        if self.status == AnalysisStatus.ANALYZING:
            raise RuntimeError("analysis is already running")
        self.status = AnalysisStatus.ANALYZING
        self.analysis = None
        try:
            result = analyze_project(self.project_root)
        except Exception:
            self.status = AnalysisStatus.FAILED
            raise
        self.analysis = result
        self.status = (AnalysisStatus.PARTIAL if any(
            file.tree is None for file in result.files
        ) or any(issue.code.endswith("_ERROR") for issue in result.issues)
                       else AnalysisStatus.READY)
        return result

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
        self.view_state = ViewState(
            expanded_ids=tuple(dict.fromkeys((*self.view_state.expanded_ids, node_id))),
            positions=dict(self.view_state.positions),
        )

    def collapse(self, node_id: str) -> None:
        self.view_state = ViewState(
            expanded_ids=tuple(item for item in self.view_state.expanded_ids
                               if item != node_id),
            positions=dict(self.view_state.positions),
        )

    def move_node(self, node_id: str, position: tuple[float, float]) -> None:
        positions = {**self.view_state.positions, node_id: position}
        self.view_state = ViewState(self.view_state.expanded_ids, positions)

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
            if not _opened_file_path(source).is_relative_to(self.project_root):
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
