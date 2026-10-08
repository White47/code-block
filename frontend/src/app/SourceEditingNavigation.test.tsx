import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { sourceTab } from './state';
import { anchor, graph, ok, overview, selectedNode, session, viewState } from './testFixtures';
import type { Anchor, SaveSourceRequest, VisibleNode } from '../types';
import type { editor as MonacoEditor } from 'monaco-editor';
import { releaseSourceModel, retainSourceModel } from './sourceModels';
import { sourceDocumentKey } from './sourceDocuments';

// Real App, SourceViewer, tabs and guards. This suite proves state/interaction,
// not Monaco token colours or native WebView execution.
vi.mock('./components/GraphEditor', () => ({ default: ({ onSelect }: { onSelect: (node: VisibleNode, source: null) => void }) =>
  <div>Graph canvas<button onClick={() => onSelect(selectedNode, null)}>Select Encoder</button>
    <button onClick={() => onSelect({ ...selectedNode, id: 'other-source', sourceAnchors: [{ ...anchor, filePath: 'other.py' }] }, null)}>Select other source</button></div> }));
const initialText = 'value = 1'; const draft = 'value = 2';
const document = { projectPath: session.projectPath!, filePath: anchor.filePath, text: initialText,
  revision: 'epoch-a:bytes-1:identity-1', encoding: 'utf-8', newline: 'LF' as const, readOnlyReason: null };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(finish => { resolve = finish; });
  return { promise, resolve };
}
function install(openTabs = ['Overview', 'Architecture', sourceTab(anchor).id]) {
  const layout = { ...viewState, activeView: null, openTabs };
  const api = {
    get_session: vi.fn().mockResolvedValue(ok(session)),
    get_preferences: vi.fn().mockResolvedValue(ok({ language: 'en', issue: null })),
    get_view_state: vi.fn().mockResolvedValue(ok(layout)),
    get_overview: vi.fn().mockResolvedValue(ok(overview)),
    get_view: vi.fn().mockResolvedValue(ok(graph)),
    get_problems: vi.fn().mockResolvedValue(ok([
      { code: 'anchor-two', message: 'Another anchor in this file', filePath: anchor.filePath, line: 2, canOpenSource: true },
    ])),
    save_layout: vi.fn().mockResolvedValue(ok(layout)),
    read_source: vi.fn().mockResolvedValue(ok({ filePath: anchor.filePath, text: initialText, startLine: 20, endLine: 22 })),
    open_source_document: vi.fn().mockResolvedValue(ok(document)),
    save_source_document: vi.fn(async (request: SaveSourceRequest) => ok({ ...document, ...request, revision: 'epoch-a:bytes-2:identity-2' })),
    open_project: vi.fn().mockResolvedValue(ok({ ...session, projectPath: 'E:\\project-b' })),
    choose_project: vi.fn().mockResolvedValue(ok(session)), analyze: vi.fn().mockResolvedValue(ok(session)),
    get_cache_info: vi.fn().mockResolvedValue(ok({ dataDir: 'cache', analysisPath: 'cache/a.json', exists: true, status: 'Ready', outdatedReasons: [], cacheIssue: null })),
    get_analysis_settings: vi.fn().mockResolvedValue(ok({ ignorePaths: ['.venv'], issue: null })),
    set_analysis_settings: vi.fn().mockResolvedValue(ok({ ignorePaths: ['tests'], issue: null })),
    approve_close: vi.fn().mockResolvedValue(ok(null)), cancel_close: vi.fn().mockResolvedValue(ok(null)),
  };
  window.pywebview = { api } as unknown as NonNullable<Window['pywebview']>;
  return api;
}
function sourceEditor() {
  // The adapter may expose a textarea or Monaco's accessible textbox.
  const host = documentQuery();
  const editor = host?.querySelector<HTMLTextAreaElement>('textarea') ?? host?.querySelector<HTMLElement>('[role="textbox"]');
  expect(editor, 'SourceViewer must expose its editable source adapter').not.toBeNull();
  expect(editor).not.toBeUndefined();
  return editor!;
}
function documentQuery() { return globalThis.document.querySelector('.primary-editor .source-view'); }
function textOf(editor: HTMLElement) { return editor instanceof HTMLTextAreaElement ? editor.value : editor.textContent; }
function edit(text = draft) { fireEvent.change(sourceEditor(), { target: { value: text } }); }
async function start(api = install()) {
  render(<App/>);
  await waitFor(() => expect(screen.getByRole('tab', { name: /model.py/ }).getAttribute('aria-selected')).toBe('true'));
  await waitFor(() => expect(api.open_source_document).toHaveBeenCalledWith(session.projectPath, anchor));
  await waitFor(() => expect(textOf(sourceEditor())).toBe(initialText));
  edit(); return api;
}
function ctrlSave() {
  const event = new KeyboardEvent('keydown', { key: 's', ctrlKey: true, bubbles: true, cancelable: true });
  window.dispatchEvent(event); expect(event.defaultPrevented).toBe(true);
}
function closeSource() {
  const tab = screen.getByRole('tab', { name: /model.py/ });
  fireEvent.click(within(tab.closest('.tab')!).getByRole('button', { name: /^Close / }));
}
afterEach(() => { cleanup(); releaseSourceModel(sourceDocumentKey(session.projectPath!, anchor.filePath)); delete window.pywebview; vi.useRealTimers(); });

