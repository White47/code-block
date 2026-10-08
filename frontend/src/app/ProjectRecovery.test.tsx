import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { anchor, graph, ok, overview, session, viewState } from './testFixtures';
import type { Envelope, Layout, Session, ViewState } from '../types';

// Render the actual App and UI. Only the unavailable native Python boundary
// is simulated; this is not a native-WebView acceptance test.
const projectA = 'E:\\recovery-a';
const projectB = 'E:\\recovery-b';
const sourceId = `source:${encodeURIComponent(JSON.stringify({ ...anchor, filePath: 'legacy.py' }))}`;
const oldState: ViewState = {
  ...viewState, activeView: 'FunctionFlow', selectedId: 'old-step',
  graphRoots: { FunctionFlow: 'old-root' },
  openTabs: ['Overview', sourceId, 'FunctionFlow'], detailIslands: [],
};
const freshState: ViewState = {
  ...oldState, selectedId: 'fresh-step', graphRoots: { FunctionFlow: 'fresh-root' },
  openTabs: ['Overview', 'FunctionFlow'],
};
function failure<T>(code: string, message: string): Envelope<T> {
  return { ok: false, schemaVersion: 1, error: { code, message } };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(finish => { resolve = finish; });
  return { promise, resolve };
}
function recoveryBridge(status: Session['status'], hasAnalysis = status === 'Ready') {
  let current: Session & { hasAnalysis: boolean } = {
    ...session, projectPath: projectA, status, recentProjects: [projectB],
    hasAnalysis,
    outdatedReasons: status === 'Outdated' ? ['analysis version mismatch'] : [],
  };
  let persisted = structuredClone(oldState);
  const writes: { project: string | null; layout: Layout }[] = [];
  const events: string[] = [];
  const api = {
    get_session: vi.fn(async () => ok(current)),
    get_preferences: vi.fn(async () => ok({ language: 'en' as const, issue: null })),
    get_view_state: vi.fn(async () => ok(structuredClone(persisted))),
    get_overview: vi.fn(async () => ok({ ...overview, projectPath: current.projectPath!, projectName: 'recovered-project' })),
    get_problems: vi.fn(async () => ok([])),
    get_view: vi.fn(async () => current.hasAnalysis
      ? ok({ ...graph, viewKind: 'FunctionFlow' as const, nodes: [], rootIds: ['fresh-root'] })
      : failure<typeof graph>('NOT_READY', 'Analysis unavailable')),
    read_source: vi.fn(async () => ok({ filePath: 'legacy.py', text: 'pass\n', startLine: 1, endLine: 1 })),
    save_viewport: vi.fn(async () => ok(persisted)),
    save_layout: vi.fn(async (layout: Layout): Promise<Envelope<ViewState>> => {
      writes.push({ project: current.projectPath, layout: structuredClone(layout) });
      events.push(`save:${current.projectPath}`);
      if (current.status !== 'Ready') return failure('NOT_READY', 'Old analysis is unavailable');
      persisted = { ...persisted, ...layout };
      return ok(persisted);
    }),
    analyze: vi.fn(async (): Promise<Envelope<Session>> => {
      events.push(`analyze:${current.projectPath}`);
      current = { ...current, status: 'Ready', hasAnalysis: true, outdatedReasons: [] };
      persisted = structuredClone(freshState);
      return ok(current);
    }),
    open_project: vi.fn(async (path: string) => {
      events.push(`open:${path}`);
      current = { ...current, projectPath: path, status: 'Ready', hasAnalysis: true, outdatedReasons: [] };
      persisted = structuredClone(freshState);
      return ok(current);
    }),
    choose_project: vi.fn(async () => {
      events.push(`choose:${current.projectPath}`);
      current = { ...current, projectPath: projectB, status: 'Ready', hasAnalysis: true, outdatedReasons: [] };
      persisted = structuredClone(freshState);
      return ok(current);
    }),
  };
  window.pywebview = { api };
  return {
    api, writes, events,
    current: () => current,
    setSession: (value: Session & { hasAnalysis: boolean }) => { current = value; },
  };
}
async function hydratedOldWorkspace() {
  await waitFor(() => expect(document.querySelector('.titlebar-path')?.textContent).toBe(projectA));
  await screen.findByRole('tab', { name: 'Function flow' });
  expect(screen.getAllByRole('tab').some(tab => tab.textContent?.includes('legacy.py'))).toBe(true);
}
function reanalyze() {
  fireEvent.click(screen.getAllByRole('button', { name: 'Re-analyze' })[0]);
}
async function readyHydrated(project: string) {
  await waitFor(() => expect(document.querySelector('.titlebar-path')?.textContent).toBe(project));
  await waitFor(() => expect(document.querySelector('.statusbar')?.textContent).toContain('Ready'));
  await waitFor(() => expect(document.querySelector('.react-flow')).not.toBeNull());
  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Overview', 'Function flow']);
}
beforeEach(() => {
  // jsdom has no layout observer; keep the real GraphEditor mounted.
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { cleanup(); delete window.pywebview; vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('project recovery across obsolete Python layouts', () => {
  it.each(['NotAnalyzed', 'Outdated'] as const)('does not query unavailable analysis or save stale layout for %s, then hydrates fresh analysis', async status => {
    const bridge = recoveryBridge(status, false);
    render(<App />);
    await hydratedOldWorkspace();
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 200)); });
    expect.soft(bridge.api.get_overview).not.toHaveBeenCalled();
    expect.soft(bridge.api.get_problems).not.toHaveBeenCalled();
    expect.soft(bridge.api.get_view).not.toHaveBeenCalled();
    expect.soft(bridge.api.save_layout).not.toHaveBeenCalled();
    reanalyze();
    await waitFor(() => expect(bridge.api.analyze).toHaveBeenCalledOnce());
    await readyHydrated(projectA);
    for (const args of bridge.api.get_view.mock.calls) {
      expect(args).toEqual(['FunctionFlow', 'fresh-root', 'fresh-step', 1, 'both']);
    }
    expect(bridge.writes.some(write => write.layout.graphRoots.FunctionFlow === 'old-root')).toBe(false);
  });

  it('still queries an Outdated session when its old analysis is available', async () => {
    const bridge = recoveryBridge('Outdated', true);
    render(<App />);
    await hydratedOldWorkspace();
    await waitFor(() => expect(bridge.api.get_view).toHaveBeenCalledWith('FunctionFlow', 'old-root', 'old-step', 1, 'both'));
    expect(bridge.api.get_overview).toHaveBeenCalled();
    expect(bridge.api.get_problems).toHaveBeenCalled();
    expect(bridge.api.analyze).not.toHaveBeenCalled();
  });

  it('keeps switching queued while an old native layout write is pending beyond 15 seconds', async () => {
    vi.useFakeTimers();
    const bridge = recoveryBridge('Ready');
    const pending = deferred<Envelope<ViewState>>();
    const completed: { project: string | null; layout: Layout }[] = [];
    bridge.api.save_layout.mockImplementationOnce(async layout => {
      const response = await pending.promise;
      completed.push({ project: bridge.current().projectPath, layout: structuredClone(layout) });
      bridge.events.push(`native:finished:${bridge.current().projectPath}`);
      return response;
    });
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    expect(bridge.api.save_layout).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: /recovery-b/ }));
    await act(async () => { await vi.advanceTimersByTimeAsync(17000); });
    try {
      expect(bridge.api.open_project).not.toHaveBeenCalled();
      expect(bridge.api.choose_project).not.toHaveBeenCalled();
      expect(bridge.api.analyze).not.toHaveBeenCalled();
    } finally {
      await act(async () => { pending.resolve(ok(oldState)); });
      vi.useRealTimers();
    }
    await waitFor(() => expect(bridge.api.open_project).toHaveBeenCalledWith(projectB));
    await readyHydrated(projectB);
    expect(completed).toHaveLength(1);
    expect(completed[0].project).toBe(projectA);
    expect(completed[0].layout.graphRoots.FunctionFlow).toBe('old-root');
    expect(bridge.events.indexOf(`native:finished:${projectA}`)).toBeLessThan(bridge.events.indexOf(`open:${projectB}`));
    expect(bridge.writes.some(write => write.project === projectB && write.layout.graphRoots.FunctionFlow === 'old-root')).toBe(false);
  });
  it('re-analyzes NotAnalyzed despite NOT_READY layout writes and retries repeated analysis failures', async () => {
    const bridge = recoveryBridge('NotAnalyzed');
    const succeed = bridge.api.analyze.getMockImplementation()!;
    bridge.api.analyze
      .mockImplementationOnce(async () => failure('INTERNAL_ERROR', 'analysis attempt 1 failed'))
      .mockImplementationOnce(async () => failure('INTERNAL_ERROR', 'analysis attempt 2 failed'))
      .mockImplementation(succeed);
    render(<App />);
    await hydratedOldWorkspace();
    for (const attempt of [1, 2]) {
      reanalyze();
      await waitFor(() => expect(bridge.api.analyze).toHaveBeenCalledTimes(attempt));
      await waitFor(() => expect(screen.getByRole('alert').textContent).toContain(`analysis attempt ${attempt} failed`));
      await waitFor(() => expect((screen.getByRole('button', { name: 'Re-analyze' }) as HTMLButtonElement).disabled).toBe(false));
      expect(screen.getAllByRole('tab').some(tab => tab.textContent?.includes('legacy.py'))).toBe(true);
    }
    reanalyze();
    await waitFor(() => expect(bridge.api.analyze).toHaveBeenCalledTimes(3));
    await readyHydrated(projectA);
    expect(bridge.api.get_view).toHaveBeenLastCalledWith('FunctionFlow', 'fresh-root', 'fresh-step', 1, 'both');
    expect(bridge.writes.filter(write => write.layout.selectedId === 'old-step')
      .every(write => write.project === projectA && write.layout.graphRoots.FunctionFlow === 'old-root'
        && write.layout.openTabs.includes(sourceId))).toBe(true);
  });

  it('can cancel a chooser from an Outdated version-mismatch session then re-analyze', async () => {
    const bridge = recoveryBridge('Outdated');
    bridge.api.choose_project.mockImplementation(async () => {
      bridge.events.push(`cancel:${bridge.current().projectPath}`);
      return ok(bridge.current());
    });
    render(<App />);
    await hydratedOldWorkspace();
    fireEvent.click(screen.getByRole('button', { name: /＋ Open project/ }));
    await waitFor(() => expect(bridge.api.choose_project).toHaveBeenCalledOnce());
    await waitFor(() => expect((screen.getByRole('button', { name: /＋ Open project/ }) as HTMLButtonElement).disabled).toBe(false));
    expect(document.querySelector('.titlebar-path')?.textContent).toBe(projectA);
    expect(screen.getAllByRole('tab').some(tab => tab.textContent?.includes('legacy.py'))).toBe(true);
    reanalyze();
    await waitFor(() => expect(bridge.api.analyze).toHaveBeenCalledOnce());
    await readyHydrated(projectA);
    expect(bridge.events.indexOf(`cancel:${projectA}`)).toBeLessThan(bridge.events.indexOf(`analyze:${projectA}`));
    expect(bridge.api.get_view).toHaveBeenLastCalledWith('FunctionFlow', 'fresh-root', 'fresh-step', 1, 'both');
  });

  it('open_project and choosing another project are not blocked by an obsolete layout', async () => {
    for (const action of ['recent', 'chooser']) {
      const bridge = recoveryBridge(action === 'recent' ? 'NotAnalyzed' : 'Outdated');
      render(<App />);
      await hydratedOldWorkspace();
      if (action === 'recent') {
        const recent = document.querySelector<HTMLButtonElement>(`.recent-project[title="${projectB.replaceAll('\\', '\\\\')}"]`);
        expect(recent).not.toBeNull();
        fireEvent.click(recent!);
        await waitFor(() => expect(bridge.api.open_project).toHaveBeenCalledWith(projectB));
      } else {
        fireEvent.click(screen.getByRole('button', { name: /＋ Open project/ }));
        await waitFor(() => expect(bridge.api.choose_project).toHaveBeenCalledOnce());
      }
      await readyHydrated(projectB);
      expect(bridge.api.get_view).toHaveBeenLastCalledWith('FunctionFlow', 'fresh-root', 'fresh-step', 1, 'both');
      expect(bridge.writes.some(write => write.project === projectB && write.layout.graphRoots.FunctionFlow === 'old-root')).toBe(false);
      cleanup(); delete window.pywebview;
    }
  });

  it('drains an old-project write before mutation without sending its old layout to the new project', async () => {
    const bridge = recoveryBridge('Ready');
    const pending = deferred<Envelope<ViewState>>();
    bridge.api.save_layout.mockImplementationOnce(async layout => {
      bridge.writes.push({ project: bridge.current().projectPath, layout: structuredClone(layout) });
      bridge.events.push(`pending:start:${bridge.current().projectPath}`);
      const response = await pending.promise;
      bridge.events.push(`pending:finish:${bridge.current().projectPath}`);
      bridge.setSession({ ...bridge.current(), status: 'NotAnalyzed' });
      return response;
    });
    render(<App />);
    await hydratedOldWorkspace();
    await waitFor(() => expect(bridge.api.save_layout).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole('button', { name: /recovery-b/ }));
    expect(bridge.api.open_project).not.toHaveBeenCalled();
    expect(bridge.api.choose_project).not.toHaveBeenCalled();
    expect(bridge.api.analyze).not.toHaveBeenCalled();
    await act(async () => { pending.resolve(failure('NOT_READY', 'Old project invalidated during write')); });
    await waitFor(() => expect(bridge.api.open_project).toHaveBeenCalledWith(projectB));
    await readyHydrated(projectB);
    expect(bridge.events.indexOf(`pending:finish:${projectA}`)).toBeLessThan(bridge.events.indexOf(`open:${projectB}`));
    const oldWrites = bridge.writes.filter(write => write.layout.graphRoots.FunctionFlow === 'old-root');
    expect(oldWrites.length).toBeGreaterThan(0);
    expect(oldWrites.every(write => write.project === projectA)).toBe(true);
    expect(bridge.api.get_view).toHaveBeenLastCalledWith('FunctionFlow', 'fresh-root', 'fresh-step', 1, 'both');
  });
});
