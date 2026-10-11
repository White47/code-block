import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { editor } from 'monaco-editor';
import type { Anchor, EditableSource, Envelope, ProjectDirectory, ProjectDirectoryRequest, ProjectEntry, SaveSourceRequest } from '../types';
import App from './App';
import { sourceTab } from './state';
import { anchor, graph, ok, overview, session, viewState } from './testFixtures';
import { sourceDocumentKey } from './sourceDocuments';
import { releaseSourceModel, retainSourceModel } from './sourceModels';

// Load actual Node builtins with local signatures: this project has no @types/node.
// NodeURL also avoids Vite rewriting browser `new URL(..., import.meta.url)` assets.
const { readFileSync } = await vi.importActual<{ readFileSync: (path: string, encoding: 'utf8') => string }>('node:fs');
const { fileURLToPath, URL: NodeURL } = await vi.importActual<{
  fileURLToPath: (url: string) => string;
  URL: new (input: string, base: string) => { href: string };
}>('node:url');
const productionStyles = readFileSync(fileURLToPath(new NodeURL('../styles.css', import.meta.url).href), 'utf8');

// Keep App, project tree, tabs, source adapter, store and dirty guard real.
// The graph canvas is unrelated to filesystem moves; SourceEditor uses its normal jsdom fallback.
vi.mock('./components/GraphEditor', () => ({ default: () => <div>Graph canvas</div> }));
const projectPath = session.projectPath!;
const epoch = 'move-epoch-a';
const initialText = 'value = 1';
const draft = 'value = 2';
// Local future bridge contract: no imports from a not-yet-created production module.
type MoveRequest = { projectPath: string; projectEpoch: string; path: string; revision: string;
  operation: 'move'; destinationDirectory: string; destinationRevision: string };
