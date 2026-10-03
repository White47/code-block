import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { BridgeError, call, hasMethod, waitForBridge } from '../api/bridge';
import type { AnalysisSettings, Anchor, Graph, InspectorData, Language, Overview as OverviewData, Problem, SearchResult, Session, ViewKind, ViewState, VisibleNode } from '../types';
import { initialState, layoutOf, reducer, sourceTab, viewTab, workspaceFromPython } from './state';
import { translator } from './i18n';
import ActivityBar, { type Activity } from './components/ActivityBar';
import Sidebar from './components/Sidebar';
import EditorTabs from './components/EditorTabs';
import Overview from './components/Overview';
import SearchPanel from './components/SearchPanel';
import SourceViewer from './components/SourceViewer';
import GraphEditor from './components/GraphEditor';
import Inspector from './components/Inspector';
import Problems from './components/Problems';
import SettingsPanel from './components/SettingsPanel';
import QueryToolbar from './components/QueryToolbar';
export default function App() {
  const [workspace, dispatch] = useReducer(reducer, initialState); const workspaceRef = useRef(workspace); workspaceRef.current = workspace;
  const [language, setLanguage] = useState<Language>('zh-CN'); const t = translator(language);
  const [connection, setConnection] = useState<'waiting' | 'ready' | 'error'>('waiting');
  const [session, setSession] = useState<Session | null>(null); const [viewState, setViewState] = useState<ViewState | null>(null);
  const [overview, setOverview] = useState<OverviewData | null>(null); const [graph, setGraph] = useState<Graph | null>(null); const [details, setDetails] = useState<Record<string, Graph>>({});
  const [problems, setProblems] = useState<Problem[]>([]); const [activity, setActivity] = useState<Activity | null>('overview'); const [showProblems, setShowProblems] = useState(true);
  const [hops, setHops] = useState(1); const [direction, setDirection] = useState<'in' | 'out' | 'both'>('both');
  const [inspectorVisible, setInspectorVisible] = useState(true);
  const [node, setNode] = useState<VisibleNode | null>(null); const [detailSource, setDetailSource] = useState<string | null>(null); const [inspector, setInspector] = useState<InspectorData | null>(null); const [inspectorMissing, setInspectorMissing] = useState(false); const [inspectorLoading, setInspectorLoading] = useState(false);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false); const [graphLoading, setGraphLoading] = useState(false); const [path, setPath] = useState(''); const [revision, setRevision] = useState(0);
  const projectGeneration = useRef(0); const saveQueue = useRef(Promise.resolve()); const selectionRequest = useRef(0); const restored = useRef(false);
  const sessionRef = useRef(session); sessionRef.current = session;
  const projectOperationPending = useRef(false);
  const pollEpoch = useRef(0);
  const layoutTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const tab = workspace.tabs.find(item => item.id === workspace.active); const view = tab?.kind === 'graph' ? tab.view : undefined;
  const analyzed = session !== null && ['Ready', 'Partial', 'Outdated'].includes(session.status);
  const reportError = useCallback((value: unknown) => { setError(value instanceof BridgeError ? `${value.code}: ${value.message}` : value instanceof Error ? value.message : String(value)); }, []);
  const receiveState = useCallback((state: ViewState) => { setViewState(state); }, []);
  // All project-scoped mutations share this queue, including settings and the
  // layout snapshot that must be committed before Python changes projects.
  const enqueue = useCallback(<T,>(operation: () => Promise<T>): Promise<T> => {
    const result = saveQueue.current.then(operation);
    saveQueue.current = result.then(() => {}, () => {});
    return result;
  }, []);
  const write = useCallback((operation: () => Promise<ViewState>) => {
    if (projectOperationPending.current) return Promise.resolve();
    const generation = projectGeneration.current;
    return enqueue(async () => { const state = await operation(); if (generation === projectGeneration.current) receiveState(state); }).catch(e => { if (generation === projectGeneration.current) reportError(e); });
  }, [enqueue, receiveState, reportError]);
  const refreshSession = useCallback(async (generation = projectGeneration.current, allowProjectChange = false, pollingEpoch?: number) => {
    const requestedPath = sessionRef.current?.projectPath;
    const s = await call('get_session');
    if (generation !== projectGeneration.current || (pollingEpoch !== undefined && pollingEpoch !== pollEpoch.current) || (!allowProjectChange && (sessionRef.current?.projectPath !== requestedPath || s.projectPath !== requestedPath))) return null;
    sessionRef.current = s; setSession(s); return s;
  }, []);
  const hydrate = useCallback(async (s: Session, generation: number) => {
    if (generation !== projectGeneration.current) return false;
    restored.current = false; selectionRequest.current++; sessionRef.current = s; setSession(s); setGraph(null); setOverview(null); setDetails({}); setNode(null); setInspector(null); setDetailSource(null); setProblems([]);
    const state = await call('get_view_state');
    if (generation !== projectGeneration.current) return false;
    receiveState(state); dispatch({ type: 'restore', state: workspaceFromPython(state) }); restored.current = true; setRevision(v => v + 1); return true;
  }, [receiveState]);
  const connect = useCallback(async () => {
    const generation = ++projectGeneration.current;
    setConnection('waiting'); setError('');
    try { await waitForBridge(); const [s, prefs] = await Promise.all([call('get_session'), call('get_preferences')]); if (generation !== projectGeneration.current) return; setLanguage(prefs.language); if (prefs.issue) setError(prefs.issue); if (await hydrate(s, generation)) setConnection('ready'); } catch (e) { if (generation === projectGeneration.current) { reportError(e); setConnection('error'); } }
  }, [hydrate, reportError]);
  useEffect(() => { void connect(); }, [connect]);
  useEffect(() => { document.documentElement.lang = language; }, [language]);
  useEffect(() => {
    if (!restored.current || connection !== 'ready' || !session?.projectPath || busy || session.status === 'Analyzing') return;
    const layout = layoutOf(workspace); layoutTimer.current = setTimeout(() => { layoutTimer.current = undefined; void write(() => call('save_layout', layout)); }, 150);
    return () => { clearTimeout(layoutTimer.current); layoutTimer.current = undefined; };
  }, [workspace, connection, session?.projectPath, session?.status, busy, write]);
  useEffect(() => {
    if (!analyzed || connection !== 'ready' || busy) return;
    let live = true;
    Promise.all([call('get_overview'), call('get_problems')]).then(([o, p]) => { if (live) { setOverview(o); setProblems(p); } }).catch(e => { if (live) reportError(e); });
    return () => { live = false; };
  }, [analyzed, connection, revision, busy, reportError]);
  const root = view ? workspace.roots[view] ?? null : null;
  const detailSignature = JSON.stringify(viewState?.detailIslands.filter(i => i.viewKind === view).map(i => [i.id, i.sourceNodeId]) ?? []);
  useEffect(() => { setHops(1); setDirection('both'); }, [view, root]);
  useEffect(() => {
    setGraph(null); setNode(null); setInspector(null); setDetailSource(null); selectionRequest.current++;
    if (!view || !analyzed || busy) { setGraphLoading(false); return; }
    let live = true; setGraphLoading(true);
    call('get_view', view, root, workspaceRef.current.selectedId, hops, direction).then(g => { if (live) { setGraph(g); const n = g.nodes.find(n => n.id === workspaceRef.current.selectedId); if (n) setNode(n); void call('get_problems').then(p => { if (live) setProblems(p); }).catch(reportError); } }).catch(e => { if (live) reportError(e); }).finally(() => { if (live) setGraphLoading(false); });
    return () => { live = false; };
  }, [view, root, analyzed, revision, busy, hops, direction, reportError]);
  useEffect(() => {
    if (!view || !graph || !viewState) return;
    let live = true;
    const islands = viewState.detailIslands.filter(i => i.viewKind === view);
    Promise.all(islands.map(async island => [island.id, await call('get_detail_view', view, root, island.sourceNodeId)] as const)).then(items => { if (live) setDetails(Object.fromEntries(items)); }).catch(e => { if (live) reportError(e); });
    return () => { live = false; };
  }, [view, root, Boolean(graph), detailSignature, revision, reportError]);
  useEffect(() => {
    if (!node || !view || busy) { setInspector(null); return; }
    let live = true; const request = ++selectionRequest.current; setInspector(null); setInspectorLoading(true); setInspectorMissing(false);
    hasMethod('get_inspector').then(async available => { if (!available) { if (live) setInspectorMissing(true); return; } const value = await call('get_inspector', view, root, node.id, detailSource); if (live && request === selectionRequest.current) setInspector(value); }).catch(e => { if (live) reportError(e); }).finally(() => { if (live) setInspectorLoading(false); });
    return () => { live = false; };
  }, [node, view, root, detailSource, busy, reportError]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'w') { event.preventDefault(); if (workspaceRef.current.active) dispatch({ type: 'close', id: workspaceRef.current.active }); }
      if (event.key === 'Enter' && !(event.target instanceof HTMLElement && /INPUT|TEXTAREA|SELECT|BUTTON/.test(event.target.tagName)) && node && view && !detailSource) { event.preventDefault(); void write(() => call('open_detail_island', view, node.id, null)); }
    };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [node, view, detailSource, write]);
  useEffect(() => {
    if (!busy) return;
    const generation = projectGeneration.current;
    const epoch = pollEpoch.current;
    const timer = setInterval(() => { if (epoch !== pollEpoch.current) return; void refreshSession(generation, false, epoch).catch(e => { if (generation === projectGeneration.current && epoch === pollEpoch.current) reportError(e); }); }, 1000);
    return () => { clearInterval(timer); if (epoch === pollEpoch.current) pollEpoch.current++; };
  }, [busy, refreshSession, reportError]);
  async function runProject(operation: () => Promise<Session>) {
    if (projectOperationPending.current) return;
    projectOperationPending.current = true;
    const flushLayout = connection === 'ready' && sessionRef.current?.projectPath && sessionRef.current.status !== 'Analyzing' ? layoutOf(workspaceRef.current) : null;
    clearTimeout(layoutTimer.current); layoutTimer.current = undefined;
    const generation = ++projectGeneration.current;
    setBusy(true); setError(''); restored.current = false;
    try {
      await enqueue(async () => {
        if (flushLayout) await call('save_layout', flushLayout);
        const s = await operation();
        // The operation result is authoritative, even for the same project.
        // Discard in-flight progress polls before session/layout hydration starts.
        pollEpoch.current++; await hydrate(s, generation);
      });
    } catch (e) {
      if (generation === projectGeneration.current) {
        pollEpoch.current++;
        reportError(e);
        try { const s = await refreshSession(generation, true); if (s) await hydrate(s, generation); } catch { /* Keep the original operation failure visible. */ }
        if (generation === projectGeneration.current) restored.current = true;
      }
    } finally { if (generation === projectGeneration.current) { pollEpoch.current++; projectOperationPending.current = false; setBusy(false); } }
  }
  const saveAnalysisSettings = useCallback((payload: { ignorePaths: string[] }): Promise<AnalysisSettings> => {
    const expectedPath = sessionRef.current?.projectPath;
    const generation = projectGeneration.current;
    if (projectOperationPending.current) return Promise.reject(new BridgeError('NOT_READY', 'Wait for the project operation to finish.'));
    const layout = expectedPath && sessionRef.current?.status !== 'Analyzing' ? layoutOf(workspaceRef.current) : null;
    clearTimeout(layoutTimer.current); layoutTimer.current = undefined;
    return enqueue(async () => {
      if (sessionRef.current?.projectPath !== expectedPath) throw new BridgeError('NOT_READY', 'The project changed before settings could be saved.');
      if (layout) await call('save_layout', layout);
      const result = await call('set_analysis_settings', payload);
      if (generation === projectGeneration.current) { const s = await refreshSession(generation); if (s) await hydrate(s, generation); }
      return result;
    });
  }, [enqueue, hydrate, refreshSession]);
  function openView(kind: ViewKind, selectedRoot?: string) { if (selectedRoot) dispatch({ type: 'reveal', view: kind, root: selectedRoot, selected: selectedRoot }); else dispatch({ type: 'open', tab: viewTab(kind) }); }
  const openSource = useCallback((anchor: Anchor) => dispatch({ type: 'open', tab: sourceTab(anchor) }), []);
  const closeIsland = useCallback((id: string) => { void write(() => call('close_detail_island', id)); }, [write]);
  function reveal(result: SearchResult) { const destination = result.revealTarget; if (!destination.viewKind) { setError(destination.reason ?? 'No graph destination'); openSource(result.sourceAnchor); return; } dispatch({ type: 'reveal', view: destination.viewKind, root: destination.rootId, selected: destination.selectedId }); setRevision(n => n + 1); }
  async function changeLanguage(value: Language) { try { const p = await call('set_preferences', { language: value }); setLanguage(p.language); if (p.issue) reportError(p.issue); } catch (e) { reportError(e); } }
  function detail(item: VisibleNode) { if (view) void write(() => call('open_detail_island', view, item.id, null)); }
  const settings = <SettingsPanel language={language} session={session} onLanguage={value => { void changeLanguage(value); }} saveAnalysisSettings={saveAnalysisSettings} onError={reportError} t={t}/>;
  return <div className="desktop-shell"><header className="titlebar"><strong>CODE<span>VIZ</span></strong><span className="titlebar-path">{session?.projectPath ?? t('local')}</span><span className="native-badge"><i className="signal"/> LOCAL ENGINE</span></header><div className="shell-body"><ActivityBar activity={activity} t={t} onChange={value => { setActivity(current => current === value ? null : value); if (value === 'settings') dispatch({ type: 'open', tab: { id: 'settings', kind: 'settings' } }); }}/>{activity !== null && <aside className="sidebar">{activity === 'search' ? <SearchPanel key={session?.projectPath} enabled={analyzed && !busy} onReveal={reveal} onSource={r => openSource(r.sourceAnchor)} onError={reportError} t={t}/> : activity === 'problems' ? <><div className="panel-heading">{t('problems')}</div><Problems problems={problems} onSource={openSource} t={t}/></> : <Sidebar session={session} active={workspace.active} openView={openView} openPath={value => { void runProject(() => call('open_project', value)); }} choose={() => { void runProject(() => call('choose_project')); }} analyze={() => { void runProject(() => call('analyze')); }} busy={busy || connection !== 'ready'} t={t}/>}</aside>}<main className="workbench"><EditorTabs tabs={workspace.tabs} active={workspace.active} onOpen={tab => dispatch({ type: 'open', tab })} onClose={id => dispatch({ type: 'close', id })} t={t}/>{error && <div className="error-banner" role="alert"><span>{error}</span><button onClick={() => setError('')} aria-label={t('close')}>×</button></div>}{session?.status === 'Outdated' && <div className="outdated-banner">{t('outdated')} <button disabled={busy} onClick={() => { void runProject(() => call('analyze')); }}>{t('reanalyze')}</button></div>}{session?.status === 'Analyzing' && <div className="analysis-progress" role="status">{t('Analyzing')}…</div>}<div className="editor-area">{connection !== 'ready' ? <div className="welcome"><div className="welcome-mark">CV</div><h1>{connection === 'waiting' ? t('waiting') : t('desktop')}</h1>{connection === 'error' && <button className="primary" onClick={() => { void connect(); }}>{t('retry')}</button>}</div> : <><div className="primary-editor">{tab?.kind === 'settings' ? settings : !session?.projectPath ? <div className="welcome"><div className="welcome-mark">CV</div><h1>{t('empty')}</h1><button className="primary" disabled={busy} onClick={() => { void runProject(() => call('choose_project')); }}>{t('open')}</button><form className="path-form" onSubmit={e => { e.preventDefault(); if (path.trim()) void runProject(() => call('open_project', path.trim())); }}><input aria-label={t('path')} placeholder={t('path')} value={path} onChange={e => setPath(e.target.value)}/><button disabled={busy || !path.trim()}>{t('open')}</button></form></div> : !tab ? <div className="welcome"><p>{t('noTabs')}</p></div> : tab.kind === 'source' && tab.anchor ? <SourceViewer key={tab.id} anchor={tab.anchor} t={t}/> : !analyzed ? <div className="welcome"><h1>{t(session.status)}</h1><button className="primary" disabled={busy} onClick={() => { void runProject(() => call('analyze')); }}>{t('analyze')}</button></div> : tab.kind === 'Overview' ? <Overview data={overview} openView={openView} t={t}/> : graph && view ? <><QueryToolbar direction={direction} hops={hops} setDirection={setDirection} more={() => setHops(n => Math.min(5, n + 1))} disabled={busy || graphLoading} t={t} inspectorVisible={inspectorVisible} toggleInspector={() => setInspectorVisible(v => !v)}/><GraphEditor key={`${view}:${root ?? ''}:${revision}`} graph={graph} viewState={viewState} details={details} selectedId={workspace.selectedId} onSelect={(item, source) => { setNode(item); setDetailSource(source); dispatch({ type: 'select', id: item.id }); }} onDetail={detail} onMove={(id, position, island) => { void write(() => island ? call('move_detail_island', id, position) : call('move_node', id, position, view)); }} onViewport={(kind, viewport) => { void write(() => call('save_viewport', kind, viewport.zoom, { x: viewport.x, y: viewport.y })); }} onCloseIsland={closeIsland} t={t}/></> : <div className="welcome"><p>{graphLoading ? t('waiting') : t('select')}</p></div>}</div>{workspace.side && <section className="side-editor"><div className="side-title">{t('source')}<button onClick={() => dispatch({ type: 'side', anchor: null })} aria-label={t('close')}>×</button></div><SourceViewer anchor={workspace.side} t={t}/></section>}{view && inspectorVisible && <Inspector node={node} data={inspector} missing={inspectorMissing} loading={inspectorLoading} openSource={openSource} openSide={anchor => dispatch({ type: 'side', anchor })} openDetail={() => { if (node && !detailSource) detail(node); }} expand={collapse => { if (node) void write(() => call(collapse ? 'collapse' : 'expand', node.id)).then(() => setRevision(v => v + 1)); }} t={t}/>}</>}</div><div className="problems-heading"><button onClick={() => setShowProblems(v => !v)}>{showProblems ? '⌄' : '›'} {t('problems')} <span>{problems.length}</span></button><span>PYTHON ANALYSIS</span></div>{showProblems && <div className="problems-dock"><Problems problems={problems} onSource={openSource} t={t}/></div>}</main></div><footer className="statusbar"><span><i className="signal"/> {connection === 'ready' ? 'Python bridge' : t('waiting')}</span><span>{session ? t(session.status) : '—'}</span><span className="status-path">{session?.projectPath}</span><span>{language === 'en' ? 'EN' : '中文'} · UTF-8</span></footer></div>;
}
