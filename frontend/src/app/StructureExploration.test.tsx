import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { anchor, graph, ok, overview, selectedNode, session, viewState } from './testFixtures';
import type { Anchor, Envelope, Graph, InspectorData, Layout, ViewKind, ViewState, VisibleNode } from '../types';

function nativeApi(kind: ViewKind, selectedId: string | null = null) {
  const state: ViewState = { ...viewState, activeView: kind, selectedId,
    openTabs: ['Overview', kind], graphRoots: { [kind]: 'model' } };
  const api = {
    get_session: vi.fn(async () => ok(session)),
    get_preferences: vi.fn(async () => ok({ language: 'en' as const, issue: null })),
    get_view_state: vi.fn(async () => ok(state)),
    get_overview: vi.fn(async () => ok(overview)),
    get_problems: vi.fn(async () => ok([])),
    get_view: vi.fn(async (...args: [ViewKind, string | null, string | null, number?, string?, number?]): Promise<Envelope<Graph>> =>
      ok({ ...graph, viewKind: args[0], queryContext: {
        controlMode: kind === 'FunctionFlow' || kind === 'Execution' ? 'none' : 'relations',
        structureDepth: 1, canExpand: false, depthLimit: false, hasMore: false, budgetTruncated: false,
      } })),
    get_inspector: vi.fn(async (...args: [ViewKind, string | null, string, (string | null)?, number?]): Promise<Envelope<InspectorData>> =>
      ok({ ...selectedNode, id: args[2], evidence: [], children: [], relations: [], data: [], canOpenSource: true })),
    save_layout: vi.fn(async (layout: Layout) => ok({ ...state, ...layout })),
    save_viewport: vi.fn(async () => ok(state)),
    get_detail_view: vi.fn(async (): Promise<Envelope<Graph>> => ok({ ...graph, nodes: [{ ...selectedNode, id: 'detail:layers', displayName: 'Layers definition detail' }], edges: [] })),
    read_source: vi.fn(async (sourceAnchor: Anchor) => ok({ filePath: sourceAnchor.filePath,
      text: Array.from({ length: 40 }, (_, index) => index === 34 ? '        self.layers = nn.ModuleList([Block(), Block()])' : `# model.py line ${index + 1}`).join('\n'),
      startLine: 1, endLine: 40 })),
  };
  window.pywebview = { api };
  return { api, state };
}

const modelNode: VisibleNode = { ...selectedNode, id: 'model', displayName: 'Classifier',
  codeName: 'model.Classifier', role: 'Model', codeNodeIds: ['class:Classifier'], semanticComponentIds: ['model'], sourceAnchors: [anchor] };
const encoderNode: VisibleNode = { ...selectedNode, id: 'encoder', displayName: 'Encoder instance',
  codeName: 'model.Classifier.encoder', codeNodeIds: ['assignment:encoder'], semanticComponentIds: ['encoder'],
  metadata: { target_definition_id: 'definition:Encoder' } };
const layersNode: VisibleNode = { ...selectedNode, id: 'projection:model/encoder/layers', displayName: 'Encoder layers',
  codeName: 'model.Encoder.layers', codeNodeIds: ['assignment:Encoder.layers'], semanticComponentIds: ['definition:Encoder.layers'],
  sourceAnchors: [{ ...anchor, startLine: 35, endLine: 37 }], metadata: { definition_projection: true, definition_id: 'definition:Encoder', instance_id: 'encoder' } };

function structureGraph(depth = 1, options: { canExpand?: boolean; budgetTruncated?: boolean; depthLimit?: boolean } = {}): Graph {
  const nodes = depth > 1 ? [modelNode, encoderNode, layersNode] : [modelNode, encoderNode];
  const queryContext = { controlMode: 'structure' as const, structureDepth: depth, canExpand: options.canExpand ?? depth < 2,
    depthLimit: options.depthLimit ?? false, hasMore: false, budgetTruncated: options.budgetTruncated ?? false };
  return { ...graph, nodes, rootIds: ['model'],
    edges: nodes.slice(1).map((node, i) => ({ source: nodes[i].id, target: node.id, type: 'CONTAINS',
      confidence: 'Exact', sourceAnchors: node.sourceAnchors, codeEdgeIds: [], semanticComponentIds: node.semanticComponentIds })),
    queryContext,
  };
}

