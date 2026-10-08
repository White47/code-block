"""Verify structure exploration in an existing caller-owned native WebView2 host.

Attach only. Both own-written fixture projects must already be in UI Recent
Projects. All navigation, analysis and graph queries use actual UI controls.
No host launch/kill, fixture execution, Fit clicks, or JavaScript state writes.
"""

import argparse
import json
import re
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from verify_canvas_recovery_native import (
    DOM_GEOMETRY,
    CanvasAcceptance,
    viewport_equal,
)

# Keep the previous verifier's real editor/canvas measurement. Completed-query
# feedback is now additional chrome above the canvas, including wrapped text.
STRUCTURE_GEOMETRY = DOM_GEOMETRY.replace(
    '.static-flow-note,.query-toolbar,.graph-toolbar',
    '.static-flow-note,.query-feedback,.query-toolbar,.graph-toolbar',
).replace(
    'intersects:intersects(r), detail:',
    "intersects:intersects(r) && getComputedStyle(e).visibility !== 'hidden', detail:",
)


class StructureAcceptance(CanvasAcceptance):
    """Reuse NativeAcceptance's UI operations through its canvas subclass."""

    def geometry(self):
        return self.page.locator('.graph-editor .react-flow').evaluate(STRUCTURE_GEOMETRY)

    def open_recent(self, project, stage):
        self.project = project.resolve()
        self.activity('overview')
        # A retry can attach to an already expanded graph. Actually leave that
        # graph through the UI before reopening/analyzing the requested project.
        self.page.locator('.view-link').filter(has_text=re.compile(r'^(Overview|项目概览)')).click()
        self.inspection(stage)
        recents = self.page.locator('.recent-project')
        wanted = str(self.project).replace('\\', '/').casefold()
        matches = [recents.nth(index) for index in range(recents.count())
                   if (recents.nth(index).get_attribute('title') or '').replace('\\', '/').casefold() == wanted]
        assert len(matches) == 1, f'Caller must seed exactly one UI Recent Projects entry: {self.project}'
        self.expect(matches[0]).to_be_enabled()
        matches[0].click()
        self.expect(self.page.locator('.sidebar-project').get_by_role('button', name=re.compile(
            r'^(Re-analyze|重新分析|Analyze project|分析项目)$'))).to_be_enabled(timeout=45_000)
        self.session_for(self.project)
        self.analyze_ui()
        self.checked(f'{stage}: Recent Projects -> Analyze through UI')

    def feedback(self):
        feedback = self.page.locator('.query-feedback[role="status"]')
        self.expect(feedback).to_have_count(1)
        self.expect(feedback).not_to_contain_text(re.compile(r'Updating|正在更新', re.I))
        self.expect(feedback).not_to_contain_text(re.compile(r'failed|失败', re.I))
        return feedback.inner_text().strip()

    def capture_structure(self, stage):
        self.capture(stage)
        geometry = self.report['geometry'][stage]
        main = [card for card in geometry['cards'] if not card['detail']]
        assert main and any(card['intersects'] for card in main), geometry
        self.report.setdefault('nodeSets', {})[stage] = {
            'ids': sorted(card['id'] for card in main),
            'names': sorted(card['name'] for card in main),
            'codeNames': sorted(card['codeName'] or '' for card in main),
            'feedback': self.feedback(),
        }
        return {card['id']: card for card in main}

    def depth_button(self, fewer=False):
        label = r'^(Fewer layers|更少层)' if fewer else r'^(More layers|更多层)'
        return self.page.locator('.query-toolbar').get_by_role('button', name=re.compile(label))

    def assert_depth(self, depth):
        self.expect(self.depth_button()).to_have_text(re.compile(rf'^(More layers|更多层)\s*·\s*{depth}/5$'))
        self.expect(self.page.locator('.query-toolbar').get_by_role('button', name=re.compile(
            r'^(Upstream|Downstream|Both|上游|下游|双向)$'))).to_have_count(0)

    def assert_feedback_count(self, text, added, removed):
        for labels, count in ((r'Added nodes|新增节点', added), (r'Removed nodes|收起节点', removed)):
            assert re.search(rf'(?:{labels})\s*[:：]\s*{count}(?!\d)', text, re.I), (text, added, removed)

    def check_growth(self):
        root = self.model('model.Classifier')
        self.assert_depth(1)
        self.check_layout(root)
        shallow = self.capture_structure('architecture-depth-1')
        assert len(shallow) == 4, shallow
        assert {card['name'] for card in shallow.values()} == {'Classifier', 'embedding', 'encoder', 'head'}, shallow
        previous = shallow
        for depth, expected in ((2, 'layers'), (3, 'member'), (4, 'projection')):
            self.expect(self.depth_button()).to_be_enabled()
            self.depth_button().click()
            self.assert_depth(depth)

            def grew(geometry, before=tuple(previous)):
                before = set(before)
                current = {card['id']: card for card in geometry['cards'] if not card['detail']}
                added = set(current) - before
                return before < set(current) and all(current[node]['intersects'] for node in added)

            self.wait_geometry(grew, f'depth {depth} must add actual visible measured nodes without Fit')
            current = self.capture_structure(f'architecture-depth-{depth}')
            added = set(current) - set(previous)
            names = ' '.join((current[node]['name'] or '') + ' ' + (current[node]['codeName'] or '') for node in added)
            if expected == 'member':
                assert re.search(r'\[(?:\d+|\*)\]|member|成员|Block', names, re.I), names
                members = [current[node] for node in added if re.fullmatch(
                    r'model\.Encoder\.layers\[(?:\d+|\*)\]', current[node]['codeName'] or '')]
                assert members, names
                for member in members:
                    self.check_member_anchor(member)
            else:
                assert expected in names, (expected, names)
            self.assert_feedback_count(self.feedback(), len(added), 0)
            previous = current
        self.checked('Architecture 1 -> 2 -> 3 -> 4 adds real layers/member/projection nodes and exact feedback')
        return shallow, previous

    def check_member_anchor(self, member):
        """A repeated template is one genuine source-backed member, not two runtime objects."""
        if member['codeName'] == 'model.Encoder.layers[*]':
            assert member['name'] == 'layers[*] ×2', member
        node = self.page.get_by_test_id(f'rf__node-{member["id"]}')
        self.expect(node).to_have_count(1)
        node.click()
        inspector = self.page.locator('.inspector')
        self.expect(inspector.locator('h2')).to_have_text(member['name'])
        self.expect(inspector.locator('code').first).to_have_text(member['codeName'])
        anchors = inspector.locator('.anchor-card')
        self.expect(anchors.first).to_be_visible()
        source_lines = (self.project / 'model.py').read_text(encoding='utf-8').splitlines()
        declaration = next(index for index, text in enumerate(source_lines, 1)
                           if 'self.layers = nn.ModuleList([Block() for _ in range(2)])' in text)
        anchor = anchors.filter(has_text=re.compile(r'model\.py:\d+–\d+')).first
        self.expect(anchor).to_be_visible()
        match = re.fullmatch(r'model\.py:(\d+)–(\d+)', anchor.locator('code').inner_text().strip())
        assert match, anchor.inner_text()
        start, end = int(match.group(1)), int(match.group(2))
        assert start <= declaration <= end, (member, match.group(0), declaration)
        self.report.setdefault('memberSourceEvidence', []).append({
            'id': member['id'], 'name': member['name'], 'codeName': member['codeName'],
            'file': 'model.py', 'startLine': start, 'endLine': end,
            'declarationLine': declaration, 'declaration': source_lines[declaration - 1],
        })

    def check_projected_source(self, deep):
        candidates = [card for card in deep.values() if (card['codeName'] or '').endswith('.projection')]
        assert candidates, deep
        candidate = candidates[0]
        node = self.page.locator(
            f'.react-flow__node-code[data-id={json.dumps(candidate["id"])}]')
        self.expect(node).to_have_count(1)
        before = self.geometry()['viewport']
        node.click()
        inspector = self.page.locator('.inspector')
        self.expect(inspector.locator('h2')).to_have_text(candidate['name'])
        self.expect(inspector).to_contain_text(candidate['codeName'])
        self.expect(inspector).to_contain_text(re.compile(r'Definition|定义'))
        self.expect(inspector).to_contain_text(re.compile(r'Instance|实例'))
        state = self.wait_bridge('get_view_state', lambda state: state.get('selectedId') == candidate['id'],
                                 'actual projection selected by UI')
        self.report['projectionSelection'] = state['selectedId']
        self.page.wait_for_timeout(300)
        assert viewport_equal(before, self.geometry()['viewport']), 'selection must preserve measured camera'
        anchors = inspector.locator('.anchor-card')
        self.expect(anchors.first).to_be_visible()
        match = re.fullmatch(r'(.+):(\d+)–(\d+)', anchors.first.locator('code').inner_text().strip())
        assert match, anchors.first.inner_text()
        filename, line = match.group(1), int(match.group(2))
        source_path = (self.project / filename).resolve()
        assert source_path.is_relative_to(self.project), source_path
        source_lines = source_path.read_text(encoding='utf-8').splitlines()
        assert 1 <= line <= len(source_lines), (filename, line)
        self.capture_structure('projection-inspector')
        self.open_source('.primary-editor', filename, line, source_lines[line - 1])
        self.screenshot('projection-real-source')
        self.report['projectionSource'] = {'file': filename, 'line': line, 'text': source_lines[line - 1]}
        self.page.locator('.tab.active .close-tab').click()
        self.expect(self.page.locator('.graph-editor')).to_be_visible()
        self.checked('projected node Inspector -> actual anchored source line; selection keeps camera')

    def check_shrink(self, shallow, deep):
        previous = deep
        for depth in (3, 2, 1):
            self.expect(self.depth_button(True)).to_be_enabled()
            self.depth_button(True).click()
            self.assert_depth(depth)
            self.wait_geometry(lambda geometry, before=set(previous):
                               {card['id'] for card in geometry['cards'] if not card['detail']} < before,
                               f'depth {depth} must remove actual structural nodes')
            current = self.capture_structure(f'architecture-shrunk-{depth}')
            self.assert_feedback_count(self.feedback(), 0, len(set(previous) - set(current)))
            self.wait_bridge('get_view_state', lambda state: state.get('selectedId') is None,
                             'structural shrink clears selection before query')
            self.expect(self.page.locator('.inspector h2')).to_have_count(0)
            previous = current
        assert set(previous) == set(shallow), (previous, shallow)
        assert {card['name'] for card in previous.values()} == {card['name'] for card in shallow.values()}
        self.expect(self.depth_button(True)).to_be_disabled()
        self.checked('Architecture 4 -> 3 -> 2 -> 1 shrinks exact node sets and clears projection selection')

    def open_sidebar(self, label, kind):
        self.activity('overview')
        self.page.locator('.view-link').filter(has_text=re.compile(label)).click()
        self.expect(self.page.locator('.graph-editor')).to_be_visible()
        self.wait_bridge('get_view_state', lambda state: state.get('activeView') == kind, f'{kind} active')

    def check_ordinary_project(self, project):
        self.open_recent(project, 'ordinary-project')
        self.open_sidebar(r'^(Architecture|架构)', 'Architecture')
        self.expect(self.page.locator('.primary-editor')).to_contain_text(re.compile(r'No model|未发现模型'))
        self.page.locator('.primary-editor').get_by_role('button', name=re.compile(r'^(Project structure|项目结构)$')).click()
        self.wait_bridge('get_view_state', lambda state: state.get('activeView') == 'ProjectStructure', 'ProjectStructure active')
        self.wait_geometry(lambda geometry: bool(geometry['cards']) and any(card['intersects'] for card in geometry['cards']),
                           'ordinary project has genuine visible structure')
        self.assert_depth(1)
        initial = self.capture_structure('ordinary-project-structure-depth-1')
        assert any((card['codeName'] or '').endswith('helpers') or 'helpers' in (card['name'] or '')
                   for card in initial.values()), initial
        self.depth_button().click()
        self.assert_depth(2)
        self.wait_geometry(lambda geometry: len([card for card in geometry['cards'] if not card['detail']]) > len(initial),
                           'project structure adds real classes/functions')
        expanded = self.capture_structure('ordinary-project-structure-depth-2')
        assert any('native_normalize_record' in (card['codeName'] or '') for card in expanded.values()), expanded
        self.assert_feedback_count(self.feedback(), len(expanded) - len(initial), 0)
        self.checked('ordinary Python no-model explanation -> nonempty source-backed ProjectStructure -> real functions')
        self.overview()
        self.function_row('helpers.native_normalize_record').click()
        self.graph_tab()
        self.wait_geometry(lambda geometry: any(card['intersects'] for card in geometry['cards']), 'FunctionFlow visible')
        self.expect(self.page.locator('.query-toolbar').get_by_role('button', name=re.compile(
            r'^(More hops|Fewer hops|More layers|Fewer layers|Upstream|Downstream|Both|更多层|更少层|上游|下游|双向)'))).to_have_count(0)
        self.capture_structure('function-flow-no-ineffective-controls')
        self.checked('FunctionFlow actual graph has no ineffective depth or relation controls')
        self.check_symbol_relations()

    def relation_snapshot(self):
        return {'nodes': sorted(card['id'] for card in self.geometry()['cards'] if not card['detail']),
                'edges': sorted(self.page.locator('.react-flow__edge').evaluate_all(
                    'elements => elements.map(element => element.getAttribute("data-id"))'))}

    def check_symbol_relations(self):
        # Symbol is a local code context: establish its real focus through Search.
        self.activity('search')
        self.page.locator('.search-input').get_by_role('textbox', name=re.compile(r'^(Search|搜索)$')).fill(
            'NativeReportBuilder')
        result = self.page.locator('.search-result').filter(has=self.page.locator(
            'small', has_text=re.compile(r'^helpers\.NativeReportBuilder$')))
        self.expect(result).to_have_count(1)
        result.get_by_role('button', name=re.compile(r'^(Reveal in graph|在图中定位)$')).click()
        self.expect(self.page.locator('.graph-editor')).to_be_visible()
        state = self.wait_bridge('get_view_state', lambda state:
                                 state.get('activeView') == 'Symbol' and state.get('selectedId') is not None,
                                 'Search class result Reveal establishes a focused Symbol context')
        self.wait_geometry(lambda geometry: any(card['intersects'] for card in geometry['cards']), 'Symbol visible')
        controls = self.page.locator('.query-toolbar')
        for label in (r'^(Upstream|上游)$', r'^(Downstream|下游)$', r'^(Both|双向)$'):
            self.expect(controls.get_by_role('button', name=re.compile(label))).to_have_count(1)
        self.expect(controls.get_by_role('button', name=re.compile(r'^(More layers|Fewer layers)'))).to_have_count(0)
        # Inspect the source-backed class whose CONTAINS edges supply real neighbors.
        cards = self.page.locator('.react-flow__node-code').filter(has=self.page.locator(
            '.code-node code', has_text=re.compile(r'^helpers\.NativeReportBuilder$')))
        self.expect(cards).to_have_count(1)
        self.expect(cards.locator('.code-node')).to_have_attribute('data-role', 'Class')
        focus_id = cards.get_attribute('data-id')
        assert focus_id and state['selectedId'] == focus_id, (focus_id, state)
        assert state.get('graphRoots', {}).get('Symbol') == focus_id, state
        cards.click()
        self.expect(self.page.locator('.inspector')).to_contain_text('helpers.NativeReportBuilder')
        self.expect(self.page.locator('.inspector')).to_contain_text(re.compile(r'\bClass\b|类'))
        self.wait_bridge('get_view_state', lambda state: state.get('selectedId') == focus_id,
                         'actual class card stays the Symbol focus')
        self.report['symbolFocus'] = {'id': focus_id, 'codeName': 'helpers.NativeReportBuilder',
                                      'entry': 'Search -> unique class result -> Reveal -> class card'}
        snapshots = []
        for label, neighbor in ((r'^(Upstream|上游)$', 'helpers'),
                                (r'^(Downstream|下游)$', 'helpers.NativeReportBuilder.native_build_report')):
            button = controls.get_by_role('button', name=re.compile(label))
            self.expect(button).to_be_enabled()
            button.click()
            self.expect(button).to_have_class(re.compile(r'active'))
            self.wait_geometry(lambda geometry, neighbor=neighbor: any(
                card['codeName'] == neighbor for card in geometry['cards'] if not card['detail']),
                f'Symbol direction must display the real CONTAINS neighbor {neighbor}')
            self.feedback()
            snapshots.append(self.relation_snapshot())
        assert snapshots[0] != snapshots[1], snapshots
        more = controls.get_by_role('button', name=re.compile(r'^(More hops|更多关系跳数)'))
        self.expect(more).to_be_enabled()
        more.click()
        self.expect(more).to_have_text(re.compile(r'\b2/5$'))
        self.wait_geometry(lambda geometry: any(
            card['codeName'] == 'helpers.NativeReportBuilder.native_build_report.report_text'
            for card in geometry['cards'] if not card['detail']),
            'Symbol downstream hop two must add the source-backed method binding')
        self.feedback()
        snapshots.append(self.relation_snapshot())
        assert set(snapshots[1]['nodes']) < set(snapshots[2]['nodes']), snapshots
        assert snapshots[1]['edges'] != snapshots[2]['edges'], snapshots
        self.report['symbolRelationSnapshots'] = snapshots
        self.capture_structure('symbol-real-relations')
        self.checked('Symbol still queries real upstream/downstream and hops; displayed graph sets change')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    repository = Path(__file__).resolve().parents[1]
    parser.add_argument('--port', type=int, default=9303, help='Existing caller-owned host CDP port')
    parser.add_argument('--project', type=Path, default=repository / 'tests/fixtures/mvp_project')
    parser.add_argument('--ordinary-project', type=Path, default=repository / 'tests/fixtures/general_python_project')
    parser.add_argument('--output', type=Path, default=repository / 'build/structure-exploration-native')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {'startedAt': datetime.now(UTC).isoformat(), 'status': 'failed', 'port': args.port,
              'project': str(args.project.resolve()), 'ordinaryProject': str(args.ordinary_project.resolve()),
              'checks': [], 'errors': [], 'pageErrors': [], 'consoleErrors': [],
              'attachOnly': True, 'noFitClicks': True, 'bridgeAllowlist': ['get_session', 'get_view_state']}
    page = runner = None
    try:
        from playwright.sync_api import expect, sync_playwright

        assert (args.project / 'model.py').is_file(), 'own-written MVP model.py fixture missing'
        assert (args.ordinary_project / 'helpers.py').is_file(), 'own-written ordinary Python fixture missing'
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{args.port}', timeout=20_000)
            try:
                pages = [candidate for context in browser.contexts for candidate in context.pages]
                matches = [candidate for candidate in pages if candidate.locator('.desktop-shell').count()]
                assert len(matches) == 1, 'CDP must expose exactly one caller-owned CodeViz desktop page'
                page = matches[0]
                page.set_default_timeout(20_000)
                page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
                page.on('console', lambda message: report['consoleErrors'].append(message.text)
                        if message.type == 'error' else None)
                runner = StructureAcceptance(page, args.project.resolve(), output, report, expect)
                runner.open_recent(args.project, 'model-project')
                shallow, deep = runner.check_growth()
                runner.check_projected_source(deep)
                runner.check_shrink(shallow, deep)
                runner.check_ordinary_project(args.ordinary_project)
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
                        page.screenshot(path=str(output / 'failure.png'), full_page=True)
                        (output / 'failure.html').write_text(page.content(), encoding='utf-8')
                    except Exception as capture_error:
                        report['errors'].append(f'Failure capture: {capture_error}')
            finally:
                # Disconnect this CDP client; never close pages/contexts or host.
                browser.close()
    except Exception as error:
        report['errors'].append(f'{type(error).__name__}: {error}')
        report['traceback'] = traceback.format_exc()
    report['finishedAt'] = datetime.now(UTC).isoformat()
    path = output / 'report.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'checks': report['checks'], 'errors': report['errors'],
                      'report': str(path)}, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
