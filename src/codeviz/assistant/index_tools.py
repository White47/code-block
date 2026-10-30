"""Pure authorized candidate metadata; wire hash/secret gates belong to C."""

from collections.abc import Callable
from typing import TypeVar

from codeviz.core import Confidence

from .contracts import AgentError
from .snapshot import (
    AnalysisSnapshot,
    AnchorRecord,
    CodeEdgeRecord,
    CodeNodeRecord,
    ComponentRecord,
    RelationRecord,
    SymbolRecord,
)
from .source_policy import (
    SourceConsent,
    SourceExcerptError,
    is_protected_source_path,
    validate_source_path,
)
from .tool_contracts import (
    EntityRef,
    LocalNode,
    LocalRelation,
    LocalRelationsResult,
    RelationKey,
    RelationsRequest,
    SearchRequest,
    SymbolHit,
    SymbolSearchResult,
)

_ROLE_ALIASES = {
    "BackwardPass": ("反向传播", "backward", "backward pass", "backpropagation"),
    "ParameterUpdate": ("参数更新", "parameter update", "optimizer step", "update parameters"),
    "ForwardPass": ("前向传播", "forward pass"),
    "LossCalculation": ("损失计算", "loss calculation"),
    "ClearGradients": ("清空梯度", "zero grad"),
    "EpochLoop": ("轮次循环", "epoch loop"),
    "BatchLoop": ("批次循环", "batch loop"),
}


class _Scan:
    def __init__(self, checkpoint: Callable[[], None]) -> None:
        if not callable(checkpoint):
            raise AgentError()
        self.checkpoint = checkpoint
        self.count = 0
        checkpoint()

    def step(self) -> None:
        self.count += 1
        if self.count % 64 == 0:
            self.checkpoint()


class _Authorization:
    """Shared lexical authorization; no source reads or freshness assertions."""

    def __init__(self, snapshot: AnalysisSnapshot, consent: SourceConsent, scan: _Scan) -> None:
        if type(snapshot) is not AnalysisSnapshot or type(consent) is not SourceConsent:
            raise AgentError()
        consent.__post_init__()
        if consent.binding != snapshot.binding:
            raise SourceExcerptError("scope_mismatch")
        self.paths: set[str] = set()
        allowed = set(consent.allowed_files)
        for file in snapshot.file_inventory:
            scan.step()
            if (file.relative_path not in allowed or not file.was_parsed
                    or file.content_hash is None):
                continue
            try:
                validate_source_path(file.relative_path)
                if is_protected_source_path(file.relative_path):
                    continue
            except SourceExcerptError:
                continue
            if any(file.relative_path == ignore or file.relative_path.startswith(ignore + "/")
                   for ignore in snapshot.binding.ignore_paths):
                continue
            self.paths.add(file.relative_path)
        self.symbols: dict[str, SymbolRecord] = {}
        self.codes: dict[str, CodeNodeRecord] = {}
        self.components: dict[str, ComponentRecord] = {}
        for symbol in snapshot.symbol_inventory:
            scan.step()
            self.symbols[symbol.id] = symbol
        for code in snapshot.code_node_inventory:
            scan.step()
            self.codes[code.id] = code
        for component in snapshot.component_inventory:
            scan.step()
            self.components[component.id] = component
        self.scan = scan

    def anchors(self, anchors: tuple[AnchorRecord, ...]) -> bool:
        if not anchors:
            return False
        for anchor in anchors:
            self.scan.step()
            if anchor.file_path not in self.paths:
                return False
        return True

    def symbol(self, symbol: SymbolRecord) -> bool:
        return symbol.file_path in self.paths and self.anchors((symbol.source_anchor,))

    def code(self, code: CodeNodeRecord) -> bool:
        return ((code.file_path is None or code.file_path in self.paths)
                and self.anchors(code.source_anchors))

    def component(self, component: ComponentRecord) -> bool:
        if not self.anchors(component.source_anchors):
            return False
        for identifier in component.code_symbol_ids:
            self.scan.step()
            symbol = self.symbols.get(identifier)
            code = self.codes.get(identifier)
            if symbol is None and code is None:
                return False
            if symbol is not None and not self.symbol(symbol):
                return False
            if code is not None and not self.code(code):
                return False
        return True


