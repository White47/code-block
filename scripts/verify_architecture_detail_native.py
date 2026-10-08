"""Accept nanoGPT architecture/details in an existing caller-owned WebView2.

Attach only: Recent -> Analyze -> Overview model card, ordinary UI clicks and
real mouse drags. Never launch/kill/build a host, execute/edit project sources,
clear caches, inject styles, write JS state or invoke graph/write bridge APIs.
The caller must seed the approved project in Recent and supply built assets.
"""

import argparse
import json
import math
import re
import sys
import time
import traceback
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

from verify_general_python_native import NativeAcceptance

# Read only. All coordinates, including sampled SVG paths, are screen pixels.
GEOMETRY = """canvas => {
    const rect = e => { const r = e.getBoundingClientRect();
        return {left:r.left, right:r.right, top:r.top, bottom:r.bottom,
            width:r.width, height:r.height}; };
    const area = rect(canvas);
    const m = new DOMMatrixReadOnly(getComputedStyle(
        canvas.querySelector('.react-flow__viewport')).transform);
    const cards = [...canvas.querySelectorAll('.react-flow__node-code')].map(e => ({
        id:e.getAttribute('data-id'), name:e.querySelector('strong')?.textContent,
        codeName:e.querySelector('.code-node code')?.textContent,
        role:e.querySelector('.code-node')?.getAttribute('data-role'),
        rect:rect(e.querySelector('.code-node')),
        detail:e.getAttribute('data-id').startsWith('island:') }));
    const islands = [...canvas.querySelectorAll('.react-flow__node-island')].map(e =>
        ({id:e.getAttribute('data-id'), rect:rect(e)}));
    const edges = [...canvas.querySelectorAll('.react-flow__edge')].map(e => {
        const p = e.querySelector('path.react-flow__edge-path');
        if (!p) return {id:e.getAttribute('data-id'), error:'missing actual SVG path'};
        const matrix = p.getScreenCTM(), length = p.getTotalLength();
        if (!matrix || !(length > 0)) return {id:e.getAttribute('data-id'),
            error:'unmeasurable actual SVG path'};
        const scale = Math.max(Math.hypot(matrix.a,matrix.b), Math.hypot(matrix.c,matrix.d));
        const count = Math.max(2, Math.ceil(length * scale / .75));
        const points = Array.from({length:count+1}, (_, i) => {
            const q = p.getPointAtLength(length*i/count).matrixTransform(matrix);
            return [q.x,q.y]; });
        return {id:e.getAttribute('data-id'), d:p.getAttribute('d'), length,
            screenSampleStep:.75, points};
    });
    return {canvas:area, viewport:{pan:[m.e,m.f], zoom:m.a}, cards,islands,edges};
}"""


def overlap(a, b):
    return (min(a['right'], b['right']) > max(a['left'], b['left'])
            and min(a['bottom'], b['bottom']) > max(a['top'], b['top']))


def segment_enters(a, b, rect):
    """Open-rectangle slab intersection: touching a boundary is not entering."""
    low, high = 0.0, 1.0
    for index, start, end in ((0, rect['left'], rect['right']),
                              (1, rect['top'], rect['bottom'])):
        delta = b[index] - a[index]
        if delta == 0:
            if not start < a[index] < end:
                return False
            continue
        bounds = sorted(((start - a[index]) / delta, (end - a[index]) / delta))
        low, high = max(low, bounds[0]), min(high, bounds[1])
        if low >= high:
            return False
    return low < high


def endpoint_nodes(point, cards):
    # Handles lie on borders; allow only 2 CSS pixels for stroke/rounding.
    return [card['id'] for card in cards
            if card['rect']['left'] - 2 <= point[0] <= card['rect']['right'] + 2
            and card['rect']['top'] - 2 <= point[1] <= card['rect']['bottom'] + 2]


