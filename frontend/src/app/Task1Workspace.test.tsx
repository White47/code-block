import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { anchor, graph, ok, overview, session, viewState } from './testFixtures';

vi.mock('./components/GraphEditor', () => ({ default: () => <div data-testid="generic-canvas"/> }));
afterEach(() => { cleanup(); delete window.pywebview; });

describe('FunctionFlow workspace', () => {
  it('restores the new view and explains the static flow boundary on its canvas', async () => {
    window.pywebview = { api: {
      get_session: vi.fn().mockResolvedValue(ok(session)),
      get_preferences: vi.fn().mockResolvedValue(ok({ language: 'en', issue: null })),
      get_view_state: vi.fn().mockResolvedValue(ok({ ...viewState, activeView: 'FunctionFlow', selectedId: null,
        openTabs: ['Overview', 'FunctionFlow'], graphRoots: { FunctionFlow: 'main' }, detailIslands: [] })),
      get_overview: vi.fn().mockResolvedValue(ok({ ...overview, functions: [{ id: 'main', name: 'main', sourceAnchor: anchor }] })),
      get_problems: vi.fn().mockResolvedValue(ok([])),
      get_view: vi.fn().mockResolvedValue(ok({ ...graph, viewKind: 'FunctionFlow' })),
      save_layout: vi.fn().mockResolvedValue(ok(viewState)),
    } };
    render(<App/>);
    await screen.findByTestId('generic-canvas');
    expect(screen.getByRole('tab', { name: 'Function flow' })).toBeTruthy();
    expect(window.pywebview.api!.get_view).toHaveBeenCalledWith('FunctionFlow', 'main', null, 1, 'both');
    expect(screen.getByText('Static scope flow, not a runtime trace; calls do not inline callees.')).toBeTruthy();
  });
});