def _rank_terms(query: str, terms: tuple[str, ...]) -> int | None:
    values = tuple(term.casefold() for term in terms)
    if any(value == query for value in values):
        return 0
    if any(value.startswith(query) for value in values):
        return 1
    if any(query in value for value in values):
        return 2
    return None


def _anchor_order(anchor: AnchorRecord) -> tuple[str, int, int, int, int]:
    return (anchor.file_path, anchor.start_line, anchor.start_column or 0,
            anchor.end_line, anchor.end_column or 0)


def _hit_order(hit: SymbolHit) -> tuple[object, ...]:
    a = hit.anchor
    return (hit.rank, hit.ref.kind != "symbol", hit.qualified_name.casefold(),
            a.file_path, a.start_line, hit.ref.id, hit.qualified_name,
            _anchor_order(a), a.start_column is not None, a.end_column is not None)


_T = TypeVar("_T")


def _retain(items: list[_T], item: _T, limit: int,
            order: Callable[[_T], tuple[object, ...]]) -> None:
    items.append(item)
    items.sort(key=order)
    if len(items) > limit:
        items.pop()


def search_symbols(snapshot: AnalysisSnapshot, consent: SourceConsent,
                   request: SearchRequest, *, checkpoint: Callable[[], None]) -> SymbolSearchResult:
    if type(request) is not SearchRequest:
        raise AgentError()
    request.__post_init__()
    scan = _Scan(checkpoint)
    auth = _Authorization(snapshot, consent, scan)
    needle = request.query.strip().casefold()
    hits: list[SymbolHit] = []
    total = 0
    for symbol in snapshot.symbol_inventory:
        scan.step()
        if not symbol.definition or not auth.symbol(symbol):
            continue
        rank = _rank_terms(needle, (symbol.name, symbol.qualified_name, symbol.file_path)) if needle else 0
        if rank is None:
            continue
        total += 1
        hit = SymbolHit(EntityRef("symbol", symbol.id), symbol.name, symbol.qualified_name,
                        symbol.kind.value, symbol.scope, symbol.source_anchor, Confidence.EXACT, rank)
        _retain(hits, hit, request.limit, _hit_order)
    if needle:
        for component in snapshot.component_inventory:
            scan.step()
            if not auth.component(component):
                continue
            rank = _rank_terms(needle, (component.display_name, component.code_name or "",
                                       component.role or "", *_ROLE_ALIASES.get(component.role or "", ())))
            if rank is None:
                continue
            total += 1
            hit = SymbolHit(EntityRef("semantic", component.id), component.display_name,
                            component.code_name or component.display_name,
                            component.role or "SemanticComponent", "semantic",
                            min(component.source_anchors,
                                key=lambda a: (*_anchor_order(a), a.start_column is not None,
                                               a.end_column is not None)), component.confidence, rank)
            _retain(hits, hit, request.limit, _hit_order)
    checkpoint()
    return SymbolSearchResult(tuple(hits), total, total > len(hits))


