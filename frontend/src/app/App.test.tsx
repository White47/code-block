import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { graph, ok, overview, session, viewState } from './testFixtures';
import type { Layout, Session, ViewState } from '../types';
vi.mock('./components/GraphEditor', () => ({ default: () => <div data-testid="graph-canvas">graph</div> }));
afterEach(() => { cleanup(); delete window.pywebview; });
beforeEach(() => { window.pywebview = { api: {
  get_session: vi.fn().mockResolvedValue(ok(session)), get_preferences: vi.fn().mockResolvedValue(ok({ language: 'en', issue: null })),
  get_view_state: vi.fn().mockResolvedValue(ok(viewState)), get_overview: vi.fn().mockResolvedValue(ok(overview)), get_problems: vi.fn().mockResolvedValue(ok([])),
  get_view: vi.fn().mockResolvedValue(ok(graph)), save_layout: vi.fn().mockResolvedValue(ok(viewState)),
  get_inspector: vi.fn().mockResolvedValue(ok({ id: 'layer', displayName: 'Encoder', codeName: 'encoder', role: 'Layer', confidence: 'Exact', sourceAnchors: [], memberIds: [], data: [], children: [], evidence: [], relations: [], canOpenSource: false })),
} }; });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(finish => { resolve = finish; });
  return { promise, resolve };
}
const sessionB: Session = { ...session, projectPath: 'E:\\project-b' };
const layoutB: ViewState = { ...viewState, activeView: 'Execution', selectedId: null, openTabs: ['Execution'], graphRoots: { Execution: 'entry-b' } };
function settingsApi() {
  const api = window.pywebview!.api!;
  api.get_cache_info = vi.fn().mockResolvedValue(ok({ dataDir: 'cache', analysisPath: 'cache/a.json', exists: true, status: 'Ready', outdatedReasons: [], cacheIssue: null }));
  api.get_analysis_settings = vi.fn().mockResolvedValue(ok({ ignorePaths: ['.venv'], issue: null }));
  return api;
}
async function editSettings() {
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  const input = await screen.findByLabelText('Ignore paths · one per line');
  await waitFor(() => expect((input as HTMLTextAreaElement).value).toBe('.venv'));
  fireEvent.change(input, { target: { value: 'a-only' } });
  fireEvent.click(screen.getByText('Save analysis settings'));
}
describe('desktop workspace integration', () => {
  it('keeps the completed same-project graph visible when an older Analyzing poll arrives late', async () => {
    const api = window.pywebview!.api!;
    const poll = deferred<ReturnType<typeof ok<Session>>>();
    const analysis = deferred<ReturnType<typeof ok<Session>>>();
    api.get_session = vi.fn().mockResolvedValueOnce(ok(session)).mockImplementationOnce(() => poll.promise);
    api.analyze = vi.fn(() => analysis.promise);
    render(<App/>); await screen.findByTestId('graph-canvas');
    fireEvent.click(screen.getByText('Re-analyze'));
    await waitFor(() => expect(api.get_session).toHaveBeenCalledTimes(2), { timeout: 1800 });
    await act(async () => { analysis.resolve(ok(session)); });
    await screen.findByTestId('graph-canvas');
    await waitFor(() => expect((screen.getByText('Re-analyze') as HTMLButtonElement).disabled).toBe(false));
    await act(async () => { poll.resolve(ok({ ...session, status: 'Analyzing' })); });
    expect(screen.queryByTestId('graph-canvas')).not.toBeNull();
    expect(screen.queryByText('Analyzing…')).toBeNull();
    expect(document.querySelector('.statusbar')?.textContent).toContain('Ready');
    expect((screen.getByText('Re-analyze') as HTMLButtonElement).disabled).toBe(false);
  });
  it('ignores an old analysis poll that returns after project B has hydrated', async () => {
    const api = window.pywebview!.api!;
    const poll = deferred<ReturnType<typeof ok<Session>>>();
    const analysis = deferred<ReturnType<typeof ok<Session>>>();
    api.get_session = vi.fn().mockResolvedValueOnce(ok(session)).mockImplementationOnce(() => poll.promise);
    api.analyze = vi.fn(() => analysis.promise);
    api.open_project = vi.fn().mockResolvedValue(ok(sessionB));
    api.get_view_state = vi.fn().mockResolvedValueOnce(ok(viewState)).mockResolvedValueOnce(ok(viewState)).mockResolvedValue(ok(layoutB));
    render(<App/>); await screen.findByTestId('graph-canvas');
    fireEvent.click(screen.getByText('Re-analyze'));
    await waitFor(() => expect(api.get_session).toHaveBeenCalledTimes(2), { timeout: 1800 });
    await act(async () => { analysis.resolve(ok(session)); });
    await waitFor(() => expect((screen.getByText('project-b') as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByText('project-b'));
    await waitFor(() => expect(document.querySelector('.titlebar-path')?.textContent).toBe(sessionB.projectPath));
    await waitFor(() => expect(screen.getAllByRole('tab').map(e => e.textContent)).toEqual(['Execution']));
    await act(async () => { poll.resolve(ok({ ...session, status: 'Analyzing' })); });
    expect(document.querySelector('.titlebar-path')?.textContent).toBe(sessionB.projectPath);
    expect(screen.getAllByRole('tab').map(e => e.textContent)).toEqual(['Execution']);
    expect(screen.queryByText('Analyzing…')).toBeNull();
    expect((screen.getByText('Re-analyze') as HTMLButtonElement).disabled).toBe(false);
  });
  it('finishes A settings writes before switching Python to B, even when the user switches during the save', async () => {
    const api = settingsApi();
    const finishSettings = deferred<void>(); const writtenProjects: string[] = []; const order: string[] = [];
    let currentProject = session.projectPath!; let persisted: ViewState = viewState;
    api.save_layout = vi.fn(async (layout: Layout) => { order.push(`layout:${currentProject}`); persisted = { ...persisted, ...layout }; return ok(persisted); });
    api.get_view_state = vi.fn(async () => ok(currentProject === session.projectPath ? persisted : layoutB));
    api.set_analysis_settings = vi.fn(async payload => { order.push('settings:start'); await finishSettings.promise; writtenProjects.push(currentProject); order.push('settings:finish'); return ok({ ...payload, issue: null }); });
    api.open_project = vi.fn(async () => { order.push('open:B'); currentProject = sessionB.projectPath!; return ok(sessionB); });
    render(<App/>); await screen.findByTestId('graph-canvas'); await editSettings();
    await waitFor(() => expect(api.set_analysis_settings).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByText('project-b'));
    expect(api.open_project).not.toHaveBeenCalled();
    await act(async () => { finishSettings.resolve(); });
    await waitFor(() => expect(screen.getAllByRole('tab').map(e => e.textContent)).toEqual(['Execution']));
    expect(writtenProjects).toEqual([session.projectPath]);
    expect(order.indexOf('settings:finish')).toBeLessThan(order.indexOf('open:B'));
    expect(document.querySelector('.titlebar-path')?.textContent).toBe(sessionB.projectPath);
  });
  it('does not restore a settings hydration superseded by a queued project switch', async () => {
    const api = settingsApi(); const oldHydration = deferred<ReturnType<typeof ok<ViewState>>>(); const openB = deferred<ReturnType<typeof ok<Session>>>();
    api.set_analysis_settings = vi.fn().mockResolvedValue(ok({ ignorePaths: ['a-only'], issue: null }));
    api.get_view_state = vi.fn().mockResolvedValueOnce(ok(viewState)).mockImplementationOnce(() => oldHydration.promise).mockResolvedValue(ok(layoutB));
    api.open_project = vi.fn(() => openB.promise);
    render(<App/>); await screen.findByTestId('graph-canvas'); await editSettings();
    await waitFor(() => expect(api.get_view_state).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByText('project-b'));
    expect(api.open_project).not.toHaveBeenCalled();
    await act(async () => { oldHydration.resolve(ok({ ...viewState, activeView: 'Calls', selectedId: 'obsolete', openTabs: ['Calls'] })); });
    await waitFor(() => expect(api.open_project).toHaveBeenCalledOnce());
    expect(screen.getAllByRole('tab').map(e => e.textContent)).toContain('Settings');
    expect(screen.queryByRole('tab', { name: 'Calls' })).toBeNull();
    await act(async () => { openB.resolve(ok(sessionB)); });
    await waitFor(() => expect(screen.getAllByRole('tab').map(e => e.textContent)).toEqual(['Execution']));
  });
  it('flushes the newest tab layout before project switching within the debounce window', async () => {
    const api = window.pywebview!.api!; const flush = deferred<ReturnType<typeof ok<ViewState>>>();
    api.open_project = vi.fn().mockResolvedValue(ok(sessionB));
    api.get_view_state = vi.fn().mockResolvedValueOnce(ok(viewState)).mockResolvedValue(ok(layoutB));
    render(<App/>); await screen.findByTestId('graph-canvas');
    await waitFor(() => expect(api.save_layout).toHaveBeenCalled());
    const save = vi.mocked(api.save_layout!); save.mockClear(); save.mockImplementationOnce(() => flush.promise);
    fireEvent.click(screen.getByRole('button', { name: /^Execution/ }));
    fireEvent.click(screen.getByText('project-b'));
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ activeView: 'Execution', selectedId: null, openTabs: ['Overview', 'Architecture', 'Execution'] })));
    expect(api.open_project).not.toHaveBeenCalled();
    await act(async () => { flush.resolve(ok(viewState)); });
    await waitFor(() => expect(api.open_project).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getAllByRole('tab').map(e => e.textContent)).toEqual(['Execution']));
  });
  it('closing the active Architecture tab queries the remaining Execution graph with no stale selection', async () => {
    const api = window.pywebview!.api!;
    api.get_view_state = vi.fn().mockResolvedValue(ok({ ...viewState, openTabs: ['Overview', 'Execution', 'Architecture'], graphRoots: { Architecture: 'model', Execution: 'entry' } }));
    render(<App/>); await screen.findByTestId('graph-canvas');
    fireEvent.click(screen.getByRole('button', { name: 'Close Architecture' }));
    await waitFor(() => expect(api.get_view).toHaveBeenLastCalledWith('Execution', 'entry', null, 1, 'both'));
    await waitFor(() => expect(api.save_layout).toHaveBeenLastCalledWith(expect.objectContaining({ activeView: 'Execution', selectedId: null })));
  });
  it('hydrates Python roots and calls Inspector with exact kind/root/selected/detail arguments', async () => { render(<App/>); await screen.findByTestId('graph-canvas'); await waitFor(() => expect(window.pywebview?.api?.get_inspector).toHaveBeenCalledWith('Architecture', 'model', 'layer', null)); expect(window.pywebview?.api?.get_view).toHaveBeenCalledWith('Architecture', 'model', 'layer', 1, 'both'); });
  it('queries Python upstream/downstream and limits hops to five', async () => { render(<App/>); await screen.findByTestId('graph-canvas'); fireEvent.click(screen.getByText('Upstream')); await waitFor(() => expect(window.pywebview?.api?.get_view).toHaveBeenLastCalledWith('Architecture', 'model', 'layer', 1, 'in')); await screen.findByTestId('graph-canvas'); fireEvent.click(screen.getByText('Downstream')); await waitFor(() => expect(window.pywebview?.api?.get_view).toHaveBeenLastCalledWith('Architecture', 'model', 'layer', 1, 'out')); for (let i = 2; i <= 5; i++) { await screen.findByTestId('graph-canvas'); fireEvent.click(screen.getByText(new RegExp(`More hops · ${i - 1}/5`))); await waitFor(() => expect(window.pywebview?.api?.get_view).toHaveBeenLastCalledWith('Architecture', 'model', 'layer', i, 'out')); } await screen.findByTestId('graph-canvas'); expect((screen.getByText('More hops · 5/5') as HTMLButtonElement).disabled).toBe(true); });
  it('Ctrl+W closes active graph and persists the remaining Overview tab', async () => { render(<App/>); await screen.findByTestId('graph-canvas'); const event = new KeyboardEvent('keydown', { key: 'w', ctrlKey: true, cancelable: true, bubbles: true }); window.dispatchEvent(event); expect(event.defaultPrevented).toBe(true); await waitFor(() => expect(screen.queryByTestId('graph-canvas')).toBeNull()); await waitFor(() => expect(window.pywebview?.api?.save_layout).toHaveBeenLastCalledWith(expect.objectContaining({ activeView: 'Overview', openTabs: ['Overview'] }))); });
  it('refreshes session after analysis rejects so a failed session can be retried', async () => { const get = vi.fn().mockResolvedValueOnce(ok(session)).mockResolvedValue(ok({ ...session, status: 'Failed' })); window.pywebview!.api!.get_session = get; window.pywebview!.api!.analyze = vi.fn().mockResolvedValue({ ok: false, schemaVersion: 1, error: { code: 'INTERNAL_ERROR', message: 'Analysis failed' } }); render(<App/>); await screen.findByTestId('graph-canvas'); fireEvent.click(screen.getByText('Re-analyze')); await waitFor(() => expect(get).toHaveBeenCalledTimes(2)); expect(await screen.findByText(/INTERNAL_ERROR: Analysis failed/)).toBeDefined(); await waitFor(() => expect((screen.getByText('Re-analyze') as HTMLButtonElement).disabled).toBe(false)); });
  it('loads the new project Python layout and never applies late graph responses from the previous project', async () => { let finishOld: (value: ReturnType<typeof ok<typeof graph>>) => void = () => {}; window.pywebview!.api!.get_view = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; })).mockResolvedValue(ok({ ...graph, viewKind: 'Execution' })); window.pywebview!.api!.open_project = vi.fn().mockResolvedValue(ok({ ...session, projectPath: 'E:\\project-b' })); window.pywebview!.api!.get_view_state = vi.fn().mockResolvedValueOnce(ok(viewState)).mockResolvedValue(ok({ ...viewState, activeView: 'Execution', selectedId: null, openTabs: ['Execution'], graphRoots: { Execution: 'entry-b' } })); render(<App/>); await waitFor(() => expect(window.pywebview?.api?.get_view).toHaveBeenCalled()); fireEvent.click(screen.getByText('project-b')); await screen.findByTestId('graph-canvas'); finishOld(ok(graph)); await waitFor(() => expect(window.pywebview?.api?.get_view).toHaveBeenLastCalledWith('Execution', 'entry-b', null, 1, 'both')); expect(screen.getAllByRole('tab').map(e => e.textContent)).toEqual(['Execution']); });
});
