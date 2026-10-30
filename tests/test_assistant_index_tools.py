"""B5 R1/R3--R7: strict, authorized, complete and I/O-free index queries."""

import builtins
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import patch

from assistant_tool_fixtures import (
    add_source_less,
    index_case_with,
    make_index_case,
    make_source_case,
    require_api,
)

from codeviz.analysis.python import ParsedFile
from codeviz.analysis.symbols import SymbolIndexEntry
from codeviz.assistant.contracts import AgentError
from codeviz.assistant.snapshot import AnchorRecord, build_snapshot
from codeviz.assistant.source_excerpts import ExcerptRequest, SourceExcerptReader
from codeviz.assistant.source_policy import SourceExcerptError
from codeviz.core import (
    CodeEdge,
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


class IndexToolsTests(unittest.TestCase):
    def setUp(self):
        contracts = require_api(self, "tool_contracts", "SearchRequest", "EntityRef",
                                "RelationsRequest", "RelationKey")
        tools = require_api(self, "index_tools", "search_symbols", "get_local_relations")
        self.modules = contracts, tools

    def api(self):
        return self.modules

    def search(self, case, query="", **kwargs):
        dto, api = self.api()
        return api.search_symbols(case.snapshot, case.consent, dto.SearchRequest(query, **kwargs),
                                  checkpoint=lambda: None)

    def relations(self, case, seed="seed", kind="code", **kwargs):
        dto, api = self.api()
        return api.get_local_relations(case.snapshot, case.consent,
                                       dto.RelationsRequest(dto.EntityRef(kind, seed), **kwargs),
                                       checkpoint=lambda: None)

    def rejected(self, action, code="invalid_input"):
        with self.assertRaises(AgentError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), str(AgentError(code)))
        self.assertNotIn("private-sentinel", str(caught.exception))

    def test_r1_strict_dto_types_ranges_and_frozen_results(self):
        dto, _ = self.api()
        invalid = (
            lambda: dto.SearchRequest(True), lambda: dto.SearchRequest("x" * 2001),
            lambda: dto.SearchRequest("private-sentinel\n"),
            lambda: dto.SearchRequest("\ud800"),
            lambda: dto.EntityRef("invalid", "private-sentinel"),
            lambda: dto.EntityRef("code", ""), lambda: dto.EntityRef("code", "x" * 513),
            lambda: dto.RelationKey("invalid", 0), lambda: dto.RelationKey("code", -1),
        )
        for ordinal, action in enumerate(invalid):
            with self.subTest(invalid=ordinal):
                self.rejected(action)
        seed = dto.EntityRef("code", "seed")
        for value in (True, 1.0, 0, 21):
            with self.subTest(limit=value):
                self.rejected(lambda value=value: dto.SearchRequest("", limit=value))
        for field, values in (("hops", (True, 1.0, -1, 3)),
                              ("max_nodes", (True, 1.0, 0, 51)),
                              ("max_edges", (True, 1.0, 0, 101))):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.rejected(lambda field=field, value=value: dto.RelationsRequest(seed, **{field: value}))
        for value in (True, 1.0):
            self.rejected(lambda value=value: dto.RelationKey("code", value))
        self.rejected(lambda: dto.RelationsRequest(dto.EntityRef("symbol", "s0000")))
        result = self.search(make_index_case())
        self.assertIs(type(result.results), tuple)
        self.assertFalse(hasattr(result, "__dict__"))
        with self.assertRaises((AttributeError, TypeError)):
            result.total_count = 99
        forbidden = {"metadata", "evidence", "possible_targets", "code_symbol_ids", "children"}
        for hit in result.results:
            self.assertFalse(forbidden & {f.name for f in fields(hit)})
        # Native B4 receipts, before ToolSession exists: body is not metadata.
        for raw in (b'def f():\r\n\treturn 1\r\n',
                    'value = "a\fb\u0085c"\nother = r"\\x01"\n'.encode('utf-8')):
            with self.subTest(source_body=raw), tempfile.TemporaryDirectory() as root:
                case = make_source_case(Path(root), raw)
                receipt = case.reader.read_excerpt(ExcerptRequest(
                    AnchorRecord('app.py', 1, None, 2, None)))
                expected = raw.decode('utf-8').replace('\r\n', '\n').rstrip('\n')
                self.assertEqual(receipt.text, expected)
                self.assertEqual(receipt.returned_anchor,
                                 AnchorRecord('app.py', 1, 0, 2,
                                              len(expected.split('\n')[1].encode('utf-8'))))
                self.assertFalse(receipt.truncated)
                try:
                    projected = dto.ExcerptToolResult(receipt.evidence_id, receipt.relative_path,
                                                      receipt.text, receipt.returned_anchor,
                                                      receipt.truncated)
                except AgentError as error:
                    self.fail('Real B4 source body rejected by DTO: ' + error.code)
                self.assertEqual((projected.text, projected.returned_anchor, projected.truncated),
                                 (receipt.text, receipt.returned_anchor, receipt.truncated))
        anchor = AnchorRecord('app.py', 1, 0, 1, 1)
        class StringSubclass(str):
            pass
        for body, returned in ((None, anchor), (b'x', anchor), (StringSubclass('x'), anchor),
                               ('\ud800', anchor), ('x' * 4001, anchor),
                               ('x', AnchorRecord('app.py', 1, 0, 201, 0))):
            with self.subTest(invalid_body=repr(body)[:30], anchor=returned):
                self.rejected(lambda body=body, returned=returned:
                              dto.ExcerptToolResult('e', 'app.py', body, returned, False))
        for body, returned in (('😀' * 4000, anchor),
                               ('x\n' * 199 + 'x', AnchorRecord('app.py', 1, 0, 200, 1))):
            with self.subTest(body_boundary=len(body)):
                try:
                    self.assertEqual(dto.ExcerptToolResult('e', 'app.py', body, returned, False).text, body)
                except AgentError as error:
                    self.fail('Valid source-body boundary rejected: ' + error.code)
        for control in ('\n', '\t', '\f', '\u0085', '\x01'):
            with self.subTest(metadata_control=repr(control)):
                self.rejected(lambda control=control: dto.EntityRef('code', 'id' + control))
                self.rejected(lambda control=control:
                              dto.ExcerptToolResult('e' + control, 'app.py', 'x', anchor, False))
                with self.assertRaises((AgentError, SourceExcerptError)) as caught:
                    dto.ExcerptToolResult('e', 'app' + control + '.py', 'x', anchor, False)
                self.assertEqual(caught.exception.code, 'invalid_input')

    def test_r3_full_scan_topk_rank_definition_and_stable_total_order(self):
        case = make_index_case(symbol_count=1101)
        result = self.search(case, "late-match")
        self.assertEqual(tuple(hit.ref.id for hit in result.results), ("s1100",))
        self.assertEqual((result.total_count, result.truncated), (1, False))
        case = make_index_case(symbol_count=21)
        result = self.search(case)
        self.assertEqual((len(result.results), result.total_count, result.truncated), (20, 21, True))
        self.assertTrue(all(hit.ref.kind == "symbol" and hit.rank == 0 for hit in result.results))

        def configure(analysis):
            entries = analysis.symbol_index.entries
            for sid, name in zip(tuple(entries), ("needle", "needle-prefix", "has-needle"), strict=True):
                entries[sid] = replace(entries[sid], name=name, qualified_name="app." + name)
            entries["reference"] = replace(entries["s0000"], id="reference", definition=False)

        case = index_case_with(configure)
        result = self.search(case, " NEEDLE ")
        self.assertEqual(tuple((h.ref.id, h.rank) for h in result.results),
                         (("s0000", 0), ("s0001", 1), ("s0002", 2)))
        self.assertEqual(result.total_count, 3)

        def reversed_input(analysis):
            configure(analysis)
            entries = analysis.symbol_index.entries
            analysis.symbol_index.entries = dict(reversed(tuple(entries.items())))
            graph = analysis.flow.graph
            graph.nodes = dict(reversed(tuple(graph.nodes.items())))
            semantic = analysis.semantic.graph
            return replace(analysis, semantic=SemanticAnalysis(replace(semantic, components=semantic.components[::-1])))

        self.assertEqual(result, self.search(index_case_with(reversed_input), "needle"))

    def test_r3_seven_fixed_chinese_english_role_aliases(self):
        aliases = (("BackwardPass", "反向传播", "backward"),
                   ("ParameterUpdate", "参数更新", "optimizer step"),
                   ("ForwardPass", "前向传播", "forward pass"),
                   ("LossCalculation", "损失计算", "loss calculation"),
                   ("ClearGradients", "清空梯度", "zero grad"),
                   ("EpochLoop", "轮次循环", "epoch loop"),
                   ("BatchLoop", "批次循环", "batch loop"))
        for role, chinese, english in aliases:
            with self.subTest(role=role):
                def configure(analysis, role=role):
                    graph = analysis.semantic.graph
                    return replace(analysis, semantic=SemanticAnalysis(replace(
                        graph, components=tuple(replace(c, role=role) for c in graph.components))))
                case = index_case_with(configure)
                left, right = self.search(case, chinese), self.search(case, english)
                self.assertEqual(tuple(h.ref for h in left.results), tuple(h.ref for h in right.results))
                self.assertEqual(tuple(h.ref.id for h in left.results), ("component-a", "component-b"))
                self.assertTrue(all(h.confidence == Confidence.PROBABLE for h in left.results))

    def test_r4_permissions_precede_rank_count_caps_and_component_projection(self):
        self.api()
        baseline = self.search(make_index_case(symbol_count=21), "s")

        def hidden_matches(analysis):
            anchor = SourceAnchor("denied.py", 1, 0, 1, 5)
            for ordinal in range(100):
                sid = f"hidden{ordinal:03d}"
                analysis.symbol_index.add_entry(SymbolIndexEntry(
                    sid, "s-hidden", "app.s-hidden", NodeKind.FUNCTION, "private",
                    "denied.py", anchor, None))

        self.assertEqual(baseline, self.search(index_case_with(hidden_matches, symbol_count=21), "s"))
        for mode in ("denied", "ignored", "protected", "missing-hash", "case-alias"):
            with self.subTest(mode=mode):
                path = "secrets.py" if mode == "protected" else "app.py"

                def configure(analysis, path=path, mode=mode):
                    if path != "app.py":
                        analysis = replace(analysis, files=analysis.files + (ParsedFile(path, "secrets", None, 10,
                                                                      parsed_success=True,
                                                                      content_hash="a" * 64),))
                        for sid, entry in tuple(analysis.symbol_index.entries.items()):
                            analysis.symbol_index.entries[sid] = replace(
                                entry, file_path=path,
                                source_anchor=SourceAnchor(path, 1, 0, 1, 5))
                    if mode == "missing-hash":
                        analysis = replace(analysis, files=tuple(replace(f, content_hash=None)
                                               if f.relative_path == "app.py" else f
                                               for f in analysis.files))
                    return analysis

                case = index_case_with(configure)
                binding = replace(case.snapshot.binding, ignore_paths=("app.py",)) if mode == "ignored" else case.snapshot.binding
                if mode == "ignored":
                    case.snapshot = build_snapshot(case.analysis, binding=binding)
                allowed = () if mode == "denied" else (("App.py",) if mode == "case-alias" else (path,))
                case.consent = replace(case.consent, binding=binding, allowed_files=allowed)
                if mode == "case-alias":
                    # Rejecting the entire malformed authorization is also conservative.
                    try:
                        result = self.search(case)
                    except (AgentError, SourceExcerptError) as error:
                        self.assertEqual(error.code, "invalid_input")
                        continue
                else:
                    result = self.search(case)
                self.assertEqual((result.results, result.total_count, result.truncated), ((), 0, False))
        for mode in ("multi-file", "missing-code", "children-only", "code-denied"):
            with self.subTest(component=mode):
                def configure(analysis, mode=mode):
                    graph = analysis.semantic.graph
                    component = graph.components[0]
                    updates = {
                        "multi-file": dict(source_anchors=component.source_anchors + (SourceAnchor("denied.py", 1, 0, 1, 5),)),
                        "missing-code": dict(code_symbol_ids=("missing-code",)),
                        "children-only": dict(source_anchors=(), code_symbol_ids=(), child_component_ids=("component-b",)),
                        "code-denied": dict(code_symbol_ids=("opaque",)),
                    }[mode]
                    if mode == "code-denied":
                        add_source_less(analysis, NodeOrigin.EXTERNAL)
                    return replace(analysis, semantic=SemanticAnalysis(replace(
                        graph, components=(replace(component, **updates), graph.components[1]))))
                result = self.search(index_case_with(configure), "component-a")
                self.assertEqual((result.results, result.total_count, result.truncated), ((), 0, False))
        # B3 cannot publish anchored failed-file facts: test the actual accepted boundary.
        case = make_index_case()
        case.analysis = replace(case.analysis, files=tuple(replace(f, parsed_success=False)
                                    if f.relative_path == "app.py" else f for f in case.analysis.files))
        self.rejected(lambda: build_snapshot(case.analysis, binding=case.snapshot.binding))

    def test_r5_unknown_source_less_and_true_origin_preservation(self):
        self.api()
        unknown = self.relations(make_index_case(), "unknown")
        self.assertEqual((unknown.status, unknown.nodes, unknown.relations, unknown.truncated),
                         ("unavailable", (), (), False))
        for origin in (NodeOrigin.EXTERNAL, NodeOrigin.SYNTHETIC):
            with self.subTest(origin=origin):
                case = index_case_with(lambda a, origin=origin: add_source_less(a, origin))
                self.assertEqual(self.relations(case, "opaque"), unknown)
                self.assertNotIn("opaque", repr(self.relations(case)))

                def backed(analysis, origin=origin):
                    node = analysis.flow.graph.nodes["seed"]
                    analysis.flow.graph.nodes["seed"] = replace(node, origin=origin,
                                                                 confidence=Confidence.PROBABLE)
                result = self.relations(index_case_with(backed), hops=0)
                self.assertEqual((result.nodes[0].origin, result.nodes[0].confidence),
                                 (origin, Confidence.PROBABLE))
                self.assertEqual(result.relations, ())

    def test_r6_bfs_last_allowed_edge_hops_direction_cycles_and_candidates(self):
        case = make_index_case(degree=1101)
        result = self.relations(case)
        self.assertEqual(tuple(n.ref.id for n in result.nodes), ("seed", "neighbor", "tail"))
        self.assertEqual(tuple(e.key.original_index for e in result.relations), (1100, 1101))
        self.assertFalse(result.truncated)
        for hops, ids in ((0, ("seed",)), (1, ("seed", "neighbor")),
                          (2, ("seed", "neighbor", "tail"))):
            with self.subTest(hops=hops):
                result = self.relations(make_index_case(), hops=hops)
                self.assertEqual(tuple(n.ref.id for n in result.nodes), ids)
                self.assertFalse(result.truncated)
                refs = {n.ref for n in result.nodes}
                self.assertTrue(all(e.source in refs and e.target in refs for e in result.relations))
                if hops == 0:
                    self.assertEqual(result.relations, ())
                else:
                    self.assertEqual(sum(e.source == e.target for e in result.relations), 1)
        incoming = self.relations(make_index_case(), "tail", hops=1)
        self.assertEqual(tuple(n.ref.id for n in incoming.nodes), ("tail", "neighbor"))
        self.assertEqual((incoming.relations[0].source.id, incoming.relations[0].target.id),
                         ("neighbor", "tail"))

        def candidates(analysis):
            edge = analysis.flow.graph.edges[0]
            analysis.flow.graph.edges[:] = [replace(edge, possible_targets=("tail",)), edge]
            graph = analysis.semantic.graph
            return replace(analysis, semantic=SemanticAnalysis(replace(graph, relations=(), components=(
                replace(graph.components[0], child_component_ids=("component-b",)), graph.components[1]))))
        case = index_case_with(candidates)
        result = self.relations(case)
        self.assertEqual(tuple(n.ref.id for n in result.nodes), ("seed", "neighbor"))
        self.assertEqual(len(result.relations), 2)
        self.assertEqual({e.key.original_index for e in result.relations}, {0, 1})
        self.assertEqual(len(self.relations(case, "component-a", "semantic").nodes), 1)

    def test_r6_independent_node_and_parallel_edge_caps_and_deadline(self):
        self.api()
        anchor = SourceAnchor("app.py", 1, 0, 1, 5)

        def many_nodes(analysis):
            graph = analysis.flow.graph
            graph.edges.clear()
            for ordinal in range(101):
                nid = f"allowed{ordinal:03d}"
                graph.add_node(CodeNode(nid, NodeKind.FUNCTION, nid, "app." + nid,
                                        "app", "python", (anchor,)))
                graph.edges.append(CodeEdge("seed", nid, EdgeType.CALLS, source_anchor=anchor))
        result = self.relations(index_case_with(many_nodes))
        self.assertEqual((len(result.nodes), len(result.relations), result.truncated), (50, 49, True))
        self.assertEqual(tuple(n.ref.id for n in result.nodes[1:]),
                         tuple(f"allowed{i:03d}" for i in range(49)))

        def parallel(analysis):
            analysis.flow.graph.edges[:] = [analysis.flow.graph.edges[0]] * 101
        result = self.relations(index_case_with(parallel))
        self.assertEqual((len(result.nodes), len(result.relations), result.truncated), (2, 100, True))
        self.assertEqual(tuple(e.key.original_index for e in result.relations), tuple(range(100)))
        dto, api = self.api()
        # Deliberately expensive sorted oracle is test-only; query must stream.
        for kind in ('code', 'semantic'):
            for reverse in (False, True):
                def dense(analysis, kind=kind, reverse=reverse):
                    ids = ['seed'] + [f'allowed{i:03d}' for i in range(60)]
                    denied = [f'denied{i:04d}' for i in range(1101)]
                    pairs = [('seed', nid) for nid in denied + ids[1:]]
                    pairs += [('seed', 'allowed000')] * 120
                    pairs += [('allowed000', 'seed'), ('seed', 'seed'),
                              ('allowed000', 'allowed001'), ('allowed001', 'allowed000')]
                    if reverse:
                        pairs.reverse()
                    if kind == 'code':
                        graph = analysis.flow.graph
                        graph.nodes.clear()
                        for nid in ids + denied:
                            a = anchor if nid in ids else SourceAnchor('denied.py', 1, 0, 1, 5)
                            graph.add_node(CodeNode(nid, NodeKind.FUNCTION, nid, nid,
                                                    'app', 'python', (a,)))
                        graph.edges[:] = [CodeEdge(left, right, EdgeType.CALLS, source_anchor=anchor)
                                          for left, right in pairs]
                        return analysis
                    components = tuple(SemanticComponent(
                        nid, nid, nid, 'BackwardPass', Confidence.PROBABLE, (), (), (),
                        (anchor if nid in ids else SourceAnchor('denied.py', 1, 0, 1, 5),))
                        for nid in ids + denied)
                    relations = tuple(SemanticRelation(left, right, EdgeType.GRADIENT_FLOW,
                                                       Confidence.PROBABLE, ('static evidence',), (anchor,))
                                      for left, right in pairs)
                    return replace(analysis, semantic=SemanticAnalysis(SemanticGraph(components, relations)))
                case = index_case_with(dense)
                inventory = (case.snapshot.code_edge_inventory if kind == 'code'
                             else case.snapshot.relation_inventory)
                allowed = [e for e in inventory if not e.source.startswith('denied')
                           and not e.target.startswith('denied')]
                for hops, node_cap, edge_cap in ((0, 50, 100), (1, 50, 100),
                                                 (2, 50, 100), (2, 3, 2)):
                    with self.subTest(oracle=kind, reverse=reverse, hops=hops,
                                      node_cap=node_cap, edge_cap=edge_cap):
                        distances, frontier, truncated = {'seed': 0}, ['seed'], False
                        for depth in range(1, hops + 1):
                            candidates = sorted({endpoint for e in allowed
                                                 if e.source in frontier or e.target in frontier
                                                 for endpoint in (e.source, e.target)
                                                 if endpoint not in distances})
                            slots = node_cap - len(distances)
                            truncated |= len(candidates) > slots
                            frontier = candidates[:slots]
                            distances.update((nid, depth) for nid in frontier)
                        edges = sorted((e for e in allowed
                                        if e.source in distances and e.target in distances
                                        and min(distances[e.source], distances[e.target]) < hops),
                                       key=lambda e: (max(distances[e.source], distances[e.target]),
                                                      e.source, e.target, e.type.value, e.original_index))
                        truncated |= len(edges) > edge_cap
                        result = self.relations(case, kind=kind, hops=hops,
                                                max_nodes=node_cap, max_edges=edge_cap)
                        self.assertEqual(tuple((n.ref.id, n.distance) for n in result.nodes),
                                         tuple(sorted(distances.items(), key=lambda item: (item[1], item[0]))))
                        self.assertEqual(tuple((e.key.original_index, e.source.id, e.target.id)
                                               for e in result.relations),
                                         tuple((e.original_index, e.source, e.target) for e in edges[:edge_cap]))
                        self.assertEqual(result.truncated, truncated)
                with self.subTest(streaming=kind, reverse=reverse):
                    large_calls = []
                    def observed_sort(values, *args, large_calls=large_calls, **kwargs):
                        values = builtins.tuple(values)
                        if len(values) >= 1101:
                            large_calls.append(('sorted', len(values)))
                        return builtins.sorted(values, *args, **kwargs)
                    class ObservedTuple(builtins.tuple):
                        def __new__(cls, values=(), *, large_calls=large_calls):
                            values = builtins.tuple(values)
                            if len(values) >= 1101:
                                large_calls.append(('tuple', len(values)))
                            return values
                    with patch.object(api, 'sorted', observed_sort, create=True), \
                         patch.object(api, 'tuple', ObservedTuple, create=True):
                        self.relations(case, kind=kind)
                    self.assertEqual(large_calls, [], 'Query copied/sorted a complete high-degree incident collection')
                request = dto.RelationsRequest(dto.EntityRef(kind, 'seed'))
                calls = [0]
                def count_check(calls=calls):
                    calls[0] += 1
                api.get_local_relations(case.snapshot, case.consent, request, checkpoint=count_check)
                for stop in (2, calls[0]):
                    for code in ('cancelled', 'timeout'):
                        with self.subTest(interrupt=kind, reverse=reverse, stop=stop, code=code):
                            seen = [0]
                            def interrupt(seen=seen, stop=stop, code=code):
                                seen[0] += 1
                                if seen[0] == stop:
                                    raise AgentError(code)
                            self.rejected(lambda case=case, request=request, interrupt=interrupt:
                                          api.get_local_relations(
                                case.snapshot, case.consent, request, checkpoint=interrupt), code)
                            self.assertEqual(seen[0], stop)
        case = make_index_case(symbol_count=1101, degree=1101)
        for query in (lambda check: api.search_symbols(case.snapshot, case.consent,
                                                      dto.SearchRequest("late-match"), checkpoint=check),
                      lambda check: api.get_local_relations(case.snapshot, case.consent,
                                                            dto.RelationsRequest(dto.EntityRef("code", "seed")),
                                                            checkpoint=check)):
            calls = [0]
            def checkpoint(calls=calls):
                calls[0] += 1
                if calls[0] == 2:
                    raise AgentError("timeout")
            with self.subTest(query=query):
                self.rejected(lambda query=query: query(checkpoint), "timeout")
                self.assertEqual(calls[0], 2)

    def test_r7_pure_queries_touch_no_io_metadata_ast_or_desktop_ports(self):
        dto, api = self.api()
        case = make_index_case()
        # Imported ports are patched after module loading; publication has already occurred.
        def forbidden(*args, **kwargs):
            raise AssertionError("Pure index query crossed an excluded boundary")
        with ExitStack() as stack:
            for target in ("builtins.open", "io.open", "os.stat", "os.scandir",
                           "socket.socket", "subprocess.Popen", "codeviz.search.search_project",
                           "codeviz.navigation.plan_reveal", "codeviz.views.builder.build_visible_graph"):
                stack.enter_context(patch(target, side_effect=forbidden))
            for method in ("stat", "open", "read_bytes", "read_text"):
                stack.enter_context(patch.object(Path, method, side_effect=forbidden))
            stack.enter_context(patch.object(SourceExcerptReader, "read_excerpt", side_effect=forbidden))
            stack.enter_context(patch("codeviz.assistant.source_excerpts.read_source_bytes", side_effect=forbidden))
            # Poison the old mutable inputs; querying the snapshot must never revisit them.
            class Excluded:
                def __getattribute__(self, name):
                    forbidden()
            case.analysis = replace(case.analysis, symbol_index=Excluded(), flow=Excluded(), semantic=Excluded())
            search = api.search_symbols(case.snapshot, case.consent, dto.SearchRequest(""),
                                         checkpoint=lambda: None)
            relations = api.get_local_relations(case.snapshot, case.consent,
                                                dto.RelationsRequest(dto.EntityRef("code", "seed")),
                                                checkpoint=lambda: None)
        self.assertEqual(search.total_count, 3)
        self.assertEqual(len(relations.nodes), 3)
        self.assertEqual(case.ledger.usage.tool_calls, 0)


if __name__ == "__main__":
    unittest.main()