def get_local_relations(snapshot: AnalysisSnapshot, consent: SourceConsent,
                        request: RelationsRequest, *,
                        checkpoint: Callable[[], None]) -> LocalRelationsResult:
    if type(request) is not RelationsRequest:
        raise AgentError()
    request.__post_init__()
    scan = _Scan(checkpoint)
    auth = _Authorization(snapshot, consent, scan)
    kind = request.seed.kind
    if kind == "symbol":
        raise AgentError()

    def node(identifier: str) -> CodeNodeRecord | ComponentRecord | None:
        if kind == "code":
            code = auth.codes.get(identifier)
            return code if code is not None and auth.code(code) else None
        component = auth.components.get(identifier)
        return component if component is not None and auth.component(component) else None

    def incident(identifier: str) -> tuple[CodeEdgeRecord, ...] | tuple[RelationRecord, ...]:
        if kind == "code":
            return snapshot.incident_code_edges(identifier)
        return snapshot.incident_relations(identifier)

    def anchors(edge: CodeEdgeRecord | RelationRecord) -> tuple[AnchorRecord, ...]:
        if isinstance(edge, CodeEdgeRecord):
            return () if edge.source_anchor is None else (edge.source_anchor,)
        return edge.source_anchors

    def authorized(edge: CodeEdgeRecord | RelationRecord) -> bool:
        return (auth.anchors(anchors(edge)) and node(edge.source) is not None
                and node(edge.target) is not None)

    seed = node(request.seed.id)
    if seed is None:
        checkpoint()
        return LocalRelationsResult("unavailable", (), (), False)
    distances = {seed.id: 0}
    records: dict[str, CodeNodeRecord | ComponentRecord] = {seed.id: seed}
    frontier = [seed.id]
    truncated = False
    for depth in range(1, request.hops + 1):
        slots = request.max_nodes - len(distances)
        candidates: list[str] = []
        for identifier in sorted(frontier):
            # Complete scan with streaming ID top-k makes collection order irrelevant.
            for edge in incident(identifier):
                scan.step()
                if not authorized(edge):
                    continue
                other = edge.target if edge.source == identifier else edge.source
                if other in distances or other in candidates:
                    continue
                candidates.append(other)
                candidates.sort()
                if len(candidates) > slots:
                    candidates.pop()
                    truncated = True
        frontier = candidates
        for identifier in frontier:
            record = node(identifier)
            if record is None:
                raise AgentError()
            distances[identifier] = depth
            records[identifier] = record
        if not frontier:
            break

    edges: list[LocalRelation] = []
    retained_keys: set[RelationKey] = set()

    def edge_order(edge: LocalRelation) -> tuple[object, ...]:
        return (max(distances[edge.source.id], distances[edge.target.id]),
                edge.source.id, edge.target.id, edge.type.value, edge.key.original_index)

    for identifier in sorted(distances, key=lambda identifier: (distances[identifier], identifier)):
        if distances[identifier] >= request.hops:
            continue
        for edge in incident(identifier):
            scan.step()
            if edge.source not in distances or edge.target not in distances:
                continue
            # Visit each edge once without storing unbounded rejected-key sets.
            eligible = [endpoint for endpoint in (edge.source, edge.target)
                        if distances[endpoint] < request.hops]
            if identifier != min(eligible):
                continue
            if not authorized(edge):
                continue
            key = RelationKey(kind, edge.original_index)
            if key in retained_keys:
                continue
            projected = LocalRelation(key, EntityRef(kind, edge.source), EntityRef(kind, edge.target),
                                      edge.type, edge.confidence, anchors(edge))
            edges.append(projected)
            retained_keys.add(key)
            edges.sort(key=edge_order)
            if len(edges) > request.max_edges:
                retained_keys.remove(edges.pop().key)
                truncated = True
    nodes: list[LocalNode] = []
    for identifier in sorted(distances, key=lambda identifier: (distances[identifier], identifier)):
        record = records[identifier]
        if isinstance(record, CodeNodeRecord):
            projected_node = LocalNode(EntityRef(kind, identifier), record.name, record.kind.value,
                                       record.origin, record.confidence, record.source_anchors,
                                       distances[identifier])
        else:
            projected_node = LocalNode(EntityRef(kind, identifier), record.display_name,
                                       record.role or "SemanticComponent", None, record.confidence,
                                       record.source_anchors, distances[identifier])
        nodes.append(projected_node)
    checkpoint()
    return LocalRelationsResult("ok", tuple(nodes), tuple(edges), truncated)
