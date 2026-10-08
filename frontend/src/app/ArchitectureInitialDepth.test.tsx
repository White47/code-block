import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { anchor, graph, ok, overview, selectedNode, session, viewState } from './testFixtures';
import type { BridgeMethods, Envelope, Graph, Overview, OverviewChoice, Session, ViewState } from '../types';

// The contract is test-local until the implementer adds it to production types.
type HintedChoice = OverviewChoice & { initialStructureDepth?: 1 | 2 };
type NativeApi = { [K in keyof BridgeMethods]: (...args: BridgeMethods[K]['args']) => Promise<Envelope<BridgeMethods[K]['data']>> };

vi.mock('./components/GraphEditor', () => ({ default: ({ graph: value }: { graph: Graph }) =>
  <div>{value.nodes.map(node => <span key={node.id}>{node.displayName}</span>)}</div> }));
afterEach(() => { cleanup(); delete window.pywebview; });

function choice(id = 'model', hint?: 1 | 2): HintedChoice {
  return { id, name: id === 'model' ? 'nanoGPTB' : 'Other root', sourceAnchor: anchor,
    ...(hint === undefined ? {} : { initialStructureDepth: hint }) };
}
function nativeApi(options: { active?: 'Overview' | 'Architecture'; hint?: 1 | 2 } = {}) {
  let currentSession: Session = { ...session };
  let state: ViewState = { ...viewState, activeView: options.active ?? 'Overview', selectedId: null,
    openTabs: ['Overview', 'Architecture'], graphRoots: { Architecture: 'model' } };
  let currentOverview: Overview = { ...overview, models: [choice('model', options.hint), choice('other-model')] };
  const getView = vi.fn<NativeApi['get_view']>(async (kind, root, _selected, _hops, _direction, depth = 1) => ok({
    ...graph, viewKind: kind, rootIds: [root ?? 'model'],
    nodes: [{ ...selectedNode, id: root ?? 'model', displayName: `${root ?? 'model'} root` },
      ...(depth > 1 ? [{ ...selectedNode, id: `${root}:detail`, displayName: `${root} depth two detail` }] : [])],
    queryContext: { controlMode: 'structure' as const, structureDepth: depth,
      canExpand: depth < 2, depthLimit: false, budgetTruncated: false },
  } satisfies Graph));
  const getOverview = vi.fn<NativeApi['get_overview']>(async () => ok(currentOverview));
  const api = {
    get_session: vi.fn(async () => ok(currentSession)),
    get_preferences: vi.fn(async () => ok({ language: 'en' as const, issue: null })),
    set_language: vi.fn(async language => ok({ language, issue: null })),
    get_view_state: vi.fn(async () => ok(state)),
    get_overview: getOverview, get_problems: vi.fn(async () => ok([])), get_view: getView,
    get_inspector: vi.fn(async (_kind, _root, id) => ok({ ...selectedNode, id,
      evidence: [], children: [], relations: [], data: [], canOpenSource: false })),
    save_layout: vi.fn(async layout => { state = { ...state, ...layout }; return ok(state); }),
    save_viewport: vi.fn(async () => ok(state)),
    get_detail_view: vi.fn(async () => ok(graph)),
    read_source: vi.fn(async value => ok({ filePath: value.filePath, text: '# source', startLine: 1, endLine: 1 })),
    open_project: vi.fn(async path => {
      currentSession = { ...currentSession, projectPath: path };
      currentOverview = { ...currentOverview, projectPath: path };
      return ok(currentSession);
    }),
    choose_project: vi.fn(async () => ok(currentSession)), analyze: vi.fn(async () => ok(currentSession)),
    move_node: vi.fn(async () => ok(state)), expand: vi.fn(async () => ok(state)),
    collapse: vi.fn(async () => ok(state)), open_detail_island: vi.fn(async () => ok(state)),
    move_detail_island: vi.fn(async () => ok(state)), close_detail_island: vi.fn(async () => ok(state)),
    search: vi.fn(async () => ok({ results: [], totalCount: 0, truncated: false })),
    get_cache_info: vi.fn(async () => ok({ dataDir: 'cache', analysisPath: null, exists: false,
      status: 'Ready' as const, outdatedReasons: [], cacheIssue: null })),
    get_analysis_settings: vi.fn(async () => ok({ ignorePaths: [], issue: null })),
    set_analysis_settings: vi.fn(async () => ok({ ignorePaths: [], issue: null })),
    set_preferences: vi.fn(async () => ok({ language: 'en' as const, issue: null })),
  } satisfies NativeApi;
  window.pywebview = { api };
  return { api, setOverview: (value: Overview) => { currentOverview = value; } };
}
async function openChoice(name = 'nanoGPTB') {
  fireEvent.click(await screen.findByRole('button', { name: new RegExp(name) }));
  await screen.findByRole('button', { name: /^More layers/ });
}
async function expectDepth(depth: 1 | 2, root = 'model') {
  await waitFor(() => {
    expect(screen.getByRole('button', { name: `Fewer layers · ${depth}/5` })).toBeDefined();
    expect(screen.queryByText(`${root} depth two detail`) !== null).toBe(depth === 2);
    expect(screen.queryByRole('alert')).toBeNull();
  });
}
async function setDepthOneExplicitly() {
  await screen.findByRole('button', { name: /^More layers/ });
  if (screen.queryByRole('button', { name: 'Fewer layers · 1/5' })) {
    const more = screen.getByRole('button', { name: /^More layers/ }) as HTMLButtonElement;
    await waitFor(() => expect(more.disabled).toBe(false));
    fireEvent.click(more);
    await expectDepth(2);
  }
  const fewer = screen.getByRole('button', { name: 'Fewer layers · 2/5' }) as HTMLButtonElement;
  await waitFor(() => expect(fewer.disabled).toBe(false));
  fireEvent.click(fewer);
  await expectDepth(1);
}

