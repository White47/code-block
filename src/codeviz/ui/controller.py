"""UI-independent project session and safe, read-only source navigation."""

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from stat import S_ISREG
from threading import Lock
from uuid import uuid4

from codeviz.analysis.project import ProjectAnalysis, analyze_project
from codeviz.assistant.snapshot import (
    AnchorRecord,
    CodeNodeRecord,
    SnapshotBinding,
    SymbolRecord,
    build_snapshot,
)
from codeviz.assistant.tool_contracts import EntityRef
from codeviz.core import (
    NodeKind,
    SourceAnchor,
    ViewIssue,
    ViewKind,
    VisibleGraph,
    VisibleNode,
)
from codeviz.persistence.fingerprint import (
    ANALYSIS_VERSION,
    PARSER_VERSION,
    SEMANTIC_VERSION,
    fingerprint_project,
    outdated_reasons,
)
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
from codeviz.views.human_understanding import build_human_understanding
from codeviz.views.query import GraphQuery
from codeviz.views.structure_identity import StructureResolver

from .assistant_context import (
    AssistantContext,
    NavigationFact,
    NodeContext,
    PublicationError,
    PublicationIdentity,
    PublicationMirror,
    ViewNodeKey,
)
from .source_editing import EditableSource, SourceEditingService
from .summary_layout import SummaryGeometry, SummaryLayoutIndex

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
_UNSET_EPOCH = object()


