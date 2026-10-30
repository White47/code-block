import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { focusedCalls, overviewCalls, upstreamCalls } from './calls-view.fixture';
import { graph, ok, overview, session, viewState } from './testFixtures';
import { releaseSourceModel } from './sourceModels';
import { sourceDocumentKey } from './sourceDocuments';
import type { Anchor, Envelope, Graph, Layout, ViewKind, ViewState, VisibleNode } from '../types';

vi.mock('./components/GraphEditor', () => ({ default: ({ graph: data, onSelect, selectedId }: {
  graph: Graph; selectedId: string | null; onSelect: (node: VisibleNode, detail: null) => void;
}) => <section aria-label={data.viewKind + ' graph'}>
  {data.nodes.map(node => <button key={node.id} aria-pressed={selectedId === node.id}
    onClick={() => onSelect(node, null)}>{node.displayName} · {node.role}</button>)}
  {data.edges.map((edge, index) => <p key={index}>{edge.type}</p>)}
</section> }));

const caller = overviewCalls.nodes.find(node => node.role === 'Function' && node.displayName === 'main')!;
const callee = overviewCalls.nodes.find(node => node.role === 'Function' && node.displayName === 'normalize')!;
const callSite = overviewCalls.nodes.find(node => node.role === 'CallSite' && node.displayName === 'normalize')!;

afterEach(() => {
  cleanup(); releaseSourceModel(sourceDocumentKey(session.projectPath!, callSite.sourceAnchors[0].filePath));
  delete window.pywebview;
});

function setup(empty = false, explicit = false) {
  let state: ViewState = { ...viewState, selectedId: 'semantic-choice', activeView: 'Architecture',
    openTabs: ['Overview', 'Architecture'], graphRoots: { Architecture: 'model' }, detailIslands: [] };
  if (explicit) state = { ...state, activeView: 'Calls', openTabs: ['Overview', 'Calls'],
    graphRoots: { Calls: caller.id }, selectedId: 'foreign-call' };
  const api = {
    get_session: vi.fn(async () => ok(session)), get_preferences: vi.fn(async () => ok({ language: 'en' as const, issue: null })),
    get_view_state: vi.fn(async () => ok(state)), get_overview: vi.fn(async () => ok(overview)),
    get_problems: vi.fn(async () => ok([])), save_layout: vi.fn(async (layout: Layout) => ok(state = { ...state, ...layout })),
    get_view: vi.fn(async (kind: ViewKind, root: string | null, selected: string | null, ...query: [number?, string?]): Promise<Envelope<Graph>> => {
      if (kind === 'Calls' && selected === 'foreign-call') return { ok: false, schemaVersion: 1,
        error: { code: 'INVALID_REQUEST', message: 'The graph request is invalid.' } };
      if (kind !== 'Calls') return ok({ ...graph, nodes: [{ ...graph.nodes[0], id: 'semantic-choice' }] });
      if (empty) return ok({ ...overviewCalls, nodes: [], edges: [], layoutHints: {} });
      return ok(root ? query[1] === 'in' ? upstreamCalls : focusedCalls : overviewCalls);
    }),
    get_inspector: vi.fn(async (_kind: ViewKind, _root: string | null, selected: string) => ok({
      ...(overviewCalls.nodes.find(node => node.id === selected) ?? caller),
      evidence: [], children: [], relations: [], data: [], canOpenSource: true,
    })),
    open_source_document: vi.fn(async (projectPath: string, anchor: Anchor) => ok({
      projectPath, filePath: anchor.filePath, text: 'raise RuntimeError("AST only")\nfrom helpers import normalize\ndef main(value):\n    return normalize(value)\n',
      revision: 'owned-fixture:1', encoding: 'utf-8', newline: 'LF' as const, readOnlyReason: null,
    })),
  };
  window.pywebview = { api };
  return api;
}

async function openCalls() {
  await screen.findByRole('region', { name: 'Architecture graph' });
  fireEvent.click(within(document.querySelector('.sidebar') as HTMLElement).getByRole('button', { name: /Calls/ }));
  return screen.findByRole('region', { name: 'Calls graph' });
}

it('opens an overview without semantic selection, focuses explicitly, preserves selection on the active direction, and returns', async () => {
  const api = setup(); render(<App/>);
  const calls = await openCalls();
  expect(within(calls).getAllByText('CALLS').length).toBeGreaterThan(0);
  expect(api.get_view).toHaveBeenLastCalledWith('Calls', null, null, 1, 'both');
  fireEvent.click(within(calls).getByRole('button', { name: 'main · Function' }));
  fireEvent.click(screen.getByRole('button', { name: 'Focus selected call' }));
  await waitFor(() => expect(api.get_view).toHaveBeenLastCalledWith('Calls', caller.id, caller.id, 1, 'both'));
  fireEvent.click(within(screen.getByRole('region', { name: 'Calls graph' })).getByRole('button', { name: 'normalize · Function' }));
  await waitFor(() => expect(api.get_inspector).toHaveBeenLastCalledWith('Calls', caller.id, callee.id, null));
  const requests = api.get_view.mock.calls.length;
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Both' })); });
  expect(api.get_view.mock.calls.length).toBe(requests);
  expect(within(screen.getByRole('region', { name: 'Calls graph' })).getByRole('button', { name: 'normalize · Function' }).getAttribute('aria-pressed')).toBe('true');
  expect(api.get_inspector).toHaveBeenLastCalledWith('Calls', caller.id, callee.id, null);
  fireEvent.click(screen.getByRole('button', { name: 'Upstream' }));
  await waitFor(() => expect(api.get_view).toHaveBeenLastCalledWith('Calls', caller.id, caller.id, 1, 'in'));
  fireEvent.click(screen.getByRole('button', { name: 'Call overview' }));
  await waitFor(() => expect(api.get_view).toHaveBeenLastCalledWith('Calls', null, null, 1, 'both'));
});

it('leaves explicit invalid call identities intact for native rejection instead of rewriting them into a valid root', async () => {
  const api = setup(false, true); render(<App/>);
  await waitFor(() => expect(api.get_view).toHaveBeenCalledWith('Calls', caller.id, 'foreign-call', 1, 'both'));
  expect(await screen.findByText(/INVALID_REQUEST/)).toBeTruthy();
  expect(screen.queryByText('No statically identified calls are available in this scope.')).toBeNull();
});

it('explains a successfully loaded empty call graph instead of leaving an unexplained blank canvas', async () => {
  setup(true); render(<App/>); await openCalls();
  expect(await screen.findByText('No statically identified calls are available in this scope.')).toBeTruthy();
  expect(screen.queryByText(/INVALID_REQUEST/)).toBeNull();
});

it('selects the original call site and opens its source anchor from the captured native graph', async () => {
  const api = setup(); render(<App/>); const calls = await openCalls();
  expect(overviewCalls.rootIds).toEqual([]);
  const call = overviewCalls.edges.find(edge => edge.type === 'CALLS' && edge.source === callSite.id)!;
  expect(call.codeEdgeIds).toEqual([]);
  expect(call.sourceAnchors).toEqual(callSite.sourceAnchors);
  fireEvent.click(within(calls).getByRole('button', { name: 'normalize · CallSite' }));
  await waitFor(() => expect(api.get_inspector).toHaveBeenLastCalledWith('Calls', null, callSite.id, null));
  fireEvent.click(within(document.querySelector('.inspector') as HTMLElement).getByRole('button', { name: 'Open source' }));
  await waitFor(() => expect(api.open_source_document).toHaveBeenCalledWith(session.projectPath, callSite.sourceAnchors[0]));
});
