"""B5 typed fixtures built only through accepted B3 and native B4 APIs."""

import importlib
import importlib.util
import os
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from codeviz.analysis.flow import FlowAnalysis
from codeviz.analysis.project import ProjectAnalysis
from codeviz.analysis.python import ParsedFile, parse_python_file
from codeviz.analysis.symbols import SymbolIndex, SymbolIndexEntry
from codeviz.assistant.budget import BudgetLedger
from codeviz.assistant.contracts import RequestScope
from codeviz.assistant.evidence import EvidenceRegistry
from codeviz.assistant.snapshot import (
    AnalysisSnapshot,
    AnchorRecord,
    SnapshotBinding,
    build_snapshot,
)
from codeviz.assistant.source_excerpts import SourceExcerptReader, SourceReadState
from codeviz.assistant.source_policy import SourceConsent
from codeviz.assistant.source_text import ExcerptLimits
from codeviz.core import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    Confidence,
    EdgeType,
    NodeKind,
    NodeOrigin,
    SemanticComponent,
    SemanticGraph,
    SemanticRelation,
    SourceAnchor,
)
from codeviz.semantic.components import SemanticAnalysis

if TYPE_CHECKING:
    from codeviz.assistant.tools import ToolSession

_DEFAULT_LIMITS = ExcerptLimits()

@dataclass
class StateHolder:
    value: SourceReadState

    def __call__(self) -> SourceReadState:
        return self.value


@dataclass
class IndexCase:
    snapshot: AnalysisSnapshot
    consent: SourceConsent
    scope: RequestScope
    ledger: BudgetLedger
    registry: EvidenceRegistry
    state: StateHolder
    analysis: ProjectAnalysis
    now: list[float]


@dataclass
class SourceCase(IndexCase):
    reader: SourceExcerptReader
    anchor: AnchorRecord


def require_api(test: unittest.TestCase, suffix: str, *exports: str):
    """Missing production APIs fail an executed test, never test collection."""
    name = "codeviz.assistant." + suffix
    test.assertIsNotNone(importlib.util.find_spec(name), "Missing B5 API: " + name)
    module = importlib.import_module(name)
    for export in exports:
        test.assertTrue(hasattr(module, export), "Missing B5 API: " + name + "." + export)
    return module


def _case(analysis: ProjectAnalysis, binding: SnapshotBinding) -> IndexCase:
    snapshot = build_snapshot(analysis, binding=binding)
    scope = RequestScope(binding.project_epoch, binding.analysis_generation, "request-b5")
    consent = SourceConsent(binding, "consent-b5", "https://provider.example", ("app.py",))
    now = [0.0]
    return IndexCase(snapshot, consent, scope, BudgetLedger(clock=lambda: now[0]),
                     EvidenceRegistry(scope), StateHolder(SourceReadState(scope, consent, False)),
                     analysis, now)


def _analysis(symbol_count: int, degree: int) -> ProjectAnalysis:
    anchor = SourceAnchor("app.py", 1, 0, 1, 5)
    denied = SourceAnchor("denied.py", 1, 0, 1, 5)
    files = tuple(ParsedFile(path, path[:-3], None, 10, parsed_success=True,
                             content_hash="a" * 64) for path in ("app.py", "denied.py"))
    index = SymbolIndex()
    for ordinal in range(symbol_count):
        sid = f"s{ordinal:04d}"
        name = "late-match" if ordinal == 1100 else sid
        index.add_entry(SymbolIndexEntry(sid, name, "app." + name, NodeKind.FUNCTION,
                                         "app", "app.py", anchor, None))
    graph = CodeGraph()
    for nid in ("seed", "neighbor", "tail"):
        graph.add_node(CodeNode(nid, NodeKind.FUNCTION, nid, "app." + nid, "app", "python",
                                (anchor,)))
    if degree == 3:
        graph.edges.extend((CodeEdge("seed", "neighbor", EdgeType.CALLS, Confidence.PROBABLE,
                                      anchor),
                            CodeEdge("neighbor", "tail", EdgeType.DATA_FLOW,
                                      Confidence.UNRESOLVED, anchor),
                            CodeEdge("seed", "seed", EdgeType.CONTROL_FLOW,
                                      source_anchor=anchor)))
    else:
        for ordinal in range(degree - 1):
            nid = f"denied{ordinal:04d}"
            graph.add_node(CodeNode(nid, NodeKind.FUNCTION, nid, "private." + nid,
                                    "private", "python", (denied,)))
            graph.edges.append(CodeEdge("seed", nid, EdgeType.CALLS, Confidence.PROBABLE,
                                         anchor))
        graph.edges.extend((CodeEdge("seed", "neighbor", EdgeType.CALLS,
                                      Confidence.PROBABLE, anchor),
                            CodeEdge("neighbor", "tail", EdgeType.DATA_FLOW,
                                      Confidence.UNRESOLVED, anchor)))
    components = tuple(SemanticComponent(cid, cid, "app." + cid, "BackwardPass",
                                         Confidence.PROBABLE, ("static evidence",), (),
                                         ("s0000",) if symbol_count else (), (anchor,))
                       for cid in ("component-a", "component-b"))
    relation = SemanticRelation("component-a", "component-b", EdgeType.GRADIENT_FLOW,
                                Confidence.PROBABLE, ("static evidence",), (anchor,))
    return ProjectAnalysis(files, index, (), flow=FlowAnalysis(graph),
                           semantic=SemanticAnalysis(SemanticGraph(components, (relation,))))


