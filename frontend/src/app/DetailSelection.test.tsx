import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { anchor, graph, ok, overview, selectedNode, session, viewState } from './testFixtures';
import type { Anchor, BridgeMethods, Envelope, Graph, InspectorData, ViewState, VisibleNode } from '../types';

type NativeMethod<K extends keyof BridgeMethods> = (...args: BridgeMethods[K]['args']) => Promise<Envelope<BridgeMethods[K]['data']>>;
const localAnchor: Anchor = { ...anchor, filePath: 'block.py', startLine: 7, endLine: 9 };
const mainNode: VisibleNode = { ...selectedNode, id: 'GPT', displayName: 'GPT main', codeName: 'model.GPT' };
const headNode: VisibleNode = { ...selectedNode, id: 'lm_head', displayName: 'Language model head', codeName: 'model.GPT.lm_head' };
const localNode: VisibleNode = { ...selectedNode, id: 'Blocktemplate', displayName: 'Block template',
  codeName: 'block.Block', sourceAnchors: [localAnchor], memberIds: ['local-attention'] };
const localChild: VisibleNode = { ...selectedNode, id: 'local-attention', displayName: 'Local attention', codeName: 'block.Block.attn' };

// Exercise App's actual callback dispatch; Inspector, SourceViewer and tabs stay real.
vi.mock('./components/GraphEditor', () => ({ default: ({ graph: value, details, selectedId, onSelect, onCloseIsland, viewState: state }: {
  graph: Graph; details: Record<string, Graph>; selectedId: string | null; viewState: ViewState | null;
  onSelect: (node: VisibleNode, source: string | null) => void; onCloseIsland: (id: string) => void;
}) => <div data-testid="graph-canvas" data-selected-id={selectedId ?? ''}>
  <span data-testid="main-node-ids">{value.nodes.map(node => node.id).join(',')}</span>
  {value.nodes.map(node => <button key={node.id} onClick={() => onSelect(node, null)}>Select main {node.id}</button>)}
  {state?.detailIslands.map(island => <div key={island.id}>
    {details[island.id]?.nodes.map(node => <button key={node.id} onClick={() => onSelect(node, island.sourceNodeId)}>Select local {node.id}</button>)}
    <button onClick={() => onCloseIsland(island.id)}>Close local island</button>
  </div>)}
</div> }));

afterEach(() => { cleanup(); delete window.pywebview; });

