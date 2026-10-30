"""Calls projection retains static facts without executing analyzed source."""
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.test_training import analyze_files

from codeviz.assistant.tool_contracts import EntityRef
from codeviz.core import Confidence, EdgeType, NodeKind, NodeOrigin, ViewKind
from codeviz.persistence.store import CacheStore
from codeviz.ui.assistant_context import ViewNodeKey
from codeviz.ui.controller import AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.presenter import build_inspector
from codeviz.ui.web_bridge import DesktopBridge
from codeviz.views import ViewRequest, ViewState, build_visible_graph


class CallsViewTests(unittest.TestCase):
    def _calls_bridge(self, root: Path, source: str):
        (root / 'run.py').write_text(source, encoding='utf-8')
        controller = AppController()
        controller.open_project(root)
        controller.analyze()
        bridge = DesktopBridge(
            controller, CacheStore(root / '.cache'), PreferencesStore(root / '.preferences'))
        self.assertTrue(bridge.analyze()['ok'])
        symbols = {node.name: node.id for node in controller.analysis.flow.graph.nodes.values()
                   if node.kind is NodeKind.FUNCTION}
        return controller, bridge, symbols

    def test_real_calls_are_projected(self):
        analysis = analyze_files({"run.py": "def target(): return 1\ndef caller(): return target()\nraise RuntimeError('AST only')\n"})
        facts = [edge for edge in analysis.flow.graph.edges if edge.type is EdgeType.CALLS]
        self.assertTrue(facts)
        view = build_visible_graph(analysis, ViewRequest(ViewKind.CALLS))
        shown = [edge for edge in view.edges if edge.type is EdgeType.CALLS]
        self.assertEqual({(e.source, e.target, e.confidence, (e.source_anchor,)) for e in facts},
                         {(e.source, e.target, e.confidence, e.source_anchors) for e in shown})
        visible_ids = {node.id for node in view.nodes}
        self.assertFalse(any(graph_node.kind in (NodeKind.FLOW_STEP, NodeKind.CONTROL_BLOCK)
                             for node_id, graph_node in analysis.flow.graph.nodes.items()
                             if node_id in visible_ids))
        for fact in facts:
            site = analysis.flow.graph.nodes[fact.source]
            owner = site.metadata['scope_symbol_id']
            self.assertTrue(any(edge.source == owner and edge.target == fact.source
                                and edge.type is EdgeType.CONTAINS
                                for edge in view.edges))
        self.assertTrue(all(not edge.code_edge_ids for edge in view.edges))
        self.assertTrue(all(node.code_node_ids == (node.id,) for node in view.nodes))

    def test_focus_directions_hops_selection_and_cycles(self):
        analysis = analyze_files({"run.py": "def a(): return b()\ndef b(): return c()\ndef c(): return c()\n"})
        symbols = {n.name: n.id for n in analysis.flow.graph.nodes.values()
                   if n.kind is NodeKind.FUNCTION}
        def targets(**kwargs):
            view = build_visible_graph(analysis, ViewRequest(ViewKind.CALLS, **kwargs))
            return view, {e.target for e in view.edges if e.type is EdgeType.CALLS}
        one, actual = targets(root_id=symbols['a'], direction='downstream')
        self.assertEqual(actual, {symbols['b']})
        two, actual = targets(root_id=symbols['a'], direction='downstream', hops=2)
        self.assertEqual(actual, {symbols['b'], symbols['c']})
        _, actual = targets(root_id=symbols['b'], direction='upstream')
        self.assertEqual(actual, {symbols['b']})
        _, actual = targets(root_id=symbols['b'], direction='both')
        self.assertEqual(actual, {symbols['b'], symbols['c']})
        overview, _ = targets()
        selected, _ = targets(selected_id=symbols['c'])
        self.assertEqual(overview.nodes, selected.nodes)
        self.assertEqual(overview.edges, selected.edges)
        self.assertEqual(selected.root_ids, ())
        for invalid in ('missing', 'project:fake', 'projection:fake'):
            with self.assertRaises(ValueError):
                targets(root_id=invalid)
        with self.assertRaises(ValueError):
            targets(root_id=symbols['a'], selected_id=symbols['c'], direction='downstream')
        for hops in range(1, 6):
            cyclic, _ = targets(root_id=symbols['c'], hops=hops, direction='downstream')
            self.assertEqual(len(cyclic.edges), 2)
        self.assertLess(two.layout_hints[symbols['a']][1], two.layout_hints[symbols['c']][1])
        self.assertGreater(one.layout_hints[symbols['b']][1], one.layout_hints[symbols['a']][1])

    def test_tight_focus_budget_keeps_first_hop_before_earlier_source_second_hop(self):
        analysis = analyze_files({'run.py':
                                  'def c(): return 1\ndef b(): return c()\ndef x(): return 2\n'
                                  'def a():\n    b()\n    x()\n'})
        graph = analysis.flow.graph
        symbols = {node.name: node.id for node in graph.nodes.values()
                   if node.kind is NodeKind.FUNCTION}
        first_hop = next(edge for edge in graph.edges
                         if edge.type is EdgeType.CALLS and edge.target == symbols['b'])
        second_hop = next(edge for edge in graph.edges
                          if edge.type is EdgeType.CALLS and edge.target == symbols['c'])
        self.assertLess(second_hop.source_anchor.start_line, first_hop.source_anchor.start_line)

        view = build_visible_graph(analysis, ViewRequest(
            ViewKind.CALLS, root_id=symbols['a'], direction='downstream', hops=2, max_nodes=5))

        self.assertEqual({(edge.source, edge.target) for edge in view.edges
                          if edge.type is EdgeType.CALLS},
                         {(first_hop.source, first_hop.target)})
        self.assertTrue(any(edge.type is EdgeType.CONTAINS and edge.source == symbols['a']
                            and edge.target == first_hop.source for edge in view.edges))
        self.assertNotIn(second_hop.source, {node.id for node in view.nodes})
        self.assertLessEqual(len(view.nodes), 5)
        self.assertTrue(view.query_context['budget_truncated'])

    def test_verified_parent_chains_and_fail_closed_traceability(self):
        source = "def target(): return 1\ndef caller(flag):\n    if flag:\n        return target()\n"
        for corruption in ('none', 'owner', 'missing_contains', 'edge_anchor', 'site_kind', 'target_anchor', 'missing_scope', 'mismatched_id', 'mismatched_anchor', 'cycle'):
            analysis = analyze_files({'run.py': source})
            graph = analysis.flow.graph
            call = next(e for e in graph.edges if e.type is EdgeType.CALLS)
            site = graph.nodes[call.source]
            if corruption == 'owner':
                graph.nodes[site.id] = replace(site, metadata={'scope_symbol_id': call.target})
            elif corruption == 'missing_contains':
                graph.edges[:] = [e for e in graph.edges if not
                                  (e.type is EdgeType.CONTAINS and e.target == site.id)]
            elif corruption == 'edge_anchor':
                graph.edges[graph.edges.index(call)] = replace(call, source_anchor=None)
            elif corruption == 'missing_scope':
                graph.nodes[site.id] = replace(site, metadata={})
            elif corruption == 'mismatched_id':
                graph.nodes[site.id] = replace(site, id='forged')
            elif corruption == 'mismatched_anchor':
                graph.edges[graph.edges.index(call)] = replace(call, source_anchor=graph.nodes[call.target].source_anchors[0])
            elif corruption == 'cycle':
                parent = next(e for e in graph.edges if e.type is EdgeType.CONTAINS and e.target == site.id)
                graph.edges.append(replace(parent, source=site.id, target=parent.source))
            elif corruption == 'site_kind':
                graph.nodes[site.id] = replace(site, kind=NodeKind.FLOW_STEP)
            elif corruption == 'target_anchor':
                graph.nodes[call.target] = replace(graph.nodes[call.target],
                                                  source_anchors=(), origin=NodeOrigin.EXTERNAL)
            view = build_visible_graph(analysis, ViewRequest(ViewKind.CALLS))
            if corruption != 'none':
                self.assertEqual(view.edges, ())
                self.assertEqual(len(view.issues), 1)
                self.assertEqual(view.issues[0].code, 'CALLS_TRACEABILITY_UNAVAILABLE')
            else:
                self.assertTrue(view.edges)
                shown_call = next(e for e in view.edges if e.type is EdgeType.CALLS)
                self.assertEqual((shown_call.source, shown_call.target, shown_call.confidence,
                                  shown_call.source_anchors),
                                 (call.source, call.target, call.confidence, (call.source_anchor,)))
                owner = graph.nodes[site.id].metadata['scope_symbol_id']
                self.assertTrue(any(e.source == owner and e.target == site.id
                                    and e.type is EdgeType.CONTAINS for e in view.edges))

    def test_uncertain_facts_deduplicate_only_full_fact_and_do_not_expand_candidates(self):
        analysis = analyze_files({'run.py': 'def t(): return 1\ndef candidate(): return 2\ndef caller(): return t()\n'})
        graph = analysis.flow.graph
        call = next(e for e in graph.edges if e.type is EdgeType.CALLS)
        candidate = next(n.id for n in graph.nodes.values() if n.name == 'candidate')
        probable = replace(call, confidence=Confidence.PROBABLE, possible_targets=(candidate,))
        graph.edges.extend((call, probable))
        view = build_visible_graph(analysis, ViewRequest(ViewKind.CALLS))
        self.assertEqual(len([e for e in view.edges if e.type is EdgeType.CALLS]), 2)
        self.assertNotIn(candidate, {n.id for n in view.nodes})

    def test_overview_and_detail_budgets_aggregates_and_empty(self):
        source = 'def target(): return 1\ndef caller():\n' + '    target()\n' * 150
        analysis = analyze_files({'run.py': source})
        root = next(n.id for n in analysis.flow.graph.nodes.values() if n.name == 'caller')
        for nodes, edges in ((100, 300), (18, 40), (1, 1), (4, 1), (999, 999)):
            view = build_visible_graph(analysis, ViewRequest(ViewKind.CALLS, root_id=root,
                                                            max_nodes=nodes, max_edges=edges))
            ids = {n.id for n in view.nodes}
            self.assertLessEqual(len(ids), min(nodes, 100))
            self.assertLessEqual(len(view.edges), min(edges, 300))
            self.assertTrue(all(e.source in ids and e.target in ids for e in view.edges))
            self.assertTrue(view.query_context['budget_truncated'])
            self.assertIn(root, ids)
            if nodes >= 3:
                self.assertTrue(any(edge.type is EdgeType.CALLS for edge in view.edges))
            for aggregate in view.aggregations:
                node = next(n for n in view.nodes if n.id == aggregate.id)
                self.assertEqual(node.member_ids, aggregate.member_ids)
                self.assertLessEqual(len(node.member_ids), 20)
                self.assertEqual(node.metadata['hidden_count'], view.hidden_counts['nodes'])
                self.assertFalse(any(aggregate.id in (e.source, e.target) for e in view.edges))
        empty = build_visible_graph(analyze_files({'run.py': 'value = 1\n'}), ViewRequest(ViewKind.CALLS))
        self.assertEqual((empty.nodes, empty.edges, empty.root_ids), ((), (), ()))

    def test_controller_details_persistence_inspector_and_real_c3_binding(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'run.py').write_text(
                'def target(): return 1\ndef caller(): return target()\n'
                'def other_target(): return 2\ndef other(): return other_target()\n'
                'raise RuntimeError("AST only")\n', encoding='utf-8')
            controller = AppController()
            controller.open_project(root)
            controller.analyze()
            view = controller.view(ViewKind.CALLS)
            call = next(e for e in view.edges if e.type is EdgeType.CALLS)
            inspector = build_inspector(view, call.source, controller.analysis)
            self.assertIn('Static call sites and static targets; not a runtime execution trace', inspector.evidence)
            self.assertTrue(inspector.can_open_source)
            self.assertIn(EdgeType.CALLS, {r.type for r in inspector.relations})
            detail = controller.detail_view(ViewKind.CALLS, 'unrelated-main-root', call.source)
            self.assertEqual(detail.root_ids, (call.source,))
            self.assertLessEqual(len(detail.nodes), 18)
            self.assertLessEqual(len(detail.edges), 40)
            focused = controller.view(ViewKind.CALLS, call.source)
            foreign = next(edge.source for edge in view.edges
                           if edge.type is EdgeType.CALLS
                           and edge.source not in {node.id for node in focused.nodes})
            self.assertFalse(controller.valid_identity(foreign, ViewKind.CALLS, call.source))
            unused = next(n.id for n in controller.analysis.flow.graph.nodes.values()
                          if n.id not in {v.id for v in view.nodes})
            self.assertFalse(controller.valid_identity(unused, ViewKind.CALLS))
            controller.view_state = ViewState(active_view=ViewKind.CALLS, selected_id=unused,
                                             graph_roots={'Calls': unused},
                                             positions_by_view={'Calls': {unused: (1., 2.)}})
            controller.sanitize_view_state()
            self.assertIsNone(controller.view_state.selected_id)
            self.assertIsNone(controller.view_state.graph_roots['Calls'])
            self.assertEqual(controller.view_state.positions_by_view['Calls'], {})
            with self.assertRaises(ValueError):
                controller.save_layout(ViewKind.CALLS, unused, (), {'Calls': None})
            expected = controller.capture_assistant_context()
            self.assertTrue(controller._assistant_publish_view(expected, view, ViewKind.CALLS, None))
            current = controller.capture_assistant_context()
            for node in view.nodes:
                key = ViewNodeKey(ViewKind.CALLS, None, None, node.id)
                self.assertIn(EntityRef('code', node.id), current.context_map[key].targets)
            expected = current
            self.assertTrue(controller._assistant_publish_view(expected, detail, ViewKind.CALLS, None, call.source))
            current = controller.capture_assistant_context()
            self.assertIn(ViewNodeKey(ViewKind.CALLS, None, call.source, call.source), current.context_map)

    def test_visible_aggregate_selection_is_valid_without_refocusing(self):
        analysis = analyze_files({'run.py': 'def t(): return 1\ndef f():\n' + '    t()\n' * 120})
        view = build_visible_graph(analysis, ViewRequest(ViewKind.CALLS))
        aggregate = view.aggregations[0]
        selected = build_visible_graph(analysis, ViewRequest(ViewKind.CALLS, selected_id=aggregate.id))
        self.assertEqual(selected.nodes, view.nodes)
        self.assertEqual(selected.root_ids, ())

    def test_bridge_delivers_real_calls_details_inspector_and_rejects_explicit_bad_ids(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'run.py').write_text(
                'def c(): return 1\ndef b(): return c()\ndef a(): return b()\n'
                'def foreign_target(): return 2\ndef foreign(): return foreign_target()\n',
                encoding='utf-8')
            controller = AppController()
            controller.open_project(root)
            controller.analyze()
            bridge = DesktopBridge(controller, CacheStore(root / '.cache'), PreferencesStore(root / '.preferences'))
            self.assertTrue(bridge.analyze()['ok'])
            symbols = {node.name: node.id for node in controller.analysis.flow.graph.nodes.values()
                       if node.kind is NodeKind.FUNCTION}
            response = bridge.get_view('Calls', root_id=symbols['a'], hops=2,
                                       direction='downstream')
            self.assertTrue(response['ok'], response)
            delivered_ids = {node['id'] for node in response['data']['nodes']}
            self.assertIn(symbols['c'], delivered_ids)
            self.assertIn(ViewNodeKey(ViewKind.CALLS, symbols['a'], None, symbols['c']),
                          controller.capture_assistant_context().context_map)
            saved = bridge.save_layout({
                'activeView': 'Calls', 'selectedId': symbols['c'], 'openTabs': [],
                'graphRoots': {'Calls': symbols['a']},
            })
            self.assertTrue(saved['ok'], saved)
            self.assertTrue(bridge.get_inspector('Calls', symbols['a'], symbols['c'])['ok'])
            self.assertTrue(bridge.get_detail_view('Calls', symbols['a'], symbols['c'])['ok'])
            self.assertIn(ViewNodeKey(ViewKind.CALLS, symbols['a'], symbols['c'], symbols['c']),
                          controller.capture_assistant_context().context_map)
            self.assertFalse(controller.valid_identity(
                symbols['foreign_target'], ViewKind.CALLS, symbols['a']))
            foreign = bridge.save_layout({
                'activeView': 'Calls', 'selectedId': symbols['foreign_target'], 'openTabs': [],
                'graphRoots': {'Calls': symbols['a']},
            })
            self.assertFalse(foreign['ok'])
            self.assertEqual(foreign['error']['code'], 'INVALID_REQUEST')
            for kwargs in ({'root_id': 'missing'}, {'selected_id': 'missing'}):
                rejected = bridge.get_view('Calls', **kwargs)
                self.assertFalse(rejected['ok'])
                self.assertEqual(rejected['error']['code'], 'INVALID_REQUEST')

    def test_two_hop_downstream_scope_remains_available_to_bridge_consumers(self):
        with TemporaryDirectory() as temporary:
            controller, bridge, symbols = self._calls_bridge(
                Path(temporary), 'def c(): return 1\ndef b(): return c()\ndef a(): return b()\n')

            response = bridge.get_view(
                'Calls', root_id=symbols['a'], hops=2, direction='downstream')

            self.assertTrue(response['ok'], response)
            self.assertIn(symbols['c'], {node['id'] for node in response['data']['nodes']})
            self.assertTrue(bridge.get_inspector('Calls', symbols['a'], symbols['c'])['ok'])
            self.assertIn(ViewNodeKey(ViewKind.CALLS, symbols['a'], None, symbols['c']),
                          controller.capture_assistant_context().context_map)

    def test_two_hop_upstream_scope_remains_available_to_bridge_consumers(self):
        with TemporaryDirectory() as temporary:
            _controller, bridge, symbols = self._calls_bridge(
                Path(temporary), 'def c(): return 1\ndef b(): return c()\ndef a(): return b()\n')

            response = bridge.get_view(
                'Calls', root_id=symbols['c'], hops=2, direction='upstream')

            self.assertTrue(response['ok'], response)
            self.assertIn(symbols['a'], {node['id'] for node in response['data']['nodes']})
            self.assertTrue(bridge.get_inspector('Calls', symbols['c'], symbols['a'])['ok'])

    def test_last_calls_scope_rejects_a_valid_node_outside_that_scope(self):
        with TemporaryDirectory() as temporary:
            controller, bridge, symbols = self._calls_bridge(
                Path(temporary),
                'def c(): return 1\ndef b(): return c()\ndef a(): return b()\n'
                'def foreign_target(): return 2\ndef foreign(): return foreign_target()\n')
            self.assertTrue(bridge.get_view(
                'Calls', root_id=symbols['a'], hops=1, direction='downstream')['ok'])

            inspector = bridge.get_inspector(
                'Calls', symbols['a'], symbols['foreign_target'])
            saved = bridge.save_layout({
                'activeView': 'Calls', 'selectedId': symbols['foreign_target'], 'openTabs': [],
                'graphRoots': {'Calls': symbols['a']},
            })

            self.assertFalse(controller.valid_identity(
                symbols['foreign_target'], ViewKind.CALLS, symbols['a']))
            self.assertFalse(inspector['ok'])
            self.assertFalse(saved['ok'])

    def test_project_switch_clears_the_last_calls_scope(self):
        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            controller, bridge, symbols = self._calls_bridge(
                Path(first), 'def c(): return 1\ndef b(): return c()\ndef a(): return b()\n')
            self.assertTrue(bridge.get_view(
                'Calls', root_id=symbols['a'], hops=2, direction='downstream')['ok'])

            opened = bridge.open_project(str(Path(second)))

            self.assertTrue(opened['ok'], opened)
            self.assertIsNone(controller._calls_query)

    def test_reanalysis_clears_the_last_calls_scope(self):
        with TemporaryDirectory() as temporary:
            controller, bridge, symbols = self._calls_bridge(
                Path(temporary), 'def c(): return 1\ndef b(): return c()\ndef a(): return b()\n')
            self.assertTrue(bridge.get_view(
                'Calls', root_id=symbols['a'], hops=2, direction='downstream')['ok'])

            analyzed = bridge.analyze()

            self.assertTrue(analyzed['ok'], analyzed)
            self.assertIsNone(controller._calls_query)
            self.assertFalse(bridge.get_inspector('Calls', symbols['a'], symbols['c'])['ok'])

    def test_detail_view_does_not_replace_the_last_main_calls_scope(self):
        with TemporaryDirectory() as temporary:
            controller, bridge, symbols = self._calls_bridge(
                Path(temporary), 'def c(): return 1\ndef b(): return c()\ndef a(): return b()\n')
            self.assertTrue(bridge.get_view(
                'Calls', root_id=symbols['a'], hops=2, direction='downstream')['ok'])
            main_scope = controller._calls_query

            detail = bridge.get_detail_view('Calls', symbols['a'], symbols['c'])

            self.assertTrue(detail['ok'], detail)
            self.assertEqual(controller._calls_query, main_scope)
            saved = bridge.save_layout({
                'activeView': 'Calls', 'selectedId': symbols['c'], 'openTabs': [],
                'graphRoots': {'Calls': symbols['a']},
            })
            self.assertTrue(saved['ok'], saved)

    def test_module_lexical_external_unresolved_and_real_control_parent_chain(self):
        source = ('import math\ndef t(): return 1\nt()\n'
                  'values = [t() for x in range(2)]\n'
                  'def f(flag):\n    if flag:\n        math.sqrt(2)\n        missing()\n')
        analysis = analyze_files({'run.py': source})
        graph = analysis.flow.graph
        view = build_visible_graph(analysis, ViewRequest(ViewKind.CALLS))
        kinds = {n.role for n in view.nodes}
        self.assertTrue({'Module', 'LexicalScope', 'ExternalSymbol', 'UnresolvedSymbol'} <= kinds)
        self.assertFalse({'FlowStep', 'ControlBlock'} & kinds)
        # One real control parent proves ownership but is not projected as a main node.
        site = next(e.source for e in graph.edges if e.type is EdgeType.CALLS)
        parent_edge = next(e for e in graph.edges if e.type is EdgeType.CONTAINS and e.target == site)
        owner = graph.nodes[site].metadata['scope_symbol_id']
        block = replace(graph.nodes[parent_edge.source], id='verified-control', kind=NodeKind.CONTROL_BLOCK)
        graph.add_node(block)
        graph.edges.remove(parent_edge)
        graph.add_edge(replace(parent_edge, source=block.id))
        graph.add_edge(replace(parent_edge, source=owner, target=block.id))
        chained = build_visible_graph(analysis, ViewRequest(ViewKind.CALLS))
        self.assertNotIn(block.id, {n.id for n in chained.nodes})
        self.assertTrue(any(edge.source == owner and edge.target == site
                            and edge.type is EdgeType.CONTAINS for edge in chained.edges))

        # A second intermediate exceeds the strict ownership contract and fails closed.
        second = replace(block, id='second-control')
        graph.add_node(second)
        owner_edge = next(e for e in graph.edges if e.source == owner and e.target == block.id)
        graph.edges.remove(owner_edge)
        graph.add_edge(replace(owner_edge, source=owner, target=second.id))
        graph.add_edge(replace(owner_edge, source=second.id, target=block.id))
        rejected = build_visible_graph(analysis, ViewRequest(ViewKind.CALLS))
        self.assertFalse(any(edge.source == site and edge.type is EdgeType.CALLS
                             for edge in rejected.edges))
        self.assertEqual(rejected.issues[0].code, 'CALLS_TRACEABILITY_UNAVAILABLE')

    def test_edge_only_truncation_has_bounded_aggregate(self):
        analysis = analyze_files({'run.py': 'def t(): return 1\ndef f(): return t()\n'})
        graph = analysis.flow.graph
        call = next(e for e in graph.edges if e.type is EdgeType.CALLS)
        graph.edges.append(replace(call, confidence=Confidence.PROBABLE))
        view = build_visible_graph(analysis, ViewRequest(ViewKind.CALLS, max_edges=2))
        self.assertEqual(view.hidden_counts['nodes'], 0)
        self.assertEqual(view.hidden_counts['edges'], 1)
        self.assertEqual(len(view.aggregations), 1)
        self.assertEqual(view.aggregations[0].member_ids, (call.source,))