def route_statistics(edges):
    """Residual sampled polyline crossings/shared spans; never require zero."""
    crossings = shared = 0
    pairs = []

    def cross(a, b):
        return a[0] * b[1] - a[1] * b[0]

    # Coalesce straight sample runs before the pair comparison. This preserves
    # the sampled polyline while avoiding quadratic work on long straight runs.
    def coalesced(points):
        result = []
        for point in points:
            while len(result) >= 2:
                a, b = result[-2:]
                u = (b[0] - a[0], b[1] - a[1])
                v = (point[0] - b[0], point[1] - b[1])
                if abs(cross(u, v)) > 1e-8 or u[0] * v[0] + u[1] * v[1] < 0:
                    break
                result.pop()
            result.append(point)
        return result

    routes = [{**edge, 'points': coalesced(edge['points'])} for edge in edges]
    for first, second in combinations(routes, 2):
        pair_crossings = pair_shared = 0
        for a, b in zip(first['points'], first['points'][1:], strict=False):
            for c, d in zip(second['points'], second['points'][1:], strict=False):
                if (max(a[0], b[0]) < min(c[0], d[0])
                        or max(c[0], d[0]) < min(a[0], b[0])
                        or max(a[1], b[1]) < min(c[1], d[1])
                        or max(c[1], d[1]) < min(a[1], b[1])):
                    continue
                u = (b[0] - a[0], b[1] - a[1])
                v = (d[0] - c[0], d[1] - c[1])
                w = (c[0] - a[0], c[1] - a[1])
                denominator = cross(u, v)
                if abs(denominator) > 1e-8:
                    t, s = cross(w, v) / denominator, cross(w, u) / denominator
                    if 0 < t < 1 and 0 < s < 1:
                        pair_crossings += 1
                elif abs(cross(w, u)) < 1e-8:
                    axis = 0 if abs(u[0]) >= abs(u[1]) else 1
                    extent = min(max(a[axis], b[axis]), max(c[axis], d[axis])) - max(
                        min(a[axis], b[axis]), min(c[axis], d[axis]))
                    if extent > 1e-8:
                        pair_shared += 1
        crossings += pair_crossings
        shared += pair_shared
        if pair_crossings or pair_shared:
            pairs.append({'edges': [first['id'], second['id']],
                          'crossingSegmentPairs': pair_crossings,
                          'sharedSegmentPairs': pair_shared})
    return {'lineCrossingSegmentPairs': crossings, 'sharedPathSegmentPairs': shared,
            'pairs': pairs, 'zeroCrossingRequired': False}