function inspector(node: VisibleNode, evidence: string[] = []): InspectorData {
  return { ...node, evidence, children: node.memberIds, relations: node.id === localNode.id
    ? [{ type: 'CONTAINS', otherId: localChild.id, direction: 'out', confidence: 'Exact' }] : [],
  data: [], canOpenSource: true };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(finish => { resolve = finish; });
  return { promise, resolve };
}
function nativeApi(initialSelectedId = 'GPT') {
  let currentSession = { ...session };
  let state: ViewState = { ...viewState, selectedId: initialSelectedId, graphRoots: { Architecture: 'GPT' },
    detailIslands: [{ id: 'GPT-detail', viewKind: 'Architecture', sourceNodeId: 'GPT', position: null }] };
  const api = {
    get_session: vi.fn(async () => ok(currentSession)),
    get_preferences: vi.fn(async () => ok({ language: 'en' as const, issue: null })),
    get_view_state: vi.fn(async () => ok(state)),
    get_overview: vi.fn(async () => ok({ ...overview, projectPath: currentSession.projectPath!, models: [
      { id: 'GPT', name: 'GPT root', sourceAnchor: anchor, initialStructureDepth: 2 as const },
      { id: 'Other', name: 'Other root', sourceAnchor: anchor },
    ] })),
    get_problems: vi.fn(async () => ok([])),
    get_view: vi.fn<NativeMethod<'get_view'>>(async (kind, root, _selected, _hops, _direction, depth = 1) => ok({
      ...graph, viewKind: kind, rootIds: [root!], nodes: [{ ...mainNode, id: root!, displayName: `${root} main` },
        ...(root === 'GPT' && initialSelectedId === headNode.id ? [headNode] : [])],
      queryContext: { controlMode: 'structure', structureDepth: depth, canExpand: false },
    })),
    get_detail_view: vi.fn<NativeMethod<'get_detail_view'>>(async () => ok({ ...graph,
      nodes: [localNode, localChild], rootIds: [localNode.id], edges: [] })),
    get_inspector: vi.fn<NativeMethod<'get_inspector'>>(async (_kind, root, id) => ok(inspector(
      id === localNode.id ? localNode : id === headNode.id ? headNode : { ...mainNode, id, displayName: `${root} main` }))),
    save_layout: vi.fn<NativeMethod<'save_layout'>>(async layout => { state = { ...state, ...layout }; return ok(state); }),
    close_detail_island: vi.fn<NativeMethod<'close_detail_island'>>(async id => {
      state = { ...state, detailIslands: state.detailIslands.filter(island => island.id !== id) }; return ok(state);
    }),
    read_source: vi.fn<NativeMethod<'read_source'>>(async value => ok({ filePath: value.filePath,
      text: Array.from({ length: 12 }, (_, i) => i === 6 ? 'class Block: # local definition' : `# line ${i + 1}`).join('\n'),
      startLine: 1, endLine: 12 })),
    open_project: vi.fn<NativeMethod<'open_project'>>(async path => {
      currentSession = { ...session, projectPath: path };
      state = { ...state, selectedId: 'ProjectB', graphRoots: { Architecture: 'ProjectB' }, detailIslands: [] };
      return ok(currentSession);
    }),
  };
  window.pywebview = { api };
  return api;
}
async function ready(initialSelectedId = 'GPT') {
  const api = nativeApi(initialSelectedId);
  render(<App />);
  await screen.findByRole('button', { name: 'Select local Blocktemplate' }, { timeout: 5000 });
  await waitFor(() => expect(api.get_view).toHaveBeenLastCalledWith('Architecture', 'GPT', initialSelectedId, 1, 'both', 2));
  await screen.findByRole('heading', { name: initialSelectedId === headNode.id ? headNode.displayName : 'GPT main' });
  await waitFor(() => expect(api.save_layout).toHaveBeenCalled());
  api.save_layout.mockClear();
  return api;
}
async function selectLocal() {
  fireEvent.click(await screen.findByRole('button', { name: 'Select local Blocktemplate' }));
  await screen.findByRole('heading', { name: 'Block template' });
}
function expectMainHighlight(id = 'GPT') {
  expect(screen.getByTestId('graph-canvas').getAttribute('data-selected-id')).toBe(id);
}

