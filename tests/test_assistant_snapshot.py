"""B3 contract tests: synthetic facts, no parsing, disk freshness or authorization."""

import ast
import importlib
import importlib.util
import os
import unittest
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, fields, replace
from functools import partial
from pathlib import Path
from typing import Callable
from unittest.mock import patch

from codeviz.analysis.flow import FlowAnalysis
from codeviz.analysis.project import ProjectAnalysis
from codeviz.analysis.python import ParsedFile
from codeviz.analysis.symbols import SymbolIndex, SymbolIndexEntry
from codeviz.assistant.contracts import AgentError, RequestScope
from codeviz.core import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    Confidence,
    EdgeType,
    EntityRole,
    NodeKind,
    NodeOrigin,
    SemanticComponent,
    SemanticGraph,
    SemanticRelation,
    SourceAnchor,
)
from codeviz.semantic.components import SemanticAnalysis


class Forbidden:
    """Excluded metadata/AST must not even be inspected during projection."""

    def __getattribute__(self, name: str) -> object:
        raise AssertionError(f"excluded value accessed: {name}")


class GuardedNode(CodeNode):
    def __getattribute__(self, name: str) -> object:
        if name == "metadata":
            raise AssertionError("metadata accessed")
        return super().__getattribute__(name)


def fixture(*, cached: bool = True) -> ProjectAnalysis:
    """Fresh typed facts with shared namespace IDs and duplicate edge occurrences."""
    anchor = SourceAnchor("src/app.py", 2, 0, 3, 5)
    files: tuple[ParsedFile, ...] = (
        ParsedFile("z.py", "z", None, 0, parsed_success=False),
        ParsedFile("src/app.py", "app", None if cached else ast.Module(body=[], type_ignores=[]),
                   4, parsed_success=True if cached else None, content_hash="a" * 64),
    )
    index = SymbolIndex()
    index.add_entry(SymbolIndexEntry("shared", "main", "app.main", NodeKind.FUNCTION,
                                    "app", "src/app.py", anchor, "partial-parent"))
    index.add_entry(SymbolIndexEntry("a-symbol", "arg", "app.arg", NodeKind.PARAMETER,
                                    "app.main", "src/app.py", anchor, "shared", False))
    graph = CodeGraph()
    graph.add_node(CodeNode("shared", NodeKind.FUNCTION, "main", "app.main", "app",
                            "python", (anchor,), role=EntityRole.DEFINITION))
    graph.add_node(CodeNode("external", NodeKind.EXTERNAL_SYMBOL, "remote", "lib.remote",
                            "lib", "python", origin=NodeOrigin.EXTERNAL))
    graph.add_node(CodeNode("synthetic", NodeKind.CALL_SITE, "call", "app.call", "app",
                            "python", (anchor,), NodeOrigin.SYNTHETIC,
                            EntityRole.INVOCATION, Confidence.PROBABLE))
    first = CodeEdge("shared", "external", EdgeType.CALLS, Confidence.PROBABLE,
                     anchor, ("synthetic",))
    graph.edges.extend((first, CodeEdge("external", "synthetic", EdgeType.READS), first))
    components: tuple[SemanticComponent, ...] = (
        SemanticComponent("shared", "入口", "app.main", "entry", Confidence.PROBABLE,
                          ("静态说明", "静态说明"), ("a-component",), ("partial-code",), (anchor,)),
        SemanticComponent("a-component", "Child", code_symbol_ids=("shared",)),
    )
    relation = SemanticRelation("shared", "a-component", EdgeType.DATA_FLOW,
                                Confidence.PROBABLE, ("静态说明",), (anchor,))
    semantic = SemanticAnalysis(SemanticGraph(components, (relation, relation), ("shared",)))
    return ProjectAnalysis(files, index, (), flow=FlowAnalysis(graph), semantic=semantic)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("codeviz.assistant.snapshot"),
                             "Missing B3 API: codeviz.assistant.snapshot")
        self.api = importlib.import_module("codeviz.assistant.snapshot")
        names = ("SnapshotBinding", "SnapshotLimits", "FileRecord", "AnchorRecord",
                 "SymbolRecord", "CodeNodeRecord", "CodeEdgeRecord", "ComponentRecord",
                 "RelationRecord", "AnalysisSnapshot", "build_snapshot")
        self.assertEqual([name for name in names if not hasattr(self.api, name)], [],
                         "Missing B3 snapshot exports")
        self.binding = self.api.SnapshotBinding(
            "E:/trusted/project" if os.name == "nt" else "/trusted/project",
            ("ignored",), "epoch-a", 7)

    def build(self, analysis=None, **kwargs):
        return self.api.build_snapshot(fixture() if analysis is None else analysis,
                                       binding=self.binding, **kwargs)

    def rejected(self, action: Callable[[], object], code="invalid_input"):
        with self.assertRaises(AgentError) as caught:
            action()
        error = caught.exception
        self.assertEqual(error.code, code)
        self.assertEqual(error.message, AgentError(code).message)
        self.assertEqual(str(error), error.message)
        self.assertIsNone(error.__cause__)
        self.assertTrue(error.__suppress_context__, "public errors must use from None")

    def test_public_api_and_exact_whitelist_projection(self):
        analysis = fixture()
        snapshot = self.build(analysis)
        self.assertIsInstance(snapshot, self.api.AnalysisSnapshot)
        schemas = {
            "SnapshotBinding": "project_root ignore_paths project_epoch analysis_generation",
            "SnapshotLimits": "max_records max_anchors max_text_bytes",
            "FileRecord": "relative_path module_name line_count was_parsed content_hash",
            "AnchorRecord": "file_path start_line start_column end_line end_column",
            "SymbolRecord": "id name qualified_name kind scope file_path source_anchor "
                            "parent_symbol definition",
            "CodeNodeRecord": "id kind name qualified_name scope language source_anchors "
                              "origin role confidence file_path",
            "CodeEdgeRecord": "original_index source target type confidence source_anchor "
                              "possible_targets",
            "ComponentRecord": "id display_name code_name role confidence evidence "
                               "child_component_ids code_symbol_ids source_anchors",
            "RelationRecord": "original_index source target type confidence evidence source_anchors",
        }
        for name, schema in schemas.items():
            with self.subTest(schema=name):
                self.assertEqual(tuple(f.name for f in fields(getattr(self.api, name))),
                                 tuple(schema.split()))
        self.assertEqual(self.api.SnapshotLimits().max_records, 200000)
        self.assertEqual(self.api.SnapshotLimits().max_anchors, 200000)
        self.assertEqual(self.api.SnapshotLimits().max_text_bytes, 16777216)
        self.assertEqual(snapshot.binding, self.binding)
        self.assertEqual(snapshot.semantic_roots, ("shared",))
        groups = (
            (snapshot.files(), tuple(sorted(analysis.files, key=lambda f: f.relative_path)),
             self.api.FileRecord),
            (snapshot.symbols(), tuple(sorted(analysis.symbol_index.entries.values(),
                                             key=lambda s: s.id)), self.api.SymbolRecord),
            (snapshot.code_nodes(), tuple(sorted(analysis.flow.graph.nodes.values(),
                                                key=lambda n: n.id)), self.api.CodeNodeRecord),
            (snapshot.code_edges(), tuple(analysis.flow.graph.edges), self.api.CodeEdgeRecord),
            (snapshot.components(), tuple(sorted(analysis.semantic.graph.components,
                                                key=lambda c: c.id)), self.api.ComponentRecord),
            (snapshot.relations(), analysis.semantic.graph.relations, self.api.RelationRecord),
        )
        for projected, originals, record_type in groups:
            self.assertIs(type(projected), tuple)
            self.assertEqual(len(projected), len(originals))
            for ordinal, (record, original) in enumerate(zip(projected, originals, strict=True)):
                with self.subTest(record=record_type.__name__, ordinal=ordinal):
                    self.assertIs(type(record), record_type)
                    self.assertIsNot(record, original)
                    for field in fields(record):
                        expected = ordinal if field.name == "original_index" else getattr(
                            original, field.name)
                        actual = getattr(record, field.name)
                        if field.name in ("source_anchor", "source_anchors"):
                            old = (expected,) if field.name == "source_anchor" else expected
                            new = (actual,) if field.name == "source_anchor" else actual
                            for fresh, source in zip(new, old, strict=True):
                                if source is not None:
                                    self.assertIs(type(fresh), self.api.AnchorRecord)
                                    self.assertIsNot(fresh, source)
                                    self.assertEqual(tuple(getattr(fresh, f.name) for f in fields(fresh)),
                                                     tuple(getattr(source, f.name) for f in fields(fresh)))
                        else:
                            self.assertEqual(actual, expected)
                    self.assertFalse(hasattr(record, "metadata"))
        self.assertEqual(snapshot.get_symbol("shared").parent_symbol, "partial-parent")
        self.assertEqual(snapshot.get_component("shared").code_symbol_ids, ("partial-code",))

    def test_detachment_frozen_slots_and_builder_only_snapshot(self):
        analysis = fixture()
        snapshot = self.build(analysis)
        values = (snapshot.binding, self.api.SnapshotLimits(), *snapshot.files(),
                  *snapshot.symbols(), *snapshot.code_nodes(), *snapshot.code_edges(),
                  *snapshot.components(), *snapshot.relations(),
                  snapshot.get_symbol("shared").source_anchor)
        for value in values:
            with self.subTest(type=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                first = fields(value)[0].name
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, first, getattr(value, first))
        self.assertFalse(hasattr(snapshot, "__dict__"))
        with self.assertRaises((AttributeError, TypeError)):
            snapshot.semantic_roots = ()
        with self.assertRaises((AgentError, TypeError)):
            self.api.AnalysisSnapshot()
        # Mutate the original facts themselves, not just their owning collections.
        object.__setattr__(analysis.symbol_index.entries["shared"], "name", "MUTATED")
        object.__setattr__(analysis.flow.graph.nodes["shared"], "name", "MUTATED")
        object.__setattr__(analysis.flow.graph.edges[0], "possible_targets", ())
        object.__setattr__(analysis.semantic.graph.components[0], "evidence", ())
        object.__setattr__(analysis.semantic.graph.relations[0], "evidence", ())
        object.__setattr__(analysis.symbol_index.entries["shared"].source_anchor, "start_line", 4)
        object.__setattr__(analysis.semantic.graph, "roots", ())
        analysis.symbol_index.entries.clear()
        analysis.flow.graph.nodes.clear()
        analysis.flow.graph.edges.clear()
        object.__setattr__(analysis.semantic.graph, "components", ())
        object.__setattr__(analysis.files[1], "module_name", "MUTATED")
        self.assertEqual(snapshot.get_symbol("shared").qualified_name, "app.main")
        self.assertEqual(snapshot.get_symbol("shared").name, "main")
        self.assertEqual(snapshot.get_code_node("shared").name, "main")
        self.assertEqual(snapshot.get_symbol("shared").source_anchor.start_line, 2)
        self.assertEqual(snapshot.code_edges()[0].possible_targets, ("synthetic",))
        self.assertEqual(snapshot.get_component("shared").evidence, ("静态说明", "静态说明"))
        self.assertEqual(snapshot.relations()[0].evidence, ("静态说明",))
        self.assertEqual(snapshot.semantic_roots, ("shared",))
        self.assertEqual(snapshot.get_file("src/app.py").module_name, "app")
        self.assertEqual(len(snapshot.code_nodes()), 3)
        self.assertEqual(len(snapshot.code_edges()), 3)
        self.assertEqual(len(snapshot.components()), 2)

    def test_excluded_fields_ast_and_io_are_never_accessed(self):
        analysis = fixture()
        object.__setattr__(analysis.files[1], "tree", Forbidden())
        for name in ("issues", "resolution", "model", "training", "generic_entries"):
            object.__setattr__(analysis, name, Forbidden())
        analysis.symbol_index.references = Forbidden()
        analysis.symbol_index.scope_declarations = Forbidden()
        node = analysis.flow.graph.nodes["shared"]
        guarded = GuardedNode(**{f.name: getattr(node, f.name) for f in fields(node)})
        analysis.flow.graph.nodes["shared"] = guarded
        for value in (*analysis.symbol_index.entries.values(),
                      *analysis.semantic.graph.components, *analysis.flow.graph.edges):
            object.__setattr__(value, "metadata", Forbidden())
        # Import API before blocking disk access; a builder must only consume facts.
        with ExitStack() as stack:
            for target in ("builtins.open", "io.open", "os.stat", "os.scandir",
                           "pathlib.Path.resolve", "ast.walk", "ast.iter_child_nodes",
                           "ast.parse", "codeviz.core.stable_node_id"):
                stack.enter_context(patch(target, side_effect=AssertionError("forbidden I/O/AST/ID")))
            snapshot = self.build(analysis)
            self.assertEqual(snapshot.get_symbol("shared").name, "main")

    def test_cache_live_success_failed_inventory_and_origins(self):
        cached, live = self.build(fixture()), self.build(fixture(cached=False))
        for method in ("files", "symbols", "code_nodes", "code_edges", "components", "relations"):
            self.assertEqual(getattr(cached, method)(), getattr(live, method)())
        for tree, flag, expected in ((None, True, True), (ast.Module(body=[], type_ignores=[]),
                                     False, False), (None, None, False)):
            with self.subTest(flag=flag):
                parsed = ParsedFile("only.py", "only", tree, 1, parsed_success=flag)
                snap = self.build(ProjectAnalysis((parsed,), SymbolIndex(), ()))
                self.assertIs(snap.get_file("only.py").was_parsed, expected)
                self.assertIsNone(snap.get_file("only.py").content_hash)
                self.assertEqual(snap.code_nodes(), ())
        self.assertFalse(cached.get_file("z.py").was_parsed)
        for origin in (NodeOrigin.EXTERNAL, NodeOrigin.SYNTHETIC):
            for anchors in ((), (SourceAnchor("src/app.py", 1),)):
                with self.subTest(origin=origin, anchors=bool(anchors)):
                    analysis = fixture()
                    analysis.flow.graph.nodes["external"] = CodeNode(
                        "external", NodeKind.EXTERNAL_SYMBOL, "remote", "lib.remote", "lib",
                        "python", anchors, origin)
                    record = self.build(analysis).get_code_node("external")
                    self.assertIs(record.origin, origin)
                    self.assertEqual(len(record.source_anchors), len(anchors))

    def test_binding_limits_root_ignore_and_strict_scope_fields(self):
        changes = {"project_root": ("relative", "E:relative", "", None, Path("E:/root"), "x\x00"),
                   "ignore_paths": (["ignored"], ("ignored", "ignored"), ("../x",),
                                    ("/x",), ("x/*",), ("x\\y",), ("x/./y",), (1,)),
                   "project_epoch": ("", "中文", "a b", "x" * 129, 1),
                   "analysis_generation": (-1, True, 1.0, 2**63)}
        for name, bad_values in changes.items():
            for bad in bad_values:
                with self.subTest(field=name, bad=bad):
                    self.rejected(partial(replace, self.binding, **{name: bad}))
        for name in ("max_records", "max_anchors", "max_text_bytes"):
            self.assertEqual(getattr(self.api.SnapshotLimits(**{name: 0}), name), 0)
            for bad in (-1, True, False, 1.0, "1", None):
                with self.subTest(limit=name, bad=bad):
                    self.rejected(partial(self.api.SnapshotLimits, **{name: bad}))
        self.assertEqual(replace(self.binding, analysis_generation=2**63 - 1).analysis_generation,
                         2**63 - 1)

    def test_record_constructors_strict_types_and_local_invariants(self):
        for bad in (None, {}, "analysis"):
            with self.subTest(builder_analysis=bad):
                self.rejected(partial(self.api.build_snapshot, bad, binding=self.binding))
        for bad in (None, {}, "binding"):
            with self.subTest(builder_binding=bad):
                self.rejected(partial(self.api.build_snapshot, fixture(), binding=bad))
        for bad in (None, {}, "limits"):
            with self.subTest(builder_limits=bad):
                self.rejected(partial(self.build, limits=bad))
        snap = self.build()
        samples = (
            (snap.get_file("src/app.py"), {"line_count": (-1, True, 1.5, 0),
              "was_parsed": (1, "True"), "content_hash": ("A" * 64, "a" * 63, 1),
              "module_name": (None, 3, "\ud800")}),
            (snap.get_symbol("shared"), {"kind": ("Function", EdgeType.CALLS),
              "definition": (1,), "parent_symbol": ("", "x\n"), "name": (None,)}),
            (snap.get_code_node("shared"), {"kind": ("Function",), "origin": ("Source",),
              "role": ("Definition",), "confidence": ("Exact",), "source_anchors": ([], ()),
              "name": ("",), "language": ("",)}),
            (snap.code_edges()[0], {"original_index": (-1, True, 1.0),
              "type": ("CALLS",), "confidence": ("Probable",), "possible_targets": ([],)}),
            (snap.get_component("shared"), {"evidence": ([], (1,)),
              "child_component_ids": ([],), "code_symbol_ids": ([],), "source_anchors": ([],),
              "confidence": ("Probable",), "display_name": ("\ud800",)}),
            (snap.relations()[0], {"original_index": (-1, True, 1.0), "evidence": ([], ()),
              "source_anchors": ([], ()), "type": ("DATA_FLOW",), "confidence": ("Probable",)}),
        )
        for sample, invalid in samples:
            for name, bad_values in invalid.items():
                for bad in bad_values:
                    with self.subTest(record=type(sample).__name__, field=name, bad=bad):
                        self.rejected(partial(replace, sample, **{name: bad}))
        for sample in (snap.get_symbol("shared"), snap.get_code_node("shared"),
                       snap.get_component("shared")):
            for bad in ("", "secret\n", "secret\x7f", None, 1, "\ud800"):
                with self.subTest(record=type(sample).__name__, id=bad):
                    self.rejected(partial(replace, sample, id=bad))

    def test_paths_normalization_inventory_uniqueness_and_namespace_keys(self):
        snap = self.build()
        self.assertEqual(replace(snap.get_file("src/app.py"), relative_path="src\\app.py")
                         .relative_path, "src/app.py")
        for bad in ("", "/app.py", "C:/app.py", "C:app.py", "../app.py", "src/../app.py",
                    "x\x00.py", None, 1):
            with self.subTest(path=bad):
                self.rejected(partial(replace, snap.get_file("src/app.py"), relative_path=bad))
                self.rejected(partial(replace, snap.get_symbol("shared").source_anchor,
                                      file_path=bad))
        for group in ("file", "symbol", "code", "component", "symbol-key", "code-key"):
            with self.subTest(group=group):
                analysis = fixture()
                if group == "file":
                    object.__setattr__(analysis, "files", (*analysis.files,
                        replace(analysis.files[1], relative_path="src\\app.py")))
                elif group == "component":
                    graph = analysis.semantic.graph
                    object.__setattr__(graph, "components", (*graph.components, graph.components[0]))
                else:
                    records = (analysis.symbol_index.entries if group.startswith("symbol")
                               else analysis.flow.graph.nodes)
                    if group.endswith("key"):
                        records["wrong-key"] = records.pop("shared")
                    else:
                        object.__setattr__(records[next(key for key in records if key != "shared")],
                                           "id", "shared")
                self.rejected(partial(self.build, analysis))
        self.assertIsNotNone(snap.get_symbol("shared"))
        self.assertIsNotNone(snap.get_code_node("shared"))
        self.assertIsNotNone(snap.get_component("shared"))

    def test_anchor_local_and_inventory_cross_record_constraints(self):
        anchor = self.build().get_symbol("shared").source_anchor
        for name, values in {"start_line": (0, True, 1.0, 4),
                             "end_line": (1, True, 2.0),
                             "start_column": (-1, True, 0.0),
                             "end_column": (-1, True, 5.0)}.items():
            for bad in values:
                with self.subTest(field=name, bad=bad):
                    self.rejected(partial(replace, anchor, **{name: bad}))
        self.rejected(lambda: replace(anchor, start_line=2, end_line=2,
                                       start_column=5, end_column=4))
        for owner in ("symbol", "node", "edge", "component", "relation"):
            for bad in (SourceAnchor("missing.py", 1), SourceAnchor("z.py", 1),
                        SourceAnchor("src/app.py", 1, end_line=5)):
                with self.subTest(owner=owner, anchor=bad):
                    analysis = fixture()
                    record = {"symbol": analysis.symbol_index.entries["shared"],
                              "node": analysis.flow.graph.nodes["shared"],
                              "edge": analysis.flow.graph.edges[0],
                              "component": analysis.semantic.graph.components[0],
                              "relation": analysis.semantic.graph.relations[0]}[owner]
                    plural = owner in ("node", "component", "relation")
                    object.__setattr__(record, "source_anchors" if plural else "source_anchor",
                                       (bad,) if plural else bad)
                    self.rejected(partial(self.build, analysis))
        for owner in ("symbol", "node"):
            analysis = fixture()
            record = (analysis.symbol_index.entries["shared"] if owner == "symbol"
                      else analysis.flow.graph.nodes["shared"])
            object.__setattr__(record, "file_path", "z.py")
            self.rejected(partial(self.build, analysis))

    def test_code_endpoints_candidates_and_source_requirements(self):
        for field, bad in (("source", "missing"), ("target", "missing"),
                           ("possible_targets", ("missing",)), ("confidence", Confidence.EXACT),
                           ("type", EdgeType.READS), ("possible_targets", ["synthetic"])):
            with self.subTest(field=field, bad=bad):
                analysis = fixture()
                object.__setattr__(analysis.flow.graph.edges[0], field, bad)
                self.rejected(partial(self.build, analysis))
        for field, bad in (("source_anchors", ()), ("name", ""), ("language", "")):
            with self.subTest(node_field=field):
                analysis = fixture()
                object.__setattr__(analysis.flow.graph.nodes["shared"], field, bad)
                self.rejected(partial(self.build, analysis))
        snap = self.build()
        # Candidate membership is not a direct incident edge endpoint.
        self.assertEqual(tuple(edge.original_index for edge in snap.code_edges(node_id="synthetic")),
                         (1,))
        self.assertIsNone(snap.code_edges()[1].source_anchor)

    def test_semantic_links_provenance_evidence_and_gradient_confidence(self):
        cases = (("graph", "roots", ("missing",)),
                 ("component", "child_component_ids", ("missing",)),
                 ("relation", "source", "missing"), ("relation", "target", "missing"),
                 ("relation", "evidence", ()), ("relation", "source_anchors", ()))
        for owner, name, bad in cases:
            with self.subTest(owner=owner, field=name):
                analysis = fixture()
                graph = analysis.semantic.graph
                record = {"graph": graph, "component": graph.components[0],
                          "relation": graph.relations[0]}[owner]
                object.__setattr__(record, name, bad)
                self.rejected(partial(self.build, analysis))
        analysis = fixture()
        component = analysis.semantic.graph.components[0]
        for name in ("child_component_ids", "code_symbol_ids", "source_anchors"):
            object.__setattr__(component, name, ())
        self.rejected(lambda: self.build(analysis))
        for confidence in (Confidence.EXACT, Confidence.PROBABLE, Confidence.UNRESOLVED):
            with self.subTest(gradient=confidence):
                analysis = fixture()
                relation = analysis.semantic.graph.relations[0]
                object.__setattr__(relation, "type", EdgeType.GRADIENT_FLOW)
                object.__setattr__(relation, "confidence", confidence)
                if confidence == Confidence.EXACT:
                    self.rejected(partial(self.build, analysis))
                else:
                    self.assertIs(self.build(analysis).relations()[0].confidence, confidence)

    def test_queries_sorted_bounded_direct_and_original_ordinals(self):
        snap = self.build()
        for method, expected, key in (("files", ("src/app.py", "z.py"), "relative_path"),
                                     ("symbols", ("a-symbol", "shared"), "id"),
                                     ("code_nodes", ("external", "shared", "synthetic"), "id"),
                                     ("components", ("a-component", "shared"), "id")):
            with self.subTest(method=method):
                query = getattr(snap, method)
                self.assertEqual(tuple(getattr(r, key) for r in query()), expected)
                self.assertEqual(query(limit=1), query()[:1])
                self.assertEqual(query(limit=0), ())
                self.assertEqual(query(limit=1000), query())
        self.assertEqual(tuple(e.original_index for e in snap.code_edges()), (0, 1, 2))
        self.assertEqual(tuple(e.original_index for e in snap.code_edges(node_id="shared")), (0, 2))
        self.assertEqual(snap.code_edges(node_id="shared", limit=1), snap.code_edges()[:1])
        self.assertEqual(tuple(r.original_index for r in snap.relations(component_id="a-component")),
                         (0, 1))
        for query, name in ((snap.code_edges, "node_id"), (snap.relations, "component_id")):
            self.assertEqual(query(**{name: "missing"}), ())
            self.assertEqual(query(**{name: "missing"}, limit=0), ())
        for method in ("get_file", "get_symbol", "get_code_node", "get_component"):
            self.assertIsNone(getattr(snap, method)("missing.py" if method == "get_file"
                                                  else "missing"))
        # Default 100 is a real cap; duplicates still retain their source ordinals.
        analysis = fixture()
        analysis.flow.graph.edges[:] = [analysis.flow.graph.edges[0]] * 105
        object.__setattr__(analysis.semantic.graph, "relations",
                           (analysis.semantic.graph.relations[0],) * 105)
        bounded = self.build(analysis)
        for query in (bounded.code_edges, bounded.relations):
            with self.subTest(default_limit=query.__name__):
                self.assertIs(type(query()), tuple)
                self.assertEqual(len(query()), 100)
                self.assertEqual(tuple(r.original_index for r in query()), tuple(range(100)))
                self.assertEqual(len(query(limit=1000)), 105)
                self.assertTrue(all(type(r.original_index) is int for r in query(limit=1000)))

    def test_query_input_validation_including_zero_limit_and_scope_reuse(self):
        snap = self.build()
        for method in ("files", "symbols", "code_nodes", "components", "code_edges", "relations"):
            for bad in (-1, True, 1.0, 1001, "1", None):
                with self.subTest(method=method, limit=bad):
                    self.rejected(partial(getattr(snap, method), limit=bad))
        for query, keyword in ((snap.code_edges, "node_id"), (snap.relations, "component_id")):
            for bad in ("", "secret\n", "x\x7f", 1, [], "\ud800"):
                with self.subTest(filter=keyword, bad=bad):
                    self.rejected(partial(query, **{keyword: bad}, limit=0))
        for method in ("get_symbol", "get_code_node", "get_component"):
            for bad in ("", "x\n", None, 1, "\ud800"):
                self.rejected(partial(getattr(snap, method), bad))
        for bad in ("", "../secret.py", "C:/secret.py", None, 1, "\ud800"):
            self.rejected(partial(snap.get_file, bad))
        for request in ("request-a", "request-b"):
            self.assertTrue(snap.matches_scope(RequestScope("epoch-a", 7, request)))
        self.assertFalse(snap.matches_scope(RequestScope("epoch-b", 7, "request-a")))
        self.assertFalse(snap.matches_scope(RequestScope("epoch-a", 8, "request-a")))
        for name, bad in (("project_epoch", "中文"), ("analysis_generation", True),
                          ("analysis_generation", 2**63), ("request_id", "")):
            scope = RequestScope("different-epoch", 8, "request-a")
            object.__setattr__(scope, name, bad)
            self.rejected(partial(snap.matches_scope, scope))
        for bad in (None, {}, "scope"):
            self.rejected(partial(snap.matches_scope, bad))
        for mismatch in (False, True):
            for name in ("project_epoch", "analysis_generation", "request_id"):
                with self.subTest(missing_scope_slot=name, mismatch=mismatch):
                    scope = RequestScope("different-epoch" if mismatch else "epoch-a",
                                         8 if mismatch else 7, "request-a")
                    object.__delattr__(scope, name)
                    try:
                        self.rejected(partial(snap.matches_scope, scope))
                    except AttributeError:
                        self.fail("missing scope slot leaked AttributeError instead of AgentError")

    def test_record_and_anchor_budgets_zero_exact_and_exceeded(self):
        # Six categories: 2 files + 2 symbols + 3 nodes + 3 edges + 2 components + 2 relations.
        # Anchor occurrences: 2 symbols + 2 nodes + 2 edges + 1 component + 2 relations.
        for name, exact in (("max_records", 14), ("max_anchors", 9)):
            self.build(limits=self.api.SnapshotLimits(**{name: exact}))
            for bad in (0, exact - 1):
                with self.subTest(budget=name, capacity=bad):
                    self.rejected(partial(self.build, limits=self.api.SnapshotLimits(**{name: bad})),
                                  "budget_exceeded")
        empty = ProjectAnalysis((), SymbolIndex(), ())
        snap = self.build(empty, limits=self.api.SnapshotLimits(max_records=0, max_anchors=0))
        self.assertEqual(snap.files(), ())
        self.assertEqual(snap.semantic_roots, ())

    def test_utf8_text_budget_binding_roots_repeated_references_and_early_stop(self):
        # Enum values and numeric fields are not text fields. Explicit schema accounting
        # avoids depending on snapshot private counters or serialization overhead.
        analysis = fixture()
        texts = [self.binding.project_root, *self.binding.ignore_paths, self.binding.project_epoch]
        for file in analysis.files:
            texts.extend((file.relative_path, file.module_name))
            if file.content_hash is not None:
                texts.append(file.content_hash)
        for symbol in analysis.symbol_index.entries.values():
            texts.extend((symbol.id, symbol.name, symbol.qualified_name, symbol.scope,
                          symbol.file_path, symbol.source_anchor.file_path))
            if symbol.parent_symbol is not None:
                texts.append(symbol.parent_symbol)
        for node in analysis.flow.graph.nodes.values():
            texts.extend((node.id, node.name, node.qualified_name, node.scope, node.language))
            texts.extend(a.file_path for a in node.source_anchors)
            if node.file_path is not None:
                texts.append(node.file_path)
        for edge in analysis.flow.graph.edges:
            texts.extend((edge.source, edge.target, *edge.possible_targets))
            if edge.source_anchor is not None:
                texts.append(edge.source_anchor.file_path)
        for component in analysis.semantic.graph.components:
            texts.extend((component.id, component.display_name, *component.evidence,
                          *component.child_component_ids, *component.code_symbol_ids))
            texts.extend(a.file_path for a in component.source_anchors)
            texts.extend(t for t in (component.code_name, component.role) if t is not None)
        for relation in analysis.semantic.graph.relations:
            texts.extend((relation.source, relation.target, *relation.evidence))
            texts.extend(a.file_path for a in relation.source_anchors)
        texts.extend(analysis.semantic.graph.roots)
        exact = sum(len(t.encode("utf-8")) for t in texts)
        self.build(analysis, limits=self.api.SnapshotLimits(max_text_bytes=exact))
        for capacity in (0, exact - 1):
            with self.subTest(capacity=capacity):
                self.rejected(partial(self.build, analysis, limits=self.api.SnapshotLimits(
                    max_text_bytes=capacity)), "budget_exceeded")
        # Prefix exhausts capacity before invalid trailing surrogate: don't encode
        # the entire oversized input or report its later Unicode error instead.
        oversized = fixture()
        object.__setattr__(oversized.files[1], "module_name", "中" * 10000 + "\ud800")
        self.rejected(lambda: self.build(oversized, limits=self.api.SnapshotLimits(
            max_text_bytes=512)), "budget_exceeded")

        # Fixed sequence cap is independent of the three existing budgets and
        # counts occurrences, including empty strings and repeated references.
        maximum = 200000
        binding = replace(self.binding, ignore_paths=())
        for count in (maximum - 1, maximum, 1000000):
            with self.subTest(sequence_evidence=count, partial_code_ids=1):
                component = SemanticComponent("c", "C", evidence=("",) * count,
                                              code_symbol_ids=("unknown",))
                facts = ProjectAnalysis((), SymbolIndex(), (), semantic=SemanticAnalysis(
                    SemanticGraph((component,), (), ())))
                action = partial(self.api.build_snapshot, facts, binding=binding,
                                 limits=self.api.SnapshotLimits(1, 0, 64))
                if count == maximum - 1:
                    projected = action().get_component("c")
                    self.assertEqual(projected.evidence, ("",) * count)
                    self.assertEqual(projected.code_symbol_ids, ("unknown",))
                else:
                    self.rejected(action, "budget_exceeded")

        # Each whitelisted string tuple reserves its length before inspecting
        # even its first invalid element. Roots have no public record constructor.
        samples = self.build()
        constructors = (
            (self.binding, "ignore_paths"),
            (samples.code_edges()[0], "possible_targets"),
            (samples.get_component("shared"), "evidence"),
            (samples.get_component("shared"), "child_component_ids"),
            (samples.get_component("shared"), "code_symbol_ids"),
            (samples.relations()[0], "evidence"),
        )
        invalid_oversized = (1,) + ("",) * maximum
        for record, name in constructors:
            with self.subTest(constructor=type(record).__name__, sequence_field=name):
                self.rejected(partial(replace, record, **{name: invalid_oversized}),
                              "budget_exceeded")
            if name != "ignore_paths":
                with self.subTest(constructor_exact=type(record).__name__, sequence_field=name):
                    values = ("shared",) * maximum
                    self.assertEqual(getattr(replace(record, **{name: values}), name), values)
        for owner, name in (("binding", "ignore_paths"), ("edge", "possible_targets"),
                            ("component", "evidence"), ("component", "child_component_ids"),
                            ("component", "code_symbol_ids"), ("relation", "evidence"),
                            ("graph", "roots")):
            with self.subTest(builder_owner=owner, sequence_field=name):
                facts = fixture()
                current_binding = replace(self.binding)
                graph = facts.semantic.graph
                source = {"binding": current_binding, "edge": facts.flow.graph.edges[0],
                          "component": graph.components[0], "relation": graph.relations[0],
                          "graph": graph}[owner]
                object.__setattr__(source, name, invalid_oversized)
                self.rejected(partial(self.api.build_snapshot, facts, binding=current_binding),
                              "budget_exceeded")

        # Fixture has 11 string-tuple occurrences: ignore 1, candidates 2,
        # component evidence/children/code IDs 5, relation evidence 2, roots 1.
        # Replacing its two-entry component evidence with 199991 reaches 200000.
        # One extra occurrence in any other field must exceed the aggregate cap.
        for owner, name, extra in (("binding", "ignore_paths", "ignored-more"),
                                   ("edge", "possible_targets", "synthetic"),
                                   ("component", "child_component_ids", "a-component"),
                                   ("component", "code_symbol_ids", "unknown"),
                                   ("relation", "evidence", ""),
                                   ("graph", "roots", "shared")):
            with self.subTest(aggregate_owner=owner, sequence_field=name):
                facts = fixture()
                current_binding = replace(self.binding)
                graph = facts.semantic.graph
                object.__setattr__(graph.components[0], "evidence", ("",) * (maximum - 9))
                self.api.build_snapshot(facts, binding=current_binding)
                source = {"binding": current_binding, "edge": facts.flow.graph.edges[0],
                          "component": graph.components[0], "relation": graph.relations[0],
                          "graph": graph}[owner]
                object.__setattr__(source, name, (*getattr(source, name), extra))
                self.rejected(partial(self.api.build_snapshot, facts, binding=current_binding),
                              "budget_exceeded")

        shared_values = ("unknown",) * (maximum // 2)
        component = SemanticComponent("c", "C", evidence=shared_values,
                                      code_symbol_ids=shared_values)
        graph = SemanticGraph((component,), (), ())
        facts = ProjectAnalysis((), SymbolIndex(), (), semantic=SemanticAnalysis(graph))
        with self.subTest(shared_tuple_multiple_fields="exact"):
            snapshot = self.api.build_snapshot(facts, binding=binding)
            self.assertEqual(snapshot.get_component("c").evidence, shared_values)
            self.assertEqual(snapshot.get_component("c").code_symbol_ids, shared_values)
        with self.subTest(shared_tuple_multiple_fields="exceeded"):
            object.__setattr__(graph, "roots", ("c",))
            self.rejected(partial(self.api.build_snapshot, facts, binding=binding),
                          "budget_exceeded")


class B5CompleteInventoryTests(unittest.TestCase):
    """R2 additive contracts; existing B3 tests above remain unchanged."""

    def test_full_inventories_keep_late_edges_and_legacy_limit(self):
        from assistant_tool_fixtures import make_index_case

        case = make_index_case(symbol_count=1101, degree=1101)
        snapshot = case.snapshot
        for name in ("symbol_inventory", "code_node_inventory", "component_inventory",
                     "code_edge_inventory", "relation_inventory", "incident_code_edges",
                     "incident_relations"):
            self.assertTrue(hasattr(snapshot, name), "Missing B5 snapshot API: " + name)
        self.assertEqual(len(snapshot.symbol_inventory), 1101)
        self.assertEqual(snapshot.symbol_inventory[-1].id, "s1100")
        self.assertEqual(len(snapshot.incident_code_edges("seed")), 1101)
        self.assertEqual(snapshot.incident_code_edges("seed")[-1].original_index, 1100)
        self.assertEqual(snapshot.code_edge_inventory[-1].original_index, 1101)
        self.assertEqual(len(snapshot.symbols(limit=1000)), 1000)
        for name in ("file_inventory", "symbol_inventory", "code_node_inventory",
                     "component_inventory", "code_edge_inventory", "relation_inventory"):
            with self.subTest(inventory=name):
                self.assertIs(type(getattr(snapshot, name)), tuple)
                self.assertIs(getattr(snapshot, name), getattr(snapshot, name))
        references = sum(len(snapshot.incident_code_edges(node.id))
                         for node in snapshot.code_node_inventory)
        self.assertLessEqual(references, 2 * len(snapshot.code_edge_inventory))
        self.assertEqual(references, 2 * len(snapshot.code_edge_inventory))

    def test_incident_direction_selfloop_unknown_and_strict_ids(self):
        from assistant_tool_fixtures import make_index_case

        snapshot = make_index_case().snapshot
        self.assertTrue(hasattr(snapshot, "incident_code_edges"),
                        "Missing B5 snapshot API: incident_code_edges")
        self.assertTrue(hasattr(snapshot, "incident_relations"),
                        "Missing B5 snapshot API: incident_relations")
        self.assertEqual(tuple(e.original_index for e in snapshot.incident_code_edges("seed")),
                         (0, 2))
        self.assertEqual(tuple(e.original_index for e in snapshot.incident_code_edges("neighbor")),
                         (0, 1))
        for cid in ("component-a", "component-b"):
            self.assertEqual(tuple(e.original_index for e in snapshot.incident_relations(cid)), (0,))
        for method in (snapshot.incident_code_edges, snapshot.incident_relations):
            self.assertEqual(method("unknown"), ())
            for invalid in (None, True, 1.0, "", "x" * 513):
                with self.subTest(method=method.__name__, invalid_type=type(invalid).__name__):
                    with self.assertRaises(AgentError) as caught:
                        method(invalid)
                    self.assertEqual(caught.exception.code, "invalid_input")
                    self.assertEqual(str(caught.exception), str(AgentError("invalid_input")))

    def test_accessor_detachment_immutability_and_failed_publication(self):
        from assistant_tool_fixtures import make_index_case

        case = make_index_case()
        self.assertTrue(hasattr(case.snapshot, "code_edge_inventory"),
                        "Missing B5 snapshot API: code_edge_inventory")
        before = case.snapshot.code_edge_inventory
        case.analysis.flow.graph.edges.clear()
        case.analysis.flow.graph.nodes.clear()
        case.analysis.symbol_index.entries.clear()
        self.assertEqual(len(before), 3)
        self.assertEqual(case.snapshot.incident_code_edges("seed"), (before[0], before[2]))
        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            before[0].source = "changed"
        with self.assertRaises(TypeError):
            before[0] = before[1]
        # A rejected independent build must not replace or mutate a published snapshot.
        api = importlib.import_module("codeviz.assistant.snapshot")
        with self.assertRaises(AgentError) as caught:
            api.build_snapshot(fixture(), binding=case.snapshot.binding,
                               limits=api.SnapshotLimits(max_records=1))
        self.assertEqual(caught.exception.code, "budget_exceeded")
        self.assertIs(case.snapshot.code_edge_inventory, before)
        self.assertEqual(case.snapshot.incident_code_edges("seed"), (before[0], before[2]))


if __name__ == "__main__":
    unittest.main()
