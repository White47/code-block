import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { anchor, graph, ok, overview, session, viewState } from '../testFixtures';
import { sourceTab } from '../state';
import { releaseSourceModel } from '../sourceModels';
import { sourceDocumentKey } from '../sourceDocuments';
import type { Anchor, SaveSourceRequest } from '../../types';

// All tests enter through the existing App. No import of a not-yet-created component.
vi.mock('./GraphEditor', () => ({ default: () => <div>Graph canvas</div> }));
const projectPath = session.projectPath!;
const projectEpoch = 'project-epoch-a';
type Entry = { path: string; name: string; kind: 'file' | 'directory' | 'link' | 'other'; revision: string;
  readOnlyReason: string | null; canOpen: boolean; canCreate: boolean; canRename: boolean; canDelete: boolean };
type ListingRequest = { projectPath: string; directory: string; projectEpoch?: string; cursor?: string | null };
function entry(path: string, kind: Entry['kind'] = 'file', readOnlyReason: string | null = null): Entry {
  return { path, name: path.split('/').pop()!, kind, revision: `entry:${path}:1`, readOnlyReason,
    canOpen: kind === 'file', canCreate: kind === 'directory' && !readOnlyReason, canRename: !readOnlyReason, canDelete: !readOnlyReason };
}
function listing(request: ListingRequest, entries: Entry[]) {
  return ok({ projectPath: request.projectPath, projectEpoch, directory: request.directory,
    directoryRevision: `directory:${request.directory}:1`, entries, nextCursor: null });
}
function install() {
  let root = [entry('model.py'), entry('src', 'directory'),
    ...['.git', '.venv', 'venv', 'node_modules'].map(name => entry(name, 'directory', 'Protected directory')),
    { ...entry('linked', 'link', 'Symbolic link'), canOpen: false, canCreate: false },
    { ...entry('image.bin'), canOpen: false, readOnlyReason: 'Binary file' }];
  const layout = { ...viewState, activeView: null, openTabs: ['Overview', sourceTab(anchor).id] };
  const document = { projectPath, filePath: 'model.py', text: 'value = 1', revision: 'source:model.py:1',
    encoding: 'utf-8', newline: 'LF' as const, readOnlyReason: null as string | null };
  const api = {
    get_session: vi.fn().mockResolvedValue(ok(session)),
    get_preferences: vi.fn().mockResolvedValue(ok({ language: 'zh-CN', issue: null })),
    get_view_state: vi.fn().mockResolvedValue(ok(layout)), get_overview: vi.fn().mockResolvedValue(ok(overview)),
    get_view: vi.fn().mockResolvedValue(ok(graph)), get_problems: vi.fn().mockResolvedValue(ok([])),
    save_layout: vi.fn().mockResolvedValue(ok(layout)), read_source: vi.fn().mockResolvedValue(ok({ filePath: 'model.py', text: 'value = 1', startLine: 1, endLine: 1 })),
    open_source_document: vi.fn(async (path: string, location: Anchor) => ok({ ...document, projectPath: path, filePath: location.filePath })),
    save_source_document: vi.fn(async (request: SaveSourceRequest) => ok({ ...document, ...request, revision: 'source:model.py:2' })),
    list_project_directory: vi.fn(async (request: ListingRequest) => listing(request, request.directory === '' ? root : [entry(`${request.directory}/child.txt`)])),
    create_project_entry: vi.fn(async (request: { projectPath: string; projectEpoch: string; parent: string; parentRevision: string; name: string; kind: 'file' | 'directory' }) => {
      const newPath = [request.parent, request.name].filter(Boolean).join('/');
      if (!request.parent) root = [...root, entry(newPath, request.kind)];
      return ok({ operation: 'create' as const, oldPath: null, newPath, affectedPaths: [newPath] });
    }),
    prepare_project_entry_operation: vi.fn(async (request: { projectPath: string; projectEpoch: string; path: string; revision: string; operation: 'rename' | 'delete'; newName?: string }) =>
      ok({ operationToken: 'token-1', operation: request.operation, path: request.path,
        newPath: request.operation === 'rename' ? request.newName! : null, affectedPaths: [request.path] })),
    apply_project_entry_operation: vi.fn(async (request: { projectPath: string; projectEpoch: string; operationToken: string }) => {
      void request;
      return ok({ operation: 'delete' as const, oldPath: 'model.py', newPath: null, affectedPaths: ['model.py'] });
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
async function start(api = install()) {
  render(<App />);
  const tree = await screen.findByRole('tree', { name: '项目文件' });
  await screen.findByRole('treeitem', { name: 'model.py' });
  return { api, tree };
}
async function row(name: string, path = name) {
  const result = await screen.findByRole('treeitem', { name });
  expect(result.getAttribute('data-path')).toBe(path); return result;
}
async function nameDialog(button: '新建文件' | '新建文件夹') {
  fireEvent.click(screen.getByRole('button', { name: button }));
  const dialog = await screen.findByRole('dialog');
  return { dialog, input: within(dialog).getByRole('textbox', { name: '名称' }),
    submit: within(dialog).getByRole('button', { name: '创建' }) };
}
async function flush() { await act(async () => { await Promise.resolve(); }); }
function disabled(element: HTMLElement) {
  return element.getAttribute('aria-disabled') === 'true' || (element instanceof HTMLButtonElement && element.disabled);
}
afterEach(() => {
  cleanup();
  for (const path of ['model.py', '.git/config']) releaseSourceModel(sourceDocumentKey(projectPath, path));
  delete window.pywebview;
});

describe('project Explorer user paths in real App', () => {
  it('shows an expanded root, lazy collapsed folders and all four root actions', async () => {
    const { api, tree } = await start();
    const root = within(tree).getByRole('treeitem', { name: 'project-a' });
    expect(root.getAttribute('aria-expanded')).toBe('true'); expect(root.getAttribute('data-path')).toBe('');
    for (const name of ['新建文件', '新建文件夹', '刷新', '全部折叠']) expect(screen.getByRole('button', { name })).toBeDefined();
    const src = await row('src'); expect(src.getAttribute('aria-expanded')).toBe('false');
    expect(api.list_project_directory.mock.calls.map(([request]) => request.directory)).toEqual(['']);
    fireEvent.click(src); await row('child.txt', 'src/child.txt');
    expect(api.list_project_directory).toHaveBeenCalledWith({ projectPath, projectEpoch, directory: 'src' });
    fireEvent.click(src); expect(screen.queryByRole('treeitem', { name: 'child.txt' })).toBeNull();
    expect(api.analyze).not.toHaveBeenCalled();
  });
  it('collapses all expanded folders and refreshes a listing without hydrating the workspace', async () => {
    const { api } = await start(); const hydrationCount = api.get_view_state.mock.calls.length;
    fireEvent.click(await row('src')); await row('child.txt', 'src/child.txt');
    fireEvent.click(screen.getByRole('button', { name: '全部折叠' }));
    expect(screen.queryByRole('treeitem', { name: 'child.txt' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await waitFor(() => expect(api.list_project_directory.mock.calls.filter(([request]) => request.directory === '').length).toBeGreaterThan(1));
    expect(api.get_view_state).toHaveBeenCalledTimes(hydrationCount); expect(api.analyze).not.toHaveBeenCalled();
  });
  it.each([['新建文件', 'file', 'notes.txt'], ['新建文件夹', 'directory', 'assets']] as const)(
    '%s sends the root epoch and directory revision, then displays the created entry', async (button, kind, name) => {
      const { api } = await start(); const { input, submit } = await nameDialog(button);
      fireEvent.change(input, { target: { value: name } }); fireEvent.click(submit);
      await waitFor(() => expect(api.create_project_entry).toHaveBeenCalledWith({ projectPath, projectEpoch,
        parent: '', parentRevision: 'directory::1', name, kind }));
      await row(name); expect(api.analyze).not.toHaveBeenCalled();
    },
  );
  it('rejects empty, traversal and separator names without sending create requests', async () => {
    const { api } = await start(); const { dialog, input, submit } = await nameDialog('新建文件');
    for (const name of ['', '.', '..', 'a/b', 'a\\b']) {
      fireEvent.change(input, { target: { value: name } }); fireEvent.click(submit); await flush();
      expect(screen.getByRole('dialog')).toBe(dialog); expect(api.create_project_entry).not.toHaveBeenCalled();
      expect(disabled(submit) || within(dialog).queryAllByRole('alert').length > 0 || input.getAttribute('aria-invalid') === 'true').toBe(true);
    }
  });
  it('root refresh invalidates cached children so reopening src lists externally replaced files', async () => {
    const api = install();
    const listDirectory = api.list_project_directory.getMockImplementation()!;
    let children = [entry('src/old.txt')];
    api.list_project_directory.mockImplementation(async request => request.directory === 'src'
      ? listing(request, children) : listDirectory(request));
    await start(api);
    fireEvent.click(await row('src')); await row('old.txt', 'src/old.txt');
    children = [entry('src/new.txt')];
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await waitFor(() => expect(api.list_project_directory.mock.calls.filter(([request]) => request.directory === '')).toHaveLength(2));
    await flush();
    const src = await row('src');
    if (src.getAttribute('aria-expanded') === 'true') fireEvent.click(src);
    expect(screen.queryByRole('treeitem', { name: 'old.txt' })).toBeNull();
    fireEvent.click(await row('src'));
    await waitFor(() => expect(api.list_project_directory.mock.calls.filter(([request]) => request.directory === 'src').length).toBeGreaterThan(1));
    await row('new.txt', 'src/new.txt');
    expect(screen.queryByRole('treeitem', { name: 'old.txt' })).toBeNull();
    expect(api.analyze).not.toHaveBeenCalled();
  });
  it('root refresh prevents a pending old child response from resurrecting src contents', async () => {
    const api = install();
    const listDirectory = api.list_project_directory.getMockImplementation()!;
    let resolve!: (response: Awaited<ReturnType<Api['list_project_directory']>>) => void;
    let childLoads = 0;
    api.list_project_directory.mockImplementation(async request => {
      if (request.directory !== 'src') return listDirectory(request);
      if (++childLoads === 1) return new Promise(done => { resolve = done; });
      return listing(request, [entry('src/new.txt')]);
    });
    await start(api);
    fireEvent.click(await row('src'));
    await waitFor(() => expect(childLoads).toBe(1));
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await waitFor(() => expect(api.list_project_directory.mock.calls.filter(([request]) => request.directory === '')).toHaveLength(2));
    await flush();
    await act(async () => { resolve(listing({ projectPath, directory: 'src', projectEpoch }, [entry('src/old.txt')])); });
    expect(screen.queryByRole('treeitem', { name: 'old.txt' })).toBeNull();
    const src = await row('src');
    if (src.getAttribute('aria-expanded') === 'true') fireEvent.click(src);
    fireEvent.click(await row('src'));
    await row('new.txt', 'src/new.txt');
    expect(screen.queryByRole('treeitem', { name: 'old.txt' })).toBeNull();
    expect(childLoads).toBeGreaterThan(1);
  });
  it('vendor containing .git allows creation while rename and delete remain disabled', async () => {
    const api = install();
    const listDirectory = api.list_project_directory.getMockImplementation()!;
    const vendor = { ...entry('vendor', 'directory', 'Contains protected directory: vendor/.git'), canCreate: true };
    api.list_project_directory.mockImplementation(async request => {
      if (request.directory === 'vendor') return listing(request, [entry('vendor/.git', 'directory', 'Protected directory')]);
      const response = await listDirectory(request);
      return request.directory === '' ? listing(request, [...response.data.entries, vendor]) : response;
    });
    await start(api);
    const target = await row('vendor'); fireEvent.click(target);
    await waitFor(() => expect(screen.getAllByRole('treeitem', { name: '.git' }).some(child => child.getAttribute('data-path') === 'vendor/.git')).toBe(true));
    fireEvent.contextMenu(target);
    const menu = await screen.findByRole('menu', { name: 'vendor' });
    for (const action of ['重命名', '删除']) {
      const command = within(menu).getByRole('menuitem', { name: action });
      expect(disabled(command)).toBe(true); fireEvent.click(command);
    }
    fireEvent.keyDown(menu, { key: 'Escape' });
    target.focus(); fireEvent.keyDown(target, { key: 'F2' }); fireEvent.keyDown(target, { key: 'Delete' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(api.prepare_project_entry_operation).not.toHaveBeenCalled();
    expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
    fireEvent.contextMenu(target);
    const createMenu = await screen.findByRole('menu', { name: 'vendor' });
    for (const action of ['新建文件', '新建文件夹']) {
      expect(disabled(within(createMenu).getByRole('menuitem', { name: action }))).toBe(false);
    }
  });
  it('preserves native create error code/message and leaves the name available for retry', async () => {
    const api = install(); api.create_project_entry.mockResolvedValue({ ok: false, schemaVersion: 1,
      error: { code: 'ENTRY_EXISTS', message: 'notes.txt already exists' } } as never);
    await start(api); const { input, submit } = await nameDialog('新建文件');
    fireEvent.change(input, { target: { value: 'notes.txt' } }); fireEvent.click(submit);
    await waitFor(() => expect(screen.getAllByRole('alert').some(alert => alert.textContent?.includes('ENTRY_EXISTS') && alert.textContent.includes('notes.txt already exists'))).toBe(true));
    expect((input as HTMLInputElement).value).toBe('notes.txt'); expect(screen.getByRole('dialog')).toBeDefined();
    expect(screen.queryByRole('treeitem', { name: 'notes.txt' })).toBeNull();
  });
  it('protected directories expose disabled mutations; links and binary entries cannot open an editor', async () => {
    const { api } = await start(); const opened = api.open_source_document.mock.calls.length;
    for (const name of ['.git', '.venv', 'venv', 'node_modules']) {
      const target = await row(name); target.focus(); fireEvent.contextMenu(target);
      const menu = await screen.findByRole('menu');
      for (const action of ['新建文件', '新建文件夹', '重命名', '删除']) {
        const command = within(menu).queryByRole('menuitem', { name: action });
        if (command) { expect(disabled(command)).toBe(true); fireEvent.click(command); }
      }
      fireEvent.keyDown(target, { key: 'F2' }); fireEvent.keyDown(target, { key: 'Delete' });
      expect(screen.queryByRole('dialog')).toBeNull(); fireEvent.keyDown(menu, { key: 'Escape' });
    }
    for (const name of ['linked', 'image.bin']) {
      const target = await row(name); fireEvent.click(target); fireEvent.doubleClick(target); fireEvent.keyDown(target, { key: 'Enter' });
    }
    await flush(); expect(api.open_source_document).toHaveBeenCalledTimes(opened);
    expect(api.create_project_entry).not.toHaveBeenCalled(); expect(api.prepare_project_entry_operation).not.toHaveBeenCalled();
    expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
    expect(screen.queryByRole('tab', { name: /linked|image.bin/ })).toBeNull();
    expect(api.list_project_directory.mock.calls.some(([request]) => request.directory === 'linked')).toBe(false);
  });
  it('protected safe text can open normally and to the side while mutations and binary opening remain disabled', async () => {
    const api = install();
    const listDirectory = api.list_project_directory.getMockImplementation()!;
    const openDocument = api.open_source_document.getMockImplementation()!;
    api.list_project_directory.mockImplementation(async request => request.directory === '.git'
      ? listing(request, [entry('.git/config', 'file', 'Protected directory')])
      : listDirectory(request));
    api.open_source_document.mockImplementation(async (path, location) => {
      const response = await openDocument(path, location);
      return location.filePath === '.git/config'
        ? ok({ ...response.data, text: 'safe protected text', readOnlyReason: 'Protected directory' })
        : response;
    });
    await start(api);
    fireEvent.click(await row('.git'));
    const safeFile = await row('config', '.git/config');
    expect(disabled(safeFile)).toBe(false);
    fireEvent.click(safeFile);
    const primary = globalThis.document.querySelector<HTMLElement>('.primary-editor')!;
    const primaryText = await within(primary).findByRole('textbox', { name: '.git/config' });
    expect((primaryText as HTMLTextAreaElement).value).toBe('safe protected text');
    expect(screen.getByRole('tab', { name: /config/ })).toBeDefined();
    expect(api.open_source_document.mock.calls.some(([path, location]) => path === projectPath && location.filePath === '.git/config')).toBe(true);
    fireEvent.contextMenu(safeFile);
    const menu = await screen.findByRole('menu');
    for (const action of ['重命名', '删除']) {
      const command = within(menu).getByRole('menuitem', { name: action });
      expect(disabled(command)).toBe(true); fireEvent.click(command);
    }
    expect(screen.queryByRole('dialog')).toBeNull();
    const openSide = within(menu).getByRole('menuitem', { name: '侧边打开' });
    expect(disabled(openSide)).toBe(false); fireEvent.click(openSide);
    await waitFor(() => {
      const side = globalThis.document.querySelector<HTMLElement>('.side-editor');
      expect(side).not.toBeNull();
      expect((within(side!).getByRole('textbox', { name: '.git/config' }) as HTMLTextAreaElement).value).toBe('safe protected text');
    });
    const opened = api.open_source_document.mock.calls.length;
    const binary = await row('image.bin');
    expect(disabled(binary)).toBe(true); fireEvent.click(binary);
    fireEvent.contextMenu(binary);
    const binaryMenu = await screen.findByRole('menu');
    const binarySide = within(binaryMenu).getByRole('menuitem', { name: '侧边打开' });
    expect(disabled(binarySide)).toBe(true); fireEvent.click(binarySide);
    await flush();
    expect(api.open_source_document).toHaveBeenCalledTimes(opened);
    expect(screen.queryByRole('tab', { name: /image.bin/ })).toBeNull();
    expect(api.prepare_project_entry_operation).not.toHaveBeenCalled();
    expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('F2 and Delete act on the focused tree row, while source editor keystrokes never trigger mutations', async () => {
    const { api } = await start();
    const input = await screen.findByRole('textbox', { name: 'model.py' }); input.focus();
    fireEvent.keyDown(input, { key: 'F2' }); fireEvent.keyDown(input, { key: 'Delete' });
    expect(screen.queryByRole('dialog')).toBeNull();
    const target = await row('model.py'); target.focus(); fireEvent.keyDown(target, { key: 'F2' });
    let dialog = await screen.findByRole('dialog');
    expect((within(dialog).getByRole('textbox', { name: '名称' }) as HTMLInputElement).value).toBe('model.py');
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }));
    target.focus(); fireEvent.keyDown(target, { key: 'Delete' }); dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button', { name: '移到回收站' })).toBeDefined();
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }));
    expect(api.prepare_project_entry_operation).not.toHaveBeenCalled(); expect(api.apply_project_entry_operation).not.toHaveBeenCalled();
  });
  it('a late previous refresh cannot replace a newer directory revision', async () => {
    const { api } = await start();
    let resolve!: (response: Awaited<ReturnType<Api['list_project_directory']>>) => void;
    api.list_project_directory.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await waitFor(() => expect(api.list_project_directory).toHaveBeenCalledTimes(2));
    api.list_project_directory.mockImplementation(async request => ({ ...listing(request, [entry('fresh.txt')]),
      data: { ...listing(request, [entry('fresh.txt')]).data, directoryRevision: 'directory::3' } }));
    fireEvent.click(screen.getByRole('button', { name: '刷新' })); await row('fresh.txt');
    await act(async () => { resolve(listing({ projectPath, directory: '' }, [entry('obsolete.txt')])); });
    expect(screen.queryByRole('treeitem', { name: 'obsolete.txt' })).toBeNull(); await row('fresh.txt');
  });
});
