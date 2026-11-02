import { afterEach, describe, expect, it, vi } from 'vitest';
import { BridgeError, call, waitForBridge } from './bridge';
import type { Anchor, Envelope, Session } from '../types';
import { viewState } from '../app/testFixtures';
const session: Session = { projectPath: null, status: 'NotAnalyzed', hasAnalysis: false, outdatedReasons: [], cacheIssue: null, recentProjects: [] };
function nativeSelection() {
  let resolve!: (response: Envelope<Session>) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Envelope<Session>>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const mutationCases = [
  { method: 'choose_project', invoke: () => call('choose_project'), data: session },
  { method: 'open_project', invoke: () => call('open_project', 'E:\\next'), data: session },
  { method: 'analyze', invoke: () => call('analyze'), data: session },
  { method: 'save_layout', invoke: () => call('save_layout', {
    activeView: viewState.activeView, selectedId: viewState.selectedId,
    openTabs: viewState.openTabs, graphRoots: viewState.graphRoots }), data: viewState },
  { method: 'move_node', invoke: () => call('move_node', 'node', { x: 2, y: 3 }, 'Symbol'), data: viewState },
  { method: 'save_viewport', invoke: () => call('save_viewport', 'Symbol', 1, { x: 2, y: 3 }), data: viewState },
  { method: 'expand', invoke: () => call('expand', 'node'), data: viewState },
  { method: 'collapse', invoke: () => call('collapse', 'node'), data: viewState },
  { method: 'open_detail_island', invoke: () => call('open_detail_island', 'Architecture', 'node', null), data: viewState },
  { method: 'move_detail_island', invoke: () => call('move_detail_island', 'island', { x: 2, y: 3 }), data: viewState },
  { method: 'close_detail_island', invoke: () => call('close_detail_island', 'island'), data: viewState },
  { method: 'set_analysis_settings', invoke: () => call('set_analysis_settings', { ignorePaths: ['ignored'] }),
    data: { ignorePaths: ['ignored'] } },
] as const;
afterEach(() => { delete window.pywebview; vi.useRealTimers(); });
describe('desktop bridge contract', () => {
  it('waits through the native empty API placeholder before the first application call', async () => {
    vi.useFakeTimers();
    window.pywebview = { api: {} };
    let settled = false;
    const pending = call('get_session');
    void pending.then(() => { settled = true; }, () => { settled = true; });
    await vi.advanceTimersByTimeAsync(50);
    expect(settled).toBe(false);
    const getSession = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: session }));
    window.pywebview.api!.get_session = getSession;
    await vi.advanceTimersByTimeAsync(50);
    await expect(pending).resolves.toEqual(session);
    expect(getSession).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
  it.each(mutationCases.flatMap(testCase => (['resolve', 'Python error', 'promise rejection'] as const)
    .map(outcome => ({ ...testCase, outcome }))))('keeps $method pending until native completion: $outcome', async ({ method, invoke, data, outcome }) => {
    vi.useFakeTimers();
    let resolve!: (response: Envelope<unknown>) => void;
    let reject!: (error: Error) => void;
    const native = new Promise<Envelope<unknown>>((res, rej) => { resolve = res; reject = rej; });
    const nativeMethod = vi.fn(() => native);
    window.pywebview = { api: { [method]: nativeMethod } };
    let settled = false;
    const pending = invoke();
    // Attach rejection handling before advancing clocks: baseline deadlines
    // must produce assertion failures, never unhandled rejected promises.
    void pending.then(() => { settled = true; }, () => { settled = true; });
    await vi.advanceTimersByTimeAsync(method === 'analyze' ? 300001 : 17000);
    try {
      expect(nativeMethod).toHaveBeenCalledOnce();
      expect(settled).toBe(false);
      expect(vi.getTimerCount()).toBe(0);
      if (outcome === 'resolve') {
        resolve({ ok: true, schemaVersion: 1, data });
        await expect(pending).resolves.toEqual(data);
      } else {
        const failure = expect(pending).rejects.toMatchObject({
          code: outcome === 'Python error' ? 'NOT_READY' : 'BRIDGE_ERROR', message: 'Late layout failure',
        });
        if (outcome === 'Python error') resolve({ ok: false, schemaVersion: 1,
          error: { code: 'NOT_READY', message: 'Late layout failure' } });
        else reject(new Error('Late layout failure'));
        await failure;
      }
      expect(vi.getTimerCount()).toBe(0);
    } finally { resolve({ ok: true, schemaVersion: 1, data }); }
  });
  it('times out outside the desktop and removes listeners', async () => { vi.useFakeTimers(); const remove = vi.spyOn(window, 'removeEventListener'); const pending = expect(waitForBridge(100)).rejects.toMatchObject({ code: 'DESKTOP_UNAVAILABLE' }); await vi.advanceTimersByTimeAsync(101); await pending; expect(remove).toHaveBeenCalledWith('pywebviewready', expect.any(Function)); expect(vi.getTimerCount()).toBe(0); });
  it('waits for official pywebviewready', async () => { vi.useFakeTimers(); const pending = waitForBridge(100); const api = {}; window.pywebview = { api }; window.dispatchEvent(new Event('pywebviewready')); expect(await pending).toBe(api); expect(vi.getTimerCount()).toBe(0); });
  it('calls snake_case methods and unwraps camelCase DTO', async () => { const open = vi.fn().mockResolvedValue({ ok: true, schemaVersion: 1, data: session }); window.pywebview = { api: { open_project: open } }; expect(await call('open_project', 'E:\\project')).toEqual(session); expect(open).toHaveBeenCalledWith('E:\\project'); });
  it('sends the actual inspector arguments including island context', async () => { const get = vi.fn().mockResolvedValue({ ok: true, schemaVersion: 1, data: null }); window.pywebview = { api: { get_inspector: get } }; await call('get_inspector', 'Architecture', 'root', 'child', 'island-source'); expect(get).toHaveBeenCalledWith('Architecture', 'root', 'child', 'island-source'); });
  it('preserves all five anchor keys, including null columns', async () => { const anchor: Anchor = { filePath: 'model.py', startLine: 20, endLine: 23, startColumn: null, endColumn: null }; const read = vi.fn().mockResolvedValue({ ok: true, schemaVersion: 1, data: { filePath: 'model.py', text: '', startLine: 20, endLine: 23 } }); window.pywebview = { api: { read_source: read } }; await call('read_source', anchor); expect(read).toHaveBeenCalledWith(anchor); });
  it('rejects missing methods explicitly after official readiness', async () => { window.pywebview = { api: {} }; const pending = call('get_session'); window.dispatchEvent(new Event('pywebviewready')); await expect(pending).rejects.toMatchObject({ code: 'METHOD_UNAVAILABLE' }); });
  it('preserves Python errors', async () => { window.pywebview = { api: { get_session: vi.fn().mockResolvedValue({ ok: false, schemaVersion: 1, error: { code: 'NOT_READY', message: 'Waiting for analysis' } }) } }; await expect(call('get_session')).rejects.toMatchObject({ code: 'NOT_READY', message: 'Waiting for analysis' }); });
  it('rejects unsupported envelopes', async () => { window.pywebview = { api: { get_session: vi.fn().mockResolvedValue({ ok: true, schemaVersion: 2, data: session }) } }; await expect(call('get_session')).rejects.toMatchObject({ code: 'INVALID_RESPONSE' }); });
  it('bounds hanging calls and clears timers', async () => { vi.useFakeTimers(); window.pywebview = { api: { get_session: () => new Promise(() => {}) } }; const pending = expect(call('get_session')).rejects.toBeInstanceOf(BridgeError); await vi.advanceTimersByTimeAsync(15001); await pending; expect(vi.getTimerCount()).toBe(0); });
  it.each([
    ['selection', { ...session, projectPath: 'E:\\chosen-project' }],
    ['cancellation', session],
  ] as const)('waits beyond 15 seconds for native %s to finish', async (_, result) => {
    vi.useFakeTimers();
    const native = nativeSelection();
    window.pywebview = { api: { choose_project: () => native.promise } };
    let settled = false;
    const pending = call('choose_project');
    void pending.then(() => { settled = true; }, () => { settled = true; });
    await vi.advanceTimersByTimeAsync(17000);
    expect(settled).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
    native.resolve({ ok: true, schemaVersion: 1, data: result });
    await expect(pending).resolves.toEqual(result);
    expect(vi.getTimerCount()).toBe(0);
  });
  it.each(['Python error', 'promise rejection'] as const)('recovers after a late native %s', async kind => {
    vi.useFakeTimers();
    const native = nativeSelection();
    window.pywebview = { api: { choose_project: () => native.promise,
      get_session: async () => ({ ok: true, schemaVersion: 1, data: session }) } };
    let settled = false;
    const pending = call('choose_project');
    void pending.then(() => { settled = true; }, () => { settled = true; });
    await vi.advanceTimersByTimeAsync(17000);
    expect(settled).toBe(false);
    const failure = expect(pending).rejects.toMatchObject({
      code: kind === 'Python error' ? 'NOT_READY' : 'BRIDGE_ERROR', message: 'Native selection failed',
    });
    if (kind === 'Python error') native.resolve({ ok: false, schemaVersion: 1,
      error: { code: 'NOT_READY', message: 'Native selection failed' } });
    else native.reject(new Error('Native selection failed'));
    await failure;
    await expect(call('get_session')).resolves.toEqual(session);
    expect(vi.getTimerCount()).toBe(0);
  });
  it('validates late native selection envelopes', async () => {
    vi.useFakeTimers();
    const native = nativeSelection();
    window.pywebview = { api: { choose_project: () => native.promise } };
    let settled = false;
    const pending = call('choose_project');
    void pending.then(() => { settled = true; }, () => { settled = true; });
    await vi.advanceTimersByTimeAsync(17000);
    expect(settled).toBe(false);
    const failure = expect(pending).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
    native.resolve({ ok: true, schemaVersion: 2, data: session });
    await failure;
    expect(vi.getTimerCount()).toBe(0);
  });
  it('waits beyond five minutes for analysis and accepts its late result', async () => {
    vi.useFakeTimers();
    const native = nativeSelection();
    window.pywebview = { api: { analyze: () => native.promise } };
    let settled = false;
    const pending = call('analyze');
    void pending.then(() => { settled = true; }, () => { settled = true; });
    await vi.advanceTimersByTimeAsync(300001);
    try {
      expect(settled).toBe(false);
      native.resolve({ ok: true, schemaVersion: 1, data: session });
      await expect(pending).resolves.toEqual(session);
      expect(vi.getTimerCount()).toBe(0);
    } finally { native.resolve({ ok: true, schemaVersion: 1, data: session }); }
  });
  it('uses Python direction and hops and object coordinates', async () => { const get = vi.fn().mockResolvedValue({ ok: true, schemaVersion: 1, data: null }); const move = vi.fn().mockResolvedValue({ ok: true, schemaVersion: 1, data: null }); window.pywebview = { api: { get_view: get, move_node: move } }; await call('get_view', 'Symbol', null, 'x', 5, 'in'); await call('move_node', 'x', { x: 2, y: 9 }, 'Symbol'); expect(get).toHaveBeenCalledWith('Symbol', null, 'x', 5, 'in'); expect(move).toHaveBeenCalledWith('x', { x: 2, y: 9 }, 'Symbol'); });
});
