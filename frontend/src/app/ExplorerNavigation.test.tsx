import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { editor } from 'monaco-editor';
import type { Anchor, SaveSourceRequest } from '../types';
import App from './App';
import { initialState, reducer, sourceTab } from './state';
import { anchor, graph, ok, overview, session, viewState } from './testFixtures';
import { releaseSourceModel, retainSourceModel } from './sourceModels';
import { sourceDocumentKey } from './sourceDocuments';
import { ExplorerState } from './explorerState';

// Keep App, Sidebar, SourceViewer, document store, tabs and guards real.
vi.mock('./components/GraphEditor', () => ({ default: () => <div>Graph canvas</div> }));
const projectPath = session.projectPath!;
const epoch = 'project-epoch-a';
const initialText = 'value = 1';
const draft = 'value = 2';
type Entry = { path: string; name: string; kind: 'file' | 'directory' | 'link' | 'other'; revision: string;
  readOnlyReason: string | null; canOpen: boolean; canCreate: boolean; canRename: boolean; canDelete: boolean };
type ListRequest = { projectPath: string; directory: string; projectEpoch?: string; cursor?: string | null };
type CreateRequest = { projectPath: string; projectEpoch: string; parent: string; parentRevision: string; name: string; kind: 'file' | 'directory' };
type PrepareRequest = { projectPath: string; projectEpoch: string; path: string; revision: string; operation: 'rename' | 'delete'; newName?: string };
type ApplyRequest = { projectPath: string; projectEpoch: string; operationToken: string };
function entry(path: string, kind: Entry['kind'] = 'file'): Entry {
  return { path, name: path.split('/').pop()!, kind, revision: `entry:${path}:1`, readOnlyReason: null,
    canOpen: kind === 'file', canCreate: kind === 'directory', canRename: true, canDelete: true };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function failure(code: string, message: string) { return { ok: false as const, schemaVersion: 1, error: { code, message } }; }
function source(filePath: string, path = projectPath, text = initialText, revision = `source:${filePath}:1`) {
  return { projectPath: path, filePath, text, revision, encoding: 'utf-8', newline: 'LF' as const, readOnlyReason: null };
}
function install(openTabs = ['Overview', 'Architecture', sourceTab(anchor).id], directoryEntries?: Map<string, Entry[]>, fileSystemCaseSensitive?: boolean) {
  const layout = { ...viewState, activeView: null, openTabs };
  const files = new Map([['model.py', source('model.py')], ['src/child.txt', source('src/child.txt')], ['unrelated.txt', source('unrelated.txt')]]);
  const directories = directoryEntries ?? new Map<string, Entry[]>([['', [entry('model.py'), entry('src', 'directory'), entry('unrelated.txt')]], ['src', [entry('src/child.txt')]]]);
  for (const children of directories.values()) for (const child of children) {
    if (child.kind === 'file' && !files.has(child.path)) files.set(child.path, source(child.path));
  }
  const plans = new Map<string, PrepareRequest>();
  let tokenNumber = 0;
  const api = {
    get_session: vi.fn().mockResolvedValue(ok({ ...session, ...(fileSystemCaseSensitive === undefined ? {} : { fileSystemCaseSensitive }) })),
    get_preferences: vi.fn().mockResolvedValue(ok({ language: 'zh-CN', issue: null })),
    get_view_state: vi.fn().mockResolvedValue(ok(layout)),
    get_overview: vi.fn().mockResolvedValue(ok(overview)), get_view: vi.fn().mockResolvedValue(ok(graph)),
    get_problems: vi.fn().mockResolvedValue(ok([{ code: 'anchor-two', message: 'Another anchor', filePath: 'model.py', line: 2, canOpenSource: true }])),
    save_layout: vi.fn().mockResolvedValue(ok(layout)),
    read_source: vi.fn().mockResolvedValue(ok({ filePath: 'model.py', text: initialText, startLine: 1, endLine: 2 })),
    open_source_document: vi.fn(async (path: string, location: Anchor) => ok({ ...(files.get(location.filePath) ?? source(location.filePath)), projectPath: path })),
    save_source_document: vi.fn(async (request: SaveSourceRequest) => {
      const saved = source(request.filePath, request.projectPath, request.text, `source:${request.filePath}:2`);
      files.set(request.filePath, saved);
      for (const children of directories.values()) for (const child of children) if (child.path === request.filePath) child.revision = `entry:${child.path}:2`;
      return ok(saved);
    }),
    list_project_directory: vi.fn(async (request: ListRequest) => ok({ projectPath: request.projectPath,
      projectEpoch: request.projectPath === projectPath ? epoch : 'project-epoch-b', directory: request.directory,
      directoryRevision: `directory:${request.directory}:1`, entries: directories.get(request.directory) ?? [], nextCursor: null as string | null })),
    create_project_entry: vi.fn(async (request: CreateRequest) => {
      const newPath = [request.parent, request.name].filter(Boolean).join('/');
      directories.set(request.parent, [...(directories.get(request.parent) ?? []), entry(newPath, request.kind)]);
      return ok({ operation: 'create' as const, oldPath: null, newPath, affectedPaths: [newPath] });
    }),
    prepare_project_entry_operation: vi.fn(async (request: PrepareRequest) => {
      const operationToken = `operation-${++tokenNumber}`; plans.set(operationToken, request);
      const newPath = request.operation === 'rename' ? [...request.path.split('/').slice(0, -1), request.newName!].join('/') : null;
      const affectedPaths = [request.path, ...[...files.keys()].filter(path => path.startsWith(`${request.path}/`))];
      return ok({ operationToken, operation: request.operation, path: request.path, newPath, affectedPaths });
    }),
    apply_project_entry_operation: vi.fn(async (request: ApplyRequest) => {
      const plan = plans.get(request.operationToken)!;
      const oldPath = plan.path;
      const newPath = plan.operation === 'rename' ? [...oldPath.split('/').slice(0, -1), plan.newName!].join('/') : null;
      const affectedPaths = [oldPath, ...[...files.keys()].filter(path => path.startsWith(`${oldPath}/`))];
      for (const path of affectedPaths) {
        const existing = files.get(path); files.delete(path);
        if (existing && newPath) { const renamed = newPath + path.slice(oldPath.length); files.set(renamed, { ...existing, filePath: renamed }); }
      }
      const moved = new Map<string, Entry[]>();
      for (const [directory, children] of directories) {
        if (directory === oldPath || directory.startsWith(`${oldPath}/`)) {
          if (!newPath) continue;
        }
        const renamedDirectory = newPath && (directory === oldPath || directory.startsWith(`${oldPath}/`)) ? newPath + directory.slice(oldPath.length) : directory;
        moved.set(renamedDirectory, children.flatMap(child => {
          if (child.path !== oldPath && !child.path.startsWith(`${oldPath}/`)) return [child];
          return newPath ? [entry(newPath + child.path.slice(oldPath.length), child.kind)] : [];
        }));
      }
      directories.clear(); for (const [directory, children] of moved) directories.set(directory, children);
      return ok({ operation: plan.operation, oldPath, newPath, affectedPaths });
    }),
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
type Api = ReturnType<typeof install>;
function sourceEditor(side = false) {
  const host = globalThis.document.querySelector(side ? '.side-editor .source-view' : '.primary-editor .source-view');
  const input = host?.querySelector<HTMLTextAreaElement>('textarea') ?? host?.querySelector<HTMLElement>('[role="textbox"]');
  expect(input, 'real source adapter must be mounted').toBeTruthy(); return input!;
}
function textOf(input = sourceEditor()) { return input instanceof HTMLTextAreaElement ? input.value : input.textContent; }
function edit(text = draft) { fireEvent.change(sourceEditor(), { target: { value: text } }); }
async function start(api = install(), dirty = true) {
  render(<App />);
  await waitFor(() => expect(textOf(sourceEditor())).toBe(initialText));
  if (dirty) edit();
  // This assertion deliberately fails on the existing Sidebar, rather than importing a missing component.
  return { api, tree: await screen.findByRole('tree', { name: '项目文件' }) };
}
async function item(name: string) {
  const row = await screen.findByRole('treeitem', { name });
  expect(row.getAttribute('data-path')).toBeTruthy();
  return row;
}
async function context(name: string, action: '重命名' | '删除' | '侧边打开') {
  fireEvent.contextMenu(await item(name));
  fireEvent.click(await screen.findByRole('menuitem', { name: action }));
}
async function requestRename(name = 'model.py', newName = 'renamed.py') {
  await context(name, '重命名');
  const dialog = await screen.findByRole('dialog');
  fireEvent.change(within(dialog).getByRole('textbox', { name: '名称' }), { target: { value: newName } });
  fireEvent.click(within(dialog).getByRole('button', { name: '重命名' }));
}
async function requestDelete(name = 'model.py') {
  await context(name, '删除');
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByRole('button', { name: '取消' })).toBeDefined();
  fireEvent.click(within(dialog).getByRole('button', { name: '移到回收站' }));
}
async function decision(name: '保存' | '放弃' | '取消') {
  const dialog = await screen.findByRole('dialog');
  // The guard uses the existing App Save/Discard/Cancel names.
  fireEvent.click(within(dialog).getByRole('button', { name }));
}
function model(path: string) {
  const dispose = vi.fn(); retainSourceModel(sourceDocumentKey(projectPath, path), { dispose } as unknown as editor.ITextModel);
  return dispose;
}
async function flush() { await act(async () => { await Promise.resolve(); }); }
function noMutation(api: Api) {
  expect(api.prepare_project_entry_operation).not.toHaveBeenCalled();
  expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
}
function lookupFreshEntry(explorer: ExplorerState, path: string, caseSensitive = true) {
  const lookup: (path: string, caseSensitive?: boolean) => ReturnType<ExplorerState['freshEntry']> = explorer.freshEntry.bind(explorer);
  return lookup(path, caseSensitive);
}
function tabIdentities() {
  return within(screen.getByRole('tablist')).getAllByRole('button', { name: /^关闭 / })
    .map(button => button.getAttribute('aria-label'));
}
async function continueDeferredDiscard() {
  await flush();
  const discard = screen.queryByRole('button', { name: '放弃' });
  if (discard) fireEvent.click(discard);
  await flush();
}
afterEach(() => {
  cleanup();
  for (const path of ['model.py', 'renamed.py', 'src/child.txt', 'renamed/child.txt', 'unrelated.txt', 'a.txt', 'b.txt', 'B.txt', 'target/child.txt', 'APP.PY', 'app.py', 'SRC/APP.PY', 'SRC\\APP.PY', 'src/app.py', 'renamed/APP.PY']) releaseSourceModel(sourceDocumentKey(projectPath, path));
  delete window.pywebview; vi.useRealTimers();
});

describe('fresh physical identity lookup through real ExplorerState', () => {
  it('fresh physical identity: prefers the original exact path even when a case-folded alias appears first', async () => {
    const exact = { ...entry('src/app.py'), revision: 'exact-physical-revision' };
    const alias = { ...entry('src/APP.PY'), revision: 'alias-physical-revision' };
    install(undefined, new Map([['src', [alias, exact]]]), false);
    const explorer = new ExplorerState(projectPath);
    try { await expect(lookupFreshEntry(explorer, 'src/app.py', false)).resolves.toEqual(exact); }
    finally { explorer.dispose(); }
  });
  it('fresh physical identity: resolves a unique case-folded physical path and returns its actual spelling and revision', async () => {
    const alias = { ...entry('src/APP.PY'), revision: 'post-save-physical-revision' };
    install(undefined, new Map([['src', [alias]]]), false);
    const explorer = new ExplorerState(projectPath);
    try { await expect(lookupFreshEntry(explorer, 'src/app.py', false)).resolves.toEqual(alias); }
    finally { explorer.dispose(); }
  });
  it.each(['same page', 'next page'] as const)('fresh physical identity: rejects ambiguous case-folded candidates on the %s when the original exact path is absent', async pagination => {
    const candidates = [entry('src/App.py'), entry('src/APP.PY')];
    const api = install(undefined, new Map([['src', candidates]]), false);
    if (pagination === 'next page') api.list_project_directory.mockImplementation(async request => ok({ projectPath: request.projectPath,
      projectEpoch: epoch, directory: request.directory, directoryRevision: 'directory:src:unchanged',
      entries: request.cursor ? [candidates[1]] : [candidates[0]], nextCursor: request.cursor ? null : 'page-2' }));
    const explorer = new ExplorerState(projectPath);
    try {
      await expect(lookupFreshEntry(explorer, 'src/app.py', false)).rejects.toMatchObject({ code: 'ENTRY_CONFLICT' });
      if (pagination === 'next page') expect(api.list_project_directory).toHaveBeenLastCalledWith({ projectPath, projectEpoch: epoch, directory: 'src', cursor: 'page-2' });
      noMutation(api);
    } finally { explorer.dispose(); }
  });
  it('fresh physical identity: waits for an exact match on the next page instead of choosing an earlier alias', async () => {
    const alias = { ...entry('src/APP.PY'), revision: 'alias-revision' };
    const exact = { ...entry('src/app.py'), revision: 'exact-next-page-revision' };
    const api = install(undefined, undefined, false);
    const secondPage = deferred<Awaited<ReturnType<Api['list_project_directory']>>>();
    api.list_project_directory.mockImplementation(async request => request.cursor ? secondPage.promise : ok({ projectPath: request.projectPath,
      projectEpoch: epoch, directory: request.directory, directoryRevision: 'directory:src:unchanged', entries: [alias], nextCursor: 'page-2' }));
    const explorer = new ExplorerState(projectPath);
    const lookup = lookupFreshEntry(explorer, 'src/app.py', false);
    let completed = false; void lookup.then(() => { completed = true; }, () => { completed = true; });
    try {
      await waitFor(() => expect(api.list_project_directory).toHaveBeenLastCalledWith({ projectPath, projectEpoch: epoch, directory: 'src', cursor: 'page-2' }));
      expect(completed).toBe(false);
    } finally {
      secondPage.resolve(ok({ projectPath, projectEpoch: epoch, directory: 'src', directoryRevision: 'directory:src:unchanged', entries: [exact], nextCursor: null }));
      await lookup.catch(() => {});
      explorer.dispose();
    }
    await expect(lookup).resolves.toEqual(exact);
    noMutation(api);
  });
});

describe('source case alias in project-entry state', () => {
  it.each([true, undefined])('source case alias: reducer case sensitivity %s leaves distinct APP.PY tab and side intact when app.py is deleted', caseSensitive => {
    const alias = { ...anchor, filePath: 'APP.PY' }; const aliasTab = sourceTab(alias);
    const workspace = { ...initialState, tabs: [...initialState.tabs, aliasTab], active: aliasTab.id, side: alias };
    const mutation = { type: 'project-entry' as const, oldPath: 'app.py', newPath: null, affectedPaths: ['app.py'],
      ...(caseSensitive === undefined ? {} : { caseSensitive }) };
    const next = reducer(workspace, mutation);
    expect(next.tabs).toEqual(workspace.tabs); expect(next.active).toBe(aliasTab.id); expect(next.side).toEqual(alias);
  });
  it.each(['alias', 'canonical'] as const)('source case alias: directory remap deduplicates slash/case aliases and preserves %s active identity and returnTabId', activeIdentity => {
    const alias = { ...anchor, filePath: 'SRC\\APP.PY' };
    const canonical = { ...anchor, filePath: 'src/APP.PY' };
    const aliasTab = sourceTab(alias); const canonicalTab = sourceTab(canonical);
    const unrelated = { ...sourceTab({ ...anchor, filePath: 'unrelated.txt' }), returnTabId: aliasTab.id };
    const workspace = { ...initialState, tabs: [...initialState.tabs, aliasTab, canonicalTab, unrelated],
      active: activeIdentity === 'alias' ? aliasTab.id : canonicalTab.id, side: alias };
    const mutation = { type: 'project-entry' as const, oldPath: 'src', newPath: 'renamed', affectedPaths: ['src'], caseSensitive: false };
    const next = reducer(workspace, mutation);
    const mapped = { ...anchor, filePath: 'renamed/APP.PY' }; const mappedId = sourceTab(mapped).id;
    expect(next.tabs.filter(tab => tab.kind === 'source').map(tab => tab.id)).toEqual([mappedId, unrelated.id]);
    expect(next.active).toBe(mappedId); expect(next.side).toEqual(mapped);
    expect(next.tabs.find(tab => tab.id === unrelated.id)?.returnTabId).toBe(mappedId);
    expect(new Set(next.tabs.map(tab => tab.id)).size).toBe(next.tabs.length);
  });
  it.each([
    ['rename', 'app.py', 'APP.PY', 'renamed.py', 'APP.PY.backup'],
    ['delete', 'app.py', 'APP.PY', null, 'APP.PY.backup'],
    ['rename', 'src', 'SRC/APP.PY', 'renamed', 'SRC-extra/APP.PY'],
    ['delete', 'src', 'SRC/APP.PY', null, 'SRC-extra/APP.PY'],
  ] as const)('source case alias: %s of %s remaps or closes alias %s tabs and side without affecting a sibling prefix', (operation, oldPath, aliasPath, newPath, unrelatedPath) => {
    const alias = { ...anchor, filePath: aliasPath };
    const aliasTab = sourceTab(alias);
    const unrelated = { ...sourceTab({ ...anchor, filePath: unrelatedPath }), returnTabId: aliasTab.id };
    const workspace = { ...initialState, tabs: [...initialState.tabs, aliasTab, unrelated], active: aliasTab.id,
      selectedId: 'retained-selection', roots: { Architecture: 'retained-root' }, side: alias };
    const mutation = { type: 'project-entry' as const, oldPath, newPath, affectedPaths: [oldPath], caseSensitive: false };
    const next = reducer(workspace, mutation);
    if (operation === 'rename') {
      const mapped = { ...alias, filePath: newPath! + aliasPath.slice(oldPath.length) };
      const mappedId = sourceTab(mapped).id;
      expect([next.tabs.find(tab => tab.id === mappedId)?.anchor, next.side]).toEqual([mapped, mapped]);
      expect(next.active).toBe(mappedId);
      expect(next.tabs.find(tab => tab.id === unrelated.id)?.returnTabId).toBe(mappedId);
    } else {
      expect([next.tabs.some(tab => tab.id === aliasTab.id), next.side]).toEqual([false, null]);
      expect(next.tabs.some(tab => tab.id === next.active)).toBe(true);
    }
    expect(next.tabs.find(tab => tab.id === unrelated.id)?.anchor).toEqual(unrelated.anchor);
    expect(next.selectedId).toBe(workspace.selectedId); expect(next.roots).toEqual(workspace.roots);
    expect(new Set(next.tabs.map(tab => tab.id)).size).toBe(next.tabs.length);
  });
});

describe('Explorer navigation through real App', () => {
  it.each([false, true, undefined])('post-save physical basename: case sensitivity %s controls fresh lookup after helper.py becomes HELPER.PY', async caseSensitive => {
    const restoredPath = caseSensitive === false ? 'SRC/HELPER.PY' : 'src/helper.py';
    const restored = { ...anchor, filePath: restoredPath };
    const directories = new Map<string, Entry[]>([['', [entry('src', 'directory')]], ['src', [entry('src/helper.py')]]]);
    const api = install(['Overview', sourceTab(restored).id], directories, caseSensitive);
    const saving = deferred<void>(); let saved = false;
    api.save_source_document.mockImplementation(async request => {
      await saving.promise;
      // Atomic replacement changes the physical basename while retaining the source view identity.
      directories.set('src', [{ ...entry('src/HELPER.PY'), revision: 'entry:src/HELPER.PY:after-save' }]);
      saved = true;
      return ok(source(request.filePath, request.projectPath, request.text, 'source:helper:after-save'));
    });
    const prepareEntry = api.prepare_project_entry_operation.getMockImplementation()!;
    api.prepare_project_entry_operation.mockImplementation(async request => {
      const physical = directories.get('src')!.find(child => child.path === request.path);
      if (!physical || physical.revision !== request.revision) return failure('ENTRY_CONFLICT', 'Prepare must use the fresh physical path and revision') as never;
      return prepareEntry(request);
    });
    const openDocument = api.open_source_document.getMockImplementation()!;
    api.open_source_document.mockImplementation((path, location) => saved && location.filePath === 'src/renamed.py'
      ? Promise.resolve(ok(source(location.filePath, path, draft, 'source:helper:after-save'))) : openDocument(path, location));
    await start(api); fireEvent.click(await item('src')); await item('helper.py');
    const identities = tabIdentities();
    try {
      await requestRename('helper.py', 'renamed.py'); await flush(); noMutation(api);
      await decision('保存'); await waitFor(() => expect(api.save_source_document).toHaveBeenCalledOnce());
      noMutation(api);
      expect(directories.get('src')![0].path).toBe('src/helper.py');
    } finally { await act(async () => { saving.resolve(); }); }
    expect(saved).toBe(true);
    expect(api.save_source_document).toHaveBeenCalledWith({ projectPath, filePath: restoredPath, text: draft, revision: `source:${restoredPath}:1` });
    if (caseSensitive === false) {
      await waitFor(() => expect(api.prepare_project_entry_operation).toHaveBeenCalledWith({ projectPath, projectEpoch: epoch,
        path: 'src/HELPER.PY', revision: 'entry:src/HELPER.PY:after-save', operation: 'rename', newName: 'renamed.py' }));
      expect(api.save_source_document.mock.invocationCallOrder[0]).toBeLessThan(api.prepare_project_entry_operation.mock.invocationCallOrder[0]);
      const postSaveListing = api.list_project_directory.mock.calls.findIndex(([request], index) => request.directory === 'src'
        && api.list_project_directory.mock.invocationCallOrder[index] > api.save_source_document.mock.invocationCallOrder[0]);
      expect(postSaveListing).toBeGreaterThanOrEqual(0);
      expect(api.list_project_directory.mock.invocationCallOrder[postSaveListing]).toBeLessThan(api.prepare_project_entry_operation.mock.invocationCallOrder[0]);
      await waitFor(() => expect(api.apply_project_entry_operation).toHaveBeenCalledOnce());
      await screen.findByRole('tab', { name: /renamed\.py/ }); await waitFor(() => expect(textOf()).toBe(draft));
      expect(screen.queryAllByRole('alert').some(alert => alert.textContent?.includes('ENTRY_CONFLICT'))).toBe(false);
    } else {
      await waitFor(() => expect(screen.queryAllByRole('alert').some(alert => alert.textContent?.includes('ENTRY_CONFLICT'))).toBe(true));
      noMutation(api); expect(tabIdentities()).toEqual(identities); expect(textOf()).toBe(draft);
    }
    expect(api.save_source_document).toHaveBeenCalledOnce();
    expect(new Set(tabIdentities()).size).toBe(tabIdentities().length); expect(api.analyze).not.toHaveBeenCalled();
  });
  it.each(['rename', 'delete'] as const)('source case alias: canceling directory %s guards dirty SRC/APP.PY descendants on a case-insensitive session', async operation => {
    const alias = { ...anchor, filePath: 'SRC/APP.PY' };
    const directories = new Map<string, Entry[]>([['', [entry('src', 'directory')]], ['src', [entry('src/app.py')]]]);
    const api = install(['Overview', sourceTab(alias).id], directories, false);
    await start(api); const identities = tabIdentities(); const dispose = model('SRC/APP.PY');
    if (operation === 'rename') await requestRename('src', 'renamed'); else await requestDelete('src');
    await flush(); noMutation(api);
    const dialog = await screen.findByRole('dialog'); expect(dialog.textContent).toContain('SRC/APP.PY');
    await decision('取消'); await flush();
    noMutation(api); expect(tabIdentities()).toEqual(identities); expect(textOf()).toBe(draft);
    expect(dispose).not.toHaveBeenCalled(); expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('source case alias: Discard retains the APP.PY draft until app.py rename commits successfully', async () => {
    const alias = { ...anchor, filePath: 'APP.PY' };
    const api = install(['Overview', sourceTab(alias).id], new Map([['', [entry('app.py')]]]), false);
    await start(api); const identities = tabIdentities(); const dispose = model('APP.PY');
    const applying = deferred<void>(); const applyEntry = api.apply_project_entry_operation.getMockImplementation()!;
    api.apply_project_entry_operation.mockImplementation(async request => { await applying.promise; return applyEntry(request); });
    try {
      await requestRename('app.py', 'renamed.py'); await flush(); noMutation(api);
      await decision('放弃'); await waitFor(() => expect(api.apply_project_entry_operation).toHaveBeenCalledOnce());
      expect(textOf()).toBe(draft); expect(tabIdentities()).toEqual(identities);
      expect(screen.getByRole('tab', { name: /APP\.PY/ }).textContent).toContain('●');
      expect(dispose).not.toHaveBeenCalled(); expect(api.save_source_document).not.toHaveBeenCalled();
    } finally { await act(async () => { applying.resolve(); }); }
    await screen.findByRole('tab', { name: /renamed\.py/ });
    expect(screen.queryByRole('tab', { name: /APP\.PY/ })).toBeNull();
    expect(dispose).toHaveBeenCalledOnce(); await waitFor(() => expect(textOf()).toBe(initialText));
  });
  it('source case alias: Save of a dirty directory descendant finishes before prepare and preserves a slash-normalized renamed draft', async () => {
    const alias = { ...anchor, filePath: 'SRC\\APP.PY' };
    const directories = new Map<string, Entry[]>([['', [entry('src', 'directory')]], ['src', [entry('src/app.py')]]]);
    const api = install(['Overview', sourceTab(alias).id], directories, false);
    let saved = false;
    api.save_source_document.mockImplementation(async request => {
      saved = true;
      directories.set('src', [{ ...entry('src/app.py'), revision: 'entry:src/app.py:2' }]);
      return ok(source(request.filePath, request.projectPath, request.text, 'source:alias:2'));
    });
    const openDocument = api.open_source_document.getMockImplementation()!;
    api.open_source_document.mockImplementation((path, location) => saved && location.filePath === 'renamed/APP.PY'
      ? Promise.resolve(ok(source(location.filePath, path, draft, 'source:alias:2'))) : openDocument(path, location));
    await start(api); const dispose = model('SRC\\APP.PY');
    await requestRename('src', 'renamed'); await flush(); noMutation(api);
    await decision('保存'); await waitFor(() => expect(api.apply_project_entry_operation).toHaveBeenCalledOnce());
    expect(api.save_source_document).toHaveBeenCalledWith({ projectPath, filePath: alias.filePath, text: draft, revision: `source:${alias.filePath}:1` });
    expect(api.save_source_document.mock.invocationCallOrder[0]).toBeLessThan(api.prepare_project_entry_operation.mock.invocationCallOrder[0]);
    await waitFor(() => expect(globalThis.document.querySelector('.primary-editor .source-heading')?.textContent).toContain('renamed/APP.PY'));
    expect(screen.queryByRole('tab', { name: /APP\.PY/ })?.textContent).not.toContain('●');
    await waitFor(() => expect(textOf()).toBe(draft)); expect(dispose).toHaveBeenCalledOnce();
  });
  it.each([true, undefined])('source case alias: a session with case sensitivity %s does not guard or release a distinct dirty APP.PY when deleting app.py', async caseSensitive => {
    const alias = { ...anchor, filePath: 'APP.PY' };
    const api = install(['Overview', sourceTab(alias).id], new Map([['', [entry('app.py'), entry('APP.PY')]]]), caseSensitive);
    await start(api); const identities = tabIdentities(); const dispose = model('APP.PY');
    await requestDelete('app.py');
    await waitFor(() => expect(api.apply_project_entry_operation).toHaveBeenCalledOnce()); await flush();
    expect(screen.queryByRole('dialog')).toBeNull(); expect(tabIdentities()).toEqual(identities);
    expect(textOf()).toBe(draft); expect(screen.getByRole('tab', { name: /APP\.PY/ }).textContent).toContain('●');
    expect(dispose).not.toHaveBeenCalled(); expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it.each([true, undefined])('source case alias: a session with case sensitivity %s permits a.txt to b.txt while distinct active B.txt is dirty', async caseSensitive => {
    const directories = new Map<string, Entry[]>([['', [entry('model.py'), entry('a.txt'), entry('B.txt')]]]);
    const api = install(undefined, directories, caseSensitive); await start(api, false);
    fireEvent.click(await item('B.txt')); await waitFor(() => expect(textOf()).toBe(initialText)); edit('distinct uppercase draft');
    const identities = tabIdentities(); directories.set('', [entry('model.py'), entry('a.txt')]);
    await requestRename('a.txt', 'b.txt');
    await waitFor(() => expect(api.apply_project_entry_operation).toHaveBeenCalledOnce()); await flush();
    expect(screen.queryAllByRole('alert').some(alert => alert.textContent?.includes('SOURCE_IDENTITY_CONFLICT'))).toBe(false);
    expect(tabIdentities()).toEqual(identities); expect(textOf()).toBe('distinct uppercase draft');
    expect(screen.getByRole('tab', { name: /B\.txt/ }).getAttribute('aria-selected')).toBe('true');
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it.each(['rename', 'delete'] as const)('source case alias: canceling %s of app.py guards dirty APP.PY and preserves its tab and model', async operation => {
    const alias = { ...anchor, filePath: 'APP.PY' };
    const api = install(['Overview', sourceTab(alias).id], new Map([['', [entry('app.py'), entry('unrelated.txt')]]]), false);
    await start(api);
    const identities = tabIdentities(); const dispose = model('APP.PY');
    if (operation === 'rename') await requestRename('app.py', 'renamed.py'); else await requestDelete('app.py');
    await flush();
    noMutation(api);
    const guard = await screen.findByRole('dialog');
    expect(guard.textContent).toContain('APP.PY');
    await decision('取消'); await flush();
    noMutation(api); expect(api.save_source_document).not.toHaveBeenCalled();
    expect(tabIdentities()).toEqual(identities); expect(textOf()).toBe(draft);
    expect(screen.getByRole('tab', { name: /APP\.PY/ }).textContent).toContain('●');
    expect(dispose).not.toHaveBeenCalled();
  });
  it.each(['rename', 'delete'] as const)('source case alias: successful %s of app.py releases the APP.PY model and updates the alias tab', async operation => {
    const alias = { ...anchor, filePath: 'APP.PY' };
    const api = install(['Overview', sourceTab(alias).id], new Map([['', [entry('app.py'), entry('unrelated.txt')]]]), false);
    await start(api, false);
    const dispose = model('APP.PY');
    if (operation === 'rename') await requestRename('app.py', 'renamed.py'); else await requestDelete('app.py');
    await waitFor(() => expect(api.apply_project_entry_operation).toHaveBeenCalledOnce());
    await flush();
    expect(dispose).toHaveBeenCalledOnce();
    expect(screen.queryByRole('tab', { name: /APP\.PY/ })).toBeNull();
    if (operation === 'rename') {
      const tab = await screen.findByRole('tab', { name: /renamed\.py/ });
      expect(tab.getAttribute('aria-selected')).toBe('true');
      await waitFor(() => expect(textOf()).toBe(initialText));
      expect(api.open_source_document.mock.calls.some(([, location]) => location.filePath === 'renamed.py')).toBe(true);
    } else expect(globalThis.document.querySelector('.primary-editor .source-view')).toBeNull();
    expect(new Set(tabIdentities()).size).toBe(tabIdentities().length);
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it.each(['rename', 'delete'] as const)('source case alias: an old pending APP.PY response cannot revive source state after app.py %s', async operation => {
    const alias = { ...anchor, filePath: 'APP.PY' };
    const api = install(['Overview', sourceTab(alias).id], new Map([['', [entry('app.py'), entry('unrelated.txt')]]]), false);
    const opening = deferred<Awaited<ReturnType<Api['open_source_document']>>>();
    const openDocument = api.open_source_document.getMockImplementation()!;
    api.open_source_document.mockImplementation((path, location) => location.filePath === 'APP.PY'
      ? opening.promise : openDocument(path, location));
    render(<App />);
    await item('app.py');
    await waitFor(() => expect(api.open_source_document.mock.calls.some(([, location]) => location.filePath === 'APP.PY')).toBe(true));
    try {
      if (operation === 'rename') await requestRename('app.py', 'renamed.py'); else await requestDelete('app.py');
      await waitFor(() => expect(api.apply_project_entry_operation).toHaveBeenCalledOnce());
      await flush();
    } finally {
      await act(async () => { opening.resolve(ok(source('APP.PY', projectPath, 'obsolete alias source text'))); });
    }
    expect(screen.queryByDisplayValue('obsolete alias source text')).toBeNull();
    expect(screen.queryByRole('tab', { name: /APP\.PY/ })).toBeNull();
    if (operation === 'rename') {
      await screen.findByRole('tab', { name: /renamed\.py/ });
      await waitFor(() => expect(textOf()).toBe(initialText));
    } else expect(globalThis.document.querySelector('.primary-editor .source-view')).toBeNull();
    expect(screen.queryAllByRole('alert').some(alert => alert.textContent?.includes('SOURCE_PROJECT_CHANGED'))).toBe(false);
  });
  it('rejects rename a.txt to an externally deleted dirty b.txt without merging tabs or losing either draft', async () => {
    const directories = new Map<string, Entry[]>([['', [entry('model.py'), entry('a.txt'), entry('b.txt')]]]);
    const api = install(undefined, directories);
    await start(api, false);
    fireEvent.click(await item('b.txt')); await waitFor(() => expect(textOf()).toBe(initialText)); edit('target b draft');
    fireEvent.click(await item('a.txt')); await waitFor(() => expect(textOf()).toBe(initialText)); edit('source a draft');
    const identities = tabIdentities();
    directories.set('', [entry('model.py'), entry('a.txt')]);
    await requestRename('a.txt', 'b.txt'); await continueDeferredDiscard();
    expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryAllByRole('alert').length).toBeGreaterThan(0));
    expect(tabIdentities()).toEqual(identities);
    expect(new Set(tabIdentities()).size).toBe(tabIdentities().length);
    for (const [name, text] of [['a.txt', 'source a draft'], ['b.txt', 'target b draft']]) {
      const tab = screen.getByRole('tab', { name: new RegExp(name.replace('.', '\\.')) });
      expect(tab.textContent).toContain('●'); fireEvent.click(tab);
      await waitFor(() => expect(textOf()).toBe(text));
    }
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('rejects a case-folded rename target when active dirty B.txt was externally deleted before a.txt becomes b.txt', async () => {
    const directories = new Map<string, Entry[]>([['', [entry('model.py'), entry('a.txt'), entry('B.txt')]]]);
    const api = install(undefined, directories, false);
    await start(api, false);
    fireEvent.click(await item('B.txt'));
    await waitFor(() => expect(textOf()).toBe(initialText)); edit('active uppercase target draft');
    const targetTab = screen.getByRole('tab', { name: /B\.txt/ });
    expect(targetTab.getAttribute('aria-selected')).toBe('true');
    const identities = tabIdentities();
    // The session declares a case-insensitive filesystem without relying on the test runner OS.
    directories.set('', [entry('model.py'), entry('a.txt')]);
    await requestRename('a.txt', 'b.txt'); await continueDeferredDiscard();
    expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryAllByRole('alert').length).toBeGreaterThan(0));
    expect(tabIdentities()).toEqual(identities);
    expect(new Set(tabIdentities()).size).toBe(tabIdentities().length);
    expect(screen.queryByRole('tab', { name: /b\.txt/ })).toBeNull();
    const retained = screen.getByRole('tab', { name: /B\.txt/ });
    expect(retained.getAttribute('aria-selected')).toBe('true'); expect(retained.textContent).toContain('●');
    expect(textOf()).toBe('active uppercase target draft');
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('rejects a folder rename whose mapped descendant already has a dirty target document and side editor', async () => {
    const directories = new Map<string, Entry[]>([
      ['', [entry('model.py'), entry('src', 'directory'), entry('target', 'directory')]],
      ['src', [entry('src/child.txt')]], ['target', [entry('target/child.txt')]],
    ]);
    const api = install(undefined, directories);
    await start(api, false);
    fireEvent.click(await item('target'));
    await context('child.txt', '侧边打开');
    await waitFor(() => expect(textOf(sourceEditor(true))).toBe(initialText));
    fireEvent.change(sourceEditor(true), { target: { value: 'target descendant draft' } });
    fireEvent.click(await item('child.txt'));
    await waitFor(() => expect(textOf()).toBe('target descendant draft'));
    fireEvent.click(await item('target')); fireEvent.click(await item('src'));
    fireEvent.click(await item('child.txt')); await waitFor(() => expect(textOf()).toBe(initialText)); edit('source descendant draft');
    const identities = tabIdentities();
    directories.set('', [entry('model.py'), entry('src', 'directory')]); directories.delete('target');
    await requestRename('src', 'target'); await continueDeferredDiscard();
    expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryAllByRole('alert').length).toBeGreaterThan(0));
    expect(tabIdentities()).toEqual(identities);
    expect(new Set(tabIdentities()).size).toBe(tabIdentities().length);
    expect(textOf()).toBe('source descendant draft');
    expect(textOf(sourceEditor(true))).toBe('target descendant draft');
    expect(globalThis.document.querySelector('.side-editor .source-heading')?.textContent).toContain('target/child.txt');
    const sourceTabId = sourceTab({ filePath: 'src/child.txt', startLine: 1, endLine: 1, startColumn: null, endColumn: null }).id;
    const targetTab = screen.getAllByRole('tab', { name: /child.txt/ }).find(tab =>
      !within(tab.parentElement!).queryByRole('button', { name: `关闭 ${sourceTabId}` }))!;
    fireEvent.click(targetTab); await waitFor(() => expect(textOf()).toBe('target descendant draft'));
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('rejects a folder rename while its mapped target descendant has a pending source open', async () => {
    const directories = new Map<string, Entry[]>([
      ['', [entry('model.py'), entry('src', 'directory'), entry('target', 'directory')]],
      ['src', [entry('src/child.txt')]], ['target', [entry('target/child.txt')]],
    ]);
    const api = install(undefined, directories);
    await start(api, false);
    const opening = deferred<Awaited<ReturnType<Api['open_source_document']>>>();
    const openDocument = api.open_source_document.getMockImplementation()!;
    api.open_source_document.mockImplementation((path, location) => location.filePath === 'target/child.txt'
      ? opening.promise : openDocument(path, location));
    fireEvent.click(await item('target')); fireEvent.click(await item('child.txt'));
    await waitFor(() => expect(api.open_source_document.mock.calls.some(([, location]) => location.filePath === 'target/child.txt')).toBe(true));
    const identities = tabIdentities();
    directories.set('', [entry('model.py'), entry('src', 'directory')]); directories.delete('target');
    try {
      await requestRename('src', 'target'); await flush();
      expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
      await waitFor(() => expect(screen.queryAllByRole('alert').length).toBeGreaterThan(0));
      expect(tabIdentities()).toEqual(identities);
      expect(new Set(tabIdentities()).size).toBe(tabIdentities().length);
    } finally {
      await act(async () => { opening.resolve(ok(source('target/child.txt', projectPath, 'pending target text'))); });
    }
    await waitFor(() => expect(textOf()).toBe('pending target text'));
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it.each(['rename', 'delete', 'create'] as const)('FILE_OPERATION_FAILED after a native %s changes disk refreshes tree and Outdated while retaining drafts without retrying writes', async operation => {
    const api = install(); await start(api);
    await context('model.py', '侧边打开'); await waitFor(() => expect(textOf(sourceEditor(true))).toBe(draft));
    const identities = tabIdentities(); const hydrations = api.get_view_state.mock.calls.length;
    const dispose = model('model.py');
    const listDirectory = api.list_project_directory.getMockImplementation()!;
    let diskChanged = false;
    api.list_project_directory.mockImplementation(async request => {
      const response = await listDirectory(request);
      if (!diskChanged || request.directory !== '') return response;
      return ok({ ...response.data, directoryRevision: 'directory::after-failed-write', entries: [
        ...response.data.entries.filter(child => operation === 'create' || child.path !== 'model.py'),
        ...(operation === 'delete' ? [] : [entry(operation === 'create' ? 'notes.txt' : 'renamed.py')]),
      ] });
    });
    let listingCountAtFailure = 0;
    const failAfterDiskChange = async () => {
      diskChanged = true; listingCountAtFailure = api.list_project_directory.mock.calls.length;
      api.get_session.mockResolvedValue(ok({ ...session, status: 'Outdated' }));
      return failure('FILE_OPERATION_FAILED', 'Disk changed but native operation did not complete') as never;
    };
    if (operation === 'create') {
      api.create_project_entry.mockImplementation(failAfterDiskChange);
      fireEvent.click(screen.getByRole('button', { name: '新建文件' }));
      const dialog = await screen.findByRole('dialog');
      fireEvent.change(within(dialog).getByRole('textbox', { name: '名称' }), { target: { value: 'notes.txt' } });
      fireEvent.click(within(dialog).getByRole('button', { name: '创建' }));
    } else {
      api.apply_project_entry_operation.mockImplementation(failAfterDiskChange);
      if (operation === 'rename') await requestRename(); else await requestDelete();
      await decision('放弃');
    }
    await waitFor(() => expect(screen.queryAllByRole('alert').some(alert => alert.textContent?.includes('FILE_OPERATION_FAILED'))).toBe(true));
    expect(tabIdentities()).toEqual(identities); expect(textOf()).toBe(draft); expect(textOf(sourceEditor(true))).toBe(draft);
    expect(screen.getByRole('tab', { name: /model.py/ }).textContent).toContain('●'); expect(dispose).not.toHaveBeenCalled();
    expect(api.save_source_document).not.toHaveBeenCalled(); expect(api.get_view_state).toHaveBeenCalledTimes(hydrations);
    await waitFor(() => expect(globalThis.document.querySelector('.statusbar')?.textContent).toContain('已过期'));
    await waitFor(() => expect(api.list_project_directory.mock.calls.slice(listingCountAtFailure).some(([request]) => request.directory === '')).toBe(true));
    if (operation === 'create') await item('notes.txt');
    else {
      await waitFor(() => expect(screen.queryByRole('treeitem', { name: 'model.py' })).toBeNull());
      if (operation === 'rename') await item('renamed.py');
    }
    expect(api.create_project_entry).toHaveBeenCalledTimes(operation === 'create' ? 1 : 0);
    expect(api.prepare_project_entry_operation).toHaveBeenCalledTimes(operation === 'create' ? 0 : 1);
    expect(api.apply_project_entry_operation).toHaveBeenCalledTimes(operation === 'create' ? 0 : 1);
    expect(api.analyze).not.toHaveBeenCalled();
  });
  it.each([
    ['toolbar', '新建文件', 'file', 'notes.txt'],
    ['menu', '新建文件夹', 'directory', 'assets'],
  ] as const)('%s creates %s inside vendor despite its protected descendant reason', async (surface, action, kind, name) => {
    const api = install();
    const listDirectory = api.list_project_directory.getMockImplementation()!;
    const vendor = { ...entry('vendor', 'directory'), readOnlyReason: 'Contains protected directory: vendor/.git',
      canCreate: true, canRename: false, canDelete: false };
    let children: Entry[] = [{ ...entry('vendor/.git', 'directory'), readOnlyReason: 'Protected directory',
      canCreate: false, canRename: false, canDelete: false }];
    api.list_project_directory.mockImplementation(async request => {
      const response = await listDirectory(request);
      return request.directory === '' ? ok({ ...response.data, entries: [...response.data.entries, vendor] })
        : request.directory === 'vendor' ? ok({ ...response.data, entries: children }) : response;
    });
    const createEntry = api.create_project_entry.getMockImplementation()!;
    api.create_project_entry.mockImplementation(async request => {
      const response = await createEntry(request);
      children = [...children, entry(response.data.newPath, request.kind)];
      return response;
    });
    await start(api, false);
    const target = await item('vendor'); fireEvent.click(target);
    const protectedChild = await item('.git');
    expect(protectedChild.getAttribute('data-path')).toBe('vendor/.git');
    let command: HTMLElement;
    if (surface === 'menu') {
      fireEvent.contextMenu(target);
      command = within(await screen.findByRole('menu', { name: 'vendor' })).getByRole('menuitem', { name: action });
    } else command = screen.getByRole('button', { name: action });
    expect((command as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(command);
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByRole('textbox', { name: '名称' }), { target: { value: name } });
    fireEvent.click(within(dialog).getByRole('button', { name: '创建' }));
    await waitFor(() => expect(api.create_project_entry).toHaveBeenCalledWith({ projectPath, projectEpoch: epoch,
      parent: 'vendor', parentRevision: 'directory:vendor:1', name, kind }));
    expect((await item(name)).getAttribute('data-path')).toBe(`vendor/${name}`);
    noMutation(api); expect(api.analyze).not.toHaveBeenCalled();
  });
  it('loads a real project tree independently of analysis', async () => {
    const api = install(['Overview']); api.get_session.mockResolvedValue(ok({ ...session, status: 'NotAnalyzed', hasAnalysis: false }));
    render(<App />);
    await screen.findByRole('tree', { name: '项目文件' }); await item('model.py');
    expect(api.list_project_directory).toHaveBeenCalledWith(expect.objectContaining({ projectPath, directory: '' }));
    expect(api.analyze).not.toHaveBeenCalled(); expect(api.get_view).not.toHaveBeenCalled();
  });
  it('opens generic text with the existing tabs and shares drafts with an existing source anchor', async () => {
    const { api } = await start(); fireEvent.click(await item('model.py'));
    await waitFor(() => expect(textOf()).toBe(draft));
    expect(screen.queryByRole('dialog')).toBeNull(); expect(api.save_source_document).not.toHaveBeenCalled();
    expect(api.open_source_document.mock.calls.filter(([, a]) => a.filePath === 'model.py')).toHaveLength(1);
    fireEvent.click(await item('unrelated.txt'));
    await waitFor(() => expect(textOf()).toBe(initialText)); edit('plain text draft');
    expect(screen.getByRole('tab', { name: /unrelated.txt/ }).textContent).toContain('●');
    fireEvent.click(screen.getAllByRole('tab', { name: /model.py/ })[0]);
    await waitFor(() => expect(textOf()).toBe(draft));
  });
  it('opens the same shared draft in the split editor', async () => {
    const { api } = await start(); await context('model.py', '侧边打开');
    await waitFor(() => expect(textOf(sourceEditor(true))).toBe(draft));
    fireEvent.change(sourceEditor(true), { target: { value: 'edited in split' } });
    await waitFor(() => expect(textOf()).toBe('edited in split'));
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it.each(['rename', 'delete'] as const)('canceling the dirty %s guard keeps tabs, model and draft before prepare', async operation => {
    const { api } = await start(); const dispose = model('model.py');
    if (operation === 'rename') await requestRename(); else await requestDelete();
    await decision('取消'); await flush();
    noMutation(api); expect(dispose).not.toHaveBeenCalled(); expect(textOf()).toBe(draft);
    expect(screen.getByRole('tab', { name: /model.py/ }).textContent).toContain('●');
  });
  it.each(['rename', 'delete'] as const)('a failed guard save blocks %s without applying or releasing the model', async operation => {
    const api = install(); api.save_source_document.mockResolvedValue(failure('SOURCE_CONFLICT', 'External bytes changed') as never);
    await start(api); const dispose = model('model.py');
    if (operation === 'rename') await requestRename(); else await requestDelete();
    await decision('保存');
    await waitFor(() => expect(screen.getAllByRole('alert').some(node => node.textContent?.includes('SOURCE_CONFLICT') && node.textContent.includes('External bytes changed'))).toBe(true));
    noMutation(api); expect(dispose).not.toHaveBeenCalled(); expect(textOf()).toBe(draft);
  });
  it('awaits a pending save and prepares against the refreshed entry revision', async () => {
    const api = install(); await start(api);
    const saving = deferred<ReturnType<typeof ok<ReturnType<typeof source>>>>();
    api.save_source_document.mockImplementationOnce(() => saving.promise);
    fireEvent.keyDown(window, { key: 's', ctrlKey: true });
    await waitFor(() => expect(api.save_source_document).toHaveBeenCalledOnce());
    await requestRename(); await flush(); noMutation(api);
    // The save result and a fresh listing establish a new identity before prepare.
    api.list_project_directory.mockImplementation(async request => ok({ projectPath: request.projectPath, projectEpoch: epoch,
      directory: request.directory, directoryRevision: 'directory::2', entries: [ { ...entry('model.py'), revision: 'entry:model.py:2' }, entry('unrelated.txt') ], nextCursor: null }));
    await act(async () => { saving.resolve(ok(source('model.py', projectPath, draft, 'source:model.py:2'))); });
    await waitFor(() => expect(api.prepare_project_entry_operation).toHaveBeenCalledWith({ projectPath, projectEpoch: epoch,
      path: 'model.py', revision: 'entry:model.py:2', operation: 'rename', newName: 'renamed.py' }));
    const result = await api.prepare_project_entry_operation.mock.results[0].value;
    await waitFor(() => expect(api.apply_project_entry_operation).toHaveBeenCalledWith({ projectPath, projectEpoch: epoch, operationToken: result.data.operationToken }));
  });
  it('save precedes a fresh prepare token and apply, then refreshes Outdated without hydrating workspace', async () => {
    const { api } = await start(); const hydrations = api.get_view_state.mock.calls.length;
    api.get_session.mockResolvedValue(ok({ ...session, status: 'Outdated' }));
    await requestRename(); noMutation(api); await decision('保存');
    await waitFor(() => expect(api.apply_project_entry_operation).toHaveBeenCalledOnce());
    expect(api.save_source_document).toHaveBeenCalledWith({ projectPath, filePath: 'model.py', text: draft, revision: 'source:model.py:1' });
    expect(api.save_source_document.mock.invocationCallOrder[0]).toBeLessThan(api.prepare_project_entry_operation.mock.invocationCallOrder[0]);
    expect(api.prepare_project_entry_operation.mock.calls[0][0].revision).toBe('entry:model.py:2');
    await waitFor(() => expect(globalThis.document.querySelector('.statusbar')?.textContent).toContain('已过期'));
    expect(api.get_view_state).toHaveBeenCalledTimes(hydrations); expect(api.analyze).not.toHaveBeenCalled();
  });
  it.each(['prepare', 'apply'] as const)('failed %s after Discard retains the original dirty document and model', async stage => {
    const api = install();
    if (stage === 'prepare') api.prepare_project_entry_operation.mockResolvedValue(failure('ENTRY_CONFLICT', 'Entry was replaced') as never);
    else api.apply_project_entry_operation.mockResolvedValue(failure('ENTRY_CONFLICT', 'Entry was replaced') as never);
    await start(api); const dispose = model('model.py'); await requestRename(); await decision('放弃');
    await waitFor(() => expect(screen.getAllByRole('alert').some(node => node.textContent?.includes('Entry was replaced'))).toBe(true));
    if (stage === 'prepare') expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
    expect(dispose).not.toHaveBeenCalled(); expect(textOf()).toBe(draft);
    expect(screen.getByRole('tab', { name: /model.py/ }).textContent).toContain('●');
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('successful rename reopens all old anchors and the split at the new path, releasing only affected models', async () => {
    const { api } = await start();
    fireEvent.click(screen.getByRole('button', { name: /Another anchor/ }));
    await waitFor(() => expect(screen.getAllByRole('tab', { name: /model.py/ })).toHaveLength(2));
    await context('model.py', '侧边打开'); await waitFor(() => expect(textOf(sourceEditor(true))).toBe(draft));
    const affected = model('model.py'); const unrelated = model('unrelated.txt');
    await requestRename(); await decision('保存');
    await waitFor(() => expect(screen.getAllByRole('tab', { name: /renamed.py/ })).toHaveLength(2));
    expect(screen.queryByRole('tab', { name: /model.py/ })).toBeNull();
    await waitFor(() => expect(globalThis.document.querySelector('.side-editor .source-heading')?.textContent).toContain('renamed.py'));
    await waitFor(() => expect(textOf()).toBe(draft));
    expect(api.open_source_document.mock.calls.some(([, a]) => a.filePath === 'renamed.py')).toBe(true);
    expect(affected).toHaveBeenCalledOnce(); expect(unrelated).not.toHaveBeenCalled();
  });
  it.each(['rename', 'delete'] as const)('a folder %s guards dirty descendants while preserving an unrelated draft', async operation => {
    const { api } = await start(undefined, false);
    fireEvent.click(await item('unrelated.txt')); await waitFor(() => expect(textOf()).toBe(initialText)); edit('unrelated draft');
    fireEvent.click(await item('src')); fireEvent.click(await item('child.txt'));
    await waitFor(() => expect(textOf()).toBe(initialText)); edit('child draft');
    await context('child.txt', '侧边打开'); await waitFor(() => expect(textOf(sourceEditor(true))).toBe('child draft'));
    const childDispose = model('src/child.txt'); const unrelatedDispose = model('unrelated.txt');
    if (operation === 'rename') await requestRename('src', 'renamed'); else await requestDelete('src');
    noMutation(api); await decision('保存');
    await waitFor(() => expect(api.apply_project_entry_operation).toHaveBeenCalledOnce());
    expect(api.save_source_document.mock.calls.map(([request]) => request.filePath)).toEqual(['src/child.txt']);
    expect(childDispose).toHaveBeenCalledOnce(); expect(unrelatedDispose).not.toHaveBeenCalled();
    if (operation === 'delete') {
      expect(screen.queryByRole('tab', { name: /child.txt/ })).toBeNull();
      expect(globalThis.document.querySelector('.side-editor .source-view')).toBeNull();
    } else {
      await waitFor(() => expect(api.open_source_document.mock.calls.some(([, a]) => a.filePath === 'renamed/child.txt')).toBe(true));
      await waitFor(() => expect(globalThis.document.querySelector('.side-editor .source-heading')?.textContent).toContain('renamed/child.txt'));
    }
    fireEvent.click(screen.getByRole('tab', { name: /unrelated.txt/ }));
    await waitFor(() => expect(textOf()).toBe('unrelated draft'));
    expect(screen.getByRole('tab', { name: /unrelated.txt/ }).textContent).toContain('●');
  });
  it('successful Discard/delete removes every related view without saving', async () => {
    const { api } = await start(); await context('model.py', '侧边打开');
    await waitFor(() => expect(textOf(sourceEditor(true))).toBe(draft));
    const dispose = model('model.py'); await requestDelete(); await decision('放弃');
    await waitFor(() => expect(screen.queryByRole('tab', { name: /model.py/ })).toBeNull());
    expect(globalThis.document.querySelector('.side-editor .source-view')).toBeNull();
    expect(dispose).toHaveBeenCalledOnce(); expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('canceling the recycle-bin confirmation never prepares or discards a dirty source', async () => {
    const { api } = await start(); const dispose = model('model.py'); await context('model.py', '删除');
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button', { name: '移到回收站' })).toBeDefined();
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' })); await flush();
    noMutation(api); expect(textOf()).toBe(draft); expect(dispose).not.toHaveBeenCalled();
  });
  it('creating a file after a successful source save refreshes the parent revision before committing', async () => {
    const api = install();
    let directoryRevision = 'directory::1';
    let created = false;
    const saveSource = api.save_source_document.getMockImplementation()!;
    api.save_source_document.mockImplementation(async request => {
      const result = await saveSource(request);
      directoryRevision = 'directory::2';
      return result;
    });
    api.list_project_directory.mockImplementation(async request => ok({ projectPath: request.projectPath,
      projectEpoch: epoch, directory: request.directory, directoryRevision,
      entries: [entry('model.py'), ...(created ? [entry('notes.txt')] : [])], nextCursor: null }));
    api.create_project_entry.mockImplementation(async request => {
      // Retain the native optimistic-concurrency check: stale parents must fail.
      if (request.parentRevision !== directoryRevision) {
        return failure('FILE_CONFLICT', 'Parent directory changed after source save') as never;
      }
      created = true;
      return ok({ operation: 'create' as const, oldPath: null, newPath: 'notes.txt', affectedPaths: ['notes.txt'] });
    });
    await start(api);
    await item('model.py');
    expect(api.list_project_directory).toHaveBeenCalledWith({ projectPath, directory: '' });
    fireEvent.keyDown(window, { key: 's', ctrlKey: true });
    await waitFor(() => expect(api.save_source_document).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getByRole('tab', { name: /model.py/ }).textContent).not.toContain('●'));
    expect(textOf()).toBe(draft);
    fireEvent.click(screen.getByRole('button', { name: '新建文件' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByRole('textbox', { name: '名称' }), { target: { value: 'notes.txt' } });
    const listingCount = api.list_project_directory.mock.calls.length;
    fireEvent.click(within(dialog).getByRole('button', { name: '创建' }));
    await waitFor(() => expect(api.create_project_entry).toHaveBeenCalledWith({ projectPath, projectEpoch: epoch,
      parent: '', parentRevision: 'directory::2', name: 'notes.txt', kind: 'file' }));
    expect(api.create_project_entry).toHaveBeenCalledOnce();
    const freshIndex = api.list_project_directory.mock.calls.findIndex(([request], index) => index >= listingCount && request.directory === '');
    expect(freshIndex).toBeGreaterThanOrEqual(listingCount);
    expect(api.list_project_directory.mock.invocationCallOrder[freshIndex]).toBeLessThan(api.create_project_entry.mock.invocationCallOrder[0]);
    await item('notes.txt');
    expect(screen.queryAllByRole('alert').some(alert => alert.textContent?.includes('FILE_CONFLICT'))).toBe(false);
    fireEvent.click(screen.getByRole('tab', { name: /model.py/ }));
    await waitFor(() => expect(textOf()).toBe(draft));
    expect(api.analyze).not.toHaveBeenCalled();
  });
  it('reopening the same project bootstraps a new epoch before listing nested directories', async () => {
    const api = install(['Overview']);
    const reopenedSession = { ...session, recentProjects: [projectPath] };
    const opening = deferred<void>();
    let currentEpoch = epoch;
    api.get_session.mockResolvedValue(ok(reopenedSession));
    api.open_project.mockImplementation(async () => {
      // Keep the backend identity unchanged until the project transition completes.
      await opening.promise;
      currentEpoch = 'project-epoch-b';
      return ok(reopenedSession);
    });
    api.list_project_directory.mockImplementation(async request => {
      if (request.projectEpoch !== undefined && request.projectEpoch !== currentEpoch) {
        return failure('PROJECT_CHANGED', 'The previous project epoch is no longer valid') as never;
      }
      return ok({ projectPath: request.projectPath, projectEpoch: currentEpoch, directory: request.directory,
        directoryRevision: `directory:${request.directory}:${currentEpoch}`,
        entries: request.directory === ''
          ? [entry('src', 'directory'), entry(currentEpoch === epoch ? 'before-reopen.txt' : 'after-reopen.txt')]
          : [entry('src/fresh-child.txt')], nextCursor: null });
    });
    render(<App />);
    await item('before-reopen.txt');
    expect(api.list_project_directory).toHaveBeenCalledWith({ projectPath, directory: '' });
    fireEvent.click(screen.getByRole('button', { name: /project-a/ }));
    await waitFor(() => expect(api.open_project).toHaveBeenCalledWith(projectPath));
    await flush();
    const listingCount = api.list_project_directory.mock.calls.length;
    // Any pre-transition work sees epoch A; only the resolved reopen establishes B.
    await act(async () => { opening.resolve(); });
    await waitFor(() => expect(api.list_project_directory.mock.calls.slice(listingCount)
      .some(([request]) => request.projectPath === projectPath && request.directory === '' && request.projectEpoch === undefined)).toBe(true));
    await item('after-reopen.txt');
    expect(screen.queryByRole('treeitem', { name: 'before-reopen.txt' })).toBeNull();
    fireEvent.click(await item('src'));
    await item('fresh-child.txt');
    expect(api.list_project_directory).toHaveBeenLastCalledWith({ projectPath, projectEpoch: 'project-epoch-b', directory: 'src' });
    expect(screen.queryAllByRole('alert').some(alert => alert.textContent?.includes('PROJECT_CHANGED'))).toBe(false);
    expect(api.analyze).not.toHaveBeenCalled();
  });
  it('late old-project directory results cannot replace the new project tree', async () => {
    const api = install(); const listing = deferred<Awaited<ReturnType<Api['list_project_directory']>>>();
    api.list_project_directory.mockImplementationOnce(() => listing.promise);
    render(<App />); await waitFor(() => expect(api.list_project_directory).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole('button', { name: /project-b/ }));
    await waitFor(() => expect(api.open_project).toHaveBeenCalledOnce());
    await waitFor(() => expect(api.list_project_directory.mock.calls.some(([request]) => request.projectPath === 'E:\\project-b')).toBe(true));
    await act(async () => { listing.resolve(ok({ projectPath, projectEpoch: epoch, directory: '', directoryRevision: 'old', entries: [entry('ghost.txt')], nextCursor: null })); });
    expect(screen.queryByRole('treeitem', { name: 'ghost.txt' })).toBeNull();
    expect(screen.getByRole('tree', { name: '项目文件' })).toBeDefined();
  });
  it('late old-project source opening cannot revive a model or tab after switching projects', async () => {
    const { api } = await start(undefined, false); const opening = deferred<Awaited<ReturnType<Api['open_source_document']>>>();
    api.open_source_document.mockImplementationOnce(() => opening.promise); fireEvent.click(await item('unrelated.txt'));
    await waitFor(() => expect(api.open_source_document.mock.calls.some(([, a]) => a.filePath === 'unrelated.txt')).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: /project-b/ })); await waitFor(() => expect(api.open_project).toHaveBeenCalledOnce());
    await act(async () => { opening.resolve(ok(source('unrelated.txt', projectPath, 'obsolete text'))); });
    expect(screen.queryByRole('tab', { name: /unrelated.txt/ })).toBeNull();
    expect(screen.queryByDisplayValue('obsolete text')).toBeNull();
  });
});
