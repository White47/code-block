"""UI-independent project session and safe, read-only source navigation."""

import os
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from stat import S_ISREG

from codeviz.analysis.project import ProjectAnalysis, analyze_project
from codeviz.core import NodeKind, SourceAnchor, ViewIssue, ViewKind, VisibleGraph
from codeviz.persistence.fingerprint import fingerprint_project, outdated_reasons
from codeviz.persistence.store import CacheError, CacheStore, CacheVersionMismatch
from codeviz.source_handles import opened_file_path
from codeviz.views import (
    DetailIslandState,
    ViewportState,
    ViewRequest,
    ViewState,
    build_visible_graph,
)
from codeviz.views.architecture_structure import build_architecture_structure
from codeviz.views.data import critical_data_slice
from codeviz.views.query import GraphQuery
from codeviz.views.structure_identity import StructureResolver

_DETAIL_UNAVAILABLE_ERRORS = frozenset({
    "requested root is not available in this view",
    "selected component is outside the requested view",
    "selected code node is not available",
    "data context must focus a critical data node",
})
DETAIL_IDENTITY_ERRORS = _DETAIL_UNAVAILABLE_ERRORS | frozenset({
    "invalid structural detail source", "node has no local detail graph",
    "unknown structural component", "unknown structural aggregate",
    "invalid structural occurrence path", "invalid structural occurrence transition",
    "invalid structural occurrence identity",
})
_MAX_VIEW_ISSUES = 20


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
        self.view_issues: tuple[ViewIssue, ...] = ()
        self._structure_depths: dict[ViewKind, tuple[str | None, int]] = {}

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
        self.view_issues = ()
        self._structure_depths = {}
        if self.cache_store is None:
            return
        try:
            self.view_state = self.cache_store.load_view_state(root)
        except (CacheError, OSError, ValueError) as error:
            self.cache_issue = f"View state: {error}"
        try:
            cached = self.cache_store.load_analysis(root)
        except CacheVersionMismatch:
            self.outdated_reasons = ("analysis versions changed; reanalysis required",)
            self.status = AnalysisStatus.OUTDATED
            cached = None
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
            self.sanitize_view_state()
        try:
            self.cache_store.record_recent_project(root)
        except (CacheError, OSError, ValueError) as error:
            if self.cache_issue is None:
                self.cache_issue = f"Recent projects: {error}"

    @staticmethod
    def _result_status(result: ProjectAnalysis) -> AnalysisStatus:
        if (any(not file.was_parsed for file in result.files) or
                any(issue.code.endswith(("_ERROR", "_PARTIAL"))
                    or issue.code == "SCOPE_DECLARATION_CONFLICT" for issue in result.issues)):
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
        self.sanitize_view_state()
        self.view_issues = ()
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

    def sanitize_view_state(self) -> None:
        """Clean stale identities while retaining tabs, positions and annotations."""
        if self.analysis is None:
            return
        state = self.view_state
        roots = dict(state.graph_roots)
        for kind, root in roots.items():
            if root is not None:
                try:
                    self.view(ViewKind(kind), root, _remember_depth=False)
                except ValueError as error:
                    if str(error) not in DETAIL_IDENTITY_ERRORS:
                        raise
                    roots[kind] = None
        selected = state.selected_id
        if selected is not None and state.active_view is not None:
            try:
                if state.active_view in (ViewKind.ARCHITECTURE, ViewKind.PROJECT_STRUCTURE):
                    active_root = roots.get(state.active_view.value)
                    visible = self.view(state.active_view, active_root,
                                        structure_depth=self.current_structure_depth(state.active_view, active_root),
                                        _remember_depth=False)
                    if (selected not in {node.id for node in visible.nodes}
                            and not self._valid_detail_identity(selected, state.active_view, active_root)):
                        selected = None
                else:
                    self.view(state.active_view, roots.get(state.active_view.value), selected)
            except ValueError as error:
                if str(error) not in DETAIL_IDENTITY_ERRORS:
                    raise
                selected = None
        islands = []
        for island in state.detail_islands:
            try:
                if not self.valid_identity(island.source_node_id, island.view_kind):
                    continue
                self.detail_view(island.view_kind, None, island.source_node_id)
            except ValueError as error:
                if str(error) not in DETAIL_IDENTITY_ERRORS:
                    raise
                continue
            islands.append(island)
        self.view_state = replace(
            state, selected_id=selected, graph_roots=roots, detail_islands=tuple(islands),
            expanded_ids=tuple(node for node in state.expanded_ids if self.valid_identity(node)),
            positions={node: point for node, point in state.positions.items() if self.valid_identity(node)},
            positions_by_view={kind: {node: point for node, point in positions.items()
                                      if self.valid_identity(node, ViewKind(kind))}
                               for kind, positions in state.positions_by_view.items()},
        )
        if self.view_state != state:
            self._persist_view_state()

    def structure_namespace(self) -> str:
        return (sha256(os.path.normcase(str(self.project_root.resolve())).encode()).hexdigest()[:24]
                if self.project_root is not None else "")

    def current_structure_depth(self, kind: ViewKind, root_id: str | None) -> int:
        context = self._structure_depths.get(kind)
        return context[1] if context is not None and context[0] == root_id else 1

    def valid_identity(self, node_id: str, kind: ViewKind | None = None,
                       root_id: str | None = None) -> bool:
        if self.analysis is None:
            return False
        resolver = StructureResolver(self.analysis, self.structure_namespace())
        if node_id.startswith("aggregate:Architecture:"):
            fields = node_id.split(":")
            if len(fields) != 7 or fields[2] != resolver.namespace or kind not in (None, ViewKind.ARCHITECTURE):
                return False
            aggregate_root = None if fields[3] == "all" else fields[3]
            if root_id is not None and root_id != aggregate_root:
                return False
            try:
                depth, nodes, edges = (int(item) for item in fields[4:])
            except ValueError:
                return False
            if not (1 <= depth <= 5 and 1 <= nodes <= 100 and 1 <= edges <= 300):
                return False
            try:
                graph = build_visible_graph(self.analysis, ViewRequest(
                    ViewKind.ARCHITECTURE, root_id=aggregate_root, structure_depth=depth,
                    max_nodes=nodes, max_edges=edges,
                    project_namespace=self.structure_namespace()), self.view_state)
                return node_id in {item.id for item in graph.aggregations}
            except ValueError as error:
                if str(error) not in DETAIL_IDENTITY_ERRORS:
                    raise
                return False
        if kind is ViewKind.PROJECT_STRUCTURE:
            return resolver.project_valid(node_id, root_id)
        if kind is ViewKind.ARCHITECTURE or node_id.startswith("projection:"):
            if kind not in (None, ViewKind.ARCHITECTURE):
                return False
            if (kind is ViewKind.ARCHITECTURE and self.analysis.flow is not None
                    and node_id in self.analysis.flow.graph.nodes
                    and self.analysis.flow.graph.nodes[node_id].kind is NodeKind.DATA):
                roots = resolver.roots()
                if root_id is not None:
                    if root_id not in roots:
                        return False
                    roots = (root_id,)
                return (node_id in critical_data_slice(
                    self.analysis, roots, ViewKind.ARCHITECTURE).root_by_node
                    or self._valid_detail_identity(node_id, kind, root_id))
            try:
                resolver.resolve(node_id, root_id)
                return True
            except ValueError as error:
                if str(error) not in DETAIL_IDENTITY_ERRORS:
                    raise
                return (kind is ViewKind.ARCHITECTURE and root_id is not None
                        and self._valid_detail_identity(node_id, kind, root_id))
        if node_id.startswith("project:"):
            return kind is None and resolver.project_valid(node_id)
        known = set(self.analysis.symbol_index.entries)
        if self.analysis.flow:
            known.update(self.analysis.flow.graph.nodes)
            known.update(node.id for node in self.analysis.flow.data_nodes)
        if self.analysis.semantic:
            known.update(component.id for component in self.analysis.semantic.graph.components)
        if node_id in known:
            return True
        for view_kind in ((kind,) if kind else tuple(ViewKind)):
            if view_kind not in (ViewKind.EXECUTION, ViewKind.FUNCTION_FLOW,
                                 ViewKind.DATA_FLOW, ViewKind.SYMBOL):
                continue
            try:
                graph = self.view(view_kind, root_id if kind else
                                  self.view_state.graph_roots.get(view_kind.value))
                if node_id in {node.id for node in graph.nodes}:
                    return True
            except ValueError as error:
                if str(error) not in DETAIL_IDENTITY_ERRORS:
                    raise
                continue
        return False

    def _valid_detail_identity(self, node_id: str, kind: ViewKind,
                               root_id: str | None = None) -> bool:
        """Accept only members of an existing, source-validated bounded island."""
        for island in self.detail_islands(kind):
            try:
                graph = self.detail_view(kind, root_id, island.source_node_id)
                if node_id in {node.id for node in graph.nodes}:
                    return True
            except ValueError as error:
                if str(error) not in DETAIL_IDENTITY_ERRORS:
                    raise
                continue
        return False

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
        if selected_id is not None and selected_id.startswith(("projection:", "project:")):
            if not self.valid_identity(selected_id, active_view,
                                       graph_roots.get(active_view.value) if active_view else None):
                raise ValueError("unknown selected node ID")
            if active_view is ViewKind.ARCHITECTURE and self.analysis is not None:
                root_id = graph_roots.get(active_view.value)
                visible = build_visible_graph(self.analysis, ViewRequest(
                    active_view, root_id=root_id,
                    structure_depth=self.current_structure_depth(active_view, root_id),
                    project_namespace=self.structure_namespace()), self.view_state)
                if (selected_id not in {node.id for node in visible.nodes}
                        and not self._valid_detail_identity(selected_id, active_view, root_id)):
                    raise ValueError("selected node is outside the active graph and detail islands")
        self.view_state = replace(self.view_state, active_view=active_view,
                                  selected_id=selected_id, open_tabs=open_tabs,
                                  graph_roots=dict(graph_roots))
        self._persist_view_state()

    def view(self, kind: ViewKind, root_id: str | None = None,
             selected_id: str | None = None, *, hops: int = 1,
             direction: str = "both", structure_depth: int = 1,
             _remember_depth: bool = True) -> VisibleGraph:
        if self.analysis is None:
            raise RuntimeError("analyze the project before opening a graph")
        request = ViewRequest(kind, root_id=root_id, selected_id=selected_id,
                              hops=hops, direction=direction, structure_depth=structure_depth,
                              project_namespace=self.structure_namespace())
        result = build_visible_graph(self.analysis, request, self.view_state)
        if _remember_depth and kind in (ViewKind.ARCHITECTURE, ViewKind.PROJECT_STRUCTURE):
            self._structure_depths[kind] = (root_id, structure_depth)
        if (kind in (ViewKind.ARCHITECTURE, ViewKind.PROJECT_STRUCTURE) and selected_id is not None
                and selected_id not in {node.id for node in result.nodes}
                and self.view_state.selected_id == selected_id):
            self.view_state = replace(self.view_state, selected_id=None)
            self._persist_view_state()
        self._record_view_issues(result.issues)
        return result

    def _record_view_issues(self, issues: tuple[ViewIssue, ...]) -> None:
        combined = tuple(dict.fromkeys((*self.view_issues, *issues)))
        self.view_issues = combined[:_MAX_VIEW_ISSUES]

    def detail_view(self, kind: ViewKind, root_id: str | None,
                    source_node_id: str) -> VisibleGraph:
        if self.analysis is None:
            raise RuntimeError("analyze the project before opening node details")
        detail_root = root_id
        detail_focus = source_node_id
        if kind in (ViewKind.ARCHITECTURE, ViewKind.PROJECT_STRUCTURE):
            resolver = StructureResolver(self.analysis, self.structure_namespace())
            if kind is ViewKind.PROJECT_STRUCTURE:
                if not resolver.project_valid(source_node_id, root_id):
                    raise ValueError("node has no local detail graph")
                return build_visible_graph(self.analysis, ViewRequest(
                    kind, root_id=source_node_id, max_nodes=18, max_edges=40,
                    structure_depth=1, project_namespace=self.structure_namespace()), ViewState())
            try:
                resolved = resolver.resolve(source_node_id, root_id)
            except ValueError as error:
                raise ValueError("invalid structural detail source") from error
            definition_id = resolver.definition_id(resolved.component)
            local_data = critical_data_slice(
                self.analysis, (definition_id,) if definition_id is not None else (), kind)
            can_expand_structure = len(resolved.path) < 6 and bool(
                resolver.children(resolved.component.id))
            if not can_expand_structure and not local_data.nodes:
                raise ValueError("node has no local detail graph")
            result = build_architecture_structure(self.analysis, ViewRequest(
                kind, root_id=root_id, max_nodes=18, max_edges=40,
                structure_depth=3, project_namespace=self.structure_namespace()),
                ViewState(), source_node_id)
            self._record_view_issues(result.issues)
            return result
        expanded_ids: tuple[str, ...] = (detail_focus,)
        if kind is ViewKind.EXECUTION and self.analysis.semantic is not None:
            query = GraphQuery(self.analysis.semantic.graph)
            if detail_focus in query.components:
                expanded_ids = tuple(query.descendants((detail_focus,)))
        request = ViewRequest(
            kind, root_id=detail_root, selected_id=detail_focus,
            expanded_ids=expanded_ids, hops=1, max_nodes=18, max_edges=40,
            structure_depth=1, project_namespace=self.structure_namespace(),
        )
        detail_state = ViewState(expanded_ids=expanded_ids)
        try:
            result = build_visible_graph(self.analysis, request, detail_state)
        except ValueError as error:
            if str(error) not in _DETAIL_UNAVAILABLE_ERRORS:
                raise
            raise ValueError("node has no local detail graph") from error
        if detail_focus not in {node.id for node in result.nodes}:
            raise ValueError("node has no local detail graph")
        self._record_view_issues(result.issues)
        return result

    def expand(self, node_id: str) -> None:
        if node_id.startswith(("projection:", "project:")) and not self.valid_identity(node_id):
            raise ValueError("unknown node ID")
        if not node_id:
            raise ValueError("node ID cannot be empty")
        self.view_state = replace(self.view_state,
            expanded_ids=tuple(dict.fromkeys((*self.view_state.expanded_ids, node_id))),
            positions=dict(self.view_state.positions),
        )
        self._persist_view_state()

    def collapse(self, node_id: str) -> None:
        if node_id.startswith(("projection:", "project:")) and not self.valid_identity(node_id):
            raise ValueError("unknown node ID")
        self.view_state = replace(self.view_state,
            expanded_ids=tuple(item for item in self.view_state.expanded_ids
                               if item != node_id),
            positions=dict(self.view_state.positions),
        )
        self._persist_view_state()

    def move_node(self, node_id: str, position: tuple[float, float],
                  view_kind: ViewKind | None = None) -> None:
        kind = view_kind or self.view_state.active_view
        if node_id.startswith(("projection:", "project:")) and not self.valid_identity(
                node_id, kind, self.view_state.graph_roots.get(kind.value) if kind else None):
            raise ValueError("unknown node ID")
        if kind is None:
            positions = {**self.view_state.positions, node_id: position}
            self.view_state = replace(self.view_state, positions=positions)
        else:
            positions_by_view = {
                key: dict(value)
                for key, value in self.view_state.positions_by_view.items()
            }
            positions = dict(self.view_state.positions_for(kind))
            positions[node_id] = position
            positions_by_view[kind.value] = positions
            self.view_state = replace(
                self.view_state, positions_by_view=positions_by_view)
        self._persist_view_state()

    def save_viewport(self, kind: ViewKind, zoom: float,
                      pan: tuple[float, float]) -> None:
        viewports = {**self.view_state.viewport_by_view,
                     kind.value: ViewportState(zoom, pan)}
        self.view_state = replace(self.view_state, viewport_by_view=viewports,
                                  positions=dict(self.view_state.positions))
        self._persist_view_state()

    def open_detail_island(self, kind: ViewKind, source_node_id: str,
                           position: tuple[float, float] | None) -> DetailIslandState:
        if source_node_id.startswith(("projection:", "project:")):
            if not self.valid_identity(source_node_id, kind, self.view_state.graph_roots.get(kind.value)):
                raise ValueError("unknown detail source ID")
            self.detail_view(kind, self.view_state.graph_roots.get(kind.value), source_node_id)
        island_id = f"{kind.value}:{source_node_id}"
        for island in self.view_state.detail_islands:
            if island.id == island_id:
                return island
        if len(self.detail_islands(kind)) >= 3:
            raise ValueError("close a detail island before opening another")
        island = DetailIslandState(island_id, kind, source_node_id, position)
        self.view_state = replace(
            self.view_state,
            detail_islands=(*self.view_state.detail_islands, island),
            positions=dict(self.view_state.positions),
            viewport_by_view=dict(self.view_state.viewport_by_view),
        )
        self._persist_view_state()
        return island

    def move_detail_island(self, island_id: str,
                           position: tuple[float, float]) -> None:
        found = False
        islands: list[DetailIslandState] = []
        for island in self.view_state.detail_islands:
            if island.id == island_id:
                island = replace(island, position=position)
                found = True
            islands.append(island)
        if not found:
            raise ValueError("detail island is not open")
        self.view_state = replace(
            self.view_state, detail_islands=tuple(islands),
            positions=dict(self.view_state.positions),
            viewport_by_view=dict(self.view_state.viewport_by_view),
        )
        self._persist_view_state()

    def close_detail_island(self, island_id: str) -> None:
        islands = tuple(island for island in self.view_state.detail_islands
                        if island.id != island_id)
        if len(islands) == len(self.view_state.detail_islands):
            return
        self.view_state = replace(
            self.view_state, detail_islands=islands,
            positions=dict(self.view_state.positions),
            viewport_by_view=dict(self.view_state.viewport_by_view),
        )
        self._persist_view_state()

    def detail_islands(self, kind: ViewKind) -> tuple[DetailIslandState, ...]:
        return tuple(island for island in self.view_state.detail_islands
                     if island.view_kind is kind)

    def reset_positions(self) -> None:
        """Restore automatic positions for this project's graphs only."""
        islands = tuple(replace(island, position=None)
                        for island in self.view_state.detail_islands)
        self.view_state = replace(
            self.view_state, positions={}, positions_by_view={},
            detail_islands=islands,
            viewport_by_view=dict(self.view_state.viewport_by_view),
        )
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
