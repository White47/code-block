"""Assert canvas recovery in a caller-owned native WebView2 host.

Attach only (default CDP port 9302). Open the own-written MVP fixture through
Recent Projects, then Analyze through UI. Never launch/kill a host, run fixture
code, click Fit, or mutate browser state through JavaScript/bridge calls.
"""

import argparse
import json
import re
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from verify_general_python_native import NativeAcceptance

DOM_GEOMETRY = """canvas => {
    const rect = element => {
        const r = element.getBoundingClientRect();
        return {x:r.x, y:r.y, width:r.width, height:r.height,
                left:r.left, right:r.right, top:r.top, bottom:r.bottom};
    };
    const area = rect(canvas);
    const editor = canvas.closest('.primary-editor');
    const editorRect = rect(editor);
    const style = getComputedStyle(editor);
    const px = value => parseFloat(value) || 0;
    const visibleChrome = [...editor.querySelectorAll(
        '.static-flow-note,.query-toolbar,.graph-toolbar')].filter(e =>
        e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
    // Measure against the real editor, not the possibly collapsed wrapper.
    // Inspector/source panes are siblings, already excluded from editor width.
    const left = Math.max(0, editorRect.left + px(style.borderLeftWidth) + px(style.paddingLeft));
    const right = Math.min(innerWidth, editorRect.right - px(style.borderRightWidth) - px(style.paddingRight));
    const top = Math.max(0, editorRect.top + px(style.borderTopWidth) + px(style.paddingTop),
        ...visibleChrome.map(e => rect(e).bottom + px(getComputedStyle(e).marginBottom)));
    const bottom = Math.min(innerHeight, editorRect.bottom - px(style.borderBottomWidth) - px(style.paddingBottom));
    const available = {left, right, top, bottom, width:right-left, height:bottom-top};
    const intersects = r => r.width > 0 && r.height > 0 &&
        r.right > area.left && r.left < area.right &&
        r.bottom > area.top && r.top < area.bottom;
    const matrix = new DOMMatrixReadOnly(getComputedStyle(
        canvas.querySelector('.react-flow__viewport')).transform);
    const cards = [...canvas.querySelectorAll('.react-flow__node-code')].map(e => {
        const r = rect(e.querySelector('.code-node'));
        return {id:e.getAttribute('data-id'), name:e.querySelector('strong')?.textContent,
                codeName:e.querySelector('code')?.textContent, rect:r,
                intersects:intersects(r), detail:e.getAttribute('data-id').startsWith('island:')};
    });
    const islands = [...canvas.querySelectorAll('.react-flow__node-island')].map(e => {
        const r = rect(e); return {id:e.getAttribute('data-id'), rect:r,
                                  intersects:intersects(r)};
    });
    return {canvas:area, editor:editorRect, available,
            chrome:visibleChrome.map(e => ({class:e.className, rect:rect(e)})), cards, islands,
            viewport:{pan:[matrix.e, matrix.f], zoom:matrix.a}};
}"""


def assert_canvas_geometry(geometry):
    """Require actual visible canvas to fill the editor after rendered chrome."""
    canvas, available = geometry['canvas'], geometry['available']
    tolerance = 8  # CSS pixels: allow borders, fractional WebView2 scaling and gaps.
    evidence = f'canvas must fill available editor after toolbar/static note: {geometry}'
    assert available['height'] > 0 and available['width'] > 0, evidence
    assert canvas['height'] >= min(300, available['height'] - tolerance), evidence
    for edge in ('left', 'right', 'top', 'bottom'):
        assert abs(canvas[edge] - available[edge]) <= tolerance, evidence


def viewport_equal(first, second, tolerance=1.0):
    return (abs(first['zoom'] - second['zoom']) < 0.005
            and all(abs(a - b) < tolerance for a, b in zip(first['pan'], second['pan'], strict=True)))