describe('source editing navigation and save guards', () => {
  it('a matching codeviz:close-failed restores editing, preserves source and permits another close attempt', async () => {
    const api = await start(); const requestId = 'destroy-failed-current';
    window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail: { requestId } }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /^save/i }));
    await waitFor(() => expect(api.approve_close).toHaveBeenCalledWith(requestId));
    await act(async () => { await Promise.resolve(); });
    expect((sourceEditor() as HTMLTextAreaElement).readOnly).toBe(true);
    window.dispatchEvent(new CustomEvent('codeviz:close-failed', { detail: { requestId } }));
    await waitFor(() => expect((sourceEditor() as HTMLTextAreaElement).readOnly).toBe(false));
    expect(textOf(sourceEditor())).toBe(draft);
    edit('preserved retry draft');
    const retry = 'destroy-retry-new';
    window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail: { requestId: retry } }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(api.cancel_close).toHaveBeenCalledWith(retry));
    expect(textOf(sourceEditor())).toBe('preserved retry draft');
    expect(api.save_source_document).toHaveBeenCalledOnce();
  });
  it('a stale or malformed codeviz:close-failed cannot unlock the current approved close', async () => {
    const api = await start(); const requestId = 'current-approved-close';
    window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail: { requestId } }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /^save/i }));
    await waitFor(() => expect(api.approve_close).toHaveBeenCalledWith(requestId));
    for (const detail of [undefined, {}, { requestId: 42 }, { requestId: '' }, { requestId: 'old-renderer-close' }]) {
      window.dispatchEvent(new CustomEvent('codeviz:close-failed', { detail }));
    }
    await act(async () => { await Promise.resolve(); });
    expect((sourceEditor() as HTMLTextAreaElement).readOnly).toBe(true);
    edit('stale event must not permit edits'); expect(textOf(sourceEditor())).toBe(draft);
    fireEvent.click(screen.getByRole('button', { name: /project-b/ }));
    expect(api.open_project).not.toHaveBeenCalled(); expect(api.save_source_document).toHaveBeenCalledOnce();
    window.dispatchEvent(new CustomEvent('codeviz:close-failed', { detail: { requestId } }));
    await waitFor(() => expect((sourceEditor() as HTMLTextAreaElement).readOnly).toBe(false));
    expect(textOf(sourceEditor())).toBe(draft);
  });
  it('a duplicate old destroy-failure cannot unlock a subsequent close request', async () => {
    const api = await start(); const first = 'first-destroy-failure';
    window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail: { requestId: first } }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /^save/i }));
    await waitFor(() => expect(api.approve_close).toHaveBeenCalledWith(first));
    window.dispatchEvent(new CustomEvent('codeviz:close-failed', { detail: { requestId: first } }));
    await waitFor(() => expect((sourceEditor() as HTMLTextAreaElement).readOnly).toBe(false));
    edit('next request draft'); const second = 'second-approved-close';
    window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail: { requestId: second } }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /^save/i }));
    await waitFor(() => expect(api.approve_close).toHaveBeenCalledWith(second));
    window.dispatchEvent(new CustomEvent('codeviz:close-failed', { detail: { requestId: first } }));
    await act(async () => { await Promise.resolve(); });
    expect((sourceEditor() as HTMLTextAreaElement).readOnly).toBe(true);
    expect(textOf(sourceEditor())).toBe('next request draft');
  });
  it('publishes the exact native readiness marker after registering its listener and clears it on unmount', async () => {
    const readinessAtRegistration: unknown[] = [];
    const original = window.addEventListener.bind(window);
    const listener = vi.spyOn(window, 'addEventListener').mockImplementation((...args: Parameters<typeof window.addEventListener>) => {
      if (args[0] === 'codeviz:close-request') readinessAtRegistration.push(window.__codevizCloseReady);
      original(...args);
    });
    try {
      await start();
      expect(readinessAtRegistration.length).toBeGreaterThan(0);
      expect(readinessAtRegistration.every(value => value !== true)).toBe(true);
      expect(window.__codevizCloseReady).toBe(true);
      cleanup(); expect(window.__codevizCloseReady).toBe(false);
    } finally { listener.mockRestore(); }
  });
  it('freezes editing, save and existing-tab navigation before the native close decision', async () => {
    const api = await start();
    window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail: { requestId: 'freeze-before-decision' } }));
    const dialog = await screen.findByRole('dialog');
    expect((sourceEditor() as HTMLTextAreaElement).readOnly).toBe(true);
    edit('must not become a new draft');
    expect(textOf(sourceEditor())).toBe(draft);
    ctrlSave(); expect(api.save_source_document).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('tab', { name: 'Architecture' }));
    expect(screen.getByRole('tab', { name: /model.py/ }).getAttribute('aria-selected')).toBe('true');
    fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(api.cancel_close).toHaveBeenCalledWith('freeze-before-decision'));
    await waitFor(() => expect((sourceEditor() as HTMLTextAreaElement).readOnly).toBe(false));
  });
  it('keeps the lock through a delayed native approval and after approval until window destruction', async () => {
    const api = await start(); const approval = deferred<ReturnType<typeof ok<null>>>();
    api.approve_close.mockImplementationOnce(() => approval.promise);
    window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail: { requestId: 'delayed-approval' } }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /^save/i }));
    await waitFor(() => expect(api.approve_close).toHaveBeenCalledWith('delayed-approval'));
    expect((sourceEditor() as HTMLTextAreaElement).readOnly).toBe(true);
    edit('late-window draft'); ctrlSave();
    fireEvent.click(screen.getByRole('tab', { name: 'Architecture' }));
    fireEvent.click(screen.getByRole('button', { name: /project-b/ }));
    expect(screen.getByRole('tab', { name: /model.py/ }).getAttribute('aria-selected')).toBe('true');
    expect(textOf(sourceEditor())).toBe(draft);
    expect(api.open_project).not.toHaveBeenCalled(); expect(api.save_source_document).toHaveBeenCalledOnce();
    await act(async () => { approval.resolve(ok(null)); });
    expect((sourceEditor() as HTMLTextAreaElement).readOnly).toBe(true);
    edit('after-approval draft'); expect(textOf(sourceEditor())).toBe(draft);
  });
  it('unfreezes source editing if native approval fails instead of destroying the window', async () => {
    const api = await start(); api.approve_close.mockRejectedValueOnce(new Error('Native destroy could not be scheduled'));
    window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail: { requestId: 'approval-failure' } }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /^save/i }));
    await waitFor(() => expect(screen.getAllByRole('alert').some(item => item.textContent?.includes('Native destroy could not be scheduled'))).toBe(true));
    await waitFor(() => expect((sourceEditor() as HTMLTextAreaElement).readOnly).toBe(false));
    edit('retryable draft'); expect(textOf(sourceEditor())).toBe('retryable draft');
  });
  it('keeps editing frozen until native cancellation has completed', async () => {
    const api = await start(); const cancellation = deferred<ReturnType<typeof ok<null>>>();
    api.cancel_close.mockImplementationOnce(() => cancellation.promise);
    window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail: { requestId: 'delayed-cancellation' } }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(api.cancel_close).toHaveBeenCalledOnce());
    expect((sourceEditor() as HTMLTextAreaElement).readOnly).toBe(true);
    edit('premature retry'); expect(textOf(sourceEditor())).toBe(draft);
    await act(async () => { cancellation.resolve(ok(null)); });
    await waitFor(() => expect((sourceEditor() as HTMLTextAreaElement).readOnly).toBe(false));
    edit('after cancel'); expect(textOf(sourceEditor())).toBe('after cancel');
  });
  it.each(['settings', 'same-path reopen'] as const)('%s releases clean models and opens a fresh document revision before workspace hydration', async action => {
    const api = install();
    api.get_session.mockResolvedValue(ok({ ...session, recentProjects: [session.projectPath!] }));
    api.open_project.mockResolvedValue(ok(session));
    api.open_source_document.mockResolvedValueOnce(ok(document)).mockResolvedValue(ok({ ...document,
      text: 'new epoch text', revision: 'opening-2:raw-bytes-new:identity-new' }));
    await start(api); ctrlSave();
    await waitFor(() => expect(screen.getByRole('tab', { name: /model.py/ }).textContent).not.toContain('●'));
    const lifecycle: string[] = [];
    const dispose = vi.fn(() => { lifecycle.push('dispose'); });
    retainSourceModel(sourceDocumentKey(session.projectPath!, anchor.filePath), { dispose } as unknown as MonacoEditor.ITextModel);
    const hydrationCount = api.get_view_state.mock.calls.length;
    api.get_view_state.mockImplementation(async () => {
      lifecycle.push('hydrate'); return ok({ ...viewState, activeView: null,
        openTabs: ['Overview', 'Architecture', sourceTab(anchor).id] });
    });
    if (action === 'same-path reopen') fireEvent.click(screen.getByRole('button', { name: /^project-a/ }));
    else {
      fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
      const ignore = await screen.findByLabelText('Ignore paths · one per line');
      await waitFor(() => expect((ignore as HTMLTextAreaElement).value).toBe('.venv'));
      fireEvent.change(ignore, { target: { value: 'tests' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save analysis settings' }));
    }
    await waitFor(() => expect(api.get_view_state.mock.calls.length).toBeGreaterThan(hydrationCount));
    expect(lifecycle.indexOf('dispose')).toBeGreaterThanOrEqual(0);
    expect(lifecycle.indexOf('dispose')).toBeLessThan(lifecycle.indexOf('hydrate'));
    await waitFor(() => expect(api.open_source_document).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(textOf(sourceEditor())).toBe('new epoch text'));
    edit('new epoch edit'); ctrlSave();
    await waitFor(() => expect(api.save_source_document).toHaveBeenLastCalledWith({ projectPath: session.projectPath,
      filePath: anchor.filePath, text: 'new epoch edit', revision: 'opening-2:raw-bytes-new:identity-new' }));
  });
  it.each(['settings', 'same-path reopen'] as const)('%s invalidates a pending document open before its late old-epoch response', async action => {
    const api = install(); const oldOpen = deferred<ReturnType<typeof ok<typeof document>>>();
    api.open_source_document.mockImplementationOnce(() => oldOpen.promise).mockResolvedValue(ok({ ...document,
      text: 'fresh reopened document', revision: 'new-opening-epoch' }));
    render(<App/>);
    await waitFor(() => expect(api.open_source_document).toHaveBeenCalledOnce());
    if (action === 'same-path reopen') {
      fireEvent.click(screen.getByRole('button', { name: /＋ Open project/ }));
      await waitFor(() => expect(api.choose_project).toHaveBeenCalledOnce());
    } else {
      fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
      const ignore = await screen.findByLabelText('Ignore paths · one per line');
      await waitFor(() => expect((ignore as HTMLTextAreaElement).value).toBe('.venv'));
      fireEvent.change(ignore, { target: { value: 'tests' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save analysis settings' }));
      await waitFor(() => expect(api.set_analysis_settings).toHaveBeenCalledOnce());
    }
    try {
      await waitFor(() => expect(api.open_source_document).toHaveBeenCalledTimes(2));
      await waitFor(() => expect(textOf(sourceEditor())).toBe('fresh reopened document'));
    } finally { await act(async () => { oldOpen.resolve(ok({ ...document, text: 'obsolete opening text' })); }); }
    expect(textOf(sourceEditor())).toBe('fresh reopened document');
  });
  it('uses Ctrl+S to save captured project/file/text/revision then refreshes session without hydration', async () => {
    const api = await start(); const hydrations = api.get_view_state.mock.calls.length;
    api.get_session.mockResolvedValue(ok({ ...session, status: 'Outdated' }));
    ctrlSave();
    await waitFor(() => expect(api.save_source_document).toHaveBeenCalledWith({
      projectPath: session.projectPath, filePath: anchor.filePath, text: draft, revision: document.revision,
    }));
    await waitFor(() => expect(globalThis.document.querySelector('.statusbar')?.textContent).toContain('Outdated'));
    expect(api.get_view_state).toHaveBeenCalledTimes(hydrations);
    expect(api.analyze).not.toHaveBeenCalled(); expect(textOf(sourceEditor())).toBe(draft);
  });
  it('preserves later input while an earlier save completes and uses the new baseline on the next save', async () => {
    const api = await start(); const saving = deferred<ReturnType<typeof ok<typeof document>>>();
    api.save_source_document.mockImplementationOnce(() => saving.promise);
    ctrlSave(); await waitFor(() => expect(api.save_source_document).toHaveBeenCalledOnce());
    edit('value = 3');
    await act(async () => { saving.resolve(ok({ ...document, text: draft, revision: 'epoch-a:bytes-2:identity-2' })); });
    expect(textOf(sourceEditor())).toBe('value = 3');
    expect(screen.getByRole('tab', { name: /model.py/ }).textContent).toContain('●');
    ctrlSave();
    await waitFor(() => expect(api.save_source_document).toHaveBeenLastCalledWith({
      projectPath: session.projectPath, filePath: anchor.filePath, text: 'value = 3', revision: 'epoch-a:bytes-2:identity-2',
    }));
  });
  it('shares the draft across different anchors and normal graph/tab switching without prompting', async () => {
    const api = await start();
    fireEvent.click(screen.getByRole('tab', { name: 'Architecture' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: /Another anchor in this file/ }));
    await waitFor(() => expect(textOf(sourceEditor())).toBe(draft));
    const second: Anchor = { ...anchor, startLine: 2, endLine: 2 };
    expect(screen.getAllByRole('tab', { name: /model.py/ })).toHaveLength(2);
    expect(screen.queryByRole('dialog')).toBeNull(); expect(api.save_source_document).not.toHaveBeenCalled();
    expect(sourceTab(second).id).not.toBe(sourceTab(anchor).id);
  });
  it('cancel keeps the last dirty source view and does not save', async () => {
    const api = await start(); closeSource();
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }));
    expect(textOf(sourceEditor())).toBe(draft); expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('closing one of two anchors preserves the shared dirty document without a guard', async () => {
    const api = await start();
    fireEvent.click(screen.getByRole('button', { name: /Another anchor in this file/ }));
    await waitFor(() => expect(screen.getAllByRole('tab', { name: /model.py/ })).toHaveLength(2));
    const active = screen.getAllByRole('tab', { name: /model.py/ }).find(tab => tab.getAttribute('aria-selected') === 'true')!;
    fireEvent.click(within(active.closest('.tab')!).getByRole('button', { name: /^Close / }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: /model.py/ }));
    await waitFor(() => expect(textOf(sourceEditor())).toBe(draft));
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('shares text between the split source and a source tab and keeps it when split closes', async () => {
    const api = await start();
    fireEvent.click(screen.getByRole('tab', { name: 'Architecture' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Select Encoder' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Open to side' }));
    const side = globalThis.document.querySelector('.side-editor')!;
    await waitFor(() => expect(side.querySelector<HTMLTextAreaElement>('textarea')?.value).toBe(draft));
    fireEvent.change(side.querySelector('textarea')!, { target: { value: 'value = 4' } });
    fireEvent.click(screen.getByRole('tab', { name: /model.py/ }));
    await waitFor(() => expect(textOf(sourceEditor())).toBe('value = 4'));
    fireEvent.click(within(side as HTMLElement).getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(textOf(sourceEditor())).toBe('value = 4'); expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it.each(['close', 'replace'] as const)('cancel retains a dirty split when its last source view would %s', async action => {
    const api = await start();
    fireEvent.click(screen.getByRole('tab', { name: 'Architecture' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Select Encoder' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Open to side' }));
    await screen.findByRole('textbox', { name: anchor.filePath });
    closeSource();
    await waitFor(() => expect(screen.queryByRole('tab', { name: /model.py/ })).toBeNull());
    expect(screen.queryByRole('dialog')).toBeNull();
    const side = globalThis.document.querySelector('.side-editor')!;
    if (action === 'close') fireEvent.click(within(side as HTMLElement).getByRole('button', { name: 'Close' }));
    else {
      // Exercise the actual App selection and Inspector split callbacks.
      fireEvent.click(screen.getByRole('button', { name: 'Select other source' }));
      fireEvent.click(await screen.findByRole('button', { name: 'Open to side' }));
    }
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(side.querySelector<HTMLTextAreaElement>('textarea')?.value).toBe(draft);
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('discard closes the last dirty source view without writing it', async () => {
    const api = await start(); closeSource();
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /discard/i }));
    await waitFor(() => expect(screen.queryByRole('tab', { name: /model.py/ })).toBeNull());
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('save waits for native completion before closing the last dirty source view', async () => {
    const api = await start(); const saving = deferred<ReturnType<typeof ok<typeof document>>>();
    api.save_source_document.mockImplementationOnce(() => saving.promise); closeSource();
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /^save/i }));
    await waitFor(() => expect(api.save_source_document).toHaveBeenCalledOnce());
    expect(screen.getByRole('tab', { name: /model.py/ })).toBeDefined();
    await act(async () => { saving.resolve(ok({ ...document, text: draft, revision: 'epoch-a:bytes-2:identity-2' })); });
    await waitFor(() => expect(screen.queryByRole('tab', { name: /model.py/ })).toBeNull());
  });
  it('conflict retains draft and blocks source closing', async () => {
    const api = await start(); api.save_source_document.mockRejectedValue(new Error('SOURCE_CONFLICT: External edit'));
    closeSource(); fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /^save/i }));
    await waitFor(() => expect(screen.getAllByRole('alert').some(item => item.textContent?.includes('External edit'))).toBe(true));
    expect(textOf(sourceEditor())).toBe(draft); expect(screen.getByRole('tab', { name: /model.py/ }).textContent).toContain('●');
  });
  it('save failure blocks project mutation and keeps the pending navigation draft', async () => {
    const api = await start();
    api.save_source_document.mockRejectedValue(new Error('SOURCE_CONFLICT: External edit'));
    fireEvent.click(screen.getByRole('button', { name: /project-b/ }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /^save/i }));
    await waitFor(() => expect(api.save_source_document).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getAllByRole('alert').some(item => item.textContent?.includes('External edit'))).toBe(true));
    expect(api.open_project).not.toHaveBeenCalled(); expect(textOf(sourceEditor())).toBe(draft);
  });
  it('batch guard saves sequentially and a conflict stops later saves while retaining remaining drafts', async () => {
    const api = install();
    api.open_source_document.mockImplementation(async (path: string, location: Anchor) => ok({ ...document,
      projectPath: path, filePath: location.filePath }));
    api.get_problems.mockResolvedValue(ok(['second.py', 'third.py'].map(filePath => ({
      code: filePath, message: `Open ${filePath}`, filePath, line: 1, canOpenSource: true,
    }))));
    await start(api);
    for (const file of ['second.py', 'third.py']) {
      fireEvent.click(await screen.findByRole('button', { name: new RegExp(`Open ${file}`) }));
      await waitFor(() => expect(textOf(sourceEditor())).toBe(initialText)); edit(`draft ${file}`);
    }
    api.save_source_document.mockImplementation(async (request: SaveSourceRequest) => {
      if (request.filePath === 'second.py') throw new Error('SOURCE_CONFLICT: Second file changed');
      return ok({ ...document, ...request, revision: 'saved-first' });
    });
    fireEvent.click(screen.getByRole('button', { name: /project-b/ }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /^save/i }));
    await waitFor(() => expect(screen.getAllByRole('alert').some(item => item.textContent?.includes('Second file changed'))).toBe(true));
    expect(api.save_source_document.mock.calls.map(([request]) => (request as SaveSourceRequest).filePath)).toEqual(['model.py', 'second.py']);
    expect(api.open_project).not.toHaveBeenCalled();
    expect(textOf(sourceEditor())).toBe('draft third.py');
    for (const file of ['second.py', 'third.py']) expect(screen.getByRole('tab', { name: new RegExp(file) }).textContent).toContain('●');
  });
  it('does not discard newly entered text when a guard save resolves an older snapshot', async () => {
    const api = await start(); const saving = deferred<ReturnType<typeof ok<typeof document>>>();
    api.save_source_document.mockImplementationOnce(() => saving.promise); closeSource();
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /^save/i }));
    await waitFor(() => expect(api.save_source_document).toHaveBeenCalledOnce());
    const editor = sourceEditor();
    // A guard may freeze input. If input remains enabled, later input must survive.
    if (editor instanceof HTMLTextAreaElement && (editor.disabled || editor.readOnly)) {
      await act(async () => { saving.resolve(ok({ ...document, text: draft, revision: 'epoch-a:bytes-2:identity-2' })); });
      await waitFor(() => expect(screen.queryByRole('tab', { name: /model.py/ })).toBeNull());
    } else {
      edit('value = 5');
      await act(async () => { saving.resolve(ok({ ...document, text: draft, revision: 'epoch-a:bytes-2:identity-2' })); });
      expect(screen.getByRole('tab', { name: /model.py/ })).toBeDefined();
      expect(textOf(sourceEditor())).toBe('value = 5');
    }
  });
  it.each(['recent project', 'project chooser', 'reanalyze', 'analysis settings'] as const)(
    'cancel blocks %s and retains the source draft', async action => {
      const api = await start();
      if (action === 'recent project') fireEvent.click(screen.getByRole('button', { name: /project-b/ }));
      else if (action === 'project chooser') fireEvent.click(screen.getByRole('button', { name: /＋ Open project/ }));
      else if (action === 'reanalyze') fireEvent.click(screen.getByRole('button', { name: 'Re-analyze' }));
      else {
        fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
        const ignore = await screen.findByLabelText('Ignore paths · one per line');
        await waitFor(() => expect((ignore as HTMLTextAreaElement).value).toBe('.venv'));
        fireEvent.change(ignore, { target: { value: 'tests' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save analysis settings' }));
      }
      fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /cancel/i }));
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      expect(api.open_project).not.toHaveBeenCalled(); expect(api.choose_project).not.toHaveBeenCalled();
      expect(api.analyze).not.toHaveBeenCalled(); expect(api.set_analysis_settings).not.toHaveBeenCalled();
      if (action === 'analysis settings') fireEvent.click(screen.getByRole('tab', { name: /model.py/ }));
      await waitFor(() => expect(textOf(sourceEditor())).toBe(draft));
    },
  );
  it.each(['cancel', 'discard', 'save'] as const)('handles the actual colon native-close protocol with %s', async decision => {
    const api = await start(); const requestId = 'window-1:renderer-1:close-nonce';
    window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail: { requestId } }));
    const dialog = await screen.findByRole('dialog');
    expect(api.approve_close).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: new RegExp(`^${decision}`, 'i') }));
    if (decision === 'cancel') {
      await waitFor(() => expect(api.cancel_close.mock.calls).toEqual([[requestId]]));
      expect(api.approve_close).not.toHaveBeenCalled(); expect(textOf(sourceEditor())).toBe(draft);
    } else {
      await waitFor(() => expect(api.approve_close.mock.calls).toEqual([[requestId]]));
      expect(api.cancel_close).not.toHaveBeenCalled();
    }
    if (decision === 'save') expect(api.save_source_document).toHaveBeenCalledOnce();
    else expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('does not approve native close until an in-flight save actually completes', async () => {
    const api = await start(); const saving = deferred<ReturnType<typeof ok<typeof document>>>();
    api.save_source_document.mockImplementationOnce(() => saving.promise); ctrlSave();
    await waitFor(() => expect(api.save_source_document).toHaveBeenCalledOnce());
    const requestId = 'pending-save-close';
    window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail: { requestId } }));
    await act(async () => { await Promise.resolve(); });
    expect(api.approve_close).not.toHaveBeenCalled(); expect(api.cancel_close).not.toHaveBeenCalled();
    expect(textOf(sourceEditor())).toBe(draft);
    await act(async () => { saving.resolve(ok({ ...document, text: draft, revision: 'saved-2' })); });
    await waitFor(() => expect(api.approve_close).toHaveBeenCalledWith(requestId));
  });
  it('native conflict cancels closing without clearing the draft', async () => {
    const api = await start(); api.save_source_document.mockRejectedValue(new Error('SOURCE_CONFLICT: External edit'));
    const requestId = 'conflicting-save-close';
    window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail: { requestId } }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /^save/i }));
    await waitFor(() => expect(api.cancel_close).toHaveBeenCalledWith(requestId));
    expect(api.approve_close).not.toHaveBeenCalled(); expect(textOf(sourceEditor())).toBe(draft);
  });
  it('consumes a native requestId once and ignores malformed event payloads', async () => {
    const api = await start(); ctrlSave();
    await waitFor(() => expect((sourceEditor() as HTMLTextAreaElement).value).toBe(draft));
    await waitFor(() => expect(screen.getByRole('tab', { name: /model.py/ }).textContent).not.toContain('●'));
    for (const detail of [undefined, {}, { requestId: '' }, { requestId: 42 }]) {
      window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail }));
    }
    const requestId = 'single-use-close';
    window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail: { requestId } }));
    await waitFor(() => expect(api.approve_close).toHaveBeenCalledOnce());
    window.dispatchEvent(new CustomEvent('codeviz:close-request', { detail: { requestId } }));
    await act(async () => { await Promise.resolve(); });
    expect(api.approve_close.mock.calls).toEqual([[requestId]]); expect(api.cancel_close).not.toHaveBeenCalled();
  });
  it('the 40-tab limit cannot silently evict the only dirty source view', async () => {
    const tabs = [sourceTab(anchor).id, ...Array.from({ length: 38 }, (_, index) => sourceTab({
      ...anchor, filePath: `cached-${index}.py`,
    }).id), 'Overview'];
    const api = install(tabs);
    api.get_problems.mockResolvedValue(ok([{ code: 'new-file', message: 'Open another file', filePath: 'new.py', line: 1, canOpenSource: true }]));
    render(<App/>);
    await waitFor(() => expect(screen.getAllByRole('tab')).toHaveLength(40));
    fireEvent.click(screen.getByRole('tab', { name: /model.py/ }));
    await waitFor(() => expect(textOf(sourceEditor())).toBe(initialText)); edit();
    fireEvent.click(await screen.findByRole('button', { name: /Open another file/ }));
    await act(async () => { await Promise.resolve(); });
    const dialog = screen.queryByRole('dialog');
    if (dialog) {
      fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }));
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    }
    expect(screen.getByRole('tab', { name: /model.py/ })).toBeDefined();
    expect(screen.getAllByRole('tab').length).toBeLessThanOrEqual(40);
    fireEvent.click(screen.getByRole('tab', { name: /model.py/ }));
    await waitFor(() => expect(textOf(sourceEditor())).toBe(draft));
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
});