type PreparedMove = { operationToken: string; operation: 'move'; path: string; newPath: string; affectedPaths: string[] };
type MoveResult = { operation: 'move'; oldPath: string; newPath: string; affectedPaths: string[] };
type ApplyRequest = { projectPath: string; projectEpoch: string; operationToken: string };
function entry(path: string, kind: ProjectEntry['kind'] = 'file'): ProjectEntry {
  return { path, name: path.split('/').pop()!, kind, revision: `entry:${path}:1`, readOnlyReason: null,
    canOpen: kind === 'file', canCreate: kind === 'directory', canRename: true, canDelete: true };
}
function source(filePath: string, text = initialText, revision = `source:${filePath}:1`): EditableSource {
  return { projectPath, filePath, text, revision, encoding: 'utf-8', newline: 'LF', readOnlyReason: null };
}
function failure(code: string, message: string) {
  return { ok: false as const, schemaVersion: 1, error: { code, message } };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function install(options: { caseSensitive?: boolean; restoredPath?: string } = {}) {
  const restoredPath = options.restoredPath ?? 'model.py';
  const layout = { ...viewState, activeView: null, openTabs: ['Overview', sourceTab({ ...anchor, filePath: restoredPath }).id] };
  const directories = new Map<string, ProjectEntry[]>([
    ['', [entry('model.py'), entry('src', 'directory'), entry('target', 'directory'), entry('unrelated.txt'), entry('unloaded', 'directory')]],
    ['src', [entry('src/helper.py'), entry('src/nested', 'directory')]],
    ['src/nested', [entry('src/nested/leaf.txt')]], ['target', []], ['unloaded', [entry('unloaded/secret.txt')]],
  ]);
  const revisions = new Map([...directories.keys()].map(path => [path, `directory:${path}:1`]));
  const files = new Map([...directories.values()].flat().filter(e => e.kind === 'file').map(e => [e.path, source(e.path)]));
  const physicalPath = (path: string) => files.has(path) ? path : options.caseSensitive === false
    ? [...files.keys()].find(key => key.replace(/\\/g, '/').toLowerCase() === path.replace(/\\/g, '/').toLowerCase()) : undefined;
  if (!physicalPath(restoredPath)) files.set(restoredPath, source(restoredPath));
  const plans = new Map<string, PreparedMove>();
  let tokenNumber = 0;
  const api = {
    get_session: vi.fn().mockResolvedValue(ok({ ...session, ...(options.caseSensitive === undefined ? {} : { fileSystemCaseSensitive: options.caseSensitive }) })),
    get_preferences: vi.fn().mockResolvedValue(ok({ language: 'zh-CN', issue: null })),
    get_view_state: vi.fn().mockResolvedValue(ok(layout)), save_layout: vi.fn().mockResolvedValue(ok(layout)),
    get_overview: vi.fn().mockResolvedValue(ok(overview)), get_view: vi.fn().mockResolvedValue(ok(graph)),
    get_problems: vi.fn().mockResolvedValue(ok([{ code: 'anchor-two', message: 'Another anchor', filePath: restoredPath, line: 2, canOpenSource: true }])),
    read_source: vi.fn().mockResolvedValue(ok({ filePath: restoredPath, text: initialText, startLine: 1, endLine: 2 })),
    open_source_document: vi.fn(async (_path: string, location: Anchor): Promise<Envelope<EditableSource>> => {
      const found = files.get(physicalPath(location.filePath) ?? location.filePath);
      return found ? ok({ ...found, filePath: location.filePath, revision: found.revision.replace(found.filePath, location.filePath) }) : failure('SOURCE_NOT_FOUND', location.filePath);
    }),
    save_source_document: vi.fn(async (request: SaveSourceRequest): Promise<Envelope<EditableSource>> => {
      const saved = source(request.filePath, request.text, `source:${request.filePath}:2`);
      const path = physicalPath(request.filePath) ?? request.filePath;
      files.set(path, { ...saved, filePath: path });
      for (const children of directories.values()) for (const child of children) if (child.path === path) child.revision = `entry:${child.path}:2`;
      return ok({ ...saved });
    }),
    list_project_directory: vi.fn(async (request: ProjectDirectoryRequest): Promise<Envelope<ProjectDirectory>> => ok({
      projectPath: request.projectPath, projectEpoch: epoch, directory: request.directory,
      directoryRevision: revisions.get(request.directory) ?? `directory:${request.directory}:1`,
      entries: (directories.get(request.directory) ?? []).map(e => ({ ...e })), nextCursor: null,
    })),
    create_project_entry: vi.fn().mockResolvedValue(failure('UNEXPECTED_CREATE', 'Move must use prepare/apply')),
    prepare_project_entry_operation: vi.fn(async (request: MoveRequest): Promise<Envelope<PreparedMove>> => {
      const physical = [...directories.values()].flat().find(e => e.path === request.path);
      if (request.projectPath !== projectPath || request.projectEpoch !== epoch) return failure('PROJECT_CHANGED', 'Wrong project identity');
      if (request.operation !== 'move' || 'newName' in request) return failure('INVALID_OPERATION', 'Move has no newName');
      if (!physical || physical.revision !== request.revision) return failure('ENTRY_CONFLICT', 'Fresh physical source required');
      if (revisions.get(request.destinationDirectory) !== request.destinationRevision) return failure('ENTRY_CONFLICT', 'Fresh destination revision required');
      const destination = request.destinationDirectory;
      const newPath = [destination, physical.name].filter(Boolean).join('/');
      const destinationEntry = [...directories.values()].flat().find(e => e.path === destination);
      // A directory's readOnlyReason can prohibit moving the directory itself
      // (e.g. it contains .git) while canCreate still permits adding a sibling.
      if (physical.readOnlyReason || !physical.canRename || (destination && (destinationEntry?.kind !== 'directory' || !destinationEntry.canCreate))) return failure('ENTRY_READ_ONLY', 'Protected entry');
      if (destination === physical.path || destination.startsWith(`${physical.path}/`) || newPath === physical.path) return failure('INVALID_MOVE', 'Self or descendant');
      if ((directories.get(destination) ?? []).some(e => options.caseSensitive === false ? e.name.toLowerCase() === physical.name.toLowerCase() : e.name === physical.name)) return failure('ENTRY_CONFLICT', 'Destination collision');
      const plan: PreparedMove = { operationToken: `move-${++tokenNumber}`, operation: 'move', path: physical.path, newPath,
        affectedPaths: [physical.path, ...[...files.keys()].filter(path => path.startsWith(`${physical.path}/`))] };
      plans.set(plan.operationToken, plan);
      return ok({ ...plan, affectedPaths: [...plan.affectedPaths] });
    }),
    apply_project_entry_operation: vi.fn(async (request: ApplyRequest): Promise<Envelope<MoveResult>> => {
      const plan = plans.get(request.operationToken);
      if (!plan || request.projectPath !== projectPath || request.projectEpoch !== epoch) return failure('INVALID_TOKEN', 'Unknown or consumed token');
      plans.delete(request.operationToken);
      const remap = (path: string) => path === plan.path || path.startsWith(`${plan.path}/`) ? plan.newPath + path.slice(plan.path.length) : path;
      const parent = (path: string) => path.split('/').slice(0, -1).join('/');
      const movedEntry = [...directories.values()].flat().find(e => e.path === plan.path)!;
      const next = new Map<string, ProjectEntry[]>();
      for (const [directory, children] of directories) next.set(remap(directory), children.filter(e => e.path !== plan.path).map(e => ({ ...e, path: remap(e.path) })));
      const destination = parent(plan.newPath);
      next.set(destination, [...(next.get(destination) ?? []), { ...movedEntry, path: plan.newPath }]);
      directories.clear(); for (const [path, children] of next) directories.set(path, children);
      for (const [path, revision] of [...revisions]) if (remap(path) !== path) { revisions.delete(path); revisions.set(remap(path), revision); }
      for (const [path, document] of [...files]) if (remap(path) !== path) { files.delete(path); files.set(remap(path), { ...document, filePath: remap(path) }); }
      for (const path of [parent(plan.path), destination]) revisions.set(path, `directory:${path}:after-move`);
      return ok({ operation: 'move', oldPath: plan.path, newPath: plan.newPath, affectedPaths: [...plan.affectedPaths] });
    }),
    open_project: vi.fn().mockResolvedValue(ok({ ...session, projectPath: 'E:\\project-b' })),
    choose_project: vi.fn().mockResolvedValue(ok(session)), analyze: vi.fn().mockResolvedValue(ok(session)),
    get_cache_info: vi.fn().mockResolvedValue(ok({ dataDir: 'cache', analysisPath: 'cache/a.json', exists: true, status: 'Ready', outdatedReasons: [], cacheIssue: null })),
    get_analysis_settings: vi.fn().mockResolvedValue(ok({ ignorePaths: ['.venv'], issue: null })),
    set_analysis_settings: vi.fn().mockResolvedValue(ok({ ignorePaths: ['tests'], issue: null })),
    approve_close: vi.fn().mockResolvedValue(ok(null)), cancel_close: vi.fn().mockResolvedValue(ok(null)),
  };
  window.pywebview = { api } as unknown as NonNullable<Window['pywebview']>;
  return { api, directories, revisions, files };
}
type Backend = ReturnType<typeof install>;
type Api = Backend['api'];
const retained = new Set<string>();
const loadedStyles = new Set<HTMLStyleElement>();
function model(path: string) {
  const key = sourceDocumentKey(projectPath, path);
  const dispose = vi.fn();
  retainSourceModel(key, { dispose } as unknown as editor.ITextModel); retained.add(key);
  return dispose;
}
async function flush() { await act(async () => { await Promise.resolve(); }); }
function noMove(api: Api) {
  expect(api.prepare_project_entry_operation).not.toHaveBeenCalled();
  expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
  expect(api.create_project_entry).not.toHaveBeenCalled();
}
function noWrite(api: Api) { noMove(api); expect(api.save_source_document).not.toHaveBeenCalled(); }
function tabIds() {
  return within(screen.getByRole('tablist')).getAllByRole('button', { name: /^关闭 / }).map(e => e.getAttribute('aria-label'));
}
function activateSourceTab(location: Anchor) {
  const close = screen.getByRole('button', { name: `关闭 ${sourceTab(location).id}` });
  fireEvent.click(within(close.parentElement!).getByRole('tab'));
}
async function alertContaining(text: string) {
  await waitFor(() => expect(screen.queryAllByRole('alert').some(e => e.textContent?.includes(text))).toBe(true));
}
function sourceEditor(side = false) {
  const input = document.querySelector<HTMLTextAreaElement>(`${side ? '.side-editor' : '.primary-editor'} .source-view textarea`);
  expect(input, 'real SourceEditor fallback must be mounted').toBeTruthy();
  return input!;
}
async function start(backend = install(), dirty = false) {
  const mounted = render(<App />);
  await waitFor(() => expect(sourceEditor().value).toBe(initialText));
  if (dirty) fireEvent.change(sourceEditor(), { target: { value: draft } });
  await screen.findByRole('tree', { name: '项目文件' });
  return { ...backend, ...mounted };
}
async function item(path: string) {
  const tree = await screen.findByRole('tree', { name: '项目文件' });
  return waitFor(() => {
    const row = within(tree).getAllByRole('treeitem').find(e => e.getAttribute('data-path') === path);
    expect(row, `loaded tree row ${path || '(root)'}`).toBeDefined(); return row!;
  });
}
async function moveDialog(path = 'model.py') {
  fireEvent.contextMenu(await item(path));
  fireEvent.click(screen.getByRole('menuitem', { name: '移动到…' }));
  return screen.getByRole('dialog', { name: '移动到…' });
}
async function requestMenu(path = 'model.py', destination = 'target') {
  const dialog = await moveDialog(path);
  fireEvent.change(within(dialog).getByRole('textbox', { name: '目标目录' }), { target: { value: destination } });
  // Avoid prescribing the submit button's label beyond its accessible presence.
  const submit = within(dialog).getAllByRole('button').find(e => e.getAttribute('type') === 'submit');
  expect(submit, 'move dialog must have a submit button').toBeDefined(); fireEvent.click(submit!);
}
async function decision(name: '保存' | '放弃' | '取消') {
  fireEvent.click(within(await screen.findByRole('dialog', { name: '源码有未保存的修改' })).getByRole('button', { name }));
}
// DataTransfer is absent in jsdom. This models the browser boundary, not the tree.
function transfer(values: Record<string, string> = {}) {
  const data = new Map(Object.entries(values));
  let protectedMode = false;
  return { effectAllowed: 'uninitialized', dropEffect: 'none', files: [], items: [],
    get types() { return [...data.keys()]; },
    setData: (type: string, value: string) => { data.set(type, value); },
    getData: (type: string) => protectedMode ? '' : data.get(type) ?? '',
    setProtectedMode: (value: boolean) => { protectedMode = value; },
    clearData: (type?: string) => { if (type) data.delete(type); else data.clear(); },
  };
}
async function beginDrag(path = 'model.py') {
  const row = await item(path);
  expect(row.draggable, 'movable source must expose real drag controls').toBe(true);
  const dataTransfer = transfer(); fireEvent.dragStart(row, { dataTransfer });
  expect(dataTransfer.effectAllowed).toBe('move');
  const mime = dataTransfer.types.find(type => type !== 'text/plain' && type !== 'text/uri-list');
  expect(mime, 'drag must emit an internal structured token').toBeDefined();
  const token = JSON.parse(dataTransfer.getData(mime!)) as Record<string, unknown>;
  expect(token.path).toBe(path);
  expect(Object.values(token)).toContain(epoch);
  const nonce = Object.keys(token).find(key => /nonce/i.test(key));
  expect(nonce, 'token must include a per-mount nonce').toBeDefined();
  expect(typeof token[nonce!]).toBe('string'); expect(token[nonce!]).not.toBe('');
  return { row, dataTransfer, mime: mime!, token };
}
async function drop(dataTransfer: ReturnType<typeof transfer>, destination = 'target', allowed = true) {
  const target = await item(destination);
  const over = new Event('dragover', { bubbles: true, cancelable: true });
  Object.defineProperty(over, 'dataTransfer', { value: dataTransfer });
  // HTML drag data store is protected during dragover; payload authentication belongs to drop.
  dataTransfer.setProtectedMode(true);
  try { fireEvent(target, over); } finally { dataTransfer.setProtectedMode(false); }
  expect(over.defaultPrevented, 'dragover acceptance must match the displayed drop feedback').toBe(allowed);
  expect(dataTransfer.dropEffect).toBe(allowed ? 'move' : 'none');
  fireEvent.drop(target, { dataTransfer }); await flush();
}
async function requestDrop(path = 'model.py', destination = 'target') { await drop((await beginDrag(path)).dataTransfer, destination); }
async function requestMove(via: 'menu' | 'drop', path = 'model.py', destination = 'target') {
  if (via === 'menu') await requestMenu(path, destination); else await requestDrop(path, destination);
}
async function committed(api: Api, path = 'model.py', destination = 'target', revision = `entry:${path}:1`, destinationRevision = `directory:${destination}:1`) {
  await waitFor(() => expect(api.apply_project_entry_operation).toHaveBeenCalledOnce());
  expect(api.prepare_project_entry_operation).toHaveBeenCalledExactlyOnceWith({ projectPath, projectEpoch: epoch, path, revision,
    operation: 'move', destinationDirectory: destination, destinationRevision });
  const prepared = await api.prepare_project_entry_operation.mock.results[0].value;
  expect(prepared.ok).toBe(true);
  if (prepared.ok) expect(api.apply_project_entry_operation).toHaveBeenCalledExactlyOnceWith({ projectPath, projectEpoch: epoch, operationToken: prepared.data.operationToken });
  expect(api.create_project_entry).not.toHaveBeenCalled();
}
afterEach(() => {
  cleanup(); for (const key of retained) releaseSourceModel(key); retained.clear();
  for (const style of loadedStyles) style.remove(); loadedStyles.clear();
  delete window.pywebview; delete window.__codevizCloseReady;
});

describe('Explorer move through real App', () => {
  it('offers the accessible move dialog with a relative target directory textbox', async () => {
    await start();
    const dialog = await moveDialog();
    const input = within(dialog).getByRole('textbox', { name: '目标目录' });
    expect(input).toBe(document.activeElement);
  });

  it('offers already-loaded directory shortcuts without scanning unopened subtrees', async () => {
    const { api } = await start(); fireEvent.click(await item('src')); await item('src/helper.py');
    const before = api.list_project_directory.mock.calls.length;
    const dialog = await moveDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: 'src' }));
    expect((within(dialog).getByRole('textbox', { name: '目标目录' }) as HTMLInputElement).value).toBe('src');
    expect(api.list_project_directory.mock.calls.slice(before)).toHaveLength(0);
    expect(api.list_project_directory.mock.calls.some(([r]) => r.directory === 'unloaded' || r.directory === 'src/nested')).toBe(false);
    noWrite(api);
  });

  it.each(['menu', 'drop'] as const)('%s permits a canCreate destination containing protected .git siblings despite its directory rename readOnlyReason', async via => {
    const backend = install(); const { api, directories, files } = backend;
    const target = directories.get('')!.find(e => e.path === 'target')!;
    Object.assign(target, { canCreate: true, canRename: false, canDelete: false, readOnlyReason: 'Contains protected .git; directory rename is forbidden' });
    const protectedEntry = { ...entry('target/.git', 'directory'), canCreate: false, canRename: false, canDelete: false, readOnlyReason: 'Protected metadata' };
    directories.set('target', [protectedEntry]); directories.set('target/.git', []);
    await start(backend, true);
    if (via === 'menu') {
      const dialog = await moveDialog();
      const shortcut = within(dialog).getByRole('button', { name: 'target' }) as HTMLButtonElement;
      expect(shortcut.disabled, 'canCreate=true permits adding siblings beside protected metadata').toBe(false);
      fireEvent.click(shortcut);
      const submit = within(dialog).getAllByRole('button').find(e => e.getAttribute('type') === 'submit') as HTMLButtonElement;
      expect(submit.disabled, 'directory rename restrictions must not block destination capacity').toBe(false);
      fireEvent.click(submit);
    } else await requestDrop();
    noWrite(api); await decision('保存');
    await committed(api, 'model.py', 'target', 'entry:model.py:2');
    expect(directories.get('target')).toEqual(expect.arrayContaining([protectedEntry, expect.objectContaining({ path: 'target/model.py' })]));
    expect(files.get('target/model.py')?.text).toBe(draft);
    expect(api.list_project_directory.mock.calls.some(([r]) => r.directory === 'target/.git')).toBe(false);
    await waitFor(() => expect(document.querySelector('.primary-editor .source-heading')?.textContent).toContain('target/model.py'));
    await waitFor(() => expect(sourceEditor().value).toBe(draft));
  });

  it.each(['menu', 'drop'] as const)('%s moves a clean file through the same prepare/apply contract', async via => {
    const { api, directories, files } = await start();
    await requestMove(via); await committed(api);
    expect(directories.get('')!.some(e => e.path === 'model.py')).toBe(false);
    expect(directories.get('target')!.some(e => e.path === 'target/model.py')).toBe(true);
    expect(files.has('model.py')).toBe(false); expect(files.get('target/model.py')?.text).toBe(initialText);
    await waitFor(() => expect(document.querySelector('.primary-editor .source-heading')?.textContent).toContain('target/model.py'));
    expect(api.save_source_document).not.toHaveBeenCalled();
  });

  it.each(['menu', 'drop'] as const)('%s uses an empty destination for the project root', async via => {
    const { api } = await start(); fireEvent.click(await item('src')); await item('src/helper.py');
    await requestMove(via, 'src/helper.py', ''); await committed(api, 'src/helper.py', '');
    await item('helper.py');
    expect(api.prepare_project_entry_operation.mock.calls[0][0]).not.toHaveProperty('newName');
  });

  it('canceling the move dialog restores source focus without entering the dirty guard', async () => {
    const { api } = await start(install(), true); const row = await item('model.py'); row.focus();
    const dialog = await moveDialog(); fireEvent.click(within(dialog).getByRole('button', { name: '取消' })); await flush();
    expect(screen.queryByRole('dialog')).toBeNull(); expect(document.activeElement).toBe(row);
    expect(sourceEditor().value).toBe(draft); noWrite(api);
  });

  it.each(['menu', 'drop'] as const)('%s dirty Cancel preserves model, tab, split and draft before prepare', async via => {
    const { api } = await start(install(), true); await openSide('model.py');
    const dispose = model('model.py'); const ids = tabIds();
    await requestMove(via); noWrite(api); await decision('取消'); await flush();
    noWrite(api); expect(dispose).not.toHaveBeenCalled(); expect(tabIds()).toEqual(ids);
    expect(sourceEditor().value).toBe(draft); expect(sourceEditor(true).value).toBe(draft);
    expect(screen.getByRole('tab', { name: /model.py/ }).textContent).toContain('●');
  });

  it.each(['menu', 'drop'] as const)('%s waits for a deferred guard Save before preparing against fresh revisions', async via => {
    const backend = install(); const { api, revisions } = backend; await start(backend, true);
    const saving = deferred<void>(); const save = api.save_source_document.getMockImplementation()!;
    api.save_source_document.mockImplementation(async request => { await saving.promise; revisions.set('target', 'destination:post-save'); return save(request); });
    const dispose = model('model.py');
    try {
      await requestMove(via); noWrite(api); await decision('保存');
      await waitFor(() => expect(api.save_source_document).toHaveBeenCalledOnce()); noMove(api);
      expect(sourceEditor().value).toBe(draft); expect(dispose).not.toHaveBeenCalled();
    } finally { await act(async () => { saving.resolve(); }); }
    await committed(api, 'model.py', 'target', 'entry:model.py:2', 'destination:post-save');
    expect(api.save_source_document).toHaveBeenCalledExactlyOnceWith({ projectPath, filePath: 'model.py', text: draft, revision: 'source:model.py:1' });
    expect(api.save_source_document.mock.invocationCallOrder[0]).toBeLessThan(api.prepare_project_entry_operation.mock.invocationCallOrder[0]);
    const targetRead = api.list_project_directory.mock.calls.findIndex(([r], i) => r.directory === 'target' && api.list_project_directory.mock.invocationCallOrder[i] > api.save_source_document.mock.invocationCallOrder[0]);
    expect(targetRead).toBeGreaterThanOrEqual(0);
    expect(api.list_project_directory.mock.invocationCallOrder[targetRead]).toBeLessThan(api.prepare_project_entry_operation.mock.invocationCallOrder[0]);
    await waitFor(() => expect(sourceEditor().value).toBe(draft)); expect(dispose).toHaveBeenCalledOnce();
  });

  it.each(['menu', 'drop'] as const)('%s guard Save failure retains dirty document and model without preparing', async via => {
    const backend = install(); backend.api.save_source_document.mockResolvedValue(failure('SOURCE_CONFLICT', 'External bytes changed'));
    await start(backend, true); const dispose = model('model.py'); const ids = tabIds();
    await requestMove(via); await decision('保存'); await alertContaining('External bytes changed');
    noMove(backend.api); expect(sourceEditor().value).toBe(draft); expect(tabIds()).toEqual(ids); expect(dispose).not.toHaveBeenCalled();
    expect(screen.getByRole('tab', { name: /model.py/ }).textContent).toContain('●');
  });

  it.each(['prepare', 'apply'] as const)('Discard keeps the model and draft until deferred %s fails', async stage => {
    const backend = install(); const { api } = backend; await start(backend, true); await openSide('model.py');
    const pending = deferred<void>(); const dispose = model('model.py'); const ids = tabIds();
    if (stage === 'prepare') api.prepare_project_entry_operation.mockImplementation(async () => { await pending.promise; return failure('ENTRY_CONFLICT', 'Entry replaced'); });
    else api.apply_project_entry_operation.mockImplementation(async () => { await pending.promise; return failure('ENTRY_CONFLICT', 'Entry replaced'); });
    try {
      await requestMenu(); await decision('放弃');
      await waitFor(() => expect(stage === 'prepare' ? api.prepare_project_entry_operation : api.apply_project_entry_operation).toHaveBeenCalledOnce());
      expect(sourceEditor().value).toBe(draft); expect(sourceEditor(true).value).toBe(draft);
      expect(dispose).not.toHaveBeenCalled(); expect(tabIds()).toEqual(ids);
      expect(screen.getByRole('tab', { name: /model.py/ }).textContent).toContain('●');
    } finally { await act(async () => { pending.resolve(); }); }
    await alertContaining('Entry replaced');
    expect(sourceEditor().value).toBe(draft); expect(sourceEditor(true).value).toBe(draft);
    expect(dispose).not.toHaveBeenCalled(); expect(tabIds()).toEqual(ids); expect(api.save_source_document).not.toHaveBeenCalled();
    if (stage === 'prepare') expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
  });

  it.each([
    ['prepare', 'ENTRY_CONFLICT'], ['apply', 'ENTRY_CONFLICT'],
    ['prepare', 'FILE_OPERATION_FAILED'], ['apply', 'FILE_OPERATION_FAILED'],
  ] as const)('menu failure recovery: %s %s restores the original target input and inline error without retrying or discarding', async (stage, code) => {
    const backend = install(); const { api } = backend; await start(backend, true); await openSide('model.py');
    const ids = tabIds(); const dispose = model('model.py'); const pending = deferred<void>();
    const destination = 'target'; const message = `Move ${stage} failed; preserve the typed destination`;
    const hydrations = api.get_view_state.mock.calls.length;
    if (stage === 'prepare') api.prepare_project_entry_operation.mockImplementation(async () => { await pending.promise; return failure(code, message); });
    else api.apply_project_entry_operation.mockImplementation(async () => { await pending.promise; return failure(code, message); });
    try {
      await requestMenu('model.py', destination); await decision('放弃');
      await waitFor(() => expect(stage === 'prepare' ? api.prepare_project_entry_operation : api.apply_project_entry_operation).toHaveBeenCalledOnce());
      expect(sourceEditor().value).toBe(draft); expect(sourceEditor(true).value).toBe(draft); expect(dispose).not.toHaveBeenCalled();
    } finally { await act(async () => { pending.resolve(); }); }
    await alertContaining(message); await flush();
    expect(api.prepare_project_entry_operation).toHaveBeenCalledOnce();
    if (stage === 'prepare') expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
    else expect(api.apply_project_entry_operation).toHaveBeenCalledOnce();
    expect(api.save_source_document).not.toHaveBeenCalled(); expect(api.create_project_entry).not.toHaveBeenCalled();
    expect(tabIds()).toEqual(ids); expect(sourceEditor().value).toBe(draft); expect(sourceEditor(true).value).toBe(draft);
    expect(screen.getByRole('tab', { name: /model.py/ }).textContent).toContain('●'); expect(dispose).not.toHaveBeenCalled();
    if (code === 'FILE_OPERATION_FAILED') {
      const operationOrder = (stage === 'prepare' ? api.prepare_project_entry_operation : api.apply_project_entry_operation).mock.invocationCallOrder[0];
      const refreshedParents = api.list_project_directory.mock.calls.filter((_, index) =>
        api.list_project_directory.mock.invocationCallOrder[index] > operationOrder).map(([request]) => request.directory);
      expect(refreshedParents).toEqual(expect.arrayContaining(['', destination]));
      expect(api.get_view_state).toHaveBeenCalledTimes(hydrations); expect(api.analyze).not.toHaveBeenCalled();
    }
    const dialog = await screen.findByRole('dialog', { name: '移动到…' });
    expect((within(dialog).getByRole('textbox', { name: '目标目录' }) as HTMLInputElement).value).toBe(destination);
    const errors = within(dialog).getAllByRole('alert');
    expect(errors.some(node => node.textContent?.includes(code) && node.textContent.includes(message))).toBe(true);
    // Settling the recovered dialog must not re-submit its retained target.
    await flush(); expect(api.prepare_project_entry_operation).toHaveBeenCalledOnce();
    if (stage === 'apply') expect(api.apply_project_entry_operation).toHaveBeenCalledOnce();
    else expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
  });

  it.each([
    ['prepare', 'ENTRY_CONFLICT'], ['apply', 'ENTRY_CONFLICT'],
    ['prepare', 'FILE_OPERATION_FAILED'], ['apply', 'FILE_OPERATION_FAILED'],
  ] as const)('drop failure recovery: %s %s preserves draft and error without opening a move menu/dialog or retrying', async (stage, code) => {
    const backend = install(); const { api } = backend; await start(backend, true);
    const dispose = model('model.py'); const ids = tabIds(); const pending = deferred<void>();
    const message = `Drop ${stage} failed; do not invent a menu flow`;
    if (stage === 'prepare') api.prepare_project_entry_operation.mockImplementation(async () => { await pending.promise; return failure(code, message); });
    else api.apply_project_entry_operation.mockImplementation(async () => { await pending.promise; return failure(code, message); });
    try {
      await requestDrop(); await decision('放弃');
      await waitFor(() => expect(stage === 'prepare' ? api.prepare_project_entry_operation : api.apply_project_entry_operation).toHaveBeenCalledOnce());
      expect(sourceEditor().value).toBe(draft); expect(dispose).not.toHaveBeenCalled();
    } finally { await act(async () => { pending.resolve(); }); }
    await alertContaining(message); await flush();
    expect(screen.queryByRole('menu')).toBeNull(); expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('textbox', { name: '目标目录' })).toBeNull();
    expect(api.prepare_project_entry_operation).toHaveBeenCalledOnce();
    if (stage === 'prepare') expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
    else expect(api.apply_project_entry_operation).toHaveBeenCalledOnce();
    expect(api.save_source_document).not.toHaveBeenCalled(); expect(api.create_project_entry).not.toHaveBeenCalled();
    expect(sourceEditor().value).toBe(draft); expect(tabIds()).toEqual(ids); expect(dispose).not.toHaveBeenCalled();
    expect(screen.getByRole('tab', { name: /model.py/ }).textContent).toContain('●');
    if (code === 'FILE_OPERATION_FAILED') {
      const operationOrder = (stage === 'prepare' ? api.prepare_project_entry_operation : api.apply_project_entry_operation).mock.invocationCallOrder[0];
      expect(api.list_project_directory.mock.calls.filter((_, index) =>
        api.list_project_directory.mock.invocationCallOrder[index] > operationOrder).map(([request]) => request.directory))
        .toEqual(expect.arrayContaining(['', 'target']));
    }
  });

  it.each(['menu', 'drop'] as const)('%s Discard maps every actual source tab and split only after successful apply', async via => {
    const backend = install(); const { api } = backend; await start(backend, true);
    fireEvent.click(screen.getByRole('button', { name: /Another anchor/ }));
    await waitFor(() => expect(screen.getAllByRole('tab', { name: /model.py/ })).toHaveLength(2)); await openSide('model.py');
    const dispose = model('model.py'); const ids = tabIds(); const applying = deferred<void>();
    const apply = api.apply_project_entry_operation.getMockImplementation()!;
    api.apply_project_entry_operation.mockImplementation(async request => { await applying.promise; return apply(request); });
    try {
      await requestMove(via); await decision('放弃'); await waitFor(() => expect(api.apply_project_entry_operation).toHaveBeenCalledOnce());
      expect(sourceEditor().value).toBe(draft); expect(tabIds()).toEqual(ids); expect(dispose).not.toHaveBeenCalled();
    } finally { await act(async () => { applying.resolve(); }); }
    await waitFor(() => expect(document.querySelector('.side-editor .source-heading')?.textContent).toContain('target/model.py'));
    await waitFor(() => expect(document.querySelector('.primary-editor .source-heading')?.textContent).toContain('target/model.py'));
    const expectedIds = [20, 2].map(line => `关闭 ${sourceTab({ ...anchor, filePath: 'target/model.py', startLine: line, endLine: line === 20 ? 22 : 2 }).id}`);
    expect(tabIds()).toEqual(expect.arrayContaining(expectedIds)); expect(tabIds().some(id => id?.includes('%22model.py%22'))).toBe(false);
    expect(new Set(tabIds()).size).toBe(tabIds().length); expect(dispose).toHaveBeenCalledOnce();
    await waitFor(() => expect(sourceEditor().value).toBe(initialText)); expect(sourceEditor(true).value).toBe(initialText);
    expect(api.save_source_document).not.toHaveBeenCalled();
  });

  it('a directory move guards only dirty descendants and preserves an unrelated draft/model', async () => {
    const { api } = await start(); await editFile('unrelated.txt', 'unrelated draft'); const unrelated = model('unrelated.txt');
    fireEvent.click(await item('src')); await editFile('src/helper.py', 'child draft'); await openSide('src/helper.py');
    const child = model('src/helper.py'); await requestMenu('src');
    const guard = await screen.findByRole('dialog', { name: '源码有未保存的修改' });
    expect(guard.textContent).toContain('src/helper.py'); expect(guard.textContent).not.toContain('unrelated.txt');
    await decision('保存'); await committed(api, 'src');
    expect(api.save_source_document.mock.calls.map(([r]) => r.filePath)).toEqual(['src/helper.py']);
    await waitFor(() => expect(document.querySelector('.side-editor .source-heading')?.textContent).toContain('target/src/helper.py'));
    await waitFor(() => expect(sourceEditor().value).toBe('child draft')); expect(child).toHaveBeenCalledOnce(); expect(unrelated).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('tab', { name: /unrelated.txt/ }));
    await waitFor(() => expect(sourceEditor().value).toBe('unrelated draft'));
    expect(screen.getByRole('tab', { name: /unrelated.txt/ }).textContent).toContain('●');
  });

  it.each([false, true] as const)('restored case flag %s controls fresh physical src.path after an atomic case-changing Save', async caseSensitive => {
    const restoredPath = caseSensitive ? 'src/helper.py' : 'SRC/HELPER.PY';
    const backend = install({ caseSensitive, restoredPath }); const { api, directories, revisions, files } = backend;
    await start(backend, true); fireEvent.click(await item('src')); await item('src/helper.py');
    const pending = deferred<void>(); const dispose = model(restoredPath); const ids = tabIds();
    api.save_source_document.mockImplementation(async request => {
      await pending.promise;
      directories.set('src', [{ ...entry('src/HELPER.PY'), revision: 'physical:after-save' }, entry('src/nested', 'directory')]);
      revisions.set('target', 'destination:after-save');
      const saved = source(request.filePath, request.text, 'source:after-save');
      files.delete('src/helper.py'); files.set('src/HELPER.PY', { ...saved, filePath: 'src/HELPER.PY' });
      return ok(saved);
    });
    try {
      await requestMenu('src/helper.py'); await decision('保存');
      await waitFor(() => expect(api.save_source_document).toHaveBeenCalledOnce()); noMove(api);
      expect(sourceEditor().value).toBe(draft); expect(dispose).not.toHaveBeenCalled();
    } finally { await act(async () => { pending.resolve(); }); }
    expect(api.save_source_document).toHaveBeenCalledExactlyOnceWith({ projectPath, filePath: restoredPath, text: draft, revision: `source:${restoredPath}:1` });
    if (caseSensitive) {
      await alertContaining('ENTRY_CONFLICT'); noMove(api); expect(dispose).not.toHaveBeenCalled(); expect(tabIds()).toEqual(ids);
      expect(sourceEditor().value).toBe(draft);
    } else {
      await committed(api, 'src/HELPER.PY', 'target', 'physical:after-save', 'destination:after-save');
      await waitFor(() => expect(document.querySelector('.primary-editor .source-heading')?.textContent).toContain('target/HELPER.PY'));
      await waitFor(() => expect(sourceEditor().value).toBe(draft)); expect(dispose).toHaveBeenCalledOnce();
    }
  });

  it('an already-running source Save settles before drop prepares fresh source and target identities', async () => {
    const backend = install(); const { api, revisions } = backend; await start(backend, true);
    const pending = deferred<void>(); const save = api.save_source_document.getMockImplementation()!;
    api.save_source_document.mockImplementation(async request => { await pending.promise; revisions.set('target', 'destination:pending-save'); return save(request); });
    fireEvent.keyDown(window, { key: 's', ctrlKey: true });
    await waitFor(() => expect(api.save_source_document).toHaveBeenCalledOnce());
    try { await requestDrop(); await flush(); noMove(api); expect(sourceEditor().value).toBe(draft); }
    finally { await act(async () => { pending.resolve(); }); }
    await committed(api, 'model.py', 'target', 'entry:model.py:2', 'destination:pending-save');
    expect(api.save_source_document).toHaveBeenCalledOnce();
  });

  it.each(['menu', 'drop'] as const)('%s rejects an active destination identity even if its filesystem entry was externally deleted', async via => {
    const backend = install(); const { api, directories, files } = backend;
    directories.set('target', [entry('target/model.py')]); files.set('target/model.py', source('target/model.py'));
    await start(backend); fireEvent.click(await item('target')); await editFile('target/model.py', 'destination draft');
    const targetDispose = model('target/model.py');
    activateSourceTab(anchor); await waitFor(() => expect(sourceEditor().value).toBe(initialText));
    fireEvent.change(sourceEditor(), { target: { value: draft } }); const sourceDispose = model('model.py'); const ids = tabIds();
    directories.set('target', []); files.delete('target/model.py');
    fireEvent.contextMenu(await item('target'));
    fireEvent.click(screen.getByRole('menuitem', { name: '刷新' }));
    await waitFor(() => expect(screen.getAllByRole('treeitem').some(row => row.getAttribute('data-path') === 'target/model.py')).toBe(false));
    await requestMove(via); await flush();
    const discard = screen.queryByRole('button', { name: '放弃' }); if (discard) fireEvent.click(discard);
    await alertContaining('SOURCE_IDENTITY_CONFLICT');
    expect(api.apply_project_entry_operation).not.toHaveBeenCalled(); expect(api.save_source_document).not.toHaveBeenCalled();
    expect(tabIds()).toEqual(ids); expect(sourceDispose).not.toHaveBeenCalled(); expect(targetDispose).not.toHaveBeenCalled();
    expect(sourceEditor().value).toBe(draft);
    activateSourceTab({ filePath: 'target/model.py', startLine: 1, endLine: 1, startColumn: null, endColumn: null });
    await waitFor(() => expect(sourceEditor().value).toBe('destination draft'));
  });

  it.each(['', 'target'] as const)('a failed post-apply read of parent %s allows only read retry, never another prepare/apply', async failingParent => {
    const backend = install(); const { api } = backend; await start(backend); fireEvent.click(await item('target'));
    const list = api.list_project_directory.getMockImplementation()!; let failed = false;
    api.list_project_directory.mockImplementation(async request => {
      if (api.apply_project_entry_operation.mock.calls.length && request.directory === failingParent && !failed) {
        failed = true; return failure('LIST_FAILED', 'Committed move; listing failed');
      }
      return list(request);
    });
    await requestMenu(); await committed(api); await alertContaining('Committed move; listing failed');
    await waitFor(() => expect(document.querySelector('.primary-editor .source-heading')?.textContent).toContain('target/model.py'));
    expect(api.list_project_directory.mock.calls.filter(([r], i) => ['', 'target'].includes(r.directory) &&
      api.list_project_directory.mock.invocationCallOrder[i] > api.apply_project_entry_operation.mock.invocationCallOrder[0]).map(([r]) => r.directory)).toEqual(expect.arrayContaining(['', 'target']));
    const reads = api.list_project_directory.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await waitFor(() => expect(api.list_project_directory.mock.calls.length).toBeGreaterThan(reads)); await flush();
    expect(api.prepare_project_entry_operation).toHaveBeenCalledOnce(); expect(api.apply_project_entry_operation).toHaveBeenCalledOnce();
    expect(api.save_source_document).not.toHaveBeenCalled(); expect(api.analyze).not.toHaveBeenCalled();
  });

  it.each(['save', 'apply'] as const)('pending move %s locks tabs, source editing, project navigation and native close', async stage => {
    const backend = install(); const { api } = backend; await start(backend, true); await openSide('model.py');
    const pending = deferred<void>(); const ids = tabIds();
    if (stage === 'save') {
      const save = api.save_source_document.getMockImplementation()!;
      api.save_source_document.mockImplementation(async request => { await pending.promise; return save(request); });
    } else {
      const apply = api.apply_project_entry_operation.getMockImplementation()!;
      api.apply_project_entry_operation.mockImplementation(async request => { await pending.promise; return apply(request); });
    }
    try {
      await requestMenu(); await decision(stage === 'save' ? '保存' : '放弃');
      await waitFor(() => expect(stage === 'save' ? api.save_source_document : api.apply_project_entry_operation).toHaveBeenCalledOnce());
      expect(sourceEditor().readOnly).toBe(true); expect(sourceEditor(true).readOnly).toBe(true);
      fireEvent.change(sourceEditor(), { target: { value: 'must not replace draft' } });
      fireEvent.click(screen.getByRole('tab', { name: /项目概览/ }));
      fireEvent.keyDown(window, { key: 'w', ctrlKey: true });
      fireEvent.click(screen.getByRole('button', { name: /project-b/ }));
      fireEvent.click(screen.getByRole('button', { name: /打开项目/ }));
      fireEvent.click(screen.getByRole('button', { name: '重新分析' }));
      window.__codevizCloseReady = true;
      fireEvent(window, new CustomEvent('codeviz:close-request', { detail: { requestId: `move-${stage}-close` } }));
      await waitFor(() => expect(api.cancel_close).toHaveBeenCalledWith(`move-${stage}-close`));
      expect(api.approve_close).not.toHaveBeenCalled(); expect(api.open_project).not.toHaveBeenCalled();
      expect(api.choose_project).not.toHaveBeenCalled(); expect(api.analyze).not.toHaveBeenCalled();
      expect(sourceEditor().value).toBe(draft); expect(tabIds()).toEqual(ids);
      if (stage === 'save') noMove(api); else expect(api.apply_project_entry_operation).toHaveBeenCalledOnce();
    } finally { await act(async () => { pending.resolve(); }); }
    await waitFor(() => expect(api.apply_project_entry_operation).toHaveBeenCalledOnce());
    await waitFor(() => expect(sourceEditor().readOnly).toBe(false));
  });

  it('the open dirty guard locks source navigation and native close until Cancel without writes', async () => {
    const { api } = await start(install(), true); await requestMenu();
    await screen.findByRole('dialog', { name: '源码有未保存的修改' }); const ids = tabIds();
    expect(sourceEditor().readOnly).toBe(true);
    fireEvent.click(screen.getByRole('tab', { name: '项目概览' }));
    fireEvent.click(screen.getByRole('button', { name: /project-b/ }));
    fireEvent.keyDown(window, { key: 'w', ctrlKey: true });
    window.__codevizCloseReady = true;
    fireEvent(window, new CustomEvent('codeviz:close-request', { detail: { requestId: 'move-guard-close' } }));
    await waitFor(() => expect(api.cancel_close).toHaveBeenCalledWith('move-guard-close'));
    expect(api.approve_close).not.toHaveBeenCalled(); expect(api.open_project).not.toHaveBeenCalled();
    expect(tabIds()).toEqual(ids); expect(sourceEditor().value).toBe(draft); noWrite(api);
    await decision('取消'); await waitFor(() => expect(sourceEditor().readOnly).toBe(false)); noWrite(api);
  });

  it.each(['save', 'prepare', 'apply'] as const)('a rejected deferred %s promise preserves source/model and never retries a write', async stage => {
    const backend = install(); const { api } = backend; await start(backend, true);
    const pending = deferred<never>(); const ids = tabIds(); const dispose = model('model.py');
    if (stage === 'save') api.save_source_document.mockImplementation(() => pending.promise);
    else if (stage === 'prepare') api.prepare_project_entry_operation.mockImplementation(() => pending.promise);
    else api.apply_project_entry_operation.mockImplementation(() => pending.promise);
    try {
      await requestMenu(); await decision(stage === 'save' ? '保存' : '放弃');
      const operation = stage === 'save' ? api.save_source_document : stage === 'prepare' ? api.prepare_project_entry_operation : api.apply_project_entry_operation;
      await waitFor(() => expect(operation).toHaveBeenCalledOnce());
      expect(sourceEditor().value).toBe(draft); expect(tabIds()).toEqual(ids); expect(dispose).not.toHaveBeenCalled();
    } finally { await act(async () => { pending.reject(new Error('Native transport failed')); }); }
    await alertContaining('Native transport failed');
    expect(sourceEditor().value).toBe(draft); expect(tabIds()).toEqual(ids); expect(dispose).not.toHaveBeenCalled();
    if (stage === 'save') noMove(api);
    else if (stage === 'prepare') expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
    else expect(api.apply_project_entry_operation).toHaveBeenCalledOnce();
  });

  it.each(['removed', 'protected', 'read failure'] as const)('fresh destination %s after Discard blocks prepare without releasing drafts', async change => {
    const backend = install(); const { api, directories } = backend; await start(backend, true);
    const dispose = model('model.py'); const ids = tabIds(); await requestMenu();
    await screen.findByRole('dialog', { name: '源码有未保存的修改' });
    if (change === 'removed') { directories.set('', directories.get('')!.filter(e => e.path !== 'target')); directories.delete('target'); }
    else if (change === 'protected') { const target = directories.get('')!.find(e => e.path === 'target')!; target.canCreate = false; target.readOnlyReason = 'Destination protected after guard'; }
    else {
      const list = api.list_project_directory.getMockImplementation()!;
      api.list_project_directory.mockImplementation(request => request.directory === 'target'
        ? Promise.resolve(failure('LIST_FAILED', 'Destination unavailable')) : list(request));
    }
    await decision('放弃'); await alertContaining(change === 'removed' ? 'ENTRY_CONFLICT' : change === 'protected' ? 'ENTRY_READ_ONLY' : 'LIST_FAILED');
    noWrite(api); expect(sourceEditor().value).toBe(draft); expect(tabIds()).toEqual(ids); expect(dispose).not.toHaveBeenCalled();
    expect(screen.getByRole('tab', { name: /model.py/ }).textContent).toContain('●');
  });

  it('fresh Windows destination spelling and revision after Save are sent to prepare', async () => {
    const backend = install({ caseSensitive: false }); const { api, directories, revisions } = backend; await start(backend, true);
    const save = api.save_source_document.getMockImplementation()!;
    api.save_source_document.mockImplementation(async request => {
      const saved = await save(request);
      directories.set('', directories.get('')!.map(e => e.path === 'target' ? { ...e, path: 'TARGET', name: 'TARGET', revision: 'entry:TARGET:2' } : e));
      directories.delete('target'); directories.set('TARGET', []); revisions.delete('target'); revisions.set('TARGET', 'directory:TARGET:2');
      return saved;
    });
    await requestMenu(); await decision('保存'); await committed(api, 'model.py', 'TARGET', 'entry:model.py:2', 'directory:TARGET:2');
    await waitFor(() => expect(document.querySelector('.primary-editor .source-heading')?.textContent).toContain('TARGET/model.py'));
    await waitFor(() => expect(sourceEditor().value).toBe(draft));
  });

  it('dirty move to root uses the root directory revision refreshed after Save', async () => {
    const backend = install({ restoredPath: 'src/helper.py' }); const { api, revisions } = backend; await start(backend, true);
    fireEvent.click(await item('src')); await item('src/helper.py');
    const save = api.save_source_document.getMockImplementation()!;
    api.save_source_document.mockImplementation(async request => { const result = await save(request); revisions.set('', 'root:after-save'); return result; });
    await requestMenu('src/helper.py', ''); await decision('保存');
    await committed(api, 'src/helper.py', '', 'entry:src/helper.py:2', 'root:after-save');
    await waitFor(() => expect(document.querySelector('.primary-editor .source-heading')?.textContent).toContain('helper.py'));
    await waitFor(() => expect(sourceEditor().value).toBe(draft));
  });
});

