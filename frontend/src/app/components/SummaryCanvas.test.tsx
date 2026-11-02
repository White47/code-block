import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Graph, SummaryStep, ViewState } from '../../types';
import type { EffectiveUnderstanding } from '../assistant/project-understanding';
import { projectHumanUnderstandingEnhancement } from '../assistant/human-understanding-overlay';
import { translator } from '../i18n';
import TaskFlow from './TaskFlow';
import UnderstandingGraphs from './UnderstandingGraphs';
import { overview } from '../testFixtures';

const native = vi.hoisted(() => ({ hasMethod: vi.fn(() => false), call: vi.fn() }));
const graphInputs = vi.hoisted(() => [] as Graph[]);
vi.mock('../../api/bridge', () => native);
vi.mock('./GraphEditor', () => ({ default: (props: { title: string; graph: Graph; viewState: ViewState | null; onSelect: (node: unknown, detail: null) => void; onDetail: (node: unknown) => void; onMove: (id: string, point: { x: number; y: number }) => void; initialFit: string; panOnScroll: boolean }) => {
  graphInputs.push(props.graph); return (
  <div><p>{props.title}</p><p>{props.initialFit} {String(props.panOnScroll)}</p><output aria-label="Saved layout">{JSON.stringify(props.viewState?.positionsByView.Overview)}</output><button onClick={() => props.onMove(props.graph.nodes[0].id, { x: 20, y: 30 })}>Move summary node</button>{props.graph.nodes.map(node => <button key={node.id} onClick={() => props.onSelect(node, null)} onDoubleClick={() => props.onDetail(node)}>{node.id}</button>)}</div>); } }));

const anchor = { filePath: 'stats.py', startLine: 2, endLine: 5, startColumn: null, endColumn: null };
const step: SummaryStep = { id: 'task:read', kind: 'task', title: '读取分数', basis: 'source_author_statement', confidence: 'Probable', ownerSymbolIds: ['symbol:read'], evidenceIds: ['fact:read'], codeNodeIds: ['symbol:read'], sourceAnchors: [anchor], sourceLocationCount: 1 };