it('opens a hinted Overview choice with a depth two graph request and visible details', async () => {
  const { api } = nativeApi({ hint: 2 });
  render(<App/>);
  await openChoice();
  await waitFor(() => expect(api.get_view.mock.calls.at(-1)?.[5]).toBe(2));
  await expectDepth(2);
});

it('uses a depth two hint for the restored Architecture root before any explicit depth action', async () => {
  const { api } = nativeApi({ active: 'Architecture', hint: 2 });
  render(<App/>);
  await screen.findByRole('button', { name: /^More layers/ });
  await waitFor(() => expect(api.get_view.mock.calls.at(-1)?.[5]).toBe(2));
  await expectDepth(2);
});

it.each([undefined, 1] as const)('starts an Overview choice at depth one with hint %s', async hint => {
  const { api } = nativeApi({ hint });
  render(<App/>);
  await openChoice();
  await expectDepth(1);
  expect(api.get_view.mock.calls.at(-1)?.[5]).toBe(1);
});

it('consumes the hint once so reopening the same choice cannot undo explicit shrink to one', async () => {
  const { api } = nativeApi({ hint: 2 });
  render(<App/>);
  await openChoice();
  // Manual expansion lets the no-bounce assertion run even before hint support exists.
  await setDepthOneExplicitly();
  const afterShrink = api.get_view.mock.calls.length;
  fireEvent.click(screen.getByRole('tab', { name: 'Overview' }));
  await openChoice();
  await expectDepth(1);
  expect(api.get_view.mock.calls.slice(afterShrink).every(args => args[5] === 1)).toBe(true);
});

it('does not apply a late depth two Overview hint after an explicit shrink to one', async () => {
  const { api } = nativeApi({ active: 'Architecture' });
  let finish!: (value: Envelope<Overview>) => void;
  api.get_overview.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  render(<App/>);
  await expectDepth(1);
  await setDepthOneExplicitly();
  const afterShrink = api.get_view.mock.calls.length;
  await act(async () => { finish(ok({ ...overview, models: [choice('model', 2)] })); });
  fireEvent.click(screen.getByRole('tab', { name: 'Overview' }));
  await openChoice();
  await expectDepth(1);
  expect(api.get_view.mock.calls.slice(afterShrink).every(args => args[5] === 1)).toBe(true);
});

it('keeps consumed depth one across same-path hydration despite a renewed depth two hint', async () => {
  const { api } = nativeApi({ hint: 2 });
  render(<App/>);
  await openChoice();
  await setDepthOneExplicitly();
  const afterShrink = api.get_view.mock.calls.length;
  fireEvent.click(screen.getByRole('button', { name: 'Re-analyze' }));
  await waitFor(() => expect(api.analyze).toHaveBeenCalledOnce());
  await waitFor(() => expect(api.get_overview.mock.calls.length).toBeGreaterThan(1));
  await expectDepth(1);
  expect(api.get_view.mock.calls.slice(afterShrink).every(args => args[5] === 1)).toBe(true);
});

it('applies the same root hint afresh in a different project path', async () => {
  const { api } = nativeApi({ hint: 2 });
  render(<App/>);
  await openChoice();
  await setDepthOneExplicitly();
  fireEvent.click(screen.getByRole('button', { name: /^project-b / }));
  await waitFor(() => expect(api.open_project).toHaveBeenCalledWith('E:\\project-b'));
  await waitFor(() => expect(api.get_overview.mock.calls.length).toBeGreaterThan(1));
  await waitFor(() => expect(api.get_view.mock.calls.at(-1)?.[5]).toBe(2));
  await expectDepth(2);
});

it('does not reuse a hinted root depth for an unhinted different root', async () => {
  const { api } = nativeApi({ hint: 2 });
  render(<App/>);
  await openChoice();
  if (screen.queryByRole('button', { name: 'Fewer layers · 1/5' })) {
    const more = screen.getByRole('button', { name: /^More layers/ }) as HTMLButtonElement;
    await waitFor(() => expect(more.disabled).toBe(false));
    fireEvent.click(more);
  }
  await expectDepth(2);
  fireEvent.click(screen.getByRole('tab', { name: 'Overview' }));
  await openChoice('Other root');
  await expectDepth(1, 'other-model');
  expect(api.get_view.mock.calls.at(-1)?.[1]).toBe('other-model');
  expect(api.get_view.mock.calls.at(-1)?.[5]).toBe(1);
});