describe('Explorer internal drag safety through real App', () => {
  it.each(['epoch', 'nonce', 'path', 'dragNonce'] as const)('drop rejects a field-wise mismatched %s token even during an active internal drag', async field => {
    const { api } = await start(install(), true); const drag = await beginDrag();
    const dispose = model('model.py'); const ids = tabIds();
    const token = { ...drag.token };
    const key = field === 'epoch' ? Object.keys(token).find(k => /epoch/i.test(k)) : field === 'nonce' ? Object.keys(token).find(k => /^nonce$/i.test(k)) : field;
    expect(key, `serialized token must carry ${field}`).toBeDefined();
    token[key!] = field === 'path' ? 'unrelated.txt' : `foreign-${String(token[key!])}`;
    // Same MIME + a valid active ref can offer hover feedback in protected mode.
    // Reading the payload on drop must still reject every mismatched field, before the guard.
    await drop(transfer({ [drag.mime]: JSON.stringify(token) }));
    noWrite(api); expect(screen.queryByRole('dialog')).toBeNull(); expect(sourceEditor().value).toBe(draft);
    expect(tabIds()).toEqual(ids); expect(dispose).not.toHaveBeenCalled();
  });

  it.each(['plain text', 'external files', 'structured external', 'malformed'] as const)('rejects %s without an internal activeDrag', async kind => {
    const { api } = await start(install(), true); const drag = await beginDrag();
    fireEvent.dragEnd(drag.row, { dataTransfer: drag.dataTransfer });
    const incoming = transfer(kind === 'plain text' ? { 'text/plain': 'model.py' }
      : kind === 'malformed' ? { [drag.mime]: '{not-json' } : { [drag.mime]: JSON.stringify(drag.token) });
    if (kind === 'external files') Object.defineProperty(incoming, 'files', { value: [new File(['foreign'], 'model.py')] });
    await drop(incoming, 'target', false); noWrite(api); expect(sourceEditor().value).toBe(draft);
  });

  it.each(['Escape', 'dragend'] as const)('%s clears activeDrag so a later drop cannot write', async cancellation => {
    const { api } = await start(install(), true); const drag = await beginDrag();
    if (cancellation === 'Escape') fireEvent.keyDown(window, { key: 'Escape' });
    else fireEvent.dragEnd(drag.row, { dataTransfer: drag.dataTransfer });
    await drop(drag.dataTransfer, 'target', false); noWrite(api); expect(sourceEditor().value).toBe(draft);
  });

  it('starting a different drag rejects the stale previous payload path', async () => {
    const { api } = await start(); const stale = await beginDrag(); await beginDrag('unrelated.txt');
    await drop(stale.dataTransfer); noWrite(api); expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('restarting a drag from the same path expires the old per-drag token', async () => {
    const { api } = await start(install(), true); const stale = await beginDrag(); const fresh = await beginDrag();
    expect(stale.token.dragNonce).not.toBe(fresh.token.dragNonce);
    await drop(stale.dataTransfer); noWrite(api); expect(screen.queryByRole('dialog')).toBeNull(); expect(sourceEditor().value).toBe(draft);
  });

  it.each(['text/plain', 'Files'] as const)('an external %s transfer is rejected even while an internal drag is active', async type => {
    const { api } = await start(install(), true); const drag = await beginDrag();
    const incoming = transfer(type === 'Files' ? { [drag.mime]: JSON.stringify(drag.token), Files: '' } : { 'text/plain': 'model.py' });
    await drop(incoming, 'target', false); noWrite(api); expect(screen.queryByRole('dialog')).toBeNull(); expect(sourceEditor().value).toBe(draft);
  });

  it.each(['', 'src/helper.py'] as const)('root source and file target %s cannot be used as movable directory targets', async path => {
    const { api } = await start();
    if (path === '') {
      const root = await item(''); expect(root.draggable).toBe(false); fireEvent.contextMenu(root);
      expect((screen.getByRole('menuitem', { name: '移动到…' }) as HTMLButtonElement).disabled).toBe(true);
    } else {
      fireEvent.click(await item('src')); await item(path); await drop((await beginDrag()).dataTransfer, path, false);
    }
    noWrite(api);
  });

  it('unmount/remount uses a new nonce and rejects a replay from the previous mount of the same project', async () => {
    const mounted = await start(); const stale = await beginDrag(); mounted.unmount();
    const { api } = await start(); const fresh = await beginDrag();
    const nonce = Object.keys(fresh.token).find(key => /nonce/i.test(key))!;
    expect(fresh.token[nonce]).not.toBe(stale.token[nonce]);
    await drop(stale.dataTransfer); noWrite(api); noWrite(mounted.api); expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('a consumed drop cannot replay while its dirty decision is pending or after Cancel', async () => {
    const { api } = await start(install(), true); const drag = await beginDrag();
    await drop(drag.dataTransfer); await screen.findByRole('dialog', { name: '源码有未保存的修改' });
    fireEvent.drop(await item('target'), { dataTransfer: drag.dataTransfer }); await flush(); noWrite(api);
    await decision('取消'); await drop(drag.dataTransfer, 'target', false); noWrite(api);
    expect(sourceEditor().value).toBe(draft);
  });

  it('a successfully consumed drop cannot replay its write token', async () => {
    const { api } = await start(); const drag = await beginDrag(); await drop(drag.dataTransfer); await committed(api);
    await waitFor(() => expect(sourceEditor().readOnly).toBe(false));
    await drop(drag.dataTransfer, 'target', false);
    expect(api.prepare_project_entry_operation).toHaveBeenCalledOnce(); expect(api.apply_project_entry_operation).toHaveBeenCalledOnce();
  });

  it.each(['src', 'src/nested'] as const)('rejects moving a directory onto self/descendant %s with dragover refusal', async target => {
    const { api } = await start(); fireEvent.click(await item('src')); await item('src/nested');
    await drop((await beginDrag('src')).dataTransfer, target, false); noWrite(api);
  });

  it.each(['source', 'destination'] as const)('rejects a read-only %s before any write', async protectedSide => {
    const backend = install(); const row = backend.directories.get('')!.find(e => e.path === (protectedSide === 'source' ? 'model.py' : 'target'))!;
    row.readOnlyReason = 'Protected entry';
    if (protectedSide === 'destination') row.canCreate = false;
    await start(backend);
    if (protectedSide === 'source') {
      expect((await item('model.py')).draggable).toBe(false);
      fireEvent.contextMenu(await item('model.py'));
      expect((screen.getByRole('menuitem', { name: '移动到…' }) as HTMLButtonElement).disabled).toBe(true);
      fireEvent.click(screen.getByRole('menuitem', { name: '移动到…' }));
    } else await drop((await beginDrag()).dataTransfer, 'target', false);
    noWrite(backend.api);
  });

  it('source canRename supplies the move hint independently of canDelete', async () => {
    const backend = install(); backend.directories.get('')!.find(e => e.path === 'model.py')!.canDelete = false;
    const { api } = await start(backend); await requestDrop(); await committed(api);
  });

  it.each(['source hint', 'destination hint', 'protected directory'] as const)('rejects disabled %s', async reason => {
    const backend = install(); const roots = backend.directories.get('')!;
    if (reason === 'source hint') roots.find(e => e.path === 'model.py')!.canRename = false;
    else if (reason === 'destination hint') roots.find(e => e.path === 'target')!.canCreate = false;
    else roots.push({ ...entry('.git', 'directory'), readOnlyReason: 'Protected metadata', canCreate: false, canRename: false, canDelete: false });
    await start(backend);
    if (reason === 'source hint') {
      expect((await item('model.py')).draggable).toBe(false); fireEvent.contextMenu(await item('model.py'));
      expect((screen.getByRole('menuitem', { name: '移动到…' }) as HTMLButtonElement).disabled).toBe(true);
    } else await drop((await beginDrag()).dataTransfer, reason === 'protected directory' ? '.git' : 'target', false);
    noWrite(backend.api);
  });

  it('a loaded destination collision refuses dragover and drop without dirty Save', async () => {
    const backend = install(); backend.directories.set('target', [entry('target/model.py')]); backend.files.set('target/model.py', source('target/model.py'));
    const { api } = await start(backend, true); fireEvent.click(await item('target')); await item('target/model.py');
    await drop((await beginDrag()).dataTransfer, 'target', false); noWrite(api); expect(sourceEditor().value).toBe(draft);
  });

  it('a collision created after guard Discard rejects prepare without losing the source draft', async () => {
    const backend = install(); const { api } = await start(backend, true); const dispose = model('model.py');
    await requestMenu(); await screen.findByRole('dialog', { name: '源码有未保存的修改' });
    backend.directories.set('target', [entry('target/model.py')]); backend.files.set('target/model.py', source('target/model.py'));
    await decision('放弃'); await alertContaining('ENTRY_CONFLICT');
    expect(api.apply_project_entry_operation).not.toHaveBeenCalled(); expect(api.save_source_document).not.toHaveBeenCalled();
    expect(sourceEditor().value).toBe(draft); expect(dispose).not.toHaveBeenCalled();
  });

  it.each(['../outside', '/absolute', 'C:\\outside'] as const)('menu target %s cannot escape the relative project namespace', async destination => {
    const { api } = await start(install(), true); await requestMenu('model.py', destination); await flush();
    noWrite(api); expect(sourceEditor().value).toBe(draft); expect(screen.getByRole('dialog', { name: '移动到…' })).toBeDefined();
  });
});

async function openSide(path: string) {
  fireEvent.contextMenu(await item(path)); fireEvent.click(screen.getByRole('menuitem', { name: '侧边打开' }));
  await waitFor(() => expect(sourceEditor(true).value).toBe(sourceEditor().value));
}
async function editFile(path: string, text: string) {
  fireEvent.click(await item(path)); await waitFor(() => expect(sourceEditor().value).toBe(initialText));
  fireEvent.change(sourceEditor(), { target: { value: text } });
}

// These are DOM and declared-CSS contracts only. jsdom does not lay out the tree;
// actual target coordinates/rect stability belong to the native browser regression.
function installProductionStyles() {
  const style = document.createElement('style'); style.textContent = productionStyles;
  document.head.append(style); loadedStyles.add(style);
}
function feedbackSlot(tree: HTMLElement) {
  const slot = tree.previousElementSibling as HTMLElement | null;
  expect(slot?.getAttribute('role'), 'a reserved status slot must be mounted immediately above the tree, including idle').toBe('status');
  expect(slot?.parentElement).toBe(tree.parentElement);
  return slot!;
}
function declaredSlotStyles(slot: HTMLElement) {
  const style = getComputedStyle(slot);
  return { height: style.height, lineHeight: style.lineHeight,
    marginTop: style.marginTop, marginRight: style.marginRight, marginBottom: style.marginBottom, marginLeft: style.marginLeft,
    display: style.display, whiteSpace: style.whiteSpace, overflow: style.overflow, textOverflow: style.textOverflow };
}
async function hoverOnly(dataTransfer: ReturnType<typeof transfer>, target: HTMLElement, allowed: boolean) {
  const over = new Event('dragover', { bubbles: true, cancelable: true });
  Object.defineProperty(over, 'dataTransfer', { value: dataTransfer }); dataTransfer.setProtectedMode(true);
  try { fireEvent(target, over); } finally { dataTransfer.setProtectedMode(false); }
  expect(over.defaultPrevented).toBe(allowed); expect(dataTransfer.dropEffect).toBe(allowed ? 'move' : 'none'); await flush();
}

describe('Explorer hover feedback fixed slot through real App', () => {
  it('mounts an empty idle status slot before dragging and reuses it without inserting siblings above the tree', async () => {
    installProductionStyles(); const { api } = await start();
    const tree = screen.getByRole('tree', { name: '项目文件' }); const slot = feedbackSlot(tree);
    expect(slot.isConnected).toBe(true); expect(slot.textContent?.trim()).toBe(''); expect(slot.title).toBe('');
    expect(getComputedStyle(slot).display).not.toBe('none'); expect(slot.hidden).toBe(false);
    const siblings = [...tree.parentElement!.childNodes]; const target = await item('target'); const drag = await beginDrag();
    await hoverOnly(drag.dataTransfer, target, true);
    expect(feedbackSlot(tree)).toBe(slot); expect([...tree.parentElement!.childNodes]).toEqual(siblings);
    expect(slot.textContent).toBe('放下以移动到此目录。'); expect(slot.title).toBe(slot.textContent);
    expect(await item('target')).toBe(target); noWrite(api);
  });

  it.each(['dragleave', 'Escape', 'dragend'] as const)('%s clears hover text/title while keeping the same mounted status node and tree slot', async cancellation => {
    installProductionStyles(); const { api } = await start(); const tree = screen.getByRole('tree', { name: '项目文件' });
    const target = await item('target'); const drag = await beginDrag(); await hoverOnly(drag.dataTransfer, target, true);
    const slot = feedbackSlot(tree); const siblings = [...tree.parentElement!.childNodes];
    expect(slot.textContent).toBe('放下以移动到此目录。');
    if (cancellation === 'dragleave') fireEvent.dragLeave(target, { relatedTarget: document.body, dataTransfer: drag.dataTransfer });
    else if (cancellation === 'Escape') fireEvent.keyDown(window, { key: 'Escape' });
    else fireEvent.dragEnd(drag.row, { dataTransfer: drag.dataTransfer });
    await flush(); expect(feedbackSlot(tree)).toBe(slot); expect(slot.isConnected).toBe(true);
    expect([...tree.parentElement!.childNodes]).toEqual(siblings); expect(await item('target')).toBe(target);
    expect(slot.textContent?.trim()).toBe(''); expect(slot.title).toBe('');
    expect(getComputedStyle(slot).display).not.toBe('none'); expect(slot.hidden).toBe(false); noWrite(api);
  });

  it('declares fixed idle/valid/long-denied CSS dimensions with nowrap ellipsis and a full hint title', async () => {
    installProductionStyles(); const backend = install();
    const longHint = 'Protected destination: ' + 'This directory cannot accept moved files; choose a writable relative directory. '.repeat(12);
    const denied = { ...entry('.venv', 'directory'), canCreate: false, canRename: false, canDelete: false, readOnlyReason: longHint };
    backend.directories.get('')!.push(denied); const { api } = await start(backend);
    const tree = screen.getByRole('tree', { name: '项目文件' }); const slot = feedbackSlot(tree);
    const idleStyles = declaredSlotStyles(slot);
    // Read the shipped stylesheet. Do not mock CSS or assert jsdom's zero rectangles.
    expect(idleStyles.height, 'reserve an explicit fixed height even with empty text').toMatch(/^\d+(?:\.\d+)?(?:px|rem|em)$/);
    expect(parseFloat(idleStyles.height)).toBeGreaterThan(0);
    expect(idleStyles.lineHeight).toMatch(/^\d+(?:\.\d+)?(?:px|rem|em)$/); expect(parseFloat(idleStyles.lineHeight)).toBeGreaterThan(0);
    for (const margin of [idleStyles.marginTop, idleStyles.marginRight, idleStyles.marginBottom, idleStyles.marginLeft]) expect(margin).toMatch(/^-?\d+(?:\.\d+)?(?:px|rem|em)$/);
    expect(idleStyles.whiteSpace).toBe('nowrap'); expect(idleStyles.overflow).toBe('hidden'); expect(idleStyles.textOverflow).toBe('ellipsis');
    expect(idleStyles.display).not.toBe('none');
    const siblings = [...tree.parentElement!.childNodes]; const drag = await beginDrag();
    await hoverOnly(drag.dataTransfer, await item('target'), true);
    expect(feedbackSlot(tree)).toBe(slot); expect(declaredSlotStyles(slot)).toEqual(idleStyles);
    const blocked = await item('.venv'); await hoverOnly(drag.dataTransfer, blocked, false);
    expect(feedbackSlot(tree)).toBe(slot); expect(slot.textContent).toBe(longHint); expect(slot.title).toBe(longHint);
    expect(declaredSlotStyles(slot)).toEqual(idleStyles); expect([...tree.parentElement!.childNodes]).toEqual(siblings);
    fireEvent.dragLeave(blocked, { relatedTarget: document.body, dataTransfer: drag.dataTransfer }); await flush();
    expect(feedbackSlot(tree)).toBe(slot); expect(slot.textContent?.trim()).toBe(''); expect(slot.title).toBe('');
    expect(declaredSlotStyles(slot)).toEqual(idleStyles); expect([...tree.parentElement!.childNodes]).toEqual(siblings); noWrite(api);
  });

  it('keeps the complete long destination warning in the move menu dialog independently of the reserved drag status', async () => {
    installProductionStyles(); const backend = install();
    const longHint = '目录受到保护，不能接收文件；请选择其他项目内相对目录。'.repeat(20);
    const target = backend.directories.get('')!.find(e => e.path === 'target')!;
    Object.assign(target, { canCreate: false, readOnlyReason: longHint }); const { api } = await start(backend);
    const dialog = await moveDialog();
    fireEvent.change(within(dialog).getByRole('textbox', { name: '目标目录' }), { target: { value: 'target' } });
    const warning = within(dialog).getAllByRole('status').find(node => node.textContent === longHint);
    expect(warning, 'menu must continue to expose the full warning text').toBeDefined();
    expect(getComputedStyle(warning!).display).not.toBe('none'); expect(warning!.hidden).toBe(false);
    const shortcut = within(dialog).getByRole('button', { name: 'target' }) as HTMLButtonElement;
    expect(shortcut.disabled).toBe(true); expect(shortcut.title).toBe(longHint); noWrite(api);
  });
});