function structureApi(selectedId: string | null = null) {
  const result = nativeApi('Architecture', selectedId);
  result.api.get_view.mockImplementation(async (...args) => ok(structureGraph(args[5] ?? 1)));
  result.api.get_inspector.mockImplementation(async (...args) => {
    const node = [modelNode, encoderNode, layersNode].find(item => item.id === args[2]);
    if (!node) throw new Error(`Unexpected structure inspector node: ${args[2]}`);
    return ok({ ...node, evidence: ['Definition: model.Encoder; instance: model.Classifier.encoder'],
      children: [], relations: [], data: [], canOpenSource: node.sourceAnchors.length > 0 });
  });
  return result;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(finish => { resolve = finish; });
  return { promise, resolve };
}
function announcements() {
  return Array.from(document.querySelectorAll('[role="status"], [aria-live]')).map(element => element.textContent).join(' ');
}
async function structureControls() {
  const controls = await toolbar();
  const more = controls.getByRole('button', { name: /More layers|More depth|Expand structure/i }) as HTMLButtonElement;
  const fewer = controls.getByRole('button', { name: /Fewer layers|Fewer depth|Reduce structure/i }) as HTMLButtonElement;
  await waitFor(() => expect(more.disabled).toBe(false));
  return { more, fewer };
}
function canvasNodes() {
  return Array.from(document.querySelectorAll('.react-flow__node')).map(element => element.getAttribute('data-id'));
}

async function toolbar() {
  await screen.findByRole('button', { name: /^Inspector/ });
  const element = document.querySelector('.query-toolbar');
  if (!element) throw new Error('App did not render its query toolbar');
  return within(element as HTMLElement);
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { cleanup(); delete window.pywebview; vi.unstubAllGlobals(); });

