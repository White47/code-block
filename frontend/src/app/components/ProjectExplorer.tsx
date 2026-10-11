import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { hasMethod } from '../../api/bridge';
import type { Anchor, ProjectEntry } from '../../types';
import { containsSourcePath, ExplorerState, fileAnchor, moveDestinationPath, parentPath, safeBridgeError, validDestinationDirectory, validEntryName } from '../explorerState';
import type { Translate } from '../i18n';

export type ExplorerMutation = { explorer: ExplorerState; entry: ProjectEntry } & ({ operation: 'rename'; newName: string } | { operation: 'move'; destinationDirectory: string } | { operation: 'delete' } | { operation: 'create'; name: string; kind: 'file' | 'directory' });
type Dialog = { entry: ProjectEntry; operation: 'create' | 'rename' | 'delete' | 'move'; kind?: 'file' | 'directory'; name: string; error?: string };
const dragMime = 'application/x-codeviz-project-entry';
type InternalDrag = { nonce: string; dragNonce: string; projectEpoch: string; projectPath: string; path: string };
export default function ProjectExplorer({ projectPath, locked, openSource, openSide, mutate, t, caseSensitive = true }: {
  projectPath: string; locked: boolean; openSource: (anchor: Anchor) => void; openSide: (anchor: Anchor) => void;
  mutate: (request: ExplorerMutation) => Promise<boolean>; t: Translate; caseSensitive?: boolean;
}) {
  const [explorer] = useState(() => new ExplorerState(projectPath));
  useSyncExternalStore(explorer.subscribe, explorer.snapshot);
  const [available, setAvailable] = useState<boolean | null>(null);
  const [capabilities, setCapabilities] = useState({ create: false, rename: false });
  const [selected, setSelected] = useState('');
  const [menu, setMenu] = useState<{ entry: ProjectEntry; x: number; y: number } | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const tree = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLFormElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const lastFocused = useRef<HTMLElement | null>(null);
  const focusAfterMutation = useRef<string | null>(null);
  const live = useRef(true);
  const [nonce] = useState(() => crypto.randomUUID());
  const activeDragRef = useRef<InternalDrag | null>(null);
  const [hover, setHover] = useState<{ path: string; allowed: boolean; hint: string } | null>(null);
  const pendingRef = useRef(false);
  const disabled = locked || pending;
  function clearDrag() { activeDragRef.current = null; setHover(null); }
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { activeDragRef.current = null; setHover(null); } };
    window.addEventListener('keydown', escape);
    return () => { activeDragRef.current = null; window.removeEventListener('keydown', escape); };
  }, []);
  useEffect(() => { if (locked) { activeDragRef.current = null; setHover(null); } }, [locked]);
  useEffect(() => {
    live.current = true;
    explorer.resume();
    void Promise.all([hasMethod('list_project_directory'), hasMethod('create_project_entry'), hasMethod('prepare_project_entry_operation'), hasMethod('apply_project_entry_operation')]).then(([list, create, prepare, apply]) => {
      if (!live.current) return;
      setAvailable(list); setCapabilities({ create, rename: prepare && apply });
      if (list) void explorer.load('').catch(() => {});
    }).catch(e => { if (live.current) setError(safeBridgeError(e)); });
    return () => { live.current = false; activeDragRef.current = null; explorer.dispose(); };
  }, [explorer]);
  useEffect(() => {
    if (dialog) (dialogRef.current?.querySelector<HTMLInputElement>('input') ?? dialogRef.current?.querySelector<HTMLButtonElement>('button[type="button"]'))?.focus();
    else if (menu) menuRef.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
    else lastFocused.current?.focus();
  }, [dialog, menu]);
  useEffect(() => {
    if (pending || focusAfterMutation.current === null) return;
    const path = focusAfterMutation.current; focusAfterMutation.current = null;
    const rows = Array.from(tree.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? []);
    const row = rows.find(row => row.dataset.path === path) ?? rows[0];
    row?.focus();
  }, [pending]);
  const root: ProjectEntry = { path: '', name: projectPath.split(/[\\/]/).filter(Boolean).pop() ?? projectPath, kind: 'directory', revision: explorer.directories.get('')?.listing?.directoryRevision ?? '',
    readOnlyReason: null, canOpen: false, canCreate: Boolean(explorer.epoch), canRename: false, canDelete: false };
  const selectedEntry = selected === '' ? root : [...explorer.directories.values()].flatMap(state => state.listing?.entries ?? []).find(entry => entry.path === selected);
  const createParentPath = selectedEntry?.kind === 'directory' ? selectedEntry.path : parentPath(selected);
  const createParent = createParentPath === '' ? root : [...explorer.directories.values()].flatMap(state => state.listing?.entries ?? []).find(entry => entry.path === createParentPath) ?? root;
  const can = (entry: ProjectEntry, action: 'create' | 'rename' | 'delete' | 'open' | 'move') => !disabled && (action === 'open' ? entry.canOpen && entry.kind === 'file' : action === 'create' ? entry.canCreate && capabilities.create : !entry.readOnlyReason && (action === 'rename' || action === 'move' ? Boolean(entry.path) && entry.canRename && capabilities.rename : entry.canDelete && capabilities.rename));
  const loadedEntries = [...explorer.directories.values()].flatMap(state => state.listing?.entries ?? []);
  function moveHint(source: ProjectEntry, directory: string, target?: ProjectEntry): string {
    if (!can(source, 'move')) return source.readOnlyReason ?? t('moveSourceBlocked');
    if (!validDestinationDirectory(directory)) return t('moveRelativeHint');
    if (target && (target.kind !== 'directory' || !target.canCreate)) return target.readOnlyReason ?? t('moveTargetBlocked');
    if (containsSourcePath(source.path, directory, caseSensitive)) return t('moveDescendantBlocked');
    const destination = moveDestinationPath(source.path, directory);
    if (containsSourcePath(source.path, destination, caseSensitive) && containsSourcePath(destination, source.path, caseSensitive)) return t('moveSameBlocked');
    if (loadedEntries.some(entry => containsSourcePath(destination, entry.path, caseSensitive) && containsSourcePath(entry.path, destination, caseSensitive))) return t('moveCollisionBlocked');
    return '';
  }
  const dialogInvalid = dialog ? dialog.operation === 'move' ? Boolean(moveHint(dialog.entry, dialog.name, dialog.name === '' ? root : loadedEntries.find(entry => entry.path === dialog.name))) : dialog.operation !== 'delete' && !validEntryName(dialog.name) : false;
  function startDialog(entry: ProjectEntry, operation: Dialog['operation'], kind?: Dialog['kind']) {
    setMenu(null);
    if (!can(entry, operation)) return;
    setDialog({ entry, operation, kind, name: operation === 'rename' ? entry.name : '' });
  }
  function activate(entry: ProjectEntry) {
    if (disabled) return;
    setSelected(entry.path);
    if (entry.kind === 'directory') explorer.toggle(entry.path);
    else if (entry.canOpen && entry.kind === 'file') openSource(fileAnchor(entry.path));
  }
  async function submit() {
    if (!dialog || disabled || dialogInvalid) return;
    if (dialog.operation === 'move') { const request = dialog; setDialog(null); await submitMove(request.entry, request.name, request); return; }
    const request = dialog;
    let committed = false;
    setPending(true); setDialog(null); setError('');
    try {
      if (request.operation === 'create') {
        await mutateCreate();
      } else {
        const done = await mutate(request.operation === 'rename' ? { explorer, entry: request.entry, operation: 'rename', newName: request.name } : { explorer, entry: request.entry, operation: 'delete' });
        if (!done) return;
        committed = true;
        focusAfterMutation.current = request.operation === 'rename' ? [parentPath(request.entry.path), request.name].filter(Boolean).join('/') : parentPath(request.entry.path);
        explorer.invalidate(request.entry.path);
        await explorer.load(parentPath(request.entry.path));
      }
    } catch (e) { if (live.current) { if (committed) setError(safeBridgeError(e)); else setDialog({ ...request, error: safeBridgeError(e) }); } }
    finally { if (live.current) setPending(false); }
    async function mutateCreate() {
      // App owns the project queue and guards; create also enters that queue.
      const done = await mutate({ explorer, entry: request.entry, operation: 'create', name: request.name, kind: request.kind! });
      if (!done) return;
      committed = true;
      focusAfterMutation.current = [request.entry.path, request.name].filter(Boolean).join('/');
      if (live.current) {
        explorer.expanded.add(request.entry.path);
        await explorer.load(request.entry.path);
      }
    }
  }
  async function submitMove(entry: ProjectEntry, destinationDirectory: string, menuRequest?: Dialog) {
    if (disabled || pendingRef.current || moveHint(entry, destinationDirectory)) return;
    pendingRef.current = true; setPending(true); setError('');
    try {
      if (!await mutate({ explorer, entry, operation: 'move', destinationDirectory })) return;
      focusAfterMutation.current = moveDestinationPath(entry.path, destinationDirectory);
      setSelected(focusAfterMutation.current);
    } catch (error) {
      if (live.current) {
        if (menuRequest) setDialog({ ...menuRequest, error: safeBridgeError(error) });
        else setError(safeBridgeError(error));
      }
    }
    finally { pendingRef.current = false; if (live.current) setPending(false); }
  }
  function dragSource(data: DataTransfer | null) {
    const record = activeDragRef.current;
    if (!data || disabled || pendingRef.current || !record || record.nonce !== nonce || record.projectPath !== projectPath || record.projectEpoch !== explorer.epoch || !Array.from(data.types).includes(dragMime) || Array.from(data.types).includes('Files') || data.files?.length) return null;
    // Some hosts expose data during hover. Refuse visible mismatches, while
    // allowing an empty protected-mode payload until drop authenticates it.
    try { const value = data.getData(dragMime); if (value && !matchesDrag(value, record)) return null; } catch { return null; }
    const entry = loadedEntries.find(entry => entry.path === record.path);
    return entry && can(entry, 'move') ? entry : null;
  }
  function matchesDrag(value: string, record: InternalDrag) {
    try {
      const payload = JSON.parse(value) as InternalDrag | null;
      return Boolean(payload && payload.nonce === record.nonce && payload.dragNonce === record.dragNonce && payload.projectEpoch === record.projectEpoch && payload.projectPath === record.projectPath && payload.path === record.path);
    } catch { return false; }
  }
  function focusRow(path: string) {
    const row = Array.from(tree.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? []).find(row => row.dataset.path === path);
    if (row) { setSelected(path); row.focus(); }
  }
  function renderEntry(entry: ProjectEntry, depth: number) {
    const expanded = entry.kind === 'directory' && explorer.expanded.has(entry.path);
    return <div role="none" key={entry.path}><div role="treeitem" aria-label={entry.name} data-path={entry.path}
      aria-level={depth + 1} aria-selected={selected === entry.path} aria-expanded={entry.kind === 'directory' ? expanded : undefined}
      aria-disabled={disabled || (entry.kind !== 'directory' && !entry.canOpen)} tabIndex={selected === entry.path ? 0 : -1}
      className={`explorer-row ${selected === entry.path ? 'selected' : ''} ${hover?.path === entry.path ? hover.allowed ? 'drop-valid' : 'drop-disabled' : ''}`} style={{ paddingLeft: 10 + depth * 14 }} title={hover?.path === entry.path ? hover.hint : entry.readOnlyReason ?? entry.path}
      draggable={can(entry, 'move')}
      onDragStart={e => {
        clearDrag();
        if (!can(entry, 'move') || !e.dataTransfer || !explorer.epoch) { e.preventDefault(); return; }
        const record: InternalDrag = { nonce, dragNonce: crypto.randomUUID(), projectEpoch: explorer.epoch, projectPath, path: entry.path };
        activeDragRef.current = record;
        e.dataTransfer.clearData(); e.dataTransfer.setData(dragMime, JSON.stringify(record)); e.dataTransfer.effectAllowed = 'move';
      }}
      onDragEnd={clearDrag}
      onDragOver={e => {
        const source = dragSource(e.dataTransfer);
        if (!source) { if (e.dataTransfer) e.dataTransfer.dropEffect = 'none'; setHover(null); return; }
        // Empty protected-mode data is allowed only with a live internal
        // record and MIME; drop must authenticate the full payload.
        const hint = moveHint(source, entry.path, entry);
        setHover({ path: entry.path, allowed: !hint, hint: hint || t('moveDropHint') });
        e.dataTransfer.dropEffect = hint ? 'none' : 'move';
        if (!hint) e.preventDefault();
        e.stopPropagation();
      }}
      onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setHover(null); }}
      onDrop={e => {
        e.preventDefault(); e.stopPropagation();
        const source = dragSource(e.dataTransfer); const record = activeDragRef.current;
        let matches = false;
        try { matches = Boolean(record && matchesDrag(e.dataTransfer.getData(dragMime), record)); } catch { /* Foreign or expired payload. */ }
        clearDrag();
        if (!matches || !source || moveHint(source, entry.path, entry)) return;
        lastFocused.current = e.currentTarget;
        void submitMove(source, entry.path);
      }}
      onFocus={e => { setSelected(entry.path); lastFocused.current = e.currentTarget; }} onClick={() => activate(entry)}
      onContextMenu={e => { e.preventDefault(); if (disabled) return; e.currentTarget.focus(); setMenu({ entry, x: e.clientX, y: e.clientY }); }}
      onKeyDown={e => {
        if (disabled) return;
        const rows = Array.from(tree.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? []);
        const index = rows.indexOf(e.currentTarget);
        if (['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'Enter', 'F2', 'Delete'].includes(e.key)) { e.preventDefault(); e.stopPropagation(); }
        if (e.key === 'ArrowDown') rows[Math.min(index + 1, rows.length - 1)]?.focus();
        if (e.key === 'ArrowUp') rows[Math.max(0, index - 1)]?.focus();
        if (e.key === 'Home') rows[0]?.focus();
        if (e.key === 'End') rows.at(-1)?.focus();
        if (e.key === 'ArrowRight' && entry.kind === 'directory') { if (!expanded) explorer.toggle(entry.path); else rows[index + 1]?.focus(); }
        if (e.key === 'ArrowLeft') { if (expanded) explorer.toggle(entry.path); else focusRow(parentPath(entry.path)); }
        if (e.key === 'Enter') activate(entry);
        if (e.key === 'F2') startDialog(entry, 'rename');
        if (e.key === 'Delete') startDialog(entry, 'delete');
      }}><span aria-hidden="true" className="explorer-chevron">{entry.kind === 'directory' ? expanded ? '⌄' : '›' : '·'}</span><span className="explorer-name">{entry.name}</span>{entry.readOnlyReason && <span className="explorer-readonly" aria-hidden="true">◇</span>}</div>
      {expanded && <div role="group">{renderDirectory(entry.path, depth + 1)}</div>}</div>;
  }
  function renderDirectory(path: string, depth: number) {
    const state = explorer.directories.get(path);
    return <>{state?.listing?.entries.map(entry => renderEntry(entry, depth))}{state?.loading && <p className="explorer-status" role="status">{t('explorerLoading')}</p>}
      {state?.error && <div className="explorer-status" role="alert">{state.error}<button disabled={disabled} onClick={() => { void explorer.load(path).catch(() => {}); }}>{t('explorerRetry')}</button></div>}
      {state?.listing && !state.loading && !state.error && state.listing.entries.length === 0 && <p className="explorer-status" role="status">{t('explorerEmpty')}</p>}
      {state?.listing?.nextCursor && <button className="explorer-more" disabled={disabled || state.loading} onClick={() => { void explorer.load(path, true).catch(() => {}); }}>{t('explorerMore')}</button>}</>;
  }
  if (available === false) return <p className="explorer-status" role="status">{t('explorerUnavailable')}</p>;
  return <section className="project-explorer"><div className="explorer-toolbar" aria-label={t('projectFiles')}>
    <button aria-label={t('newFile')} title={t('newFile')} disabled={!can(createParent, 'create')} onClick={() => startDialog(createParent, 'create', 'file')}>＋▤</button>
    <button aria-label={t('newFolder')} title={t('newFolder')} disabled={!can(createParent, 'create')} onClick={() => startDialog(createParent, 'create', 'directory')}>＋□</button>
    <button aria-label={t('explorerRefresh')} title={t('explorerRefresh')} disabled={disabled || !available} onClick={() => { void explorer.refresh().catch(() => {}); }}>↻</button>
    <button aria-label={t('collapseAll')} title={t('collapseAll')} disabled={disabled} onClick={() => explorer.collapse()}>⇤</button></div>
    {error && <p role="alert" className="explorer-status">{error}</p>}<p role="status" className={`explorer-drag-feedback ${hover ? hover.allowed ? 'move-feedback-valid' : 'move-feedback-disabled' : ''}`} title={hover?.hint ?? ''}>{hover?.hint ?? ''}</p><div ref={tree} role="tree" aria-label={t('projectFiles')} aria-busy={pending}>{renderEntry(root, 0)}</div>
    {menu && <><div className="explorer-menu-dismiss" onClick={() => setMenu(null)}/><div ref={menuRef} role="menu" aria-label={menu.entry.name} className="explorer-menu" style={{ left: Math.max(0, Math.min(menu.x, window.innerWidth - 210)), top: Math.max(0, Math.min(menu.y, window.innerHeight - 300)) }}
      onKeyDown={e => { if (e.key === 'Escape') { e.preventDefault(); setMenu(null); } if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) { e.preventDefault(); const buttons = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')); const index = buttons.indexOf(document.activeElement as HTMLButtonElement); buttons[e.key === 'Home' ? 0 : e.key === 'End' ? buttons.length - 1 : (index + (e.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length]?.focus(); } }}>
      {([['newFile', 'create', 'file'], ['newFolder', 'create', 'directory'], ['renameEntry', 'rename', undefined], ['deleteEntry', 'delete', undefined]] as const).map(([label, operation, kind]) => <button role="menuitem" key={label} disabled={!can(menu.entry, operation)} title={menu.entry.readOnlyReason ?? undefined} onClick={() => startDialog(menu.entry, operation, kind)}>{t(label)}</button>)}
      <button role="menuitem" disabled={!can(menu.entry, 'open')} onClick={() => { openSide(fileAnchor(menu.entry.path)); setMenu(null); }}>{t('explorerSide')}</button>
      <button role="menuitem" disabled={!can(menu.entry, 'move')} title={menu.entry.readOnlyReason ?? (!menu.entry.path ? t('explorerRootProtected') : undefined)} onClick={() => startDialog(menu.entry, 'move')}>{t('moveEntry')}</button>
      <button role="menuitem" disabled={disabled} onClick={() => { void explorer.refresh(menu.entry.kind === 'directory' ? menu.entry.path : parentPath(menu.entry.path)).catch(() => {}); setMenu(null); }}>{t('explorerRefresh')}</button>
      <button role="menuitem" disabled={disabled} onClick={() => { explorer.collapse(); setMenu(null); }}>{t('collapseAll')}</button>
      {menu.entry.readOnlyReason && <p role="status">{menu.entry.readOnlyReason}</p>}{!menu.entry.path && <p role="status">{t('explorerRootProtected')}</p>}</div></>}
    {dialog && <div className="source-dialog-backdrop"><form ref={dialogRef} className="source-dialog" role="dialog" aria-modal="true" aria-labelledby="explorer-dialog-title" onSubmit={e => { e.preventDefault(); void submit(); }} onKeyDown={e => { if (e.key === 'Escape') setDialog(null); if (e.key === 'Tab') { const controls = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('input,button:not(:disabled)')); if (e.shiftKey && document.activeElement === controls[0]) { e.preventDefault(); controls.at(-1)?.focus(); } else if (!e.shiftKey && document.activeElement === controls.at(-1)) { e.preventDefault(); controls[0]?.focus(); } } }}>
      <h2 id="explorer-dialog-title">{t(dialog.operation === 'move' ? 'moveEntry' : dialog.operation === 'delete' ? 'deleteEntry' : dialog.operation === 'rename' ? 'renameEntry' : dialog.kind === 'directory' ? 'newFolder' : 'newFile')}</h2>
      {dialog.operation === 'delete' ? <p>{t('recyclePrompt')} {dialog.entry.path}</p> : <label className="explorer-name-field">{t(dialog.operation === 'move' ? 'destinationDirectory' : 'entryName')}<input aria-label={t(dialog.operation === 'move' ? 'destinationDirectory' : 'entryName')} value={dialog.name} aria-invalid={dialogInvalid} aria-describedby={dialog.operation === 'move' ? 'move-directory-hint' : undefined} onChange={e => setDialog({ ...dialog, name: e.target.value })}/></label>}
      {dialog.operation === 'move' && <><p id="move-directory-hint" className="explorer-move-hint">{t('moveRelativeHint')}</p><div className="explorer-directory-shortcuts" aria-label={t('destinationDirectory')}>{[root, ...loadedEntries.filter(entry => entry.kind === 'directory')].filter((entry, index, entries) => entries.findIndex(other => other.path === entry.path) === index).map(entry => <button key={entry.path} type="button" title={moveHint(dialog.entry, entry.path, entry) || entry.path} disabled={Boolean(moveHint(dialog.entry, entry.path, entry))} onClick={() => setDialog({ ...dialog, name: entry.path })}>{entry.path || t('projectRoot')}</button>)}</div>{dialogInvalid && <p role="status" className="explorer-move-hint">{moveHint(dialog.entry, dialog.name, dialog.name === '' ? root : loadedEntries.find(entry => entry.path === dialog.name))}</p>}</>}
      {dialog.error && <p role="alert" className="inline-error">{dialog.error}</p>}<div className="source-dialog-actions"><button type="submit" className="primary" disabled={disabled || dialogInvalid}>{t(dialog.operation === 'move' ? 'moveSubmit' : dialog.operation === 'delete' ? 'moveToRecycle' : dialog.operation === 'rename' ? 'renameEntry' : 'createEntry')}</button><button type="button" onClick={() => setDialog(null)}>{t('cancelSource')}</button></div>
    </form></div>}</section>;
}