def make_index_case(*, symbol_count: int = 3, degree: int = 3) -> IndexCase:
    return index_case_with(lambda analysis: None, symbol_count=symbol_count, degree=degree)


def index_case_with(configure: Callable[[ProjectAnalysis], ProjectAnalysis | None], *,
                    symbol_count: int = 3, degree: int = 3) -> IndexCase:
    """Configure actual analysis facts before publication; never edit a snapshot."""
    analysis = _analysis(symbol_count, degree)
    configured = configure(analysis)
    if configured is not None:
        analysis = configured
    binding = SnapshotBinding("E:/trusted/b5" if os.name == "nt" else "/trusted/b5",
                              (), "epoch-b5", 1)
    return _case(analysis, binding)


def make_source_case(root: Path, raw: bytes) -> SourceCase:
    return source_case_with(root, raw)


def source_case_with(root: Path, raw: bytes, *, limits: ExcerptLimits = _DEFAULT_LIMITS,
                     configure: Callable[[ProjectAnalysis], ProjectAnalysis | None] | None = None) -> SourceCase:
    root = root.resolve()
    (root / "app.py").write_bytes(raw)
    parsed = parse_python_file(root, "app.py")
    if not parsed.was_parsed:
        raise AssertionError("Source fixture must be accepted by the real Python parser")
    analysis = _analysis(3, 3)
    # Preserve synthetic static facts but use the parser's real raw hash/line inventory.
    analysis = ProjectAnalysis((parsed,), analysis.symbol_index, (), flow=analysis.flow,
                               semantic=analysis.semantic)
    if configure is not None:
        configured = configure(analysis)
        if configured is not None:
            analysis = configured
    case = _case(analysis, SnapshotBinding(str(root), (), "epoch-b5", 1))
    reader = SourceExcerptReader(case.snapshot, scope=case.scope, consent=case.consent,
                                 ledger=case.ledger, registry=case.registry,
                                 current_state=case.state, limits=limits)
    return SourceCase(case.snapshot, case.consent, case.scope, case.ledger, case.registry,
                      case.state, case.analysis, case.now, reader,
                      AnchorRecord("app.py", 1, None, 1, None))


def make_session(case: IndexCase | SourceCase) -> "ToolSession":
    api = importlib.import_module("codeviz.assistant.tools")
    reader = case.reader if isinstance(case, SourceCase) else SourceExcerptReader(
        case.snapshot, scope=case.scope, consent=case.consent, ledger=case.ledger,
        registry=case.registry, current_state=case.state)
    return api.ToolSession(case.snapshot, scope=case.scope, consent=case.consent,
                           ledger=case.ledger, registry=case.registry,
                           current_state=case.state, reader=reader)


def add_source_less(analysis: ProjectAnalysis, origin: NodeOrigin) -> None:
    analysis.flow.graph.add_node(CodeNode("opaque", NodeKind.EXTERNAL_SYMBOL, "opaque",
                                         "library.opaque", "library", "python", origin=origin))
    analysis.flow.graph.edges.append(CodeEdge("seed", "opaque", EdgeType.CALLS,
                                              Confidence.PROBABLE,
                                              SourceAnchor("app.py", 1, 0, 1, 5)))