def _same_indexed_definition(symbol: SymbolRecord | None, code: CodeNodeRecord) -> bool:
    """Prove namespace materialization; sharing an ID alone is not sufficient."""
    return (symbol is not None and symbol.definition
            and (symbol.id, symbol.kind, symbol.name, symbol.qualified_name,
                 symbol.scope, symbol.file_path, (symbol.source_anchor,))
            == (code.id, code.kind, code.name, code.qualified_name,
                code.scope, code.file_path, code.source_anchors))


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
                 ignore_paths: tuple[str, ...] = (), *,
                 assistant_publication_mirror: PublicationMirror | None = None) -> None:
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
        self._calls_query: tuple[str | None, int, str] | None = None
        self._source_editing = SourceEditingService()
        self._assistant_context = AssistantContext(
            publication_revision=0, project_epoch=None, analysis_generation=None,
            project_key=None, root=None, ignore_paths=(),
            status=AnalysisStatus.NOT_ANALYZED, eligible=False, snapshot=None,
            navigation_facts={}, context_map={},
        )
        self._assistant_publication_lock = Lock()
        self._assistant_publication_mirror = assistant_publication_mirror

    def capture_assistant_context(self) -> AssistantContext:
        """Capture one detached object without waiting or performing analysis."""
        if not self._assistant_publication_lock.acquire(blocking=False):
            raise PublicationError("AI_ANALYSIS_BUSY")
        try:
            return self._assistant_context
        finally:
            self._assistant_publication_lock.release()

    @property
    def project_epoch(self) -> str | None:
        """For an identity pair, callers must capture one context instead."""
        return self.capture_assistant_context().project_epoch

    @property
    def analysis_generation(self) -> int | None:
        return self.capture_assistant_context().analysis_generation

    def _assistant_replace(
        self, expected: AssistantContext, candidate: AssistantContext, *,
        ordinary: tuple[ProjectAnalysis | None, Path | None,
                        tuple[str, ...], AnalysisStatus] | None = None,
        ineligible_completion: bool = False,
    ) -> bool:
        """Prepare values first; the transaction only checks and swaps references."""
        identity = PublicationIdentity(
            candidate.publication_revision, candidate.project_epoch,
            candidate.analysis_generation, candidate.project_key, candidate.root,
            candidate.ignore_paths, candidate.status, candidate.eligible,
        )
        mirror_failed = False
        mirror = self._assistant_publication_mirror
        replace_publication = getattr(mirror, "_replace_publication", None)
        mirror_called = False
        try:
            with self._assistant_publication_lock:
                if self._assistant_context is not expected:
                    return False
                if ordinary is not None and (
                        self.analysis is not ordinary[0] or self.project_root is not ordinary[1]
                        or self.ignore_paths is not ordinary[2] or self.status is not ordinary[3]):
                    return False
                if mirror is not None:
                    mirror_called = True
                    try:
                        if callable(replace_publication):
                            replace_publication(identity, candidate.snapshot)
                        else:
                            mirror.replace_current(identity)
                    except Exception:
                        mirror_failed = True
                if not mirror_failed or (ineligible_completion and not expected.eligible
                                         and not candidate.eligible):
                    # A prior mirrored invalidation already revoked old eligibility.
                    # On completion failure this newer local revision cannot match
                    # either the old or a partially updated ineligible mirror slot.
                    self._assistant_context = candidate
        finally:
            if mirror_called:
                drain = getattr(mirror, "_drain_pending_closures", None)
                if callable(drain):
                    try:
                        drain()
                    except Exception:
                        mirror_failed = True
        if mirror_failed:
            raise PublicationError() from None
        return True

    def _assistant_invalidate(self, *, status: AnalysisStatus,
                              root: Path | None, ignore_paths: tuple[str, ...],
                              rotate_epoch: bool = False) -> AssistantContext:
        key = os.path.normcase(os.path.normpath(str(root))) if root is not None else None
        epoch = uuid4().hex if rotate_epoch else None
        while True:
            with self._assistant_publication_lock:
                expected = self._assistant_context
            candidate = AssistantContext(
                publication_revision=expected.publication_revision + 1,
                project_epoch=epoch if rotate_epoch else expected.project_epoch,
                analysis_generation=(expected.analysis_generation or 0) + 1,
                project_key=key, root=root, ignore_paths=ignore_paths, status=status,
                eligible=False, snapshot=None, navigation_facts={}, context_map={},
            )
            if self._assistant_replace(expected, candidate):
                return candidate

    def _assistant_complete(self, expected: AssistantContext, *,
                            revision_offset: int = 1,
                            project_epoch: str | None | object = _UNSET_EPOCH) -> bool:
        """Ineligible completion preserves the admitted generation, never an old snapshot."""
        ordinary = (self.analysis, self.project_root, self.ignore_paths, self.status)
        root = ordinary[1]
        restored_epoch: str | None = (expected.project_epoch if project_epoch is _UNSET_EPOCH
                                      else project_epoch if isinstance(project_epoch, str)
                                      or project_epoch is None else None)
        candidate = replace(
            expected, publication_revision=expected.publication_revision + revision_offset,
            project_epoch=restored_epoch,
            root=root, project_key=(os.path.normcase(os.path.normpath(str(root)))
                                    if root is not None else None),
            ignore_paths=ordinary[2], status=ordinary[3], eligible=False,
            snapshot=None, navigation_facts={}, context_map={},
        )
        return self._assistant_replace(expected, candidate, ordinary=ordinary,
                                       ineligible_completion=True)

    def _assistant_finish_accepted_failure(self, expected: AssistantContext, *,
                                           eligible_attempted: bool = False) -> None:
        """A successful ordinary result remains usable when publication fails."""
        try:
            self._assistant_complete(
                expected, revision_offset=2 if eligible_attempted else 1)
        except PublicationError:
            # The prior mirrored invalidation permits _assistant_complete to
            # install its newer ineligible revision locally if this mirror fails.
            pass

    def _assistant_commit(self, expected: AssistantContext,
                          candidate: AssistantContext,
                          accepted_analysis: ProjectAnalysis) -> bool:
        ordinary = (self.analysis, self.project_root, self.ignore_paths, self.status)
        snapshot = candidate.snapshot
        # Potentially long path/tuple equality and all freezing stay outside the lock.
        if (snapshot is None or not candidate.eligible
                or candidate.status not in (AnalysisStatus.READY, AnalysisStatus.PARTIAL)
                or ordinary[0] is not accepted_analysis
                or ordinary[1] != candidate.root or ordinary[2] != candidate.ignore_paths
                or ordinary[3] is not candidate.status
                or candidate.publication_revision != expected.publication_revision
                or candidate.project_epoch != expected.project_epoch
                or candidate.analysis_generation != expected.analysis_generation
                or candidate.project_key != expected.project_key
                or candidate.root != expected.root or candidate.ignore_paths != expected.ignore_paths
                or snapshot.binding.project_root != str(candidate.root)
                or snapshot.binding.ignore_paths != candidate.ignore_paths
                or snapshot.binding.project_epoch != candidate.project_epoch
                or snapshot.binding.analysis_generation != candidate.analysis_generation):
            return False
        published = replace(candidate, publication_revision=expected.publication_revision + 1)
        return self._assistant_replace(expected, published, ordinary=ordinary)

    def _assistant_publish_accepted(self, expected: AssistantContext,
                                    accepted_analysis: ProjectAnalysis) -> bool:
        if (self.analysis is not accepted_analysis or self.project_root != expected.root
                or self.ignore_paths != expected.ignore_paths
                or self.status not in (AnalysisStatus.READY, AnalysisStatus.PARTIAL)
                or expected.root is None or expected.project_epoch is None
                or expected.analysis_generation is None):
            return False
        try:
            # B3 uses actual parsed raw hashes, including AST-free validated B2 caches.
            if any(file.was_parsed and file.content_hash is None
                   for file in accepted_analysis.files):
                raise PublicationError()
            snapshot = build_snapshot(accepted_analysis, binding=SnapshotBinding(
                project_root=str(expected.root), ignore_paths=expected.ignore_paths,
                project_epoch=expected.project_epoch,
                analysis_generation=expected.analysis_generation,
            ))
            facts: dict[EntityRef, NavigationFact] = {}
            for symbol in snapshot.symbol_inventory:
                if len(symbol.id) <= 512:
                    facts[EntityRef("symbol", symbol.id)] = NavigationFact(
                        (symbol.source_anchor,), ())
            for node in snapshot.code_node_inventory:
                if len(node.id) <= 512:
                    facts[EntityRef("code", node.id)] = NavigationFact(node.source_anchors, ())
            for component in snapshot.component_inventory:
                if len(component.id) <= 512:
                    facts[EntityRef("semantic", component.id)] = NavigationFact(
                        component.source_anchors, ())
            candidate = replace(expected, status=self.status, eligible=True, snapshot=snapshot,
                                navigation_facts=facts, context_map={})
        except Exception:
            # Snapshot/projection failure disables only assistant publication.
            self._assistant_finish_accepted_failure(expected)
            return False
        try:
            return self._assistant_commit(expected, candidate, accepted_analysis)
        except PublicationError:
            self._assistant_finish_accepted_failure(expected, eligible_attempted=True)
            return False

    def _assistant_publish_view(self, expected: AssistantContext, graph: VisibleGraph,
                                kind: ViewKind, root_id: str | None,
                                detail_source_id: str | None = None) -> bool:
        """Record only provenance from a graph successfully delivered by the bridge."""
        if self.capture_assistant_context() is not expected:
            return False
        snapshot = expected.snapshot
        analysis = self.analysis
        if (not expected.eligible or snapshot is None or analysis is None
                or expected.root is None or self.project_root != expected.root
                or self.ignore_paths != expected.ignore_paths
                or self.status is not expected.status or graph.view_kind is not kind
                or snapshot.binding.project_epoch != expected.project_epoch
                or snapshot.binding.analysis_generation != expected.analysis_generation
                or snapshot.binding.project_root != str(expected.root)
                or snapshot.binding.ignore_paths != expected.ignore_paths):
            return False
        if (any(value is not None and (not value or len(value) > 512)
                for value in (root_id, detail_source_id))):
            return False

        symbols = {record.id: record for record in snapshot.symbol_inventory}
        codes = {record.id: record for record in snapshot.code_node_inventory}
        components = {record.id: record for record in snapshot.component_inventory}
        resolver = (StructureResolver(analysis, self.structure_namespace())
                    if kind is ViewKind.ARCHITECTURE else None)
        instances = ({item.id: item for item in analysis.model.instances}
                     if resolver is not None and analysis.model is not None else {})
        project_resolver = (StructureResolver(analysis, self.structure_namespace())
                            if kind is ViewKind.PROJECT_STRUCTURE else None)
        project_id = project_resolver.project_id if project_resolver is not None else None
        project_parents = (project_resolver.project_entries()
                           if project_resolver is not None else {})
        aggregation_members: dict[str, tuple[str, ...] | None] = {}
        if kind is not ViewKind.PROJECT_STRUCTURE and len(graph.aggregations) <= 100:
            for aggregation in graph.aggregations:
                if aggregation.id in aggregation_members:
                    aggregation_members[aggregation.id] = None
                else:
                    aggregation_members[aggregation.id] = aggregation.member_ids

        def project_within_root(node_id: str, requested_root: str | None) -> bool:
            if requested_root is None:
                return False
            current: str | None = node_id
            for _ in range(6):  # Project Structure displays at most five child levels.
                if current == requested_root:
                    return True
                current = project_parents.get(current) if current is not None else None
            return False

        def bounded_node(visible: VisibleNode) -> tuple[ViewNodeKey, NodeContext] | None:
            # The graph's provenance fields are the only mutable-view inputs read.
            if not isinstance(visible.id, str) or not 0 < len(visible.id) <= 512:
                return None
            key = ViewNodeKey(kind, root_id, detail_source_id, visible.id)
            candidates: list[EntityRef] = []
            rejected_ids = False
            if kind is ViewKind.PROJECT_STRUCTURE:
                project_root_id = root_id or project_id
                if (detail_source_id is not None
                        and project_within_root(detail_source_id, project_root_id)
                        and graph.root_ids == (detail_source_id,)):
                    project_root_id = detail_source_id
                project_direct = (not visible.member_ids
                                  and (graph.root_ids == (project_root_id,)
                                       or visible.id == project_root_id)
                                  and visible.id in project_parents
                                  and project_within_root(visible.id, project_root_id))
                project_members = (visible.id == project_id
                                   and root_id in (None, project_id)
                                   and graph.root_ids == (project_id,)
                                   and bool(visible.member_ids)
                                   and len(visible.code_node_ids) == len(visible.member_ids)
                                   and visible.code_node_ids[:21] == visible.member_ids[:21])
                if visible.id == project_id and visible.member_ids and not project_members:
                    rejected_ids = True
                for item in visible.code_node_ids[:21]:
                    if (isinstance(item, str) and len(item) <= 512
                            and item in symbols
                            and ((project_members and symbols[item].definition
                                  and symbols[item].kind is NodeKind.MODULE
                                  and project_parents.get(item) == project_id)
                                 or (project_direct and item == visible.id
                                     and symbols[item].definition
                                     and symbols[item].kind in
                                     (NodeKind.MODULE, NodeKind.CLASS,
                                      NodeKind.FUNCTION, NodeKind.METHOD)))):
                        candidates.append(EntityRef("symbol", item))
                    else:
                        rejected_ids = True
                if visible.member_ids and not project_members:
                    rejected_ids = True
            else:
                aggregate_members = aggregation_members.get(visible.id)
                members_authorized = (bool(visible.member_ids)
                                      and not visible.code_node_ids
                                      and not visible.semantic_component_ids
                                      and aggregate_members is not None
                                      and len(aggregate_members) == len(visible.member_ids)
                                      and aggregate_members[:21] == visible.member_ids[:21])
                semantic_ids = visible.semantic_component_ids[:21]
                execution_component = (components.get(visible.id)
                                       if kind is ViewKind.EXECUTION else None)
                resolved = None
                if resolver is not None and semantic_ids:
                    try:
                        resolved = resolver.resolve(visible.id, root_id)
                    except ValueError:
                        pass
                resolved_component = (components.get(resolved.component.id)
                                      if resolved is not None else None)
                primary_matches = (resolved_component is not None and bool(semantic_ids)
                                   and semantic_ids[0] == resolved_component.id)
                architecture_occurrence_valid = (resolver is None
                                                 or not visible.semantic_component_ids
                                                 or primary_matches)
                architecture_code_ids: tuple[str, ...] = ()
                if resolver is not None and resolved_component is not None and primary_matches:
                    architecture_code_ids = resolved_component.code_symbol_ids
                    if not architecture_code_ids:
                        instance = instances.get(resolved_component.id)
                        if instance is not None:
                            architecture_code_ids = (instance.owner_definition_id,)
                detail_definition_id = None
                if (resolver is not None and detail_source_id is not None
                        and visible.id == detail_source_id and resolved is not None
                        and semantic_ids and semantic_ids[0] == resolved.component.id):
                    definition_id = resolver.definition_id(resolved.component)
                    if (definition_id is not None and definition_id != semantic_ids[0]
                            and definition_id in components
                            and components[definition_id].role == "ModelDefinition"):
                        detail_definition_id = definition_id
                for index, item in enumerate(semantic_ids):
                    if (isinstance(item, str) and len(item) <= 512
                            and item in components
                            and ((kind is ViewKind.EXECUTION
                                  and index == 0 and execution_component is not None
                                  and item == execution_component.id)
                                 or (kind is not ViewKind.EXECUTION
                                     and (resolver is None or (resolved is not None and (
                                         (index == 0 and item == resolved.component.id)
                                         or (index == 1 and item == detail_definition_id))))))):
                        candidates.append(EntityRef("semantic", item))
                    else:
                        rejected_ids = True
                for item in visible.code_node_ids[:21]:
                    if (isinstance(item, str) and len(item) <= 512
                            and item in codes and architecture_occurrence_valid
                            and (kind in (ViewKind.ARCHITECTURE, ViewKind.EXECUTION)
                                 or item == visible.id)
                            and (resolver is None or item in architecture_code_ids
                                 or (not visible.semantic_component_ids
                                     and item == visible.id
                                     and codes[item].kind is NodeKind.DATA))
                            and (kind is not ViewKind.EXECUTION
                                 or (execution_component is not None
                                     and item in execution_component.code_symbol_ids)
                                 or (not visible.semantic_component_ids
                                     and item == visible.id
                                     and codes[item].kind is NodeKind.DATA))):
                        if (kind is ViewKind.SYMBOL and detail_source_id is None
                                and visible.code_node_ids == (item,)
                                and not visible.member_ids and not visible.semantic_component_ids
                                and _same_indexed_definition(symbols.get(item), codes[item])):
                            candidates.append(EntityRef("symbol", item))
                        else:
                            candidates.append(EntityRef("code", item))
                    else:
                        rejected_ids = True
                for item in visible.member_ids[:21]:
                    if not members_authorized:
                        rejected_ids = True
                        break
                    if not isinstance(item, str) or len(item) > 512:
                        rejected_ids = True
                        continue
                    if not architecture_occurrence_valid:
                        rejected_ids = True
                        continue
                    if resolver is not None:
                        try:
                            component_id = resolver.resolve(item, root_id).component.id
                        except ValueError:
                            component_id = None
                        if component_id in components:
                            candidates.append(EntityRef("semantic", component_id))
                            continue
                    elif kind is ViewKind.EXECUTION and item in components:
                        candidates.append(EntityRef("semantic", item))
                        continue
                    if resolver is None and item in codes:
                        candidates.append(EntityRef("code", item))
                    else:
                        rejected_ids = True
            targets = tuple(dict.fromkeys(candidates))[:20]
            target_anchors: set[AnchorRecord] = set()
            for target in targets:
                if target.kind == "symbol":
                    target_anchors.add(symbols[target.id].source_anchor)
                elif target.kind == "code":
                    target_anchors.update(codes[target.id].source_anchors)
                elif target.kind == "semantic":
                    target_anchors.update(components[target.id].source_anchors)
            if targets:
                authorized_anchors = target_anchors
            elif kind is ViewKind.PROJECT_STRUCTURE:
                symbol = symbols.get(visible.id) if project_direct else None
                authorized_anchors = ({symbol.source_anchor} if symbol is not None
                                      and symbol.definition and symbol.kind in
                                      (NodeKind.MODULE, NodeKind.CLASS, NodeKind.FUNCTION,
                                       NodeKind.METHOD) else set())
            elif resolver is not None:
                if resolved is None:
                    try:
                        resolved = resolver.resolve(visible.id, root_id)
                    except ValueError:
                        pass
                component = (components.get(resolved.component.id)
                             if resolved is not None else None)
                authorized_anchors = (set(component.source_anchors)
                                      if component is not None else set())
                code = codes.get(visible.id)
                if code is not None and code.kind is NodeKind.DATA:
                    authorized_anchors.update(code.source_anchors)
            else:
                code = codes.get(visible.id)
                if kind is ViewKind.EXECUTION:
                    authorized_anchors = (set(execution_component.source_anchors)
                                          if execution_component is not None else set())
                    if (code is not None and code.kind is NodeKind.DATA
                            and not visible.semantic_component_ids):
                        authorized_anchors.update(code.source_anchors)
                else:
                    authorized_anchors = (set(code.source_anchors)
                                          if code is not None else set())
            anchors: list[AnchorRecord] = []
            for source in visible.source_anchors[:21]:
                if not isinstance(source, SourceAnchor):
                    continue
                record = AnchorRecord(source.file_path, source.start_line,
                                      source.start_column, source.end_line or source.start_line,
                                      source.end_column)
                if record in authorized_anchors and record not in anchors:
                    anchors.append(record)
            source_only = not targets
            context = NodeContext(
                targets, tuple(anchors[:20]), source_only,
                len(set(candidates)) > 20 or len(visible.semantic_component_ids) > 20
                or len(visible.code_node_ids) > 20 or len(visible.member_ids) > 20
                or len(visible.source_anchors) > 20 or rejected_ids
                or len(visible.source_anchors) > len(anchors),
                "unmapped_visible_identity" if source_only else None,
            )
            return key, context

        # The latest delivered bucket takes precedence; stale roots and closed
        # islands are pruned on this delivery, without consulting browser state.
        open_sources = {(island.view_kind, island.source_node_id)
                        for island in self.view_state.detail_islands}
        latest_orphan = {key.view_kind: key.detail_source_id
                         for key in expected.context_map
                         if key.detail_source_id is not None
                         and (key.view_kind, key.detail_source_id) not in open_sources}
        prior = sorted(
            ((key, value) for key, value in expected.context_map.items()
             if (key.view_kind is not kind or key.detail_source_id is not None)
             and (key.view_kind, key.detail_source_id) != (kind, detail_source_id)
             and (key.detail_source_id is None
                  or (key.view_kind, key.detail_source_id) in open_sources
                  or (key.detail_source_id == latest_orphan.get(key.view_kind)
                      and (detail_source_id is None or key.view_kind is not kind)))),
            key=lambda pair: (pair[0].view_kind.value, pair[0].root_id or "",
                              pair[0].detail_source_id or "", pair[0].visible_node_id),
        )

        projected: dict[ViewNodeKey, NodeContext] = {}
        remaining = 256 * 1024

        def add(key: ViewNodeKey, value: NodeContext) -> None:
            nonlocal remaining
            if len(projected) >= 100:
                return
            # Conservative UTF-8 accounting includes duplicate navigation keys.
            size = 256 + 2 * sum(len(item.encode("utf-8")) for item in (
                key.view_kind.value, key.root_id or "", key.detail_source_id or "",
                key.visible_node_id))
            size += sum(64 + len(target.id.encode("utf-8")) for target in value.targets)
            size += sum(80 + len(anchor.file_path.encode("utf-8"))
                        for anchor in value.source_anchors)
            if size <= remaining:
                projected[key] = value
                remaining -= size

        for node in graph.nodes[:101]:
            item = bounded_node(node)
            if item is not None:
                add(*item)
        for key, value in prior:
            add(key, value)

        locations: dict[EntityRef, list[ViewNodeKey]] = {}
        for key, value in projected.items():
            for target in value.targets:
                locations.setdefault(target, []).append(key)
        facts = {target: NavigationFact(fact.source_anchors,
                                        tuple(locations.get(target, ())))
                 for target, fact in expected.navigation_facts.items()}
        if projected == expected.context_map and facts == expected.navigation_facts:
            return False
        candidate = replace(expected, publication_revision=expected.publication_revision + 1,
                            context_map=projected, navigation_facts=facts)
        return self._assistant_replace(
            expected, candidate, ordinary=(analysis, self.project_root,
                                           self.ignore_paths, self.status))

    def open_project(self, project_root: Path, *,
                     ignore_paths: tuple[str, ...] | None = None,
                     _defer_assistant_commit: bool = False) -> None:
        if self.status == AnalysisStatus.ANALYZING:
            raise RuntimeError("cannot switch project during analysis")
        root = Path(project_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("project_root must be an existing directory")
        if ignore_paths is not None and type(ignore_paths) is not tuple:
            raise ValueError("ignore_paths must be a tuple")
        previous_ignores = self.ignore_paths
        previous_epoch = self.capture_assistant_context().project_epoch
        selected_ignores = previous_ignores if ignore_paths is None else ignore_paths
        expected = self._assistant_invalidate(
            status=AnalysisStatus.NOT_ANALYZED, root=root,
            ignore_paths=selected_ignores, rotate_epoch=True)
        self.ignore_paths = selected_ignores
        try:
            self._source_editing.bind_project(root, expected.project_epoch)
        except Exception:
            self.ignore_paths = previous_ignores
            try:
                self._assistant_complete(expected, project_epoch=previous_epoch)
            except PublicationError:
                # The prior mirrored invalidation revoked the old result; the
                # ineligible completion is installed locally even on mirror failure.
                pass
            raise
        try:
            self._open_validated_project(root, expected,
                                         defer_assistant_commit=_defer_assistant_commit)
        except Exception:
            # Once source binding succeeds, ordinary cache/view data belongs to
            # the new project even if later post-processing fails.
            try:
                self._assistant_complete(expected)
            except PublicationError:
                pass
            raise

    def _abandon_project_switch(self) -> None:
        """Finish an already-revoked bridge open after Explorer binding fails."""
        with self._assistant_publication_lock:
            expected = self._assistant_context
        if expected.eligible:
            raise PublicationError()
        self._source_editing.unbind_project()
        self.project_root = None
        self.ignore_paths = ()
        self.analysis = None
        self.status = AnalysisStatus.NOT_ANALYZED
        self.view_state = ViewState()
        self.outdated_reasons = ()
        self.cache_issue = None
        self.view_issues = ()
        self._structure_depths = {}
        self._calls_query = None
        try:
            self._assistant_complete(expected, project_epoch=None)
        except PublicationError:
            # The earlier mirrored ineligible identity is already a barrier;
            # this newer ineligible completion is installed locally as well.
            pass

    def _open_validated_project(self, root: Path, expected: AssistantContext, *,
                                defer_assistant_commit: bool = False) -> None:
        self.project_root = root
        self.analysis = None
        self.status = AnalysisStatus.NOT_ANALYZED
        self.view_state = ViewState()
        self.outdated_reasons = ()
        self.cache_issue = None
        self.view_issues = ()
        self._structure_depths = {}
        self._calls_query = None
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
        if not defer_assistant_commit:
            self._assistant_finalize_project_open(expected)

    def _assistant_finalize_project_open(self, expected: AssistantContext) -> None:
        """Admit a cached result only after all project services are bound."""
        if self.analysis is not None and self.status in (AnalysisStatus.READY, AnalysisStatus.PARTIAL):
            self._assistant_publish_accepted(expected, self.analysis)
        else:
            try:
                self._assistant_complete(expected)
            except PublicationError:
                # The mirrored open invalidation already revoked old eligibility.
                # An ineligible completion is also installed locally on failure.
                pass

    def _activate_saved_analysis_settings(self, ignored: tuple[str, ...],
                                          expected: AssistantContext) -> None:
        """Adopt a committed policy without rebinding the current project."""
        self.ignore_paths = ignored
        if self.analysis is not None:
            self.status = AnalysisStatus.OUTDATED
            self.outdated_reasons = tuple(dict.fromkeys(
                (*self.outdated_reasons, "analysis config changed")))
        else:
            self.status = AnalysisStatus.NOT_ANALYZED
        try:
            self._assistant_complete(expected)
        except PublicationError:
            # The settings write followed a successful mirrored invalidation.
            pass

    def open_source_document(self, project_path: str, anchor: SourceAnchor) -> EditableSource:
        return self._source_editing.open(project_path, anchor.file_path)

    def save_source_document(self, project_path: str, file_path: str,
                             text: str, revision: str) -> EditableSource:
        if self.status == AnalysisStatus.ANALYZING:
            raise RuntimeError("source cannot change during analysis")
        document = self._source_editing.save(
            project_path, file_path, text, revision,
            before_write=self._assistant_invalidate_for_file_mutation)
        if self.analysis is not None:
            self.status = AnalysisStatus.OUTDATED
            self.outdated_reasons = tuple(dict.fromkeys(
                (*self.outdated_reasons, "source was saved; reanalysis required")))
        return document

    def _assistant_invalidate_for_file_mutation(self) -> None:
        self._assistant_invalidate(
            status=(AnalysisStatus.OUTDATED if self.analysis is not None
                    else AnalysisStatus.NOT_ANALYZED),
            root=self.project_root, ignore_paths=self.ignore_paths)

    @staticmethod
    def _result_status(result: ProjectAnalysis) -> AnalysisStatus:
        if (any(not file.was_parsed for file in result.files) or
                any(issue.code.endswith(("_ERROR", "_PARTIAL"))
                    or issue.code == "SCOPE_DECLARATION_CONFLICT" for issue in result.issues)):
            return AnalysisStatus.PARTIAL
        return AnalysisStatus.READY

    def analyze(self, *, before_analysis: Callable[[], None] | None = None) -> ProjectAnalysis:
        if self.project_root is None:
            raise RuntimeError("open a project before analyzing")
        if self.status == AnalysisStatus.ANALYZING:
            raise RuntimeError("analysis is already running")
        old_analysis = self.analysis
        self._calls_query = None
        expected = self._assistant_invalidate(
            status=AnalysisStatus.ANALYZING, root=self.project_root, ignore_paths=self.ignore_paths)
        self.status = AnalysisStatus.ANALYZING
        before = None
        try:
            if before_analysis is not None:
                before_analysis()
            if self.cache_store is not None:
                try:
                    before = fingerprint_project(self.project_root, self.ignore_paths)
                except (OSError, ValueError) as error:
                    self.cache_issue = f"Source fingerprint: {error}"
            result = analyze_project(self.project_root, self.ignore_paths)
        except Exception:
            self.analysis = old_analysis
            self.status = AnalysisStatus.FAILED
            self._assistant_complete(expected)
            raise
        self.analysis = result
        try:
            self.sanitize_view_state()
            self.view_issues = ()
            self.status = self._result_status(result)
            self.outdated_reasons = ()
        except Exception:
            self.analysis = old_analysis
            self.status = AnalysisStatus.FAILED
            self._assistant_complete(expected)
            raise
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
        if self.status in (AnalysisStatus.READY, AnalysisStatus.PARTIAL):
            self._assistant_publish_accepted(expected, result)
        else:
            self._assistant_complete(expected)
        return result

    def sanitize_view_state(self) -> None:
        """Clean stale identities while retaining tabs, positions and annotations."""
        if self.analysis is None:
            return
        state = self.view_state
        summary_index = (self._summary_layout_index()
                         if any(node.startswith("summary:v1:") for positions in
                                state.positions_by_view.values() for node in positions)
                         else None)
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
                                      if self.valid_identity(node, ViewKind(kind))
                                      or (kind == ViewKind.OVERVIEW.value
                                          and summary_index is not None
                                          and summary_index.valid_position_key(node))}
                               for kind, positions in state.positions_by_view.items()},
        )
        if self.view_state != state:
            self._persist_view_state()

    def structure_namespace(self) -> str:
        return (sha256(os.path.normcase(str(self.project_root.resolve())).encode()).hexdigest()[:24]
                if self.project_root is not None else "")

    def _summary_layout_index(self) -> SummaryLayoutIndex:
        if self.analysis is None or self.project_root is None:
            raise ValueError("analyze a project before opening a summary layout")
        understanding = build_human_understanding(self.analysis)
        contexts: list[SummaryGeometry] = []
        if understanding.architecture is not None:
            architecture = understanding.architecture
            contexts.append(SummaryGeometry(
                "architecture", architecture.id,
                tuple(node.id for node in architecture.areas),
                json.dumps(asdict(architecture), sort_keys=True, ensure_ascii=False)))
        for flow in understanding.flows:
            contexts.append(SummaryGeometry(
                "task_flow", flow.id, tuple(node.id for node in flow.steps),
                json.dumps(asdict(flow), sort_keys=True, ensure_ascii=False)))
        source_revision = json.dumps((ANALYSIS_VERSION, PARSER_VERSION, SEMANTIC_VERSION,
                                     self.ignore_paths, [
            (item.relative_path, item.content_hash) for item in self.analysis.files
        ]), sort_keys=True)
        return SummaryLayoutIndex(self.structure_namespace(), source_revision, tuple(contexts))

    def get_summary_layout(self, context: object) -> dict[str, object]:
        return self._summary_layout_index().payload(self.view_state, context)

    def move_summary_node(self, context: object, token: object, node_id: object,
                          position: object) -> dict[str, object]:
        index = self._summary_layout_index()
        self.view_state = index.move(self.view_state, context, token, node_id, position)
        self._persist_view_state()
        return index.payload(self.view_state, context)

    def save_summary_viewport(self, context: object, token: object,
                              viewport: object) -> dict[str, object]:
        index = self._summary_layout_index()
        self.view_state = index.save_viewport(self.view_state, context, token, viewport)
        self._persist_view_state()
        return index.payload(self.view_state, context)

    def current_structure_depth(self, kind: ViewKind, root_id: str | None) -> int:
        context = self._structure_depths.get(kind)
        return context[1] if context is not None and context[0] == root_id else 1

    def valid_identity(self, node_id: str, kind: ViewKind | None = None,
                       root_id: str | None = None) -> bool:
        if self.analysis is None:
            return False
        if kind is ViewKind.CALLS:
            try:
                graph = self.view(kind, root_id, _remember_depth=False)
                return node_id in {item.id for item in graph.nodes}
            except ValueError:
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
        if active_view is ViewKind.CALLS:
            root = graph_roots.get(ViewKind.CALLS.value)
            self.view(ViewKind.CALLS, root, selected_id, _remember_depth=False)
        elif graph_roots.get(ViewKind.CALLS.value) is not None:
            self.view(ViewKind.CALLS, graph_roots[ViewKind.CALLS.value], _remember_depth=False)
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
             selected_id: str | None = None, *, hops: int | None = None,
             direction: str | None = None, structure_depth: int = 1,
             _remember_depth: bool = True) -> VisibleGraph:
        if self.analysis is None:
            raise RuntimeError("analyze the project before opening a graph")
        prior_calls = self._calls_query
        if kind is ViewKind.CALLS and prior_calls is not None and prior_calls[0] == root_id:
            effective_hops = prior_calls[1] if hops is None else hops
            effective_direction = prior_calls[2] if direction is None else direction
        else:
            effective_hops = 1 if hops is None else hops
            effective_direction = "both" if direction is None else direction
        request = ViewRequest(kind, root_id=root_id, selected_id=selected_id,
                              hops=effective_hops, direction=effective_direction,
                              structure_depth=structure_depth,
                              project_namespace=self.structure_namespace())
        result = build_visible_graph(self.analysis, request, self.view_state)
        if kind is ViewKind.CALLS and _remember_depth:
            self._calls_query = (root_id, effective_hops, effective_direction)
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
        detail_root = source_node_id if kind is ViewKind.CALLS else root_id
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