class ArchitectureAcceptance(NativeAcceptance):
    def __init__(self, *args, click_node):
        super().__init__(*args)
        self.click_node = click_node

    def read_bridge(self, method):
        assert method in {'get_session', 'get_view_state'}, 'session/state reads only'
        return super().read_bridge(method)

    def geometry(self):
        return self.page.locator('.graph-editor .react-flow').evaluate(GEOMETRY)

    def settled(self):
        previous = None
        stable = 0
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            geometry = self.geometry()
            signature = json.dumps(geometry, sort_keys=True)
            stable = stable + 1 if signature == previous else 0
            if stable >= 3 and geometry['cards']:
                return geometry
            previous = signature
            self.page.wait_for_timeout(150)
        raise AssertionError('actual canvas geometry did not settle')

    def depth_button(self, fewer=False):
        pattern = r'^(Fewer layers|更少层)' if fewer else r'^(More layers|更多层)'
        return self.page.locator('.query-toolbar').get_by_role('button', name=re.compile(pattern))

    def depth(self):
        text = self.depth_button().inner_text()
        match = re.search(r'·\s*([1-5])/5$', text)
        assert match, text
        return int(match.group(1))

    def assert_depth(self, depth):
        for fewer in (False, True):
            self.expect(self.depth_button(fewer)).to_have_text(re.compile(
                rf'^(More layers|Fewer layers|更多层|更少层)\s*·\s*{depth}/5$'))
        self.expect(self.page.locator('.query-toolbar').get_by_role('button', name=re.compile(
            r'^(Upstream|Downstream|Both|上游|下游|双向)$'))).to_have_count(0)

    def capture(self, stage):
        self.inspection(stage)
        geometry = self.settled()
        self.report.setdefault('geometry', {})[stage] = geometry
        self.screenshot(stage)
        cards = geometry['cards']
        assert cards and all(card['rect']['width'] > 0 and card['rect']['height'] > 0
                             for card in cards), geometry
        overlaps = [[a['id'], b['id']] for a, b in combinations(cards, 2)
                    if overlap(a['rect'], b['rect'])]
        island_overlaps = [[island['id'], card['id']] for island in geometry['islands']
                           for card in cards if not card['detail']
                           and overlap(island['rect'], card['rect'])]
        island_overlaps += [[a['id'], b['id']] for a, b in combinations(geometry['islands'], 2)
                            if overlap(a['rect'], b['rect'])]
        violations = []
        endpoints = []
        endpoint_errors = []
        for edge in geometry['edges']:
            assert 'error' not in edge, edge
            start = endpoint_nodes(edge['points'][0], cards)
            end = endpoint_nodes(edge['points'][-1], cards)
            endpoints.append({'edge': edge['id'], 'startNodes': start, 'endNodes': end})
            if len(start) != 1 or len(end) != 1:
                endpoint_errors.append(endpoints[-1])
            allowed = set(start + end)
            for card in cards:
                if card['id'] in allowed:
                    continue
                if any(segment_enters(a, b, card['rect'])
                       for a, b in zip(edge['points'], edge['points'][1:], strict=False)):
                    violations.append({'edge': edge['id'], 'node': card['id']})
        main_edges = [edge for edge in geometry['edges'] if not edge['id'].startswith('island:')]
        toolbar = self.page.locator('.graph-toolbar small').inner_text()
        counts = re.search(r'(\d+)\s+(?:nodes|节点)\s*·\s*(\d+)\s+(?:edges|关系)', toolbar)
        assert counts, toolbar
        assert int(counts.group(1)) == len([c for c in cards if not c['detail']]), toolbar
        assert int(counts.group(2)) == len(main_edges), ('real relations must not disappear', toolbar)
        evidence = {'nodeOverlaps': overlaps, 'islandOverlaps': island_overlaps,
                    'thirdPartyInteriorViolations': violations, 'endpoints': endpoints,
                    'endpointErrors': endpoint_errors,
                    'edgeCount': len(geometry['edges']), 'mainEdgeCount': len(main_edges),
                    'toolbar': toolbar, **route_statistics(geometry['edges'])}
        self.report.setdefault('routing', {})[stage] = evidence
        assert not endpoint_errors, evidence
        assert not overlaps, evidence
        assert not island_overlaps, evidence
        assert not violations, evidence
        return geometry

    def node(self, code_name, detail=False):
        selector = ('.react-flow__node-code[data-id^="island:"]' if detail else
                    '.react-flow__node-code:not([data-id^="island:"])')
        nodes = self.page.locator(selector).filter(has=self.page.locator(
            '.code-node code', has_text=re.compile(rf'^{re.escape(code_name)}$')))
        self.expect(nodes).to_have_count(1)
        return nodes

    def select(self, node, stage):
        self.click_node(self.page, node, stage, self.report['nodeInteractions'])
        code_name = node.locator('.code-node code').inner_text()
        self.expect(self.page.locator('.inspector h2')).to_have_text(node.locator('strong').inner_text())
        self.expect(self.page.locator('.inspector-content > code:not(.member-id)')).to_have_text(code_name)
        self.expect(self.page.locator('.inspector [aria-live="polite"]')).to_have_count(0)
        return code_name

    def open_detail(self, code_name):
        node = self.node(code_name)
        self.select(node, f'detail-{code_name}')
        before = self.page.locator('.detail-island').count()
        self.page.locator('.inspector-actions').get_by_role('button', name=re.compile(
            r'^(Open detail island|展开细节岛)$')).click()
        self.expect(self.page.locator('.detail-island')).to_have_count(before + 1)
        return self.capture(f'detail-{before + 1}')

    def close_details(self):
        while self.page.locator('.detail-island').count():
            count = self.page.locator('.detail-island').count()
            button = self.page.get_by_role('button', name='Close detail island', exact=True).last
            self.click_node(self.page, button, 'close-detail-island', self.report['nodeInteractions'])
            self.expect(self.page.locator('.detail-island')).to_have_count(count - 1)

    def assert_local_data(self, code_name):
        data = self.page.locator('.inspector dl').inner_text()
        self.report.setdefault('localInspectorData', []).append({'codeName': code_name, 'data': data})
        assert not re.search(r'model\.GPT\.(?:forward|lm_head)|logits|targets|token_ids', data), data

    def source_roundtrip(self, node, stage, declaration=None):
        code_name = self.select(node, stage)
        depth = self.depth()
        before = self.settled()
        state = self.read_bridge('get_view_state')
        anchor = self.page.locator('.inspector .anchor-card').first
        self.expect(anchor).to_be_visible()
        match = re.fullmatch(r'(.+):(\d+)–(\d+)', anchor.locator('code').inner_text().strip())
        assert match, anchor.inner_text()
        filename, line, end = match.group(1), int(match.group(2)), int(match.group(3))
        path = (self.project / filename).resolve()
        assert path.is_relative_to(self.project), path
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        assert 1 <= line <= end <= len(lines), (filename, line, end)
        if declaration:
            assert any(declaration in text for text in lines[line - 1:end]), (code_name, match.group(0))
        self.open_source('.primary-editor', filename, line, lines[line - 1])
        self.screenshot(f'{stage}-source')
        self.report.setdefault('sourceEvidence', []).append({'codeName': code_name, 'file': filename,
            'startLine': line, 'endLine': end, 'highlightText': lines[line - 1],
            'anchorText': lines[line - 1:end]})
        self.page.locator('.tab.active .close-tab').click()
        self.expect(self.page.locator('.graph-editor')).to_be_visible()
        self.assert_depth(depth)
        after = self.capture(f'{stage}-returned')
        assert [e['id'] for e in before['edges']] == [e['id'] for e in after['edges']], 'source lost edges'
        restored = self.read_bridge('get_view_state')
        for key in ('positions', 'positionsByView', 'detailIslands', 'graphRoots', 'selectedId'):
            assert restored.get(key) == state.get(key), (key, state, restored)
        # Normalize DOM screen rectangles to canvas coordinates to distinguish
        # actual position persistence from a harmless viewport restoration.
        def positions(geometry):
            viewport, area = geometry['viewport'], geometry['canvas']
            return {item['id']: [(item['rect'][axis] - area[axis] - viewport['pan'][index])
                                 / viewport['zoom'] for index, axis in enumerate(('left', 'top'))]
                    for item in geometry['cards'] + geometry['islands']}
        old, new = positions(before), positions(after)
        assert old.keys() == new.keys(), (old, new)
        assert all(math.dist(old[key], new[key]) < 1 for key in old), (old, new)
        self.checked(f'{stage}: actual anchor highlight/close, depth, edge IDs and positions retained')

    def drag(self, locator, island=False):
        if island:
            title = locator.locator('.island-heading span')
            self.click_node(self.page, title, 'before-user-island-drag', self.report['nodeInteractions'])
            self.expect(title).to_be_visible()
            box = title.bounding_box()
        else:
            self.select(locator, 'before-user-node-drag')
            box = locator.locator('strong').bounding_box()
        assert box, 'drag target has no actual DOM bounds'
        x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        hit = locator.evaluate('(e,p) => e.contains(document.elementFromPoint(p.x,p.y))', {'x': x, 'y': y})
        assert hit, 'drag target is obstructed; no forced interaction permitted'
        area = self.geometry()['canvas']
        assert area['left'] < x < area['right'] and area['top'] < y < area['bottom'], box
        dx = 24 if x + 24 < area['right'] - 10 else -24
        dy = 16 if y + 16 < area['bottom'] - 10 else -16
        state = self.read_bridge('get_view_state')
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        try:
            self.page.mouse.move(x + dx, y + dy, steps=16)
        finally:
            self.page.mouse.up()
        identifier = locator.get_attribute('data-id')
        def saved(current):
            if island:
                old = next(i for i in state['detailIslands'] if 'island:' + i['id'] == identifier)
                new = next(i for i in current['detailIslands'] if 'island:' + i['id'] == identifier)
                return new['position'] is not None and new['position'] != old['position']
            return (current.get('positionsByView', {}).get('Architecture', {}).get(identifier)
                    != state.get('positionsByView', {}).get('Architecture', {}).get(identifier))
        moved = self.wait_bridge('get_view_state', saved, 'real mouse drag persisted')
        self.report.setdefault('mouseDrags', []).append({'id': identifier, 'island': island,
            'start': [x, y], 'delta': [dx, dy], 'stateAfter': moved})

    def explicit_navigation_after_local_source(self):
        """A new same-root model choice must beat an old source-return selection."""
        head = self.node('model.GPT.lm_head')
        self.select(head, 'explicit-navigation-old-head')
        head_id = head.get_attribute('data-id')
        before = self.wait_bridge('get_view_state', lambda state: state.get('selectedId') == head_id,
                                  'main head selection persisted before local source')
        local = self.node('model.GPT.transformer[h]', True)
        self.select(local, 'explicit-navigation-local-h')
        anchor = self.page.locator('.inspector .anchor-card').first
        self.expect(anchor).to_be_visible()
        match = re.fullmatch(r'(.+):(\d+)–(\d+)', anchor.locator('code').inner_text().strip())
        assert match, anchor.inner_text()
        filename, line = match.group(1), int(match.group(2))
        path = (self.project / filename).resolve()
        assert path.is_relative_to(self.project), path
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        self.open_source('.primary-editor', filename, line, lines[line - 1])
        self.page.get_by_role('tab', name=re.compile(r'^(Overview|项目概览)$')).click()
        from verify_native_desktop import overview_choice

        overview_choice(self.page, 'models', 'model.GPT').click()
        root_id = self.node('model.GPT').get_attribute('data-id')
        after = self.wait_bridge('get_view_state', lambda state: state.get('selectedId') == root_id,
                                 'explicit GPT root replaces old head after Overview')
        assert root_id != head_id, (root_id, head_id)
        assert after['graphRoots'] == before['graphRoots'], (before, after)
        for key in ('positions', 'positionsByView', 'detailIslands'):
            assert after.get(key) == before.get(key), (key, before, after)
        self.assert_depth(1)
        self.capture('explicit-same-root-navigation')
        # Closing the now-inactive source tab must not re-arm its old selection.
        source = self.page.get_by_role('tab', name=Path(filename).name, exact=True)
        self.expect(source).to_have_count(1)
        source.locator('..').locator('.close-tab').click()
        self.expect(source).to_have_count(0)
        self.page.wait_for_timeout(600)
        final = self.read_bridge('get_view_state')
        assert final['selectedId'] == root_id, (after, final)
        for key in ('graphRoots', 'positions', 'positionsByView', 'detailIslands'):
            assert final.get(key) == after.get(key), (key, after, final)
        self.report['explicitNavigation'] = {'previousMainId': head_id, 'newMainId': root_id,
                                             'before': before, 'after': final}
        self.checked('local Source -> Overview -> same-root model choice honors new selection, not old head')

    def run(self):
        self.activity('overview')
        self.page.locator('.view-link').filter(has_text=re.compile(r'^(Overview|项目概览)')).click()
        self.inspection('recent')
        wanted = str(self.project).replace('\\', '/').casefold()
        recents = self.page.locator('.recent-project')
        matches = [recents.nth(i) for i in range(recents.count())
                   if (recents.nth(i).get_attribute('title') or '').replace('\\', '/').casefold() == wanted]
        assert len(matches) == 1, f'Caller must seed exactly one actual Recent entry: {self.project}'
        self.open_project_ui(self.project, 'actual-recent')
        self.analyze_ui()
        self.overview()
        from verify_native_desktop import overview_choice

        overview_choice(self.page, 'models', 'model.GPT').click()
        self.assert_depth(2)
        initial = self.capture('initial-depth-2')
        main = [c for c in initial['cards'] if not c['detail']]
        assert not initial['islands'], 'Caller must provide a clean detail-island context'
        initial_ids = {c['id'] for c in main}
        for name in ('wte', 'wpe', 'drop', 'h', 'ln_f'):
            card = next(c for c in main if c['codeName'] == f'model.GPT.transformer[{name}]')
            assert overlap(card['rect'], initial['canvas']), ('initial major component offscreen', card)
        self.checked('actual Recent -> Analyze -> Overview GPT model card starts at 2 with major members')
        root = self.open_detail('model.GPT')
        template = 'model.GPT.transformer[h][*]'
        detail = [c for c in root['cards'] if c['detail']]
        block = next(c for c in detail if c['codeName'] == template)
        state = self.read_bridge('get_view_state')
        island = next(i for i in state['detailIslands'] if 'island:' + i['id'] + ':' in block['id'])
        occurrence_id = block['id'].removeprefix('island:' + island['id'] + ':')
        assert occurrence_id not in initial_ids, ('root detail merely copied initial main', block)
        self.source_roundtrip(self.node(template, True), 'root-block-template', 'Block(config)')
        self.close_details()
        previous = initial_ids
        for depth in (3, 2, 1):
            self.depth_button(depth < self.depth()).click()
            self.assert_depth(depth)
            current = self.capture(f'actual-depth-{depth}')
            ids = {c['id'] for c in current['cards'] if not c['detail']}
            assert (previous < ids if depth == 3 else ids < previous), (previous, ids)
            previous = ids
        self.expect(self.depth_button(True)).to_be_disabled()
        self.page.wait_for_timeout(1200)
        self.assert_depth(1)
        self.checked('actual buttons 2 -> 3 -> 2 -> 1 and stays 1 without automatic bounce')
        local = self.open_detail('model.GPT.transformer')
        self.assert_local_data('model.GPT.transformer')
        local_cards = [c for c in local['cards'] if c['detail']]
        assert len(local_cards) > 1, local_cards
        assert not any(c['codeName'] == 'model.GPT.lm_head' or c['role'] == 'DataNode'
                       and (c['codeName'] or '').startswith('model.GPT.') for c in local_cards), local_cards
        assert {c['codeName'] for c in local_cards} != {c['codeName'] for c in local['cards'] if not c['detail']}
        self.source_roundtrip(self.node('model.GPT.transformer[h]', True), 'transformer-local-h', 'Block(config)')
        # Local island selection is transient, not the saved main selection.
        # Re-select through the rendered card after the source tab roundtrip.
        self.select(self.node('model.GPT.transformer[h]', True), 'transformer-h-data-after-source')
        self.assert_local_data('model.GPT.transformer[h]')
        # Current frontend allows detail opening from main cards only. Expand via
        # actual layer controls until h/Block is a selectable main occurrence.
        self.close_details()
        for code_name, required in (('model.GPT.transformer[h]', template),
                                    (template, 'model.Block.attn')):
            while not self.page.locator('.react-flow__node-code:not([data-id^="island:"])').filter(
                    has=self.page.locator('.code-node code', has_text=re.compile(
                        rf'^{re.escape(code_name)}$'))).count():
                assert self.depth() < 5, f'No source-main drill occurrence by depth 5: {code_name}'
                self.expect(self.depth_button()).to_be_enabled()
                target = self.depth() + 1
                self.depth_button().click()
                self.assert_depth(target)
                self.capture(f'drill-main-depth-{target}')
            graph = self.open_detail(code_name)
            children = [c for c in graph['cards'] if c['detail']]
            assert any(c['codeName'] == required for c in children), (code_name, children)
            assert not any(c['codeName'] == 'model.GPT.lm_head' or c['role'] == 'DataNode'
                           and (c['codeName'] or '').startswith('model.GPT.') for c in children), children
            target = self.node(required, True)
            self.select(target, f'local-drill-{required}')
            self.assert_local_data(required)
            self.source_roundtrip(target, 'local-drill-' + ('h' if code_name.endswith('[h]') else 'Block'))
            self.close_details()
        while self.depth() > 1:
            target = self.depth() - 1
            self.depth_button(True).click()
            self.assert_depth(target)
        self.open_detail('model.GPT.transformer')
        self.drag(self.node('model.GPT.lm_head'))
        self.capture('user-node-dragged')
        # Real background pan from the shared helper also brings the island's
        # first card into view before dragging its rendered header.
        self.select(self.node('model.GPT.transformer', True), 'island-header-pan')
        self.drag(self.page.locator('.react-flow__node-island'), island=True)
        self.capture('user-island-dragged')
        self.source_roundtrip(self.node('model.GPT.transformer[h]', True), 'user-positions-roundtrip')
        self.page.wait_for_timeout(1200)
        self.assert_depth(1)
        self.expect(self.depth_button(True)).to_be_disabled()
        self.capture('final-depth-1')
        self.checked('local transformer/h/Block drill excludes unrelated head/root data; user drags survive source')
        self.explicit_navigation_after_local_source()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True, type=int, help='Existing native host CDP port')
    parser.add_argument('--project', required=True, type=Path, help='Approved existing nanoGPT project (static only)')
    parser.add_argument('--output', type=Path, help='Report directory under repository build/')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    build = Path(__file__).resolve().parents[1] / 'build'
    project = args.project.resolve()
    output = (args.output or build / 'architecture-detail-native' / f'{project.name}-{args.port}').resolve()
    if not output.is_relative_to(build.resolve()):
        parser.error('--output must be under repository build/')
    output.mkdir(parents=True, exist_ok=True)
    report = {'startedAt': datetime.now(UTC).isoformat(), 'status': 'failed', 'attachOnly': True,
              'project': str(project), 'port': args.port, 'output': str(output), 'checks': [],
              'nodeInteractions': [], 'errors': [], 'pageErrors': [], 'consoleErrors': [],
              'limitations': ['UI acceptance must be run separately on caller-built assets.',
                  'SVG is sampled every <=0.75 screen px; curved sub-sample excursions are not an analytic proof.',
                  'Cross/shared-path statistics count coalesced sampled segment pairs, not unique topological crossings.',
                  'DOM edge count is compared with rendered main toolbar and preserved across source; no get_view used.',
                  'Caller must seed Recent and provide a context without restored detail islands.',
                  'Static source reading only; no runtime tensor/config/repeat-count inference.']}
    page = None
    try:
        assert project.is_dir() and (project / 'model.py').is_file(), 'approved existing model.py required'
        source = (project / 'model.py').read_text(encoding='utf-8-sig')
        assert 'class GPT(' in source and 'class Block(' in source, 'nanoGPT source contract missing'
        from playwright.sync_api import expect, sync_playwright
        from verify_native_desktop import click_canvas_node

        with sync_playwright() as playwright:
            # Stop only the local Playwright transport on exit. Never browser.close:
            # the caller owns the native window and its CDP host lifecycle.
            browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{args.port}', timeout=20_000)
            pages = [p for context in browser.contexts for p in context.pages
                     if p.locator('.desktop-shell').count()]
            assert len(pages) == 1, 'expected exactly one actual CodeViz desktop page'
            page = pages[0]
            page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
            page.on('console', lambda message: report['consoleErrors'].append(message.text)
                    if message.type == 'error' else None)
            acceptance = ArchitectureAcceptance(page, project, output, report, expect, click_node=click_canvas_node)
            try:
                acceptance.run()
                assert not report['pageErrors'], report['pageErrors']
                assert not report['consoleErrors'], report['consoleErrors']
                report['status'] = 'passed'
            except Exception:
                if not page.is_closed():
                    try:
                        acceptance.screenshot('failure')
                        (output / 'failure.html').write_text(page.content(), encoding='utf-8')
                    except Exception as evidence_error:
                        report['errors'].append(f'Failure evidence: {evidence_error}')
                raise
    except Exception as error:
        report['errors'].append(f'{type(error).__name__}: {error}')
        report['traceback'] = traceback.format_exc()
    finally:
        report['finishedAt'] = datetime.now(UTC).isoformat()
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'status': report['status'], 'report': str(output / 'report.json'),
                          'errors': report['errors']}, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