describe('structure exploration through the real workspace', () => {
  it('keeps the bounded depth two graph after opening and closing the actual Source tab, then fewer layers works', async () => {
    const { api } = structureApi();
    render(<App />);
    const { more } = await structureControls();
    fireEvent.click(more);
    await waitFor(() => expect(canvasNodes()).toEqual(['model', 'encoder', layersNode.id]));
    const boundedIds = canvasNodes();
    const card = screen.getByText('Encoder layers').closest('.react-flow__node');
    if (!card) throw new Error('Expanded definition node was not rendered');
    fireEvent.click(card);
    await screen.findByText('model.py:35–37');
    expect(api.get_inspector).toHaveBeenLastCalledWith('Architecture', 'model', layersNode.id, null, 2);
    fireEvent.click(screen.getByRole('button', { name: 'Open source' }));
    const sourceTab = await screen.findByRole('tab', { name: 'model.py' });
    expect(sourceTab.getAttribute('aria-selected')).toBe('true');
    await waitFor(() => expect(document.querySelector('.code-table tr.highlighted pre')?.textContent)
      .toBe('        self.layers = nn.ModuleList([Block(), Block()])'));
    expect(api.read_source).toHaveBeenCalledWith(layersNode.sourceAnchors[0]);
    const close = sourceTab.closest('.tab')?.querySelector<HTMLButtonElement>('.close-tab');
    if (!close) throw new Error('Source tab did not expose its close button');
    fireEvent.click(close);
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Architecture' }).getAttribute('aria-selected')).toBe('true'));
    await waitFor(() => expect(canvasNodes()).toEqual(boundedIds));
    const controls = await toolbar();
    const fewer = controls.getByRole('button', { name: 'Fewer layers · 2/5' }) as HTMLButtonElement;
    await waitFor(() => expect(fewer.disabled).toBe(false));
    expect(api.get_view.mock.calls.at(-1)?.[5]).toBe(2);
    fireEvent.click(fewer);
    await waitFor(() => expect(canvasNodes()).toEqual(['model', 'encoder']));
    expect(api.get_view).toHaveBeenLastCalledWith('Architecture', 'model', null, 1, 'both', 1);
    expect((await toolbar()).getByRole('button', { name: 'Fewer layers · 1/5' }).hasAttribute('disabled')).toBe(true);
    await waitFor(() => expect(api.save_layout).toHaveBeenCalled());
    expect(api.save_layout.mock.calls.every(([layout]) => !('structureDepth' in layout))).toBe(true);
  });

  it('keeps depth two after visiting Overview and returning to Architecture', async () => {
    structureApi();
    render(<App />);
    fireEvent.click((await structureControls()).more);
    await waitFor(() => expect(canvasNodes()).toEqual(['model', 'encoder', layersNode.id]));
    fireEvent.click(screen.getByRole('tab', { name: 'Overview' }));
    await screen.findByRole('heading', { name: 'project-a' });
    fireEvent.click(screen.getByRole('tab', { name: 'Architecture' }));
    await waitFor(() => expect(canvasNodes()).toEqual(['model', 'encoder', layersNode.id]));
    expect((await toolbar()).getByRole('button', { name: 'Fewer layers · 2/5' }).hasAttribute('disabled')).toBe(false);
  });

  it('resets depth for a different root and does not cache the old root depth permanently', async () => {
    const { api } = structureApi();
    api.get_overview.mockResolvedValue(ok({ ...overview, models: [
      { id: 'model', name: 'Classifier root', sourceAnchor: anchor },
      { id: 'other-model', name: 'Other model root', sourceAnchor: anchor },
    ] }));
    api.get_view.mockImplementation(async (...args) => {
      const result = structureGraph(args[5] ?? 1);
      if (args[1] !== 'other-model') return ok(result);
      return ok({ ...result, rootIds: ['other-model'], nodes: [{ ...modelNode, id: 'other-model', displayName: 'Other model' }], edges: [] });
    });
    render(<App />);
    fireEvent.click((await structureControls()).more);
    await waitFor(() => expect(canvasNodes()).toContain(layersNode.id));
    fireEvent.click(screen.getByRole('tab', { name: 'Overview' }));
    fireEvent.click(await screen.findByRole('button', { name: /Other model root/ }));
    await waitFor(() => expect(canvasNodes()).toEqual(['other-model']));
    expect(api.get_view).toHaveBeenLastCalledWith('Architecture', 'other-model', 'other-model', 1, 'both', 1);
    expect((await toolbar()).getByRole('button', { name: 'Fewer layers · 1/5' }).hasAttribute('disabled')).toBe(true);
    fireEvent.click(screen.getByRole('tab', { name: 'Overview' }));
    fireEvent.click(await screen.findByRole('button', { name: /Classifier root/ }));
    await waitFor(() => expect(canvasNodes()).toEqual(['model', 'encoder']));
    expect(api.get_view).toHaveBeenLastCalledWith('Architecture', 'model', 'model', 1, 'both', 1);
  });

  it.each(['different project', 'reanalyze', 'reopen project'] as const)('resets depth two to one after %s', async operation => {
    const { api } = structureApi();
    const nextSession = { ...session, projectPath: operation === 'different project' ? 'E:\\project-b' : session.projectPath };
    const openProject = vi.fn(async () => ok(nextSession));
    const analyze = vi.fn(async () => ok(nextSession));
    window.pywebview!.api!.open_project = openProject;
    window.pywebview!.api!.analyze = analyze;
    api.get_session.mockResolvedValue(ok(nextSession));
    api.get_session.mockResolvedValueOnce(ok(operation === 'reopen project'
      ? { ...session, recentProjects: [session.projectPath!] } : session));
    render(<App />);
    fireEvent.click((await structureControls()).more);
    await waitFor(() => expect(canvasNodes()).toEqual(['model', 'encoder', layersNode.id]));
    const beforeOperation = api.get_view.mock.calls.length;
    if (operation === 'reanalyze') fireEvent.click(screen.getByRole('button', { name: 'Re-analyze' }));
    else {
      const recent = document.querySelector<HTMLButtonElement>('.recent-project');
      if (!recent) throw new Error('Recent project entry was not rendered');
      fireEvent.click(recent);
    }
    await waitFor(() => expect(operation === 'reanalyze' ? analyze : openProject).toHaveBeenCalledOnce());
    await waitFor(() => expect(api.get_view.mock.calls.length).toBeGreaterThan(beforeOperation));
    await waitFor(() => expect(canvasNodes()).toEqual(['model', 'encoder']));
    expect(api.get_view).toHaveBeenLastCalledWith('Architecture', 'model', null, 1, 'both', 1);
    expect((await toolbar()).getByRole('button', { name: 'Fewer layers · 1/5' }).hasAttribute('disabled')).toBe(true);
    fireEvent.click((await structureControls()).more);
    await waitFor(() => expect(canvasNodes()).toContain(layersNode.id));
    expect(api.get_view).toHaveBeenLastCalledWith('Architecture', 'model', null, 1, 'both', 2);
  });

  it('starts at depth one when App reopens from its saved layout', async () => {
    const { api, state } = structureApi();
    const mounted = render(<App />);
    fireEvent.click((await structureControls()).more);
    await waitFor(() => expect(canvasNodes()).toContain(layersNode.id));
    await waitFor(() => expect(api.save_layout).toHaveBeenCalled());
    const savedLayout = api.save_layout.mock.calls.at(-1)![0];
    expect(savedLayout).not.toHaveProperty('structureDepth');
    api.get_view_state.mockResolvedValue(ok({ ...state, ...savedLayout }));
    mounted.unmount();
    api.get_view.mockClear();
    render(<App />);
    await waitFor(() => expect(canvasNodes()).toEqual(['model', 'encoder']));
    expect(api.get_view).toHaveBeenLastCalledWith('Architecture', 'model', null, 1, 'both', 1);
    expect((await toolbar()).getByRole('button', { name: 'Fewer layers · 1/5' }).hasAttribute('disabled')).toBe(true);
  });

  it.each(['FunctionFlow', 'Execution'] as const)('%s hides ineffective depth and relation controls', async kind => {
    nativeApi(kind);
    render(<App />);
    const controls = await toolbar();
    expect(controls.queryByRole('button', { name: /^More hops/ })).toBeNull();
    expect(controls.queryByRole('button', { name: /^Fewer hops/ })).toBeNull();
    for (const name of ['Upstream', 'Downstream', 'Both']) {
      expect(controls.queryByRole('button', { name })).toBeNull();
    }
    expect(controls.queryByText('Relations')).toBeNull();
    fireEvent.click(controls.getByRole('button', { name: /^Inspector/ }));
    expect(screen.queryByText('Select a node to inspect it.')).toBeNull();
  });

  it.each(['Symbol', 'DataFlow'] as const)('%s without a root or selection explains Search instead of requesting an invalid graph', async kind => {
    const { api, state } = nativeApi(kind);
    state.graphRoots = { [kind]: null };
    state.selectedId = null;
    render(<App />);
    await waitFor(() => expect(screen.getByRole('tab', { name: kind === 'Symbol' ? 'Symbols' : 'Data flow' }).getAttribute('aria-selected')).toBe('true'));
    const primary = document.querySelector('.primary-editor');
    if (!primary) throw new Error('The primary editor was not rendered');
    const editor = within(primary as HTMLElement);
    const search = await editor.findByRole('button', { name: /search/i });
    expect(primary.textContent).toMatch(/search.*(?:symbol|data)|(?:symbol|data).*search/i);
    expect(api.get_view).not.toHaveBeenCalled();
    expect(api.get_inspector).not.toHaveBeenCalled();
    expect(canvasNodes()).toEqual([]);
    expect(screen.queryByRole('alert')).toBeNull();
    fireEvent.click(search);
    const query = await screen.findByRole('textbox', { name: 'Search' }) as HTMLInputElement;
    expect(query.disabled).toBe(false);
    expect(api.get_view).not.toHaveBeenCalled();
  });

  it.each(['Symbol', 'DataFlow'] as const)('%s keeps working relation controls and displays the returned neighborhood', async kind => {
    const { api } = nativeApi(kind, 'layer');
    api.get_view.mockImplementation(async (...args) => ok({ ...graph, viewKind: args[0],
      nodes: (args[3] ?? 1) > 1 ? [selectedNode, { ...layersNode, id: 'related', displayName: 'Downstream consumer' }] : [selectedNode],
      edges: [], queryContext: { controlMode: 'relations', hasMore: true, budgetTruncated: false } }));
    render(<App />);
    const controls = await toolbar();
    fireEvent.click(controls.getByRole('button', { name: 'Downstream' }));
    const more = controls.getByRole('button', { name: /^More hops/ }) as HTMLButtonElement;
    await waitFor(() => expect(more.disabled).toBe(false));
    fireEvent.click(more);
    await screen.findByText('Downstream consumer');
    expect(canvasNodes()).toContain('related');
    expect(api.get_view).toHaveBeenLastCalledWith(kind, 'model', 'layer', 2, 'out');
    expect(api.get_inspector).toHaveBeenLastCalledWith(kind, 'model', 'layer', null);
    expect(controls.getByRole('button', { name: /^Fewer hops/ })).toBeDefined();
  });

  it('depth 1 → 2 adds a source-backed definition node and reports it; depth 2 → 1 removes it and clears selection', async () => {
    const { api } = structureApi();
    render(<App />);
    const { more, fewer } = await structureControls();
    await waitFor(() => expect(canvasNodes()).toEqual(['model', 'encoder']));
    expect((await toolbar()).queryByRole('button', { name: 'Upstream' })).toBeNull();
    fireEvent.click(more);
    await screen.findByText('Encoder layers');
    await waitFor(() => expect(canvasNodes()).toEqual(['model', 'encoder', layersNode.id]));
    expect(announcements()).toMatch(/(?:added|new).*1|1.*(?:added|new)/i);
    const card = screen.getByText('Encoder layers').closest('.react-flow__node');
    if (!card) throw new Error('Expanded source-backed node was not rendered in GraphEditor');
    fireEvent.click(card);
    await screen.findByText('model.py:35–37');
    expect(api.get_inspector).toHaveBeenLastCalledWith('Architecture', 'model', layersNode.id, null, 2);
    await waitFor(() => expect(fewer.disabled).toBe(false));
    fireEvent.click(fewer);
    await waitFor(() => expect(canvasNodes()).toEqual(['model', 'encoder']));
    expect(announcements()).toMatch(/(?:removed|collapsed|hidden).*1|1.*(?:removed|collapsed|hidden)/i);
    expect(screen.queryByText('Encoder layers')).toBeNull();
    expect(screen.queryByText('model.py:35–37')).toBeNull();
    expect(api.get_view.mock.calls.at(-1)).toEqual(['Architecture', 'model', null, 1, 'both', 1]);
    await waitFor(() => expect(api.save_layout).toHaveBeenLastCalledWith(expect.objectContaining({ selectedId: null })));
    expect(api.save_layout.mock.calls.at(-1)?.[0]).not.toHaveProperty('structureDepth');
  });

  it('a pending depth request keeps the current nodes and never announces successful expansion before completion', async () => {
    const { api } = structureApi();
    render(<App />);
    const { more } = await structureControls();
    const pending = deferred<Envelope<Graph>>();
    api.get_view.mockImplementationOnce(() => pending.promise);
    fireEvent.click(more);
    await waitFor(() => expect(api.get_view.mock.calls.at(-1)?.[5]).toBe(2));
    expect(canvasNodes()).toEqual(['model', 'encoder']);
    expect(announcements()).toMatch(/updating|loading/i);
    expect(announcements()).not.toMatch(/(?:added|new).*1|1.*(?:added|new)/i);
    await act(async () => { pending.resolve(ok(structureGraph(2))); });
    await waitFor(() => expect(canvasNodes()).toContain(layersNode.id));
    expect(announcements()).toMatch(/(?:added|new).*1|1.*(?:added|new)/i);
  });

  it('a failed depth request reports the error, retains the existing graph and never claims nodes were added', async () => {
    const { api } = structureApi();
    render(<App />);
    const { more } = await structureControls();
    api.get_view.mockResolvedValueOnce({ ok: false, schemaVersion: 1, error: { code: 'QUERY_FAILED', message: 'Definition query failed' } });
    fireEvent.click(more);
    expect((await screen.findByRole('alert')).textContent).toContain('Definition query failed');
    expect(canvasNodes()).toEqual(['model', 'encoder']);
    expect(announcements()).not.toMatch(/(?:added|new).*1|1.*(?:added|new)/i);
    expect(announcements()).not.toMatch(/updating|loading/i);
  });

  it('ignores a pending structure response and its feedback after switching to FunctionFlow', async () => {
    const { api } = structureApi();
    render(<App />);
    const { more } = await structureControls();
    const pending = deferred<Envelope<Graph>>();
    api.get_view.mockImplementationOnce(() => pending.promise);
    fireEvent.click(more);
    await waitFor(() => expect(api.get_view.mock.calls.at(-1)?.[5]).toBe(2));
    api.get_view.mockImplementation(async kind => ok({ ...graph, viewKind: kind, nodes: [{ ...selectedNode, id: 'statement', displayName: 'Return prediction' }],
      queryContext: { controlMode: 'none', structureDepth: 1, canExpand: false, depthLimit: false, hasMore: false, budgetTruncated: false } }));
    fireEvent.click(screen.getByRole('button', { name: /^Function flow/ }));
    await screen.findByText('Return prediction');
    await act(async () => { pending.resolve(ok(structureGraph(2))); });
    expect(canvasNodes()).toEqual(['statement']);
    expect(screen.queryByText('Encoder layers')).toBeNull();
    expect(announcements()).not.toMatch(/(?:added|new).*1|1.*(?:added|new)/i);
    expect((await toolbar()).queryByRole('button', { name: /More layers|More depth|More hops/i })).toBeNull();
  });

  it('explains an Architecture with no model and opens the available project structure entry', async () => {
    const { api } = nativeApi('Architecture');
    api.get_view.mockImplementation(async kind => ok(kind === 'Architecture'
      ? { ...structureGraph(), nodes: [], edges: [], rootIds: [], queryContext: { controlMode: 'structure', structureDepth: 1, canExpand: false, depthLimit: false, hasMore: false, budgetTruncated: false } }
      : { ...structureGraph(), viewKind: kind, rootIds: ['project:project-a'], nodes: [
        { ...modelNode, id: 'project:project-a', displayName: 'project-a', codeName: null, role: 'Project',
          sourceAnchors: [], codeNodeIds: ['module:app'], semanticComponentIds: [], memberIds: ['module:app'] },
        { ...modelNode, id: 'module:app', displayName: 'app.py', codeName: 'app', role: 'Module',
          codeNodeIds: ['module:app'], semanticComponentIds: [], sourceAnchors: [{ ...anchor, filePath: 'app.py' }] },
      ], edges: [{ source: 'project:project-a', target: 'module:app', type: 'CONTAINS', confidence: 'Exact',
        sourceAnchors: [], codeEdgeIds: [], semanticComponentIds: [] }] }));
    render(<App />);
    await screen.findByText(/no.*model|model.*not.*(?:found|detected)/i);
    const entries = screen.getAllByRole('button', { name: /Project structure/i });
    fireEvent.click(entries.at(-1)!);
    await screen.findByText('app.py');
    expect(api.get_view.mock.calls.at(-1)?.[0]).toBe('ProjectStructure');
    expect(canvasNodes()).toEqual(['project:project-a', 'module:app']);
  });

  it('an unchanged completed graph explains that no deeper source-backed nodes exist without claiming expansion', async () => {
    const { api } = structureApi();
    render(<App />);
    const { more } = await structureControls();
    api.get_view.mockResolvedValueOnce(ok({ ...structureGraph(1), queryContext: { ...structureGraph(2).queryContext } }));
    fireEvent.click(more);
    await waitFor(() => expect(announcements()).toMatch(/no.*(?:new|more|expand|change)|nothing.*expand|deepest/i));
    expect(canvasNodes()).toEqual(['model', 'encoder']);
    expect(announcements()).not.toMatch(/(?:added|new).*1|1.*(?:added|new)/i);
    expect(more.disabled).toBe(true);
  });

  it('distinguishes a budget-truncated result from reaching the deepest available level', async () => {
    const { api } = structureApi();
    render(<App />);
    const { more } = await structureControls();
    api.get_view.mockResolvedValueOnce(ok({ ...structureGraph(1), queryContext: structureGraph(2, { canExpand: true, budgetTruncated: true }).queryContext }));
    fireEvent.click(more);
    await waitFor(() => expect(announcements()).toMatch(/budget|truncat/i));
    expect(canvasNodes()).toEqual(['model', 'encoder']);
    expect(announcements()).not.toMatch(/deepest|no.*(?:more|expand)/i);
  });

  it('enforces depth five even when additional structure exists, without changing relationship radius', async () => {
    const { api } = structureApi();
    api.get_view.mockImplementation(async (...args) => ok(structureGraph(args[5] ?? 1, { canExpand: true, depthLimit: args[5] === 5 })));
    render(<App />);
    const { more, fewer } = await structureControls();
    expect(fewer.disabled).toBe(true);
    for (const depth of [2, 3, 4, 5]) {
      await waitFor(() => expect(more.disabled).toBe(false));
      fireEvent.click(more);
      await waitFor(() => expect(api.get_view.mock.calls.at(-1)).toEqual(['Architecture', 'model', null, 1, 'both', depth]));
    }
    await waitFor(() => expect(more.disabled).toBe(true));
    expect(announcements()).toMatch(/limit|maximum|5/i);
    const count = api.get_view.mock.calls.length;
    fireEvent.click(more);
    expect(api.get_view).toHaveBeenCalledTimes(count);
    expect(canvasNodes()).toEqual(['model', 'encoder', layersNode.id]);
  });

  it('keeps legitimate hidden positions and islands available when depth resets to one', async () => {
    const { api, state } = structureApi(layersNode.id);
    state.positionsByView = { Architecture: { [layersNode.id]: [-123.5, 777.25] } };
    state.detailIslands = [{ id: 'saved-layers', viewKind: 'Architecture', sourceNodeId: layersNode.id, position: [420, 150] }];
    render(<App />);
    const { more, fewer } = await structureControls();
    await waitFor(() => expect(canvasNodes()).toEqual(['model', 'encoder']));
    expect(screen.queryByText('model.py:35–37')).toBeNull();
    expect(api.get_inspector).not.toHaveBeenCalled();
    await waitFor(() => expect(api.save_layout).toHaveBeenLastCalledWith(expect.objectContaining({ selectedId: null })));
    fireEvent.click(more);
    await screen.findByText('Layers definition detail');
    const projected = Array.from(document.querySelectorAll('.react-flow__node')).find(element => element.getAttribute('data-id') === layersNode.id);
    expect(projected?.getAttribute('style')).toContain('translate(-123.5px,777.25px)');
    expect(canvasNodes()).toContain('island:saved-layers');
    fireEvent.click(fewer);
    await waitFor(() => expect(canvasNodes()).toEqual(['model', 'encoder']));
    await waitFor(() => expect(more.disabled).toBe(false));
    fireEvent.click(more);
    await screen.findByText('Layers definition detail');
    expect(canvasNodes()).toContain('island:saved-layers');
    const restored = Array.from(document.querySelectorAll('.react-flow__node')).find(element => element.getAttribute('data-id') === layersNode.id);
    expect(restored?.getAttribute('style')).toContain('translate(-123.5px,777.25px)');
  });

  it.each([
    ['Overview', 'Architecture'],
    ['Architecture', 'Overview'],
  ])('keeps depth four and actual projected nodes when an anchored source tab opens and closes with cached tab order %j', async (...openTabs) => {
    const { api, state } = structureApi();
    state.openTabs = openTabs;
    const member: VisibleNode = { ...layersNode, id: 'projection:model/encoder/layers/member',
      displayName: 'layers[*] ×2', codeName: 'model.Encoder.layers[*]',
      metadata: { definition_projection: true, definition_code_name: 'model.Encoder',
        instance_context: 'model.Classifier.encoder.layers', repeat_count: 2 } };
    const projection: VisibleNode = { ...layersNode, id: 'projection:model/encoder/layers/member/projection',
      displayName: 'Block projection', codeName: 'model.Block.projection',
      codeNodeIds: ['assignment:Block.projection'], semanticComponentIds: ['definition:Block.projection'],
      sourceAnchors: [{ ...anchor, startLine: 7, endLine: 7 }],
      metadata: { definition_projection: true, definition_code_name: 'model.Block',
        instance_context: 'model.Classifier.encoder.layers.layers[*]' } };
    api.get_view.mockImplementation(async (...args) => {
      const depth = args[5] ?? 1;
      const result = structureGraph(depth, { canExpand: depth < 4 });
      const nodes = [...result.nodes, ...(depth >= 3 ? [member] : []), ...(depth >= 4 ? [projection] : [])];
      return ok({ ...result, nodes, edges: nodes.slice(1).map((node, index) => ({
        source: nodes[index].id, target: node.id, type: 'CONTAINS', confidence: 'Exact',
        sourceAnchors: node.sourceAnchors, semanticComponentIds: node.semanticComponentIds, codeEdgeIds: [],
      })) });
    });
    api.get_inspector.mockResolvedValue(ok({ ...projection, evidence: ['Definition: model.Block'],
      children: [], relations: [], data: [], canOpenSource: true }));
    const text = ['import torch.nn as nn', '', '', 'class Block(nn.Module):',
      '    def __init__(self):', '        super().__init__()', '        self.projection = nn.Linear(8, 8)'].join('\n');
    const readSource = vi.fn(async (sourceAnchor: Anchor) => ok({ filePath: sourceAnchor.filePath, text, startLine: 1, endLine: 7 }));
    window.pywebview!.api!.read_source = readSource;
    render(<App />);
    const { more } = await structureControls();
    for (const depth of [2, 3, 4]) {
      await waitFor(() => expect(more.disabled).toBe(false));
      fireEvent.click(more);
      await waitFor(() => expect(canvasNodes()).toHaveLength(depth + 1));
    }
    const deepIds = canvasNodes();
    const card = screen.getByText('Block projection').closest('.react-flow__node');
    if (!card) throw new Error('Deep projection was not rendered in the actual GraphEditor');
    fireEvent.click(card);
    await screen.findByText('model.py:7–7');
    expect(api.get_inspector).toHaveBeenLastCalledWith('Architecture', 'model', projection.id, null, 4);
    fireEvent.click(screen.getByRole('button', { name: 'Open source' }));
    const sourceTab = await screen.findByRole('tab', { name: 'model.py' });
    await waitFor(() => expect(document.querySelector('.code-table tr.highlighted pre')?.textContent)
      .toBe('        self.projection = nn.Linear(8, 8)'));
    expect(readSource).toHaveBeenCalledWith(projection.sourceAnchors[0]);
    await waitFor(() => expect(api.save_layout).toHaveBeenLastCalledWith(expect.objectContaining({
      activeView: null, selectedId: null,
    })));
    const sourceRequests = api.get_view.mock.calls.length;
    const close = sourceTab.closest('.tab')?.querySelector<HTMLButtonElement>('.close-tab');
    if (!close) throw new Error('The actual source tab has no close button');
    fireEvent.click(close);
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Architecture' }).getAttribute('aria-selected')).toBe('true'));
    await waitFor(() => expect(api.get_view.mock.calls.length).toBeGreaterThan(sourceRequests));
    expect(api.get_view.mock.calls.at(-1)).toEqual(['Architecture', 'model', null, 1, 'both', 4]);
    await waitFor(() => expect(canvasNodes()).toEqual(deepIds));
    const fewer = (await toolbar()).getByRole('button', { name: 'Fewer layers · 4/5' }) as HTMLButtonElement;
    await waitFor(() => expect(fewer.disabled).toBe(false));
    fireEvent.click(fewer);
    await waitFor(() => expect(canvasNodes()).toEqual(deepIds.filter(id => id !== projection.id)));
    expect(api.get_view).toHaveBeenLastCalledWith('Architecture', 'model', null, 1, 'both', 3);
    await waitFor(() => expect(api.save_layout).toHaveBeenCalled());
    expect(api.save_layout.mock.calls.every(([layout]) => !('structureDepth' in layout))).toBe(true);
    expect(api.save_layout.mock.calls.every(([layout]) =>
      layout.activeView !== null && layout.activeView !== 'Overview' || layout.selectedId === null)).toBe(true);
  });
});
