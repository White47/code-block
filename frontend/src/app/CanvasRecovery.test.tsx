import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { graph, ok, overview, session, viewState } from './testFixtures';
import type { BridgeMethods, Envelope, Graph } from '../types';

// Actual App, QueryToolbar and GraphEditor; only the native Python boundary
// is unavailable in jsdom. Existing App tests cover late project graph replies.
function nativeApi() {
  const state = { ...viewState, activeView: 'Symbol' as const, selectedId: 'helper',
    graphRoots: { Symbol: 'helper' }, openTabs: ['Overview', 'Symbol'] };
  const api = {
    get_session: vi.fn(async () => ok(session)),
    get_preferences: vi.fn(async () => ok({ language: 'en' as const, issue: null })),
    get_view_state: vi.fn(async () => ok(state)),
    get_overview: vi.fn(async () => ok(overview)),
    get_problems: vi.fn(async () => ok([])),
    get_view: vi.fn(async (...args: BridgeMethods['get_view']['args']): Promise<Envelope<Graph>> =>
      ok({ ...graph, viewKind: args[0], rootIds: [args[1]!], nodes: [], edges: [] })),
    save_layout: vi.fn(async () => ok(state)),
    save_viewport: vi.fn(async () => ok(state)),
  };
  window.pywebview = { api };
  return api;
}
function moreButton() {
  return screen.getByRole('button', { name: /^More hops/ }) as HTMLButtonElement;
}
function fewerButton() {
  const button = screen.queryByRole('button', { name: /^Fewer hops/ });
  expect(button, 'QueryToolbar must expose Fewer hops, not canvas zoom-out').not.toBeNull();
  return button as HTMLButtonElement;
}
async function ready() {
  await screen.findByRole('button', { name: /^More hops/ });
  await waitFor(() => expect(moreButton().disabled).toBe(false));
}
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { cleanup(); delete window.pywebview; vi.unstubAllGlobals(); });

describe('canvas query depth recovery', () => {
  it('keeps the same-context real canvas mounted while a hops refresh is pending and after it completes', async () => {
    const api = nativeApi();
    render(<App />);
    await ready();
    const canvas = document.querySelector('.graph-editor');
    expect(canvas).not.toBeNull();
    let resolve!: (value: Envelope<Graph>) => void;
    const pending = new Promise<Envelope<Graph>>(finish => { resolve = finish; });
    api.get_view.mockImplementationOnce(() => pending);
    fireEvent.click(moreButton());
    await waitFor(() => expect(api.get_view).toHaveBeenLastCalledWith('Symbol', 'helper', 'helper', 2, 'both'));
    try {
      expect(document.querySelector('.graph-editor'), 'pending same-context query must not discard the canvas/camera').toBe(canvas);
    } finally {
      await act(async () => { resolve(ok({ ...graph, viewKind: 'Symbol', nodes: [], edges: [], rootIds: ['helper'] })); });
    }
    await waitFor(() => expect(moreButton().disabled).toBe(false));
    expect(document.querySelector('.graph-editor')).toBe(canvas);
  });
  it('reduces expanded query depth 1 → 5 → 3 → 1 without changing root, selection or direction', async () => {
    const api = nativeApi();
    render(<App />);
    await ready();
    fireEvent.click(screen.getByRole('button', { name: 'Downstream' }));
    await waitFor(() => expect(api.get_view).toHaveBeenLastCalledWith('Symbol', 'helper', 'helper', 1, 'out'));
    for (const hops of [2, 3, 4, 5]) {
      await waitFor(() => expect(moreButton().disabled).toBe(false));
      fireEvent.click(moreButton());
      await waitFor(() => expect(api.get_view).toHaveBeenLastCalledWith('Symbol', 'helper', 'helper', hops, 'out'));
    }
    await waitFor(() => expect(fewerButton().disabled).toBe(false));
    expect(moreButton().disabled).toBe(true);
    const atUpperBound = api.get_view.mock.calls.length;
    fireEvent.click(moreButton());
    expect(api.get_view).toHaveBeenCalledTimes(atUpperBound);
    for (const hops of [4, 3, 2, 1]) {
      await waitFor(() => expect(fewerButton().disabled).toBe(false));
      fireEvent.click(fewerButton());
      await waitFor(() => expect(api.get_view).toHaveBeenLastCalledWith('Symbol', 'helper', 'helper', hops, 'out'));
    }
    await waitFor(() => expect(moreButton().disabled).toBe(false));
    expect(fewerButton().disabled).toBe(true);
    const atLowerBound = api.get_view.mock.calls.length;
    fireEvent.click(fewerButton());
    expect(api.get_view).toHaveBeenCalledTimes(atLowerBound);
    expect(api.get_view.mock.calls.map(args => args[3])).toEqual([1, 1, 2, 3, 4, 5, 4, 3, 2, 1]);
    expect(api.get_view.mock.calls.every(args => args[1] === 'helper' && args[2] === 'helper')).toBe(true);
    expect((screen.getByRole('button', { name: 'Downstream' })).classList.contains('active')).toBe(true);
  });

  it('exposes a disabled lower-bound control on the initial one-hop query', async () => {
    const api = nativeApi();
    render(<App />);
    await ready();
    expect(fewerButton().disabled).toBe(true);
    const count = api.get_view.mock.calls.length;
    fireEvent.click(fewerButton());
    await act(async () => {});
    expect(api.get_view).toHaveBeenCalledTimes(count);
    expect(api.get_view).toHaveBeenLastCalledWith('Symbol', 'helper', 'helper', 1, 'both');
  });

  it('prevents depth changes while the current query is pending, then allows shrinking', async () => {
    const api = nativeApi();
    render(<App />);
    await ready();
    const fewer = fewerButton();
    const more = moreButton();
    let resolve!: (value: Envelope<Graph>) => void;
    const pending = new Promise<Envelope<Graph>>(finish => { resolve = finish; });
    api.get_view.mockImplementationOnce(() => pending);
    fireEvent.click(moreButton());
    await waitFor(() => expect(api.get_view).toHaveBeenLastCalledWith('Symbol', 'helper', 'helper', 2, 'both'));
    try {
      const count = api.get_view.mock.calls.length;
      // App may unmount the toolbar while loading rather than leave it
      // disabled; either must prevent another query from a stale control.
      const currentFewer = screen.queryByRole('button', { name: /^Fewer hops/ });
      const currentMore = screen.queryByRole('button', { name: /^More hops/ });
      if (currentFewer) expect((currentFewer as HTMLButtonElement).disabled).toBe(true);
      if (currentMore) expect((currentMore as HTMLButtonElement).disabled).toBe(true);
      fireEvent.click(fewer);
      fireEvent.click(more);
      expect(api.get_view).toHaveBeenCalledTimes(count);
    } finally {
      await act(async () => { resolve(ok({ ...graph, viewKind: 'Symbol', nodes: [], edges: [], rootIds: ['helper'] })); });
    }
    await waitFor(() => expect(fewerButton().disabled).toBe(false));
    fireEvent.click(fewerButton());
    await waitFor(() => expect(api.get_view).toHaveBeenLastCalledWith('Symbol', 'helper', 'helper', 1, 'both'));
  });
});