describe('summary task canvas', () => {
  afterEach(() => { cleanup(); graphInputs.length = 0; native.hasMethod.mockReset().mockReturnValue(false); native.call.mockReset(); });
  it('annotates the selected node without changing the static graph, layout or selection across revisions', async () => {
    const flow = { id: 'flow:main', title: '统计分数', entrySymbolId: 'symbol:main', steps: [step, { ...step, id: 'task:compute', ownerSymbolIds: ['symbol:compute'], codeNodeIds: ['symbol:compute'] }],
      relations: [{ source: step.id, target: 'task:compute', kind: 'control_next' as const, confidence: 'Probable', evidenceIds: [], codeNodeIds: [], sourceAnchors: [anchor] }], incomplete: false, omittedCount: 0 };
    const model: EffectiveUnderstanding = { reference: { schemaVersion: 1, artifactId: 'artifact', revision: 1, projectEpoch: 'epoch', analysisGeneration: 1, configRevision: 1, consentRevision: 1, conversationId: 'chat', conversationRevision: 0, jobId: 'job', coverage: { indexedFileCount: 2, authorizedFileCount: 2, metadataFileCount: 2, sourceFileCount: 1, gapCodes: [] } },
      answer: { sections: [{ kind: 'steps', claims: [{ text: 'Explain the saved score input', basis: 'inference', evidenceIds: ['e-path'], relationEvidenceId: 'e-path' }] }],
        evidence: [{ evidenceId: 'e-path', label: 'Saved source', confidence: 'Probable', truncated: false, sourceAvailable: true, graphAvailable: true, unavailableReason: 'navigation_check_required' }],
        relationFacts: [{ evidenceId: 'e-path', sourceId: 'symbol:read', targetId: 'symbol:compute', category: 'control', type: 'CONTROL_FLOW', confidence: 'Probable', candidateTargets: [] }], incomplete: false, coverageGaps: [], checkedAt: 'now' } };
    const understanding = { status: 'partial' as const, responsibilities: [], omittedResponsibilityCount: 0, omittedFactCount: 0, discardedFactCount: 0, flows: [flow] };
    native.hasMethod.mockReturnValue(true);
    native.call.mockResolvedValue({ context: { kind: 'task_flow', id: flow.id }, layoutToken: 'static-token', positions: { [step.id]: [30, 40] }, viewport: { zoom: 1, pan: [0, 0] } });
    const actions = { names: new Map<string, string>(), projectPath: 'project', openSource: vi.fn(), openView: vi.fn(), navigate: vi.fn(), onAsk: vi.fn(), t: translator('zh-CN') };
    const rendered = render(<TaskFlow flow={flow} {...actions}/>);
    fireEvent.click(await screen.findByText(step.id));
    const staticGraph = graphInputs.at(-1);
    const update = (current: EffectiveUnderstanding | null) => rendered.rerender(<TaskFlow flow={flow} {...actions} model={current} enhancement={projectHumanUnderstandingEnhancement(understanding, current)}/>);
    update(model);
    const notes = within(document.querySelector('.summary-selection > .summary-ai-notes') as HTMLElement);
    expect(notes.getByText('Explain the saved score input')).toBeTruthy();
    expect(notes.getByText(/AI 推断 · v1/)).toBeTruthy();
    fireEvent.click(notes.getByText(/相关源码与证据/, { selector: 'summary' }));
    fireEvent.click(notes.getByRole('button', { name: '打开源码' }));
    expect(actions.navigate).toHaveBeenCalledWith('steps:0', 'e-path', 'source');
    fireEvent.click(notes.getByRole('button', { name: '围绕此节点提问' }));
    expect(actions.onAsk).toHaveBeenCalledWith('steps:0');
    update(null);
    expect(screen.queryByText('Explain the saved score input')).toBeNull();
    expect(screen.getByRole('article', { name: '任务详情' })).toBeTruthy();
    const repaired = { ...model, reference: { ...model.reference, revision: 2 }, answer: { ...model.answer, relationFacts: [] } };
    update(repaired);
    expect(screen.queryByText('Explain the saved score input')).toBeNull();
    expect(graphInputs.every(value => value === staticGraph)).toBe(true);
    expect(screen.getByLabelText('Saved layout').textContent).toContain('30,40');
    expect(native.call.mock.calls.map(args => args[0])).toEqual(['get_summary_layout']);
  });
  it('uses readable top-down rendering and real source/implementation targets', async () => {
    const openSource = vi.fn(); const openView = vi.fn();
    render(<TaskFlow flow={{ id: 'flow:main', title: '统计分数', entrySymbolId: 'symbol:main', steps: [{ ...step, codeName: 'display-only:not-a-native-target' }], relations: [], incomplete: false, omittedCount: 0 }} names={new Map([['symbol:read', 'load_scores']])} projectPath="project" openSource={openSource} openView={openView} t={translator('zh-CN')}/>);
    expect(await screen.findByText('width true')).toBeTruthy();
    fireEvent.click(screen.getByText('task:read'));
    fireEvent.click(screen.getByRole('button', { name: '打开源码 stats.py:2' }));
    expect(openSource).toHaveBeenCalledWith(anchor);
    fireEvent.click(screen.getByRole('button', { name: '查看实现 load_scores' }));
    expect(openView).toHaveBeenCalledWith('FunctionFlow', 'symbol:read');
    fireEvent.doubleClick(screen.getByText('task:read'));
    expect(openView).toHaveBeenLastCalledWith('FunctionFlow', 'symbol:read');
    expect(openView.mock.calls.flat()).not.toContain('task:read');
    expect(openView.mock.calls.flat()).not.toContain('display-only:not-a-native-target');
  });
  it('switches between distinct entry flows and clears the old local selection', async () => {
    const first = { id: 'flow:first', title: '第一个流程', entrySymbolId: 'main', steps: [step], relations: [], incomplete: false, omittedCount: 0 };
    const second = { ...first, id: 'flow:second', title: '第二个流程', steps: [{ ...step, title: '另一个职责', sourceAnchors: [{ ...anchor, filePath: 'other.py' }] }] };
    render(<UnderstandingGraphs data={{ ...overview, understanding: { status: 'source_backed', responsibilities: [], omittedResponsibilityCount: 0, omittedFactCount: 0, discardedFactCount: 0, flows: [first, second] } }} openSource={vi.fn()} openView={vi.fn()} t={translator('zh-CN')}/>);
    fireEvent.click(await screen.findByText('task:read'));
    expect(screen.getByRole('button', { name: '打开源码 stats.py:2' })).toBeTruthy();
    fireEvent.change(screen.getByLabelText('选择流程入口'), { target: { value: 'flow:second' } });
    expect(await screen.findByText('task:read')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '打开源码 stats.py:2' })).toBeNull();
    fireEvent.click(screen.getByText('task:read'));
    expect(screen.getByRole('button', { name: '打开源码 other.py:2' })).toBeTruthy();
  });
  it('reloads the layout token when analysis data refreshes with unchanged summary geometry', async () => {
    native.hasMethod.mockReturnValue(true);
    const layout = { context: { kind: 'task_flow', id: 'same-flow' }, positions: {}, viewport: { zoom: 1, pan: [0, 0] } };
    native.call.mockResolvedValueOnce({ ...layout, layoutToken: 'old-analysis' });
    const flow = { id: 'same-flow', title: '流程', entrySymbolId: 'main', steps: [step], relations: [], incomplete: false, omittedCount: 0 };
    const actions = { names: new Map<string, string>(), projectPath: 'project', openSource: vi.fn(), openView: vi.fn(), t: translator('zh-CN') };
    const rendered = render(<TaskFlow flow={flow} {...actions}/>);
    await screen.findByText('task:read');
    native.call.mockResolvedValue({ ...layout, layoutToken: 'new-analysis' });
    rendered.rerender(<TaskFlow flow={{ ...flow, steps: [...flow.steps], relations: [...flow.relations] }} {...actions}/>);
    await waitFor(() => expect(native.call.mock.calls.filter(call => call[0] === 'get_summary_layout')).toHaveLength(2));
    native.call.mockResolvedValue({ ...layout, layoutToken: 'new-analysis', positions: { 'task:read': [20, 30] } });
    fireEvent.click(await screen.findByText('Move summary node'));
    await waitFor(() => expect(native.call).toHaveBeenLastCalledWith('move_summary_node', { kind: 'task_flow', id: 'same-flow' }, 'new-analysis', 'task:read', { x: 20, y: 30 }));
    await waitFor(() => expect(screen.getByLabelText('Saved layout').textContent).toContain('"task:read":[20,30]'));
  });
});