class CanvasAcceptance(NativeAcceptance):
    def read_bridge(self, method):
        assert method in {'get_session', 'get_view_state'}, 'only read-only session/state API permitted'
        return super().read_bridge(method)

    def geometry(self):
        return self.page.locator('.graph-editor .react-flow').evaluate(DOM_GEOMETRY)

    def wait_geometry(self, predicate, description):
        deadline = time.monotonic() + 15
        last = None
        while time.monotonic() < deadline:
            last = self.geometry()
            self.report['lastGeometry'] = last
            assert_canvas_geometry(last)
            if predicate(last):
                return last
            self.page.wait_for_timeout(100)
        self.report['lastGeometry'] = last
        raise AssertionError(f'{description}; actual DOM rectangles: {last}')

    def stable_viewport(self):
        previous = None
        stable = 0
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            current = self.geometry()['viewport']
            stable = stable + 1 if previous and viewport_equal(previous, current, 0.1) else 0
            if stable >= 3:
                self.wait_bridge('get_view_state', lambda state, expected=current: viewport_equal(
                    state.get('viewportByView', {}).get('Architecture',
                                                         {'pan': [1e9, 1e9], 'zoom': -1}), expected),
                    'viewport persisted from actual UI interaction')
                return current
            previous = current
            self.page.wait_for_timeout(100)
        raise AssertionError('actual DOM viewport did not settle and persist')

    def capture(self, stage):
        self.inspection(stage)
        self.expect(self.page.locator('.error-banner, .inline-error')).to_have_count(0)
        self.report.setdefault('bodyDomText', {})[stage] = self.page.locator('body').inner_text()
        self.report.setdefault('geometry', {})[stage] = self.geometry()
        assert_canvas_geometry(self.report['geometry'][stage])
        self.report.setdefault('viewStates', {})[stage] = self.read_bridge('get_view_state')
        self.screenshot(stage)

    def model(self, name):
        self.overview()
        card = self.page.locator('.overview-card').filter(has=self.page.locator(
            '.card-heading', has_text=re.compile(r'^(Models|模型)\s*\d*\s*$')))
        self.expect(card).to_have_count(1)
        row = card.locator('.entity-row').filter(has_text=name)
        self.expect(row).to_have_count(1)
        row.click()
        self.expect(self.page.locator('.graph-editor')).to_be_visible()
        geometry = self.wait_geometry(lambda geometry: any(card['intersects']
                                                           and card['codeName'] == name
                                                           and not card['detail']
                                                           for card in geometry['cards']),
                                      f'{name} real root must appear without Fit')
        roots = [card['id'] for card in geometry['cards']
                 if card['codeName'] == name and not card['detail']]
        assert len(roots) == 1, (name, geometry)
        root = roots[0]
        state = self.wait_bridge('get_view_state', lambda state:
                                 state.get('activeView') == 'Architecture'
                                 and state.get('graphRoots', {}).get('Architecture') == root,
                                 f'{name} Architecture root')
        assert state['graphRoots']['Architecture'] == root
        self.wait_geometry(lambda geometry: any(card['id'] == root and card['intersects']
                                               and card['codeName'] == name
                                               for card in geometry['cards']),
                           f'{name} root must intersect canvas automatically, without Fit')
        self.stable_viewport()
        return root

    def recent_only(self):
        self.activity('overview')
        self.inspection('initial-native')
        wanted = str(self.project.resolve()).casefold()
        recents = self.page.locator('.recent-project')
        matches = [recents.nth(index) for index in range(recents.count())
                   if (recents.nth(index).get_attribute('title') or '').casefold() == wanted]
        assert len(matches) == 1, f'Host must seed MVP fixture in UI Recent Projects: {self.project}'
        self.expect(matches[0]).to_be_enabled()
        matches[0].click()
        self.expect(matches[0]).to_be_enabled(timeout=45_000)
        self.session_for(self.project)
        self.analyze_ui()
        self.checked('Recent Projects -> Analyze through real UI only')

    def check_layout(self, root):
        geometry = self.geometry()
        assert_canvas_geometry(geometry)
        parent = next(card for card in geometry['cards'] if card['id'] == root)
        peers = []
        for name in ('embedding', 'encoder', 'head'):
            matches = [card for card in geometry['cards'] if not card['detail'] and card['name'] == name]
            assert len(matches) == 1, (name, geometry)
            peers.append(matches[0])
        assert parent['intersects'], geometry
        assert all(parent['rect']['bottom'] <= peer['rect']['top'] + 2 for peer in peers), geometry
        assert max(peer['rect']['top'] for peer in peers) - min(
            peer['rect']['top'] for peer in peers) <= 12, geometry
        ordered = sorted(peers, key=lambda peer: peer['rect']['left'])
        assert all(left['rect']['right'] <= right['rect']['left'] + 2
                   for left, right in zip(ordered, ordered[1:], strict=False)), geometry
        self.capture('classifier-auto-visible-top-bottom-parallel-peers')
        self.checked('Classifier auto-visible; parent above children; peers parallel and nonoverlapping')

    def main_card(self, name):
        cards = self.page.locator('.react-flow__node-code:not([data-id^="island:"])').filter(
            has=self.page.locator('.code-node strong', has_text=re.compile(f'^{re.escape(name)}$')))
        self.expect(cards).to_have_count(1)
        return cards

    def check_detail(self):
        # Do not close a pre-existing island silently and then claim this was new.
        self.expect(self.page.locator('.detail-island')).to_have_count(0)
        encoder = self.main_card('encoder')
        before = self.read_bridge('get_view_state')['detailIslands']
        self.wait_geometry(lambda geometry: any(card['name'] == 'encoder' and not card['detail']
                                               and card['intersects'] for card in geometry['cards']),
                           'encoder card must be clickable within canvas without Fit')
        encoder.dblclick()
        state = self.wait_bridge('get_view_state', lambda state: len(state['detailIslands']) == len(before) + 1,
                                 'new detail island after real double click')
        old_ids = {island['id'] for island in before}
        island = next(island for island in state['detailIslands'] if island['id'] not in old_ids)
        prefix = f"island:{island['id']}"

        def visible(geometry):
            containers = [item for item in geometry['islands'] if item['id'] == prefix]
            cards = [card for card in geometry['cards'] if card['id'].startswith(prefix + ':')]
            return bool(containers and containers[0]['intersects'] and cards
                        and all(card['intersects'] for card in cards))

        self.wait_geometry(visible, 'new detail island and its actual cards must intersect canvas without Fit')
        self.stable_viewport()
        self.capture('new-detail-island-auto-visible')
        self.page.get_by_role('button', name='Close detail island', exact=True).click()
        self.expect(self.page.locator('.detail-island')).to_have_count(0)
        self.wait_bridge('get_view_state', lambda state: not any(
            entry['id'] == island['id'] for entry in state['detailIslands']), 'detail island closed')
        self.checked('double-click new detail island -> all cards auto-visible -> UI close persists')

    def assert_query(self, root, hops, direction):
        # Query settings are App-transient, not part of persisted ViewState.
        # Inspect real controls for them; use the bridge only for root/selection.
        state = self.wait_bridge('get_view_state', lambda state:
                                 state.get('activeView') == 'Architecture'
                                 and state.get('graphRoots', {}).get('Architecture') == root
                                 and state.get('selectedId') == self.query_selection,
                                 'Architecture root and selection retained through UI query change')
        active = self.page.locator('.query-toolbar button.active')
        labels = {'both': r'^(Both|双向)$', 'in': r'^(Upstream|上游)$', 'out': r'^(Downstream|下游)$'}
        self.expect(active).to_have_count(1)
        self.expect(active).to_have_text(re.compile(labels[direction]))
        counter = self.page.locator('.query-toolbar').get_by_role(
            'button', name=re.compile(r'^(More hops|更多层)'))
        self.expect(counter).to_have_count(1)
        self.expect(counter).to_have_text(re.compile(rf'^(More hops|更多层)\s*·\s*{hops}/5$'))
        actual_counter = re.fullmatch(r'(More hops|更多层)\s*·\s*([1-5])/5', counter.inner_text().strip())
        assert actual_counter, counter.inner_text()
        actual_direction = next(key for key, pattern in labels.items()
                                if re.fullmatch(pattern, active.inner_text().strip()))
        self.report.setdefault('queryStates', []).append({
            'domHops': int(actual_counter.group(2)), 'domDirection': actual_direction,
            'toolbarDom': self.page.locator('.query-toolbar').evaluate('element => element.outerHTML'),
            'bodyDomText': self.page.locator('body').inner_text(), 'viewState': state,
        })

    def hop_button(self, fewer=False):
        label = r'^(Fewer hops|更少层)' if fewer else r'^(More hops|更多层)'
        button = self.page.locator('.query-toolbar').get_by_role('button', name=re.compile(label))
        self.expect(button).to_have_count(1)
        return button

    def check_hops(self, root):
        state = self.read_bridge('get_view_state')
        assert 'selectedId' in state, state
        self.query_selection = state['selectedId']
        self.assert_query(root, 1, 'both')
        for hops in range(2, 6):
            self.expect(self.hop_button()).to_be_enabled()
            self.hop_button().click()
            self.assert_query(root, hops, 'both')
        self.capture('hops-5')
        self.expect(self.hop_button()).to_be_disabled()
        for hops in (4, 3):
            self.expect(self.hop_button(True)).to_be_enabled()
            self.hop_button(True).click()
            self.assert_query(root, hops, 'both')
        self.capture('hops-3')
        for hops in (2, 1):
            self.hop_button(True).click()
            self.assert_query(root, hops, 'both')
        self.expect(self.hop_button(True)).to_be_disabled()
        for direction, label in (('out', r'^(Downstream|下游)$'), ('in', r'^(Upstream|上游)$'),
                                 ('both', r'^(Both|双向)$')):
            self.page.locator('.query-toolbar').get_by_role('button', name=re.compile(label)).click()
            self.assert_query(root, 1, direction)
        self.capture('hops-1-direction-restored')
        self.checked('real hops 1 -> 5 -> 3 -> 1; root stable; direction changes retain hops')

    def blank_point(self):
        return self.page.locator('.react-flow').evaluate("""canvas => {
            const r = canvas.getBoundingClientRect();
            for (const fy of [.35,.55,.75,.2]) for (const fx of [.15,.25,.4,.6]) {
                const x = r.left + r.width*fx, y = r.top + r.height*fy;
                const e = document.elementFromPoint(x,y);
                if (e?.closest('.react-flow__pane') &&
                    !e.closest('.react-flow__node,.react-flow__controls,.edge-label,.graph-legend'))
                    return {x,y};
            }
            return null;
        }""")

    def pan(self, distance):
        point = self.blank_point()
        assert point, 'no genuine blank canvas point for a mouse pan'
        area = self.geometry()['canvas']
        end = min(area['right'] - 10, point['x'] + distance)
        assert end - point['x'] > 10, (point, area)
        self.page.mouse.move(point['x'], point['y'])
        self.page.mouse.down()
        try:
            self.page.mouse.move(end, point['y'] + 8, steps=15)
        finally:
            self.page.mouse.up()
        self.stable_viewport()

    def check_zoom(self, root):
        self.stable_viewport()
        before = self.geometry()['viewport']
        controls = self.page.locator('.react-flow__controls')
        controls.locator('.react-flow__controls-zoomin').click()
        after_in = self.stable_viewport()
        assert after_in['zoom'] > before['zoom'], (before, after_in)
        controls.locator('.react-flow__controls-zoomout').click()
        after_out = self.stable_viewport()
        assert after_out['zoom'] < after_in['zoom'], (after_in, after_out)
        point = self.blank_point()
        assert point, 'no blank canvas wheel target'
        self.page.mouse.move(point['x'], point['y'])
        self.page.mouse.wheel(0, 180)
        wheel_out = self.stable_viewport()
        assert wheel_out['zoom'] < after_out['zoom'], (after_out, wheel_out)
        self.page.mouse.wheel(0, -180)
        wheel_in = self.stable_viewport()
        assert wheel_in['zoom'] > wheel_out['zoom'], (wheel_out, wheel_in)
        self.assert_query(root, 1, 'both')
        self.report['zoomEvidence'] = [before, after_in, after_out, wheel_out, wheel_in]
        self.capture('real-zoom-buttons-and-wheel')
        self.checked('zoom +/- and wheel out/in change real viewport, not hops')

    def check_root_recovery(self, classifier):
        for _ in range(20):
            self.pan(self.geometry()['canvas']['width'] * 0.65)
            main = [card for card in self.geometry()['cards'] if not card['detail']]
            if main and not any(card['intersects'] for card in main):
                break
        else:
            raise AssertionError('real mouse pans did not place every main card outside canvas')
        offscreen = self.stable_viewport()
        self.capture('classifier-all-cards-offscreen-after-real-pan')
        baseline = self.model('model.Baseline')
        assert baseline != classifier, (classifier, baseline)
        recovered = self.stable_viewport()
        assert not viewport_equal(offscreen, recovered), (offscreen, recovered)
        self.capture('baseline-new-root-visible-without-fit')
        self.checked('all old cards panned offscreen -> Overview Baseline new root auto-visible without Fit')

        self.pan(25)
        saved = self.stable_viewport()
        self.capture('baseline-same-context-viewport-before-close')
        self.page.locator('.tab.active .close-tab').click()
        self.expect(self.page.locator('.graph-editor')).to_have_count(0)
        reopened = self.model('model.Baseline')
        assert reopened == baseline, (reopened, baseline)
        restored = self.stable_viewport()
        assert viewport_equal(saved, restored), (saved, restored)
        self.capture('baseline-same-context-reopened-viewport-preserved')
        self.checked('graph tab close -> Overview same root reopen preserves saved viewport')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=9302, help='Existing caller-owned native host CDP port')
    parser.add_argument('--project', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'tests/fixtures/mvp_project',
                        help='Own-written MVP fixture, already seeded in UI Recent Projects')
    parser.add_argument('--output', type=Path, default=Path('build/canvas-recovery-native'))
    parser.add_argument('--geometry-only', action='store_true',
                        help='Read current native DOM geometry only; no clicks, bridge calls or navigation')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {'startedAt': datetime.now(UTC).isoformat(), 'status': 'failed',
              'project': str(args.project.resolve()), 'port': args.port, 'checks': [],
              'errors': [], 'pageErrors': [], 'consoleErrors': [], 'noFitClicks': True,
              'bridgeAllowlist': ['get_session', 'get_view_state']}
    page = None
    runner = None
    try:
        # --help is available without Playwright, CDP, or any host activity.
        from playwright.sync_api import expect, sync_playwright

        if not args.geometry_only:
            assert (args.project / 'model.py').is_file(), 'MVP fixture model.py missing'
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{args.port}', timeout=20_000)
            try:
                pages = [candidate for context in browser.contexts for candidate in context.pages]
                page = next((candidate for candidate in pages if candidate.locator('.desktop-shell').count()), None)
                assert page is not None, 'CDP has no existing native CodeViz desktop page'
                page.set_default_timeout(20_000)
                page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
                page.on('console', lambda message: report['consoleErrors'].append(message.text)
                        if message.type == 'error' else None)
                runner = CanvasAcceptance(page, args.project.resolve(), output, report, expect)
                if args.geometry_only:
                    report['geometryOnly'] = True
                    report['geometry'] = {'current-native': runner.geometry()}
                    assert_canvas_geometry(report['geometry']['current-native'])
                    runner.checked('current native canvas fills available editor after toolbar/static note')
                else:
                    page.wait_for_function('Boolean(window.pywebview?.api?.get_session)')
                    report['initialSession'] = runner.read_bridge('get_session')
                    runner.recent_only()
                    root = runner.model('model.Classifier')
                    runner.checked('native canvas fills available editor after toolbar/static note')
                    runner.check_layout(root)
                    runner.check_detail()
                    runner.check_hops(root)
                    runner.check_zoom(root)
                    runner.check_root_recovery(root)
                expect(page.locator('.error-banner, .inline-error')).to_have_count(0)
                assert not report['pageErrors'], report['pageErrors']
                assert not report['consoleErrors'], report['consoleErrors']
                report['status'] = 'passed'
            except Exception as error:
                report['errors'].append(f'{type(error).__name__}: {error}')
                report['traceback'] = traceback.format_exc()
                if page is not None and not page.is_closed():
                    try:
                        if runner and page.locator('.graph-editor .react-flow').count():
                            report['failureGeometry'] = runner.geometry()
                            if not args.geometry_only:
                                report['failureViewState'] = runner.read_bridge('get_view_state')
                        page.screenshot(path=str(output / 'failure.png'), full_page=True)
                        report.setdefault('screenshots', []).append(str(output / 'failure.png'))
                        (output / 'failure.html').write_text(page.content(), encoding='utf-8')
                    except Exception as capture_error:
                        report['errors'].append(f'Failure capture: {capture_error}')
            finally:
                # Disconnect CDP only; never close native pages/contexts or kill host.
                browser.close()
    except Exception as error:
        report['errors'].append(f'{type(error).__name__}: {error}')
        report['traceback'] = traceback.format_exc()
    report['finishedAt'] = datetime.now(UTC).isoformat()
    path = output / 'report.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'checks': report['checks'],
                      'errors': report['errors'], 'report': str(path)}, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