describe('detail selection is transient in the real App workspace', () => {
  it('explicitly revealing the same GPT root after local Source and Overview overrides the original lm_head selection', async () => {
    const api = await ready(headNode.id);
    expect(screen.getByTestId('main-node-ids').textContent).toBe('GPT,lm_head');
    expectMainHighlight(headNode.id);
    await selectLocal();
    await waitFor(() => expect(api.get_inspector).toHaveBeenLastCalledWith('Architecture', 'GPT', 'Blocktemplate', 'GPT', 2));
    fireEvent.click(screen.getByRole('button', { name: 'Open source' }));
    await waitFor(() => expect(screen.getByRole('tab', { name: 'block.py' }).getAttribute('aria-selected')).toBe('true'));
    await waitFor(() => expect(api.read_source).toHaveBeenCalledWith(localAnchor));
    fireEvent.click(screen.getByRole('tab', { name: 'Overview' }));
    const rootCard = await screen.findByRole('button', { name: /GPT root/ });
    const beforeReveal = api.get_view.mock.calls.length;
    api.save_layout.mockClear();
    fireEvent.click(rootCard);
    await waitFor(() => expect(api.get_view.mock.calls.length).toBeGreaterThan(beforeReveal));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 200)); });
    // Both IDs exist in the main graph: restoring lm_head cannot be hidden by membership validation.
    for (const args of api.get_view.mock.calls.slice(beforeReveal)) {
      expect.soft(args.slice(0, 3)).toEqual(['Architecture', 'GPT', 'GPT']);
    }
    expect.soft(api.save_layout.mock.calls.at(-1)?.[0].selectedId).toBe('GPT');
    expect(api.save_layout.mock.calls.at(-1)?.[0].graphRoots).toEqual({ Architecture: 'GPT' });
    for (const [layout] of api.save_layout.mock.calls) expect.soft(layout.selectedId).toBe('GPT');
    expectMainHighlight('GPT');
    await screen.findByRole('heading', { name: 'GPT main' });
    expect(api.get_inspector).toHaveBeenLastCalledWith('Architecture', 'GPT', 'GPT', null, 2);
  });

  it('closing the actual local Source tab restores and persists the original lm_head main selection', async () => {
    const api = await ready(headNode.id);
    expect(screen.getByTestId('main-node-ids').textContent).toBe('GPT,lm_head');
    await selectLocal();
    await waitFor(() => expect(api.get_inspector).toHaveBeenLastCalledWith('Architecture', 'GPT', 'Blocktemplate', 'GPT', 2));
    fireEvent.click(screen.getByRole('button', { name: 'Open source' }));
    const sourceTab = await screen.findByRole('tab', { name: 'block.py' });
    await waitFor(() => expect(sourceTab.getAttribute('aria-selected')).toBe('true'));
    await waitFor(() => expect(api.read_source).toHaveBeenCalledWith(localAnchor));
    const beforeClose = api.get_view.mock.calls.length;
    api.save_layout.mockClear();
    const closeSource = sourceTab.closest('.tab')?.querySelector<HTMLButtonElement>('.close-tab');
    if (!closeSource) throw new Error('Source tab did not expose its close button');
    fireEvent.click(closeSource);
    await waitFor(() => expect(api.get_view.mock.calls.length).toBeGreaterThan(beforeClose));
    await waitFor(() => expect(api.get_view).toHaveBeenLastCalledWith('Architecture', 'GPT', 'lm_head', 1, 'both', 2));
    await screen.findByRole('heading', { name: headNode.displayName });
    expectMainHighlight(headNode.id);
    await waitFor(() => expect(api.save_layout).toHaveBeenCalled());
    for (const [layout] of api.save_layout.mock.calls) {
      expect(layout.selectedId).toBe('lm_head');
      expect(layout.graphRoots).toEqual({ Architecture: 'GPT' });
    }
    expect(api.get_view.mock.calls.slice(beforeClose).every(args => args[1] === 'GPT' && args[2] === 'lm_head')).toBe(true);
    expect(api.get_inspector).toHaveBeenLastCalledWith('Architecture', 'GPT', 'lm_head', null, 2);
    expect(screen.queryByRole('tab', { name: 'block.py' })).toBeNull();
  });

  it('passes detailSource to Inspector and highlights locally without persisting the local ID after the debounce', async () => {
    const api = await ready();
    expect(screen.getByTestId('main-node-ids').textContent).toBe('GPT');
    await selectLocal();
    await waitFor(() => expect(api.get_inspector).toHaveBeenLastCalledWith('Architecture', 'GPT', 'Blocktemplate', 'GPT', 2));
    expect(screen.getByTestId('graph-canvas').getAttribute('data-selected-id')).toBe('Blocktemplate');
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 200)); });
    for (const [layout] of api.save_layout.mock.calls) {
      expect(layout.selectedId).toBe('GPT');
      expect(layout.graphRoots).toEqual({ Architecture: 'GPT' });
    }
    expect(api.get_view.mock.calls.every(args => args[1] === 'GPT' && args[2] === 'GPT')).toBe(true);
  });

  it('maps local members and relations, opens their exact source anchor, and returns to the original native main selection', async () => {
    const api = await ready();
    await selectLocal();
    await waitFor(() => expect(document.querySelectorAll('.inspector code[title="local-attention"]')).toHaveLength(3));
    for (const label of document.querySelectorAll('.inspector code[title="local-attention"]')) expect(label.textContent).toBe(localChild.codeName);
    expect(document.querySelector('.inspector .relation code[title="local-attention"]')?.textContent).toBe(localChild.codeName);
    expect(document.querySelectorAll('.inspector .member-id[title="local-attention"]')).toHaveLength(2);
    expect(screen.getByText('block.py:7–9')).toBeDefined();
    fireEvent.click(screen.getByRole('button', { name: 'Open source' }));
    const sourceTab = await screen.findByRole('tab', { name: 'block.py' });
    expect(sourceTab.getAttribute('aria-selected')).toBe('true');
    await waitFor(() => expect(document.querySelector('.code-table tr.highlighted pre')?.textContent).toBe('class Block: # local definition'));
    expect(api.read_source).toHaveBeenCalledWith(localAnchor);
    await waitFor(() => expect(api.save_layout).toHaveBeenCalled());
    expect.soft(api.save_layout.mock.calls.at(-1)![0].selectedId).toBe('GPT');
    expect(api.save_layout.mock.calls.at(-1)![0].graphRoots).toEqual({ Architecture: 'GPT' });
    fireEvent.click(screen.getByRole('tab', { name: 'Architecture' }));
    await waitFor(() => expect(api.get_view).toHaveBeenLastCalledWith('Architecture', 'GPT', 'GPT', 1, 'both', 2));
    await screen.findByRole('heading', { name: 'GPT main' });
    expectMainHighlight();
    fireEvent.click(screen.getByRole('tab', { name: 'block.py' }));
    const closeSource = screen.getByRole('tab', { name: 'block.py' }).closest('.tab')?.querySelector<HTMLButtonElement>('.close-tab');
    if (!closeSource) throw new Error('Source tab did not expose its close button');
    fireEvent.click(closeSource);
    await screen.findByRole('heading', { name: 'GPT main' });
    expectMainHighlight();
    await waitFor(() => expect(api.save_layout).toHaveBeenLastCalledWith(expect.objectContaining({ selectedId: 'GPT', graphRoots: { Architecture: 'GPT' } })));
    expect(api.get_view.mock.calls.every(args => args[2] === 'GPT')).toBe(true);
  });

  it('closing a selected island clears local context and restores the main Inspector and highlight', async () => {
    const api = await ready();
    await selectLocal();
    fireEvent.click(screen.getByRole('button', { name: 'Close local island' }));
    await waitFor(() => expect(api.close_detail_island).toHaveBeenCalledWith('GPT-detail'));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Select local Blocktemplate' })).toBeNull());
    await screen.findByRole('heading', { name: 'GPT main' });
    expectMainHighlight();
    expect(api.get_inspector).toHaveBeenLastCalledWith('Architecture', 'GPT', 'GPT', null, 2);
    expect(screen.queryByText('block.py:7–9')).toBeNull();
  });

  it.each(['close island', 'different root', 'different project'] as const)('discards pending local Inspector responses after %s', async operation => {
    const api = await ready();
    const pending = deferred<Envelope<InspectorData>>();
    api.get_inspector.mockImplementationOnce(() => pending.promise);
    await selectLocal();
    await waitFor(() => expect(api.get_inspector).toHaveBeenLastCalledWith('Architecture', 'GPT', 'Blocktemplate', 'GPT', 2));
    const nextId = operation === 'different root' ? 'Other' : operation === 'different project' ? 'ProjectB' : 'GPT';
    if (operation === 'close island') fireEvent.click(screen.getByRole('button', { name: 'Close local island' }));
    else if (operation === 'different project') fireEvent.click(screen.getByText('project-b'));
    else {
      fireEvent.click(screen.getByRole('tab', { name: 'Overview' }));
      fireEvent.click(await screen.findByRole('button', { name: /Other root/ }));
    }
    if (operation === 'close island') {
      await waitFor(() => expect(screen.queryByRole('button', { name: 'Close local island' })).toBeNull());
    } else {
      await screen.findByRole('heading', { name: `${nextId} main` });
    }
    await act(async () => { pending.resolve(ok(inspector({ ...localNode, displayName: 'STALE local Inspector' }, ['STALE local evidence']))); });
    expect(screen.queryByText('STALE local Inspector')).toBeNull();
    expect(screen.queryByText('STALE local evidence')).toBeNull();
    expect(screen.queryByText('block.py:7–9')).toBeNull();
    expectMainHighlight(nextId);
    expect(within(document.querySelector('.inspector') as HTMLElement).getByRole('heading', { name: `${nextId} main` })).toBeDefined();
    expect(api.get_inspector.mock.calls.at(-1)?.slice(0, 4)).toEqual(['Architecture', nextId, nextId, null]);
  });
});
