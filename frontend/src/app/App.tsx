import { useCallback, useEffect, useMemo, useReducer, useRef, useState, useSyncExternalStore } from 'react';
import { BridgeError, call, hasMethod, waitForBridge } from '../api/bridge';
import type { AnalysisSettings, Anchor, Graph, InspectorData, Language, Overview as OverviewData, Problem, SearchResult, Session, ViewKind, ViewState, VisibleNode } from '../types';
import { initialState, layoutOf, reducer, sourceTab, viewTab, workspaceFromPython, type Action, type Tab } from './state';
import { SourceDocumentStore, sourceDocumentKey } from './sourceDocuments';
import { releaseSourceModel } from './sourceModels';
import { translator } from './i18n';
import ActivityBar, { type Activity } from './components/ActivityBar';
import Sidebar from './components/Sidebar';
import EditorTabs from './components/EditorTabs';
import Overview from './components/Overview';
import { ProjectAnalysisControls } from './components/ProjectUnderstanding';
import { AssistantRuntimeBoundary } from './assistant/runtime';
import { effectiveProjectUnderstanding, understandingContextAvailable } from './assistant/project-understanding';
import { currentWorkspaceNavigation, WorkspaceNavigation, type WorkspaceNavigationContext } from './assistant/workspace-navigation';
import { assistantJobReleased } from './assistant/readiness';
import { assistantIssue } from './assistant/ui';
import SearchPanel from './components/SearchPanel';
import SourceViewer from './components/SourceViewer';
import GraphEditor from './components/GraphEditor';
import Inspector from './components/Inspector';
import Problems from './components/Problems';
import SettingsPanel from './components/SettingsPanel';
import QueryToolbar from './components/QueryToolbar';
import ProjectExplorer, { type ExplorerMutation } from './components/ProjectExplorer';
import { containsSourcePath, moveDestinationPath, parentPath, remapSourcePath, validDestinationDirectory } from './explorerState';
import { AssistantStore } from './assistant/store';
import { assistantClient } from './assistant/client';
import { graphNavigationIntent, sourceNavigationIntent, type NavigationContext } from './assistant/navigation';
import type { AssistantContext, AssistantQuestionDraft, AssistantSettings, NavigationPoll } from './assistant/types';
import AssistantPanel from './components/AssistantPanel';
export default function App() {
  const localSourceTabs = useRef(new Set<string>());
  const [workspace, dispatch] = useReducer((state: typeof initialState, action: Action) => {
    const next = reducer(state, action);
    const source = state.tabs.find(item => item.id === state.active);
    const destination = next.tabs.find(item => item.id === next.active);
    // Preserve the current main selection only on a direct local-source return.
    // Explicit reveal/navigation actions retain the selection chosen by reducer.
    const returning = source?.kind === 'source' && localSourceTabs.current.has(source.id)
      && destination?.kind === 'graph' && destination.id === source.returnTabId
      && ((action.type === 'open' && action.tab.id === source.returnTabId)
        || (action.type === 'close' && action.id === source.id));
    return returning ? { ...next, selectedId: state.selectedId } : next;
  }, initialState); const workspaceRef = useRef(workspace); workspaceRef.current = workspace;
  const [language, setLanguage] = useState<Language>('zh-CN'); const t = useMemo(() => translator(language), [language]);
  const [connection, setConnection] = useState<'waiting' | 'ready' | 'error'>('waiting');
  const [session, setSession] = useState<Session | null>(null); const [viewState, setViewState] = useState<ViewState | null>(null);
  const [explorerContext, setExplorerContext] = useState<{ path: string | null; token: number } | null>(null);
  const [overview, setOverview] = useState<OverviewData | null>(null); const [graph, setGraph] = useState<Graph | null>(null); const [details, setDetails] = useState<Record<string, Graph>>({});
  const [problems, setProblems] = useState<Problem[]>([]); const [activity, setActivity] = useState<Activity | null>('overview'); const [showProblems, setShowProblems] = useState(true);
  const [hops, setHops] = useState(1); const [direction, setDirection] = useState<'in' | 'out' | 'both'>('both');
  const [inspectorVisible, setInspectorVisible] = useState(true);
  const [assistantStore] = useState(() => new AssistantStore());
  const workspaceNavContext = useRef<WorkspaceNavigationContext | null>(null);
  const workspaceNavConsume = useRef<(navigation: NavigationPoll) => void>(() => {});
  const workspaceNavMounted = useRef(false);
  const [workspaceNavigation] = useState(() => new WorkspaceNavigation(() => currentWorkspaceNavigation(workspaceNavContext.current, assistantStore.getSnapshot().state),
    navigation => workspaceNavConsume.current(navigation)));
  const workspaceNavigationState = useSyncExternalStore(workspaceNavigation.subscribe, workspaceNavigation.getSnapshot);
  useEffect(() => {
    workspaceNavMounted.current = true;
    return () => { workspaceNavMounted.current = false; queueMicrotask(() => {
      if (!workspaceNavMounted.current) workspaceNavigation.dispose();
    }); };
  }, [workspaceNavigation]);
  const assistantSnapshot = useSyncExternalStore(assistantStore.subscribe, assistantStore.getSnapshot);
  const assistantSendContext = useRef<AssistantContext | null>(null);
  const prepareAssistantContext = useCallback((context: AssistantContext) => { assistantSendContext.current = context; }, []);
  const [understandingLayer, setUnderstandingLayer] = useState<{ key: string; enhanced: boolean } | null>(null);
  const [rightPanel, setRightPanel] = useState<'inspector' | 'assistant'>('inspector');
  const [assistantContext, setAssistantContext] = useState<AssistantContext>({ kind: 'question' });
  const [assistantQuestionDraft, setAssistantQuestionDraft] = useState<AssistantQuestionDraft | null>(null);
  const assistantQuestionSerial = useRef(0);
  useEffect(() => { setAssistantQuestionDraft(null); setAssistantContext({ kind: 'question' }); }, [assistantSnapshot.state?.projectEpoch]);
  const [assistantSettings, setAssistantSettings] = useState<AssistantSettings | null>(null);
  const [assistantBinding, setAssistantBinding] = useState<{ path: string; epoch: string; token: number } | null>(null);
  const [node, setNode] = useState<VisibleNode | null>(null); const [detailSource, setDetailSource] = useState<string | null>(null); const [inspector, setInspector] = useState<InspectorData | null>(null); const [inspectorMissing, setInspectorMissing] = useState(false); const [inspectorLoading, setInspectorLoading] = useState(false);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false); const [graphLoading, setGraphLoading] = useState(false); const [path, setPath] = useState(''); const [revision, setRevision] = useState(0);
  const [layoutWarning, setLayoutWarning] = useState('');
  const [graphContext, setGraphContext] = useState('');
  const [structureQuery, setStructureQuery] = useState({ context: '', depth: 1, token: 0 });
  const initialDepths = useRef(new Map<string, number>());
  const [queryFeedback, setQueryFeedback] = useState<{ context: string; added: number; removed: number; compared: boolean; failed: boolean } | null>(null);
  const completedGraph = useRef<{ context: string; graph: Graph } | null>(null);
  function layoutSnapshot(state: typeof workspace) {
    const layout = layoutOf(state);
    return state.active && localSourceTabs.current.has(state.active) ? { ...layout, selectedId: state.selectedId } : layout;
  }
  const cameraProject = useRef<{ path: string | null; token: number } | null>(null);
  const viewportContexts = useRef<Partial<Record<ViewKind, string>>>({});
  const projectGeneration = useRef(0); const saveQueue = useRef(Promise.resolve()); const selectionRequest = useRef(0); const restored = useRef(false);
  const sessionRef = useRef(session); sessionRef.current = session;
  const projectOperationPending = useRef(false);
  const pollEpoch = useRef(0);
  const layoutTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const tab = workspace.tabs.find(item => item.id === workspace.active); const view = tab?.kind === 'graph' ? tab.view : undefined;
  const analyzed = session?.hasAnalysis === true && ['Ready', 'Partial', 'Outdated'].includes(session.status);
  const reportError = useCallback((value: unknown) => { setError(value instanceof BridgeError ? `${value.code}: ${value.message}` : value instanceof Error ? value.message : String(value)); }, []);
  const receiveState = useCallback((state: ViewState) => { setViewState(state); }, []);
  // All project-scoped mutations share this queue, including settings and the
  // best-effort layout snapshot attempted before Python changes projects.
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
  const [documents] = useState(() => new SourceDocumentStore(
    (projectPath, anchor) => call('open_source_document', projectPath, anchor),
    request => enqueue(async () => {
      const generation = projectGeneration.current;
      if (sessionRef.current?.projectPath !== request.projectPath) throw new BridgeError('SOURCE_PROJECT_CHANGED', 'The source project changed.');
      const saved = await call('save_source_document', request);
      await refreshSession(generation).catch(reportError);
      return saved;
    }),
  ));
  const [sourceVersion, sourceChanged] = useReducer(value => value + 1, 0);
  useEffect(() => documents.subscribe(sourceChanged), [documents]);
  // Identifiers only: draft text never enters AI state, consent or bridge calls.
  const savedSourceIdentity = JSON.stringify(documents.keys().map(key => [key, documents.get(key)?.revision]));
  useEffect(() => {
    if (connection !== 'ready') return;
    let live = true;
    void hasMethod('assistant_get_settings').then(async available => {
      if (available) { const settings = await assistantClient.getSettings(); if (live) setAssistantSettings(settings); }
    }).catch(() => { if (live) setAssistantSettings(null); });
    return () => { live = false; };
  }, [connection]);
  useEffect(() => {
    const projectPath = session?.projectPath;
    setAssistantBinding(null);
    if (connection !== 'ready' || !projectPath || busy || !explorerContext || explorerContext.path !== projectPath) return;
    let live = true;
    void hasMethod('assistant_get_state').then(async available => {
      if (!available || !live) return;
      const directory = await call('list_project_directory', { projectPath, directory: '' });
      if (!live || sessionRef.current?.projectPath !== projectPath || directory.projectPath !== projectPath) return;
      await assistantStore.restore(directory.projectEpoch);
      if (live) setAssistantBinding({ path: projectPath, epoch: directory.projectEpoch, token: explorerContext.token });
    }).catch(() => { if (live) setAssistantBinding(null); });
    return () => { live = false; };
  }, [assistantStore, connection, session?.projectPath, session?.status, busy, explorerContext, savedSourceIdentity]);
  const [sourceDecision, setSourceDecision] = useState<{ keys: string[]; resolve: (choice: 'save' | 'discard' | 'cancel') => void } | null>(null);
  const [guardSaving, setGuardSaving] = useState(false);
  const [sourceLocked, setSourceLocked] = useState(false);
  const settingsPending = useRef(false);
  const nativeClosePending = useRef(false);
  const activeNativeClose = useRef<{ requestId: string; phase: 'guard' | 'approval' } | null>(null);
  const guardPending = useRef(false);
  const keyFor = useCallback((anchor: Anchor) => sourceDocumentKey(sessionRef.current?.projectPath ?? '', anchor.filePath), []);
  const guardSources = useCallback(async (keys: string[], deferDiscard = false): Promise<boolean> => {
    if (guardPending.current) return false;
    guardPending.current = true;
    const unique = [...new Set(keys)];
    try {
      for (const key of unique) if (documents.get(key)?.saving) await documents.save(key);
      const dirty = unique.filter(key => documents.get(key)?.dirty);
      if (!dirty.length) return true;
      const decision = await new Promise<'save' | 'discard' | 'cancel'>(resolve => setSourceDecision({ keys: dirty, resolve }));
      if (decision === 'cancel') return false;
      if (decision === 'discard') {
        if (!deferDiscard) for (const key of dirty) documents.discard(key);
        return true;
      }
      setGuardSaving(true);
      for (const key of dirty) {
        await documents.save(key);
        if (documents.get(key)?.dirty) return false;
      }
      return true;
    } catch (error) { reportError(error); return false; }
    finally { setSourceDecision(null); setGuardSaving(false); guardPending.current = false; }
  }, [documents, reportError]);
  const mutateProjectEntry = useCallback(async (request: ExplorerMutation): Promise<boolean> => {
    if (projectOperationPending.current || settingsPending.current || guardPending.current || nativeClosePending.current) return false;
    const { explorer, entry } = request;
    const projectPath = sessionRef.current?.projectPath;
    const projectEpoch = explorer.epoch;
    if (!projectPath || projectPath !== explorer.projectPath || !projectEpoch) throw new BridgeError('PROJECT_CHANGED', 'The project changed. Refresh the file tree.');
    const generation = projectGeneration.current;
    const flag = sessionRef.current?.fileSystemCaseSensitive;
    const caseSensitive = typeof flag === 'boolean' ? flag : true;
    let sourcePath = entry.path;
    let destinationDirectory = request.operation === 'move' ? request.destinationDirectory : '';
    let unknownFailure = false;
    const assertDestinationAvailable = (destination: string) => {
      const current = workspaceRef.current;
      const paths = documents.keys().flatMap(key => {
        const [path, filePath] = JSON.parse(key) as [string, string];
        return path === projectPath ? [filePath] : [];
      });
      paths.push(...current.tabs.flatMap(tab => tab.anchor ? [tab.anchor.filePath] : []));
      if (current.side) paths.push(current.side.filePath);
      if (paths.some(path => containsSourcePath(destination, path, caseSensitive) && !containsSourcePath(sourcePath, path, caseSensitive))) {
        throw new BridgeError('SOURCE_IDENTITY_CONFLICT', 'The destination has an active source document or view. Close it before moving or renaming.');
      }
    };
    projectOperationPending.current = true; setSourceLocked(true);
    clearTimeout(layoutTimer.current); layoutTimer.current = undefined;
    try {
      if (request.operation === 'rename') assertDestinationAvailable([parentPath(entry.path), request.newName].filter(Boolean).join('/'));
      if (request.operation === 'move') {
        if (!entry.path || !validDestinationDirectory(destinationDirectory)) throw new BridgeError('INVALID_MOVE', 'Choose a relative destination directory. The project root cannot be moved.');
        const destination = moveDestinationPath(entry.path, destinationDirectory);
        if (containsSourcePath(entry.path, destinationDirectory, caseSensitive) || (containsSourcePath(entry.path, destination, caseSensitive) && containsSourcePath(destination, entry.path, caseSensitive))) throw new BridgeError('INVALID_MOVE', 'An entry cannot be moved into itself, its descendants, or its current directory.');
        assertDestinationAvailable(destination);
      }
      const affectedKeys = request.operation === 'create' ? [] : documents.keys().filter(key => {
        const [path, filePath] = JSON.parse(key) as [string, string];
        return path === projectPath && containsSourcePath(entry.path, filePath, caseSensitive);
      });
      // Saves use the same write queue. Finish the guard before reserving the
      // mutation slot, and defer Discard until the native operation succeeds.
      if (!await guardSources(affectedKeys, true)) return false;
      return await enqueue(async () => {
        if (generation !== projectGeneration.current || projectPath !== sessionRef.current?.projectPath) throw new BridgeError('PROJECT_CHANGED', 'The project changed before the file operation.');
        try {
          if (request.operation === 'create') {
            // Earlier source saves can change the parent directory revision.
            // Read it in this final queue slot, after all preceding writes.
            const parent = await explorer.load(entry.path);
            await call('create_project_entry', { projectPath, projectEpoch, parent: entry.path, parentRevision: parent.directoryRevision, name: request.name, kind: request.kind });
          } else {
            const fresh = await explorer.freshEntry(entry.path, caseSensitive);
            sourcePath = fresh.path;
            if (fresh.readOnlyReason || !(request.operation === 'delete' ? fresh.canDelete : fresh.canRename)) throw new BridgeError('ENTRY_READ_ONLY', fresh.readOnlyReason ?? 'This entry cannot be modified.');
            let destinationRevision: string | undefined;
            if (request.operation === 'move') {
              if (destinationDirectory) {
                const target = await explorer.freshEntry(destinationDirectory, caseSensitive);
                if (target.kind !== 'directory' || !target.canCreate) throw new BridgeError('ENTRY_READ_ONLY', target.readOnlyReason ?? 'The destination cannot accept entries.');
                destinationDirectory = target.path;
              }
              if (containsSourcePath(sourcePath, destinationDirectory, caseSensitive) || (containsSourcePath(sourcePath, moveDestinationPath(sourcePath, destinationDirectory), caseSensitive) && containsSourcePath(moveDestinationPath(sourcePath, destinationDirectory), sourcePath, caseSensitive))) throw new BridgeError('INVALID_MOVE', 'Choose another destination directory.');
              destinationRevision = (await explorer.load(destinationDirectory)).directoryRevision;
              assertDestinationAvailable(moveDestinationPath(sourcePath, destinationDirectory));
            }
            const prepared = await call('prepare_project_entry_operation', { projectPath, projectEpoch, path: fresh.path, revision: fresh.revision, operation: request.operation,
              ...(request.operation === 'rename' ? { newName: request.newName } : {}),
              ...(request.operation === 'move' ? { destinationDirectory, destinationRevision } : {}) });
            if ((request.operation === 'rename' || request.operation === 'move') && prepared.newPath !== null) assertDestinationAvailable(prepared.newPath);
            const result = await call('apply_project_entry_operation', { projectPath, projectEpoch, operationToken: prepared.operationToken });
            const paths = [sourcePath, ...result.affectedPaths];
            for (const key of documents.keys()) {
              const [path, filePath] = JSON.parse(key) as [string, string];
              if (path !== projectPath || !paths.some(parent => containsSourcePath(parent, filePath, caseSensitive))) continue;
              documents.discard(key); documents.release(key); releaseSourceModel(key);
            }
            const remappedLocalTabs = new Set<string>();
            for (const tab of workspaceRef.current.tabs) if (tab.anchor && paths.some(parent => containsSourcePath(parent, tab.anchor!.filePath, caseSensitive))) {
              const local = localSourceTabs.current.delete(tab.id);
              if (local && result.newPath) remappedLocalTabs.add(sourceTab({ ...tab.anchor, filePath: remapSourcePath(tab.anchor.filePath, sourcePath, result.newPath, caseSensitive) }).id);
            }
            for (const id of remappedLocalTabs) localSourceTabs.current.add(id);
            dispatch({ type: 'project-entry', oldPath: sourcePath, newPath: result.newPath, affectedPaths: result.affectedPaths, caseSensitive });
            if (request.operation === 'move') await explorer.refreshMove(sourcePath, destinationDirectory).catch(reportError);
          }
          return true;
        } catch (error) {
          if (error instanceof BridgeError && ['FILE_OPERATION_FAILED', 'BRIDGE_ERROR', 'INVALID_RESPONSE'].includes(error.code)) {
            unknownFailure = true;
            if (request.operation === 'move') await explorer.refreshMove(sourcePath, destinationDirectory).catch(reportError);
            else await explorer.refresh(request.operation === 'create' ? entry.path : parentPath(entry.path)).catch(reportError);
          }
          throw error;
        } finally {
          // Native failures may follow a disk mutation. Reconcile the session
          // without replacing tabs, graph selection, roots, or unrelated drafts.
          await refreshSession(generation).catch(reportError);
          if (unknownFailure && generation === projectGeneration.current && sessionRef.current?.projectPath === projectPath) {
            const current = sessionRef.current;
            const outdated: Session = { ...current, status: current.hasAnalysis ? 'Outdated' : current.status, outdatedReasons: [...new Set([...current.outdatedReasons, 'FILE_OPERATION_FAILED'])] };
            sessionRef.current = outdated; setSession(outdated);
          }
        }
      });
    } finally { projectOperationPending.current = false; setSourceLocked(settingsPending.current); }
  }, [documents, enqueue, guardSources, refreshSession, reportError]);
  const closeSourceTab = useCallback(async (id: string) => {
    if (projectOperationPending.current || guardPending.current) return;
    const current = workspaceRef.current;
    const closing = current.tabs.find(item => item.id === id);
    if (closing?.anchor) {
      const key = keyFor(closing.anchor);
      const retained = current.tabs.some(item => item.id !== id && item.anchor && keyFor(item.anchor) === key)
        || (current.side && keyFor(current.side) === key);
      if (!retained && !await guardSources([key])) return;
    }
    dispatch({ type: 'close', id });
  }, [guardSources, keyFor]);
  const changeSide = useCallback(async (anchor: Anchor | null) => {
    if (projectOperationPending.current || guardPending.current) return;
    const current = workspaceRef.current;
    if (current.side) {
      const key = keyFor(current.side);
      const retained = (anchor && keyFor(anchor) === key) || current.tabs.some(item => item.anchor && keyFor(item.anchor) === key);
      if (!retained && !await guardSources([key])) return;
    }
    dispatch({ type: 'side', anchor });
  }, [guardSources, keyFor]);
  const saveSource = useCallback(async (key: string) => {
    if (projectOperationPending.current || settingsPending.current) return;
    try { await documents.save(key); } catch (error) { reportError(error); }
  }, [documents, reportError]);
  const releaseSources = useCallback(() => {
    for (const key of documents.keys()) {
      documents.release(key);
      releaseSourceModel(key);
    }
  }, [documents]);
  useEffect(() => {
    const retained = new Set(workspace.tabs.flatMap(item => item.anchor ? [keyFor(item.anchor)] : []));
    if (workspace.side) retained.add(keyFor(workspace.side));
    for (const key of documents.keys()) {
      const document = documents.get(key);
      if (!retained.has(key) && !document?.dirty && !document?.saving) {
        releaseSourceModel(key); documents.release(key);
      }
    }
  }, [workspace, sourceVersion, documents, keyFor, session?.projectPath]);
  useEffect(() => {
    const consumed = new Set<string>();
    const close = (event: Event) => {
      const requestId = (event as CustomEvent<{ requestId?: unknown }>).detail?.requestId;
      if (typeof requestId !== 'string' || !requestId || consumed.has(requestId)) return;
      consumed.add(requestId);
      void (async () => {
        if (window.__codevizCloseReady !== true || projectOperationPending.current || settingsPending.current || guardPending.current) {
          await call('cancel_close', requestId); return;
        }
        nativeClosePending.current = true; projectOperationPending.current = true; setSourceLocked(true);
        activeNativeClose.current = { requestId, phase: 'guard' };
        try {
          const approved = await guardSources(documents.keys());
          if (approved) activeNativeClose.current = { requestId, phase: 'approval' };
          await call(approved ? 'approve_close' : 'cancel_close', requestId);
          if (!approved && activeNativeClose.current?.requestId === requestId) { activeNativeClose.current = null; nativeClosePending.current = false; projectOperationPending.current = false; setSourceLocked(false); }
        } catch (error) {
          if (activeNativeClose.current?.requestId === requestId) {
            activeNativeClose.current = null; nativeClosePending.current = false; projectOperationPending.current = false; setSourceLocked(false); throw error;
          }
        }
      })().catch(reportError);
    };
    const failed = (event: Event) => {
      const requestId = (event as CustomEvent<{ requestId?: unknown }>).detail?.requestId;
      if (typeof requestId !== 'string' || activeNativeClose.current?.requestId !== requestId || activeNativeClose.current.phase !== 'approval') return;
      activeNativeClose.current = null; nativeClosePending.current = false; projectOperationPending.current = false; setSourceLocked(false);
      reportError(new BridgeError('NATIVE_CLOSE_FAILED', 'The window could not close. Your source is preserved; you can retry.'));
    };
    const unload = (event: BeforeUnloadEvent) => {
      if (documents.keys().some(key => { const document = documents.get(key); return document?.dirty || document?.saving; })) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('codeviz:close-request', close); window.addEventListener('codeviz:close-failed', failed); window.addEventListener('beforeunload', unload);
    return () => { window.removeEventListener('codeviz:close-request', close); window.removeEventListener('codeviz:close-failed', failed); window.removeEventListener('beforeunload', unload); };
  }, [documents, guardSources, reportError]);
  useEffect(() => {
    window.__codevizCloseReady = connection === 'ready';
    return () => { window.__codevizCloseReady = false; };
  }, [connection]);
  const hydrate = useCallback(async (s: Session, generation: number) => {
    if (generation !== projectGeneration.current) return false;
    localSourceTabs.current.clear();
    const changedProject = !cameraProject.current || cameraProject.current.path !== s.projectPath;
    if (changedProject) {
      cameraProject.current = { path: s.projectPath, token: (cameraProject.current?.token ?? 0) + 1 };
      viewportContexts.current = {}; setGraph(null); setGraphContext('');
    }
    restored.current = false; selectionRequest.current++; sessionRef.current = s; setSession(s); setOverview(null); setDetails({}); setNode(null); setInspector(null); setDetailSource(null); setProblems([]);
    const state = await call('get_view_state');
    if (generation !== projectGeneration.current) return false;
    if (changedProject) for (const kind of Object.keys(state.viewportByView) as ViewKind[]) {
      viewportContexts.current[kind] = JSON.stringify([s.projectPath, cameraProject.current?.token, kind, state.graphRoots[kind] ?? null]);
    }
    receiveState(state); dispatch({ type: 'restore', state: workspaceFromPython(state) }); restored.current = true; setRevision(v => v + 1);
    // Reopening the same path rotates the native project epoch. Publish the
    // explorer identity only after this hydration completes and remains current.
    setExplorerContext(previous => ({ path: s.projectPath, token: (previous?.token ?? 0) + 1 }));
    return true;
  }, [receiveState]);
  const connect = useCallback(async () => {
    const generation = ++projectGeneration.current;
    setConnection('waiting'); setError('');
    try { await waitForBridge(); const [s, prefs] = await Promise.all([call('get_session'), call('get_preferences')]); if (generation !== projectGeneration.current) return; setLanguage(prefs.language); if (prefs.issue) setError(prefs.issue); if (await hydrate(s, generation)) setConnection('ready'); } catch (e) { if (generation === projectGeneration.current) { reportError(e); setConnection('error'); } }
  }, [hydrate, reportError]);
  useEffect(() => { void connect(); }, [connect]);
  useEffect(() => { document.documentElement.lang = language; }, [language]);
  useEffect(() => {
    if (!restored.current || connection !== 'ready' || !session?.projectPath || !session.hasAnalysis || busy || session.status === 'Analyzing') return;
    const layout = layoutSnapshot(workspace); layoutTimer.current = setTimeout(() => { layoutTimer.current = undefined; void write(() => call('save_layout', layout)); }, 150);
    return () => { clearTimeout(layoutTimer.current); layoutTimer.current = undefined; };
  }, [workspace, connection, session?.projectPath, session?.status, session?.hasAnalysis, busy, write]);
  useEffect(() => {
    if (!analyzed || connection !== 'ready' || busy) return;
    let live = true;
    Promise.all([call('get_overview'), call('get_problems')]).then(([o, p]) => { if (live) { setOverview(o); setProblems(p); } }).catch(e => { if (live) reportError(e); });
    return () => { live = false; };
  }, [analyzed, connection, revision, busy, reportError]);
  const assistantReady = assistantBinding !== null && assistantBinding.path === session?.projectPath
    && assistantBinding.token === explorerContext?.token;
  const assistantBlocked = !assistantReady || !analyzed || session?.status !== 'Ready' && session?.status !== 'Partial'
    || busy || sourceLocked;
  const projectUnderstanding = assistantReady && !assistantBlocked
    && assistantSettings?.configRevision === assistantSnapshot.state?.configRevision
    ? effectiveProjectUnderstanding(assistantSnapshot.state) : null;
  const understandingKey = projectUnderstanding ? `${projectUnderstanding.reference.artifactId}:${projectUnderstanding.reference.revision}` : '';
  const enhanced = projectUnderstanding !== null && (understandingLayer?.key !== understandingKey || understandingLayer.enhanced);
  const root = view ? workspace.roots[view] ?? null : null;
  const relationFocusMissing = (view === 'Symbol' || view === 'DataFlow') && !root && !workspace.selectedId;
  const cameraContext = JSON.stringify([session?.projectPath, cameraProject.current?.token, view, root]);
  const depthContext = JSON.stringify([session?.projectPath, view, root]);
  const initialChoice = overview?.projectPath === session?.projectPath && view === 'Architecture'
    ? overview?.models.find(choice => choice.id === root) : undefined;
  const rememberedDepth = initialDepths.current.get(depthContext);
  const structureDepth = structureQuery.context === cameraContext ? structureQuery.depth : (rememberedDepth ?? initialChoice?.initialStructureDepth ?? 1);
  useEffect(() => {
    if (view === 'Architecture' && initialChoice && !initialDepths.current.has(depthContext)) {
      const depth = initialChoice.initialStructureDepth ?? 1;
      initialDepths.current.set(depthContext, depth);
      setStructureQuery(previous => ({ ...previous, context: cameraContext, depth }));
    }
  }, [view, initialChoice, depthContext, cameraContext]);
  const controlMode = view === 'Architecture' || view === 'ProjectStructure' ? 'structure' : view === 'Symbol' || view === 'DataFlow' || view === 'Calls' && root !== null ? 'relations' : 'none';
  useEffect(() => {
    setStructureQuery(previous => {
      if (view) return previous.context === cameraContext ? previous : { ...previous, context: cameraContext, depth: initialDepths.current.get(depthContext) ?? 1 };
      // Non-graph tabs retain depth only while the same graph context remains open.
      const retained = workspace.tabs.some(item => item.kind === 'graph' && item.view &&
        JSON.stringify([session?.projectPath, cameraProject.current?.token, item.view, workspace.roots[item.view] ?? null]) === previous.context);
      return retained ? previous : { ...previous, context: '', depth: 1 };
    });
  }, [cameraContext, depthContext, view, session?.projectPath, workspace.tabs, workspace.roots]);
  const detailSignature = JSON.stringify(viewState?.detailIslands.filter(i => i.viewKind === view && graphContext === cameraContext && graph?.nodes.some(n => n.id === i.sourceNodeId)).map(i => [i.id, i.sourceNodeId]) ?? []);
  const localSelectionValid = detailSource === null || (graphContext === cameraContext && !!viewState?.detailIslands.some(island =>
    island.viewKind === view && island.sourceNodeId === detailSource && graph?.nodes.some(n => n.id === island.sourceNodeId)));
  useEffect(() => {
    if (detailSource === null || localSelectionValid) return;
    selectionRequest.current++; setInspector(null); setDetailSource(null);
    setNode(graphContext === cameraContext ? graph?.nodes.find(n => n.id === workspaceRef.current.selectedId) ?? null : null);
  }, [detailSource, localSelectionValid, graph, graphContext, cameraContext]);
  useEffect(() => { setHops(1); setDirection('both'); }, [view, root]);
  useEffect(() => {
    setNode(null); setInspector(null); setDetailSource(null); selectionRequest.current++;
    if (!view || !analyzed || busy) { setGraphLoading(false); return; }
    if (relationFocusMissing) { setGraph(null); setGraphContext(''); setDetails({}); setQueryFeedback(null); setGraphLoading(false); return; }
    let live = true; setGraphLoading(true); setQueryFeedback(null);
    const selected = workspaceRef.current.selectedId;
    const request = view === 'Architecture' || view === 'ProjectStructure'
      ? call('get_view', view, root, selected, hops, direction, structureDepth)
      : call('get_view', view, root, selected, hops, direction);
    request.then(g => {
      if (!live) return;
      const previous = completedGraph.current?.context === cameraContext ? completedGraph.current.graph : null;
      const before = new Set(previous?.nodes.map(n => n.id) ?? []); const after = new Set(g.nodes.map(n => n.id));
      setQueryFeedback({ context: cameraContext, added: previous ? g.nodes.filter(n => !before.has(n.id)).length : 0, removed: previous ? previous.nodes.filter(n => !after.has(n.id)).length : 0, compared: previous !== null, failed: false });
      completedGraph.current = { context: cameraContext, graph: g };
      setGraph(g); setGraphContext(cameraContext);
      const selected = workspaceRef.current.selectedId; const n = g.nodes.find(n => n.id === selected);
      if (n) setNode(n);
      else if (selected && (view === 'Architecture' || view === 'ProjectStructure' || view === 'Calls')) dispatch({ type: 'select', id: null });
      void call('get_problems').then(p => { if (live) setProblems(p); }).catch(e => { if (live) reportError(e); });
    }).catch(e => { if (live) { setQueryFeedback({ context: cameraContext, added: 0, removed: 0, compared: false, failed: true }); reportError(e); } }).finally(() => { if (live) setGraphLoading(false); });
    return () => { live = false; };
  }, [view, root, analyzed, revision, busy, hops, direction, structureDepth, structureQuery.token, cameraContext, relationFocusMissing, reportError]);
  useEffect(() => {
    if (!view || !graph || !viewState || graphContext !== cameraContext) return;
    let live = true;
    const islands = viewState.detailIslands.filter(i => i.viewKind === view && graph.nodes.some(n => n.id === i.sourceNodeId));
    Promise.all(islands.map(async island => [island.id, await call('get_detail_view', view, root, island.sourceNodeId)] as const)).then(items => { if (live) setDetails(Object.fromEntries(items)); }).catch(e => { if (live) reportError(e); });
    return () => { live = false; };
  }, [view, root, graph, graphContext, cameraContext, detailSignature, revision, reportError]);
  useEffect(() => {
    if (!node || !view || busy || !localSelectionValid) { setInspector(null); return; }
    let live = true; const request = ++selectionRequest.current; setInspector(null); setInspectorLoading(true); setInspectorMissing(false);
    hasMethod('get_inspector').then(async available => { if (!live || request !== selectionRequest.current) return; if (!available) { if (live) setInspectorMissing(true); return; } const value = await (view === 'Architecture' || view === 'ProjectStructure' ? call('get_inspector', view, root, node.id, detailSource, graph?.queryContext.structureDepth ?? structureDepth) : call('get_inspector', view, root, node.id, detailSource)); if (live && request === selectionRequest.current) setInspector(value); }).catch(e => { if (live) reportError(e); }).finally(() => { if (live) setInspectorLoading(false); });
    return () => { live = false; };
  }, [node, view, root, detailSource, localSelectionValid, busy, graph?.queryContext.structureDepth, structureDepth, reportError]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      const target = event.target instanceof HTMLElement ? event.target : null;
      const focusedSide = Boolean(target?.closest('.side-editor'));
      const editing = Boolean(target?.closest('input,textarea,select,button,summary,.monaco-editor,[role="textbox"],[role="tree"],[role="menu"],[role="dialog"],[contenteditable]:not([contenteditable="false"])'));
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'w') {
        event.preventDefault();
        if (focusedSide) void changeSide(null);
        else if (workspaceRef.current.active) void closeSourceTab(workspaceRef.current.active);
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        const anchor = focusedSide ? workspaceRef.current.side : workspaceRef.current.tabs.find(item => item.id === workspaceRef.current.active)?.anchor;
        if (anchor) { event.preventDefault(); void saveSource(keyFor(anchor)); }
      }
      if (event.key === 'Enter' && !editing && node && view && !detailSource) { event.preventDefault(); void write(() => call('open_detail_island', view, node.id, null)); }
    };
    window.addEventListener('keydown', key, true); return () => window.removeEventListener('keydown', key, true);
  }, [node, view, detailSource, write, closeSourceTab, changeSide, saveSource, keyFor]);
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
    setSourceLocked(true);
    if (!await guardSources(documents.keys())) { projectOperationPending.current = false; setSourceLocked(settingsPending.current); return; }
    releaseSources();
    const flushLayout = connection === 'ready' && sessionRef.current?.projectPath && sessionRef.current.hasAnalysis && sessionRef.current.status !== 'Analyzing' ? layoutSnapshot(workspaceRef.current) : null;
    clearTimeout(layoutTimer.current); layoutTimer.current = undefined;
    const generation = ++projectGeneration.current;
    setBusy(true); setError(''); setLayoutWarning(''); restored.current = false;
    setStructureQuery(previous => ({ context: '', depth: 1, token: previous.token + 1 }));
    try {
      await enqueue(async () => {
        if (flushLayout) {
          try { await call('save_layout', flushLayout); }
          catch (e) {
            // Obsolete layouts can fail backend validation. Do not retry this
            // snapshot after the explicit operation changes the project.
            if (generation === projectGeneration.current) setLayoutWarning(e instanceof BridgeError ? `${e.code}: ${e.message}` : e instanceof Error ? e.message : String(e));
          }
        }
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
    } finally { if (generation === projectGeneration.current) { pollEpoch.current++; projectOperationPending.current = false; setBusy(false); setSourceLocked(settingsPending.current); } }
  }
  const saveAnalysisSettings = useCallback(async (payload: { ignorePaths: string[] }): Promise<AnalysisSettings> => {
    const expectedPath = sessionRef.current?.projectPath;
    const generation = projectGeneration.current;
    if (projectOperationPending.current) return Promise.reject(new BridgeError('NOT_READY', 'Wait for the project operation to finish.'));
    if (settingsPending.current) throw new BridgeError('NOT_READY', 'Wait for analysis settings to finish.');
    settingsPending.current = true; setSourceLocked(true);
    if (!await guardSources(documents.keys())) { settingsPending.current = false; setSourceLocked(false); throw new BridgeError('SOURCE_CANCELLED', 'Source navigation cancelled.'); }
    releaseSources();
    const layout = expectedPath && sessionRef.current?.hasAnalysis && sessionRef.current.status !== 'Analyzing' ? layoutSnapshot(workspaceRef.current) : null;
    clearTimeout(layoutTimer.current); layoutTimer.current = undefined;
    return enqueue(async () => {
      if (sessionRef.current?.projectPath !== expectedPath) throw new BridgeError('NOT_READY', 'The project changed before settings could be saved.');
      if (layout) await call('save_layout', layout);
      const result = await call('set_analysis_settings', payload);
      if (generation === projectGeneration.current) { const s = await refreshSession(generation); if (s) await hydrate(s, generation); }
      return result;
    }).finally(() => { settingsPending.current = false; setSourceLocked(false); });
  }, [enqueue, hydrate, refreshSession, guardSources, documents, releaseSources]);
  function openTab(tab: Tab) {
    if (nativeClosePending.current) return;
    if (projectOperationPending.current || guardPending.current) return;
    if (!workspaceRef.current.tabs.some(item => item.id === tab.id) && workspaceRef.current.tabs.length >= 40) { setError(t('tabLimit')); return; }
    dispatch({ type: 'open', tab });
  }
  function openView(kind: ViewKind, selectedRoot?: string) {
    if (projectOperationPending.current || guardPending.current) return;
    if (!workspaceRef.current.tabs.some(item => item.id === kind) && workspaceRef.current.tabs.length >= 40) { setError(t('tabLimit')); return; }
    if (selectedRoot) dispatch({ type: 'reveal', view: kind, root: selectedRoot, selected: selectedRoot });
    else if (kind === 'Calls') dispatch({ type: 'reveal', view: kind, root: null, selected: null });
    else openTab(viewTab(kind));
  }
  const openSource = useCallback((anchor: Anchor) => {
    if (projectOperationPending.current || guardPending.current) return;
    if (!workspaceRef.current.tabs.some(item => item.id === sourceTab(anchor).id) && workspaceRef.current.tabs.length >= 40) { setError(translator(language)('tabLimit')); return; }
    const source = sourceTab(anchor);
    if (detailSource !== null && graphContext === cameraContext) {
      localSourceTabs.current.add(source.id);
    }
    dispatch({ type: 'open', tab: source });
  }, [detailSource, graphContext, cameraContext, language]);
  const closeIsland = useCallback((id: string) => { void write(() => call('close_detail_island', id)); }, [write]);
  function openAssistant(context: AssistantContext = assistantContext) {
    if (nativeClosePending.current || projectOperationPending.current || guardPending.current) return;
    setAssistantContext(context); setRightPanel('assistant'); setInspectorVisible(true);
    void assistantStore.openPanel();
  }
  const assistantDirty = documents.keys().some(key => {
    const document = documents.get(key);
    return Boolean(document && document.projectPath === session?.projectPath && (document.dirty || document.saving));
  });
  const primaryUnderstandingView = tab?.kind === 'Overview';
  const workspaceOrigin = primaryUnderstandingView && enhanced && projectUnderstanding && tab
    ? JSON.stringify([tab.id, view ? workspace.roots[view] ?? null : null, understandingKey]) : null;
  workspaceNavContext.current = workspaceOrigin && projectUnderstanding ? { origin: workspaceOrigin, model: projectUnderstanding } : null;
  useEffect(() => { workspaceNavigation.invalidate(); }, [workspaceNavigation, workspaceOrigin]);
  workspaceNavConsume.current = navigation => {
    const context = currentWorkspaceNavigation(workspaceNavContext.current, assistantStore.getSnapshot().state);
    if (!context || nativeClosePending.current || projectOperationPending.current || guardPending.current
      || !sessionRef.current?.projectPath) return;
    const reference = context.model.reference;
    const current: NavigationContext = { projectPath: sessionRef.current.projectPath, projectEpoch: reference.projectEpoch,
      analysisGeneration: reference.analysisGeneration, configRevision: reference.configRevision,
      consentRevision: reference.consentRevision, conversationId: reference.conversationId,
      conversationRevision: reference.conversationRevision, stale: false };
    if (navigation.action === 'source') {
      const file = navigation.target?.astAnchor.filePath;
      const document = file ? documents.get(sourceDocumentKey(current.projectPath, file)) ?? null : null;
      const intent = sourceNavigationIntent(navigation, current, document);
      if (intent?.openAnchor) openSource(intent.openAnchor);
      else if (intent?.reason === 'dirty_source') setError(t('aiDirty'));
    } else {
      const intent = graphNavigationIntent(navigation, current);
      if (intent?.kind === 'graph') {
        setUnderstandingLayer({ key: understandingKey, enhanced: false });
        dispatch({ type: 'reveal', view: intent.reveal.viewKind, root: intent.reveal.rootId, selected: intent.reveal.selectedId });
        setRevision(value => value + 1);
      }
    }
  };
  const currentUnderstandingAction = () => {
    const displayed = workspaceOrigin && projectUnderstanding ? { origin: workspaceOrigin, model: projectUnderstanding } : null;
    const current = currentWorkspaceNavigation(displayed, assistantStore.getSnapshot().state);
    return current !== null && current.origin === workspaceNavContext.current?.origin
      && !assistantBlocked && !nativeClosePending.current && !projectOperationPending.current && !guardPending.current;
  };
  const navigateUnderstanding = (itemId: string, evidenceId: string, action: 'source' | 'graph') => {
    if (!currentUnderstandingAction() || understandingNavigationBlocked) return;
    void workspaceNavigation.resolve(itemId, evidenceId, action).catch(reportError);
  };
  const understandingNavigationBlocked = assistantBlocked || workspaceNavigationState.busy
    || assistantSnapshot.sending || assistantSnapshot.jobs.some(job => !assistantJobReleased(job));
  const understandingLayerControls = projectUnderstanding && primaryUnderstandingView && <div className="assistant-actions project-layer-controls"
    role="group" aria-label={t('aiProjectUnderstanding')}>
    <button aria-pressed={!enhanced} onClick={() => setUnderstandingLayer({ key: understandingKey, enhanced: false })}>{t('aiStaticLayer')}</button>
    <button aria-pressed={enhanced} onClick={() => setUnderstandingLayer({ key: understandingKey, enhanced: true })}>{t('aiEnhancedLayer')}</button>
  </div>;
  const askUnderstanding = (itemId: string) => {
    if (!projectUnderstanding || !currentUnderstandingAction() || understandingNavigationBlocked) return;
    const context: AssistantContext = { kind: 'understanding', artifactId: projectUnderstanding.reference.artifactId,
      revision: projectUnderstanding.reference.revision, itemId };
    if (!understandingContextAvailable(context, assistantStore.getSnapshot().state)) {
      reportError(new BridgeError('AI_RESULT_UNAVAILABLE', t('aiUnderstandingExpired'))); return;
    }
    setAssistantQuestionDraft({ key: ++assistantQuestionSerial.current, context,
      text: t(itemId === 'project' ? 'aiProjectQuestionPrompt' : 'aiDetailQuestionPrompt') });
    openAssistant(context);
  };
  const navigateAssistant = useCallback((navigation: NavigationPoll) => {
    // Capture authority and dirty state at consumption, never when a card was rendered.
    const snapshot = assistantStore.getSnapshot(); const state = snapshot.state;
    const chat = state?.conversations.find(value => value.conversationId === snapshot.selectedConversationId);
    if (!snapshot.visible || !inspectorVisible || rightPanel !== 'assistant' || !state || !chat || !state.grant?.enabled || !sessionRef.current?.projectPath || assistantBlocked
      || nativeClosePending.current || projectOperationPending.current || guardPending.current
      || assistantBinding?.epoch !== state.projectEpoch || assistantSettings?.configRevision !== state.configRevision) return;
    const current: NavigationContext = { projectPath: sessionRef.current.projectPath,
      projectEpoch: state.projectEpoch, analysisGeneration: state.analysisGeneration ?? -1,
      configRevision: state.configRevision, consentRevision: state.grant.consentRevision,
      conversationId: chat.conversationId, conversationRevision: chat.conversationRevision, stale: false };
    if (navigation.action === 'source') {
      const file = navigation.target?.astAnchor.filePath;
      const document = file ? documents.get(sourceDocumentKey(current.projectPath, file)) ?? null : null;
      const intent = sourceNavigationIntent(navigation, current, document);
      if (intent?.openAnchor) openSource(intent.openAnchor);
      else if (intent?.reason === 'dirty_source') setError(translator(language)('aiDirty'));
    } else {
      const intent = graphNavigationIntent(navigation, current);
      if (intent?.kind === 'graph') { dispatch({ type: 'reveal', view: intent.reveal.viewKind,
        root: intent.reveal.rootId, selected: intent.reveal.selectedId }); setRevision(value => value + 1); }
    }
  }, [assistantStore, inspectorVisible, rightPanel, assistantBlocked, assistantBinding, assistantSettings, documents, openSource, language]);
  function reveal(result: SearchResult) { const destination = result.revealTarget; if (!destination.viewKind) { setError(destination.reason ?? 'No graph destination'); openSource(result.sourceAnchor); return; } dispatch({ type: 'reveal', view: destination.viewKind, root: destination.rootId, selected: destination.selectedId }); setRevision(n => n + 1); }
  async function changeLanguage(value: Language) { try { const p = await call('set_preferences', { language: value }); setLanguage(p.language); if (p.issue) reportError(p.issue); } catch (e) { reportError(e); } }
  function detail(item: VisibleNode) { if (view) void write(() => call('open_detail_island', view, item.id, null)); }
  function changeRelationDirection(value: 'in' | 'out' | 'both') {
    if (value === direction) return;
    if (view === 'Calls' && root !== null) dispatch({ type: 'select', id: root });
    setDirection(value);
  }
  function changeRelationHops(delta: number) {
    if (view === 'Calls' && root !== null) dispatch({ type: 'select', id: root });
    setHops(value => Math.max(1, Math.min(5, value + delta)));
  }
  function changeStructure(delta: number) {
    const depth = graph?.queryContext.structureDepth ?? structureDepth;
    // Consume even a not-yet-arrived hint on an explicit action. Only an explicit
    // return to one changes the remembered initial depth; ordinary exploration
    // still resets on root changes and project hydration.
    if (!initialDepths.current.has(depthContext) || depth + delta <= 1) initialDepths.current.set(depthContext, 1);
    if (delta < 0) { selectionRequest.current++; setNode(null); setInspector(null); setDetailSource(null); dispatch({ type: 'select', id: null }); }
    setStructureQuery(previous => ({ context: cameraContext, depth: Math.max(1, Math.min(5, depth + delta)), token: previous.token + 1 }));
  }
  const feedback = graphLoading ? t('updatingGraph') : queryFeedback?.context !== cameraContext ? '' : queryFeedback.failed ? t('graphUpdateFailed') : [
    queryFeedback.compared && (queryFeedback.added || queryFeedback.removed) ? `${t('addedNodes')}: ${queryFeedback.added} · ${t('removedNodes')}: ${queryFeedback.removed}` : queryFeedback.compared ? t('graphUnchanged') : t('graphUpdated'),
    graph?.queryContext.budgetTruncated ? t('graphTruncated') : controlMode === 'structure' ? graph?.queryContext.depthLimit ? t('depthMaximum') : graph?.queryContext.canExpand === false ? t('noDeeper') : '' : '',
  ].filter(Boolean).join(' · ');
  const inspectorIsland = viewState?.detailIslands.find(i => i.viewKind === view && i.sourceNodeId === detailSource);
  const inspectorDetail = inspectorIsland ? details[inspectorIsland.id] ?? null : null;
  const inspectorPanel = <Inspector graph={graphContext === cameraContext ? graph : null} detailGraph={inspectorDetail} node={node} data={inspector} missing={inspectorMissing} loading={inspectorLoading} openSource={openSource} openSide={anchor => { void changeSide(anchor); }} openDetail={() => { if (node && !detailSource) detail(node); }} onExplain={() => { if (view && node) openAssistant({ kind: 'node', viewKind: view, visibleNodeId: node.id, rootId: root, detailSourceId: detailSource }); }} expand={collapse => { if (node) void write(() => call(collapse ? 'collapse' : 'expand', node.id)).then(() => setRevision(v => v + 1)); }} t={t}/>;
  const settings = <SettingsPanel assistantStore={assistantStore} onAssistantSettings={value => { setAssistantSettings(value); const epoch = assistantStore.getSnapshot().state?.projectEpoch; if (epoch) void assistantStore.restore(epoch).catch(() => {}); }} language={language} session={session} onLanguage={value => { void changeLanguage(value); }} saveAnalysisSettings={saveAnalysisSettings} onError={reportError} t={t}/>;
  return <AssistantRuntimeBoundary store={assistantStore} getContext={() => { const context = assistantSendContext.current ?? assistantContext; assistantSendContext.current = null; return context; }}><div className="desktop-shell"><header className="titlebar"><strong>CODE<span>VIZ</span></strong><span className="titlebar-path">{session?.projectPath ?? t('local')}</span><button aria-label={t('aiAssistant')} onClick={() => openAssistant()} disabled={connection !== 'ready'}>{t('aiAssistant')}</button><span className="native-badge"><i className="signal"/> LOCAL ENGINE</span></header><div className="shell-body"><ActivityBar activity={activity} t={t} onChange={value => { if (projectOperationPending.current || guardPending.current) return; setActivity(current => current === value ? null : value); if (value === 'settings') openTab({ id: 'settings', kind: 'settings' }); }}/>{activity !== null && <aside className="sidebar">{activity === 'search' ? <SearchPanel key={session?.projectPath} enabled={analyzed && !busy} onReveal={reveal} onSource={r => openSource(r.sourceAnchor)} onError={reportError} t={t}/> : activity === 'problems' ? <><div className="panel-heading">{t('problems')}</div><Problems problems={problems} onSource={openSource} t={t}/></> : <Sidebar session={session} active={workspace.active} openView={openView} openPath={value => { void runProject(() => call('open_project', value)); }} choose={() => { void runProject(() => call('choose_project')); }} analyze={() => { void runProject(() => call('analyze')); }} busy={busy || (sourceLocked && !settingsPending.current) || connection !== 'ready'} explorer={session?.projectPath && explorerContext && explorerContext.path === session.projectPath && connection === 'ready' ? <ProjectExplorer key={JSON.stringify([explorerContext.path, explorerContext.token])} projectPath={session.projectPath} caseSensitive={session.fileSystemCaseSensitive ?? true} locked={sourceLocked || busy} openSource={openSource} openSide={anchor => { void changeSide(anchor); }} mutate={mutateProjectEntry} t={t}/> : null} t={t}/>}</aside>}<main className="workbench">{analyzed && <ProjectAnalysisControls store={assistantStore} settings={assistantSettings} blocked={assistantBlocked} dirty={assistantDirty} prepare={prepareAssistantContext} configure={() => openTab({ id: 'settings', kind: 'settings' })} authorize={() => openAssistant({ kind: 'project' })} t={t}/>}{layoutWarning && <div className="outdated-banner" role="status"><span>{t('layoutNotSaved')} {layoutWarning}</span><button onClick={() => setLayoutWarning('')} aria-label={t('close')}>×</button></div>}{understandingLayerControls}{(workspaceNavigationState.busy || workspaceNavigationState.issue) && <div className="workspace-navigation-status" role="status">{workspaceNavigationState.busy && <><span>{t('aiWorkspaceNavigating')}</span><button onClick={() => { void workspaceNavigation.stop(); }}>{t('aiStop')}</button></>}{workspaceNavigationState.issue && <p>{assistantIssue(workspaceNavigationState.issue, t)}</p>}</div>}<EditorTabs tabs={workspace.tabs} active={workspace.active} onOpen={openTab} onClose={id => { void closeSourceTab(id); }} isDirty={tab => Boolean(tab.anchor && documents.get(keyFor(tab.anchor))?.dirty)} t={t}/>{error && <div className="error-banner" role="alert"><span>{error}</span><button onClick={() => setError('')} aria-label={t('close')}>×</button></div>}{view && documents.keys().some(key => documents.get(key)?.dirty) && <div className="source-notice" role="status">{t('sourceStale')}</div>}{session?.status === 'Outdated' && <div className="outdated-banner">{t('outdated')} <button disabled={busy} onClick={() => { void runProject(() => call('analyze')); }}>{t('reanalyze')}</button></div>}{session?.status === 'Analyzing' && <div className="analysis-progress" role="status">{t('Analyzing')}…</div>}<div className="editor-area">{connection !== 'ready' ? <div className="welcome"><div className="welcome-mark">CV</div><h1>{connection === 'waiting' ? t('waiting') : t('desktop')}</h1>{connection === 'error' && <button className="primary" onClick={() => { void connect(); }}>{t('retry')}</button>}</div> : <><div className="primary-editor">
{tab?.kind === 'settings' ? settings : !session?.projectPath ? <div className="welcome"><div className="welcome-mark">CV</div><h1>{t('empty')}</h1><button className="primary" disabled={busy} onClick={() => { void runProject(() => call('choose_project')); }}>{t('open')}</button><form className="path-form" onSubmit={e => { e.preventDefault(); if (path.trim()) void runProject(() => call('open_project', path.trim())); }}><input aria-label={t('path')} placeholder={t('path')} value={path} onChange={e => setPath(e.target.value)}/><button disabled={busy || !path.trim()}>{t('open')}</button></form></div> : !tab ? <div className="welcome"><p>{t('noTabs')}</p></div> : tab.kind === 'source' && tab.anchor ? <SourceViewer key={tab.id} anchor={tab.anchor} t={t} projectPath={session.projectPath} store={documents} locked={sourceLocked || busy} onSave={saveSource} outdated={session.status === 'Outdated'} onExplain={mode => openAssistant({ kind: 'source', filePath: tab.anchor!.filePath, anchor: tab.anchor!, mode })}/> : !analyzed ? <div className="welcome"><h1>{t(session.status)}</h1><button className="primary" disabled={busy} onClick={() => { void runProject(() => call('analyze')); }}>{t('analyze')}</button></div> : tab.kind === 'Overview' ? <Overview data={overview} openView={openView} openSource={openSource} t={t} model={enhanced ? projectUnderstanding : null} disabled={understandingNavigationBlocked} navigate={navigateUnderstanding} onAsk={askUnderstanding}/> : relationFocusMissing ? <div className="welcome"><p>{t('relationFocusRequired')}</p><button className="primary" onClick={() => setActivity('search')}>{t('search')}</button></div> : graph && view ? <><div className="graph-workspace" style={{ display: graphContext === cameraContext ? undefined : 'none' }}>{view === 'FunctionFlow' && <p className="static-flow-note">{t('staticFlow')}</p>}{view === 'Calls' && <p className="static-flow-note">{t(root === null ? 'callsOverviewNote' : 'callsFocusedNote')}</p>}{view === 'Calls' && graphContext === cameraContext && graph.nodes.length === 0 && <p role="status" className="padded">{t('callsEmpty')}</p>}<p className="static-flow-note">{controlMode === 'structure' ? t(view === 'ProjectStructure' ? 'projectStructureNote' : 'architectureNote') : ''}{controlMode === 'structure' && <> · {t('root')}: {graph.nodes.find(n => n.id === root || graph.rootIds.includes(n.id))?.codeName ?? graph.nodes.find(n => graph.rootIds.includes(n.id))?.displayName ?? session.projectPath} · {t('structure')}: {graph.queryContext.structureDepth ?? structureDepth}/5</>}</p><p className="query-feedback" role="status" aria-live="polite">{feedback}</p>{view === 'Architecture' && graph.nodes.length === 0 && <div className="padded"><p>{t('noModels')}</p><button onClick={() => openView('ProjectStructure')}>{t('ProjectStructure')}</button></div>}<QueryToolbar calls={view === 'Calls' ? { focused: root !== null, canFocus: Boolean(node && localSelectionValid && graphContext === cameraContext && !graphLoading && node.codeNodeIds.length === 1 && node.codeNodeIds[0] === node.id && !node.id.startsWith('aggregate:Calls:')), focus: () => { if (node && localSelectionValid && graphContext === cameraContext && node.codeNodeIds.length === 1 && node.codeNodeIds[0] === node.id && !node.id.startsWith('aggregate:Calls:')) openView('Calls', node.id); }, overview: () => openView('Calls') } : undefined} mode={controlMode} depth={graph.queryContext.structureDepth ?? structureDepth} canExpand={graph.queryContext.canExpand} depthLimit={graph.queryContext.depthLimit} budgetTruncated={graph.queryContext.budgetTruncated} direction={direction} hops={hops} setDirection={changeRelationDirection} more={() => controlMode === 'structure' ? changeStructure(1) : changeRelationHops(1)} fewer={() => controlMode === 'structure' ? changeStructure(-1) : changeRelationHops(-1)} disabled={busy || graphLoading} t={t} inspectorVisible={inspectorVisible} toggleInspector={() => { setInspectorVisible(v => !v); if (inspectorVisible) void assistantStore.closePanel(); else if (rightPanel === 'assistant') void assistantStore.openPanel(); }}/><GraphEditor cameraContext={graphContext} restoreViewport={viewportContexts.current[view] === graphContext} graph={graph} viewState={viewState ? { ...viewState, detailIslands: viewState.detailIslands.filter(i => i.viewKind === graph.viewKind && graph.nodes.some(n => n.id === i.sourceNodeId)) } : null} details={details} selectedId={graphContext === cameraContext && detailSource !== null && localSelectionValid ? node?.id ?? null : workspace.selectedId} onSelect={(item, source) => { if (graphContext !== cameraContext) return; setNode(item); setDetailSource(source); if (source === null) dispatch({ type: 'select', id: item.id }); }} onDetail={detail} onMove={(id, position, island) => { void write(() => island ? call('move_detail_island', id, position) : call('move_node', id, position, view)); }} onViewport={(kind, viewport) => { if (graphContext !== cameraContext || busy) return; viewportContexts.current[kind] = graphContext; void write(() => call('save_viewport', kind, viewport.zoom, { x: viewport.x, y: viewport.y })); }} onCloseIsland={closeIsland} t={t}/></div>{graphContext !== cameraContext && <div className="welcome"><p>{graphLoading ? t('waiting') : t('select')}</p></div>}</> : <div className="welcome"><p>{graphLoading ? t('waiting') : t('select')}</p></div>}</div>{workspace.side && <section className="side-editor"><div className="side-title">{t('source')}<button onClick={() => { void changeSide(null); }} aria-label={t('close')}>×</button></div><SourceViewer anchor={workspace.side} t={t} projectPath={session?.projectPath ?? undefined} store={documents} locked={sourceLocked || busy} onSave={saveSource} outdated={session?.status === 'Outdated'} onExplain={mode => { if (workspace.side) openAssistant({ kind: 'source', filePath: workspace.side.filePath, anchor: workspace.side, mode }); }}/></section>}{inspectorVisible && (assistantSettings ? <aside className="right-panel"><div role="tablist" aria-label={t('inspector')} className="right-panel-tabs" onKeyDown={event => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role=tab]')); const index = tabs.indexOf(document.activeElement as HTMLButtonElement); if (index < 0) return; event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length; tabs[next]?.focus(); tabs[next]?.click(); }}><button role="tab" tabIndex={rightPanel === 'inspector' ? 0 : -1} aria-selected={rightPanel === 'inspector'} onClick={() => { setRightPanel('inspector'); void assistantStore.closePanel(); }}>{t('inspector')}</button><button role="tab" tabIndex={rightPanel === 'assistant' ? 0 : -1} aria-selected={rightPanel === 'assistant'} onClick={() => openAssistant(assistantContext)}>{t('aiAssistant')}</button><button aria-label={`${t('close')} ${t('aiAssistant')}`} onClick={() => { setInspectorVisible(false); void assistantStore.closePanel(); }}>×</button></div><div hidden={rightPanel !== 'assistant'}>{assistantReady ? <AssistantPanel questionDraft={assistantQuestionDraft} prepareContext={prepareAssistantContext} store={assistantStore} settings={assistantSettings} context={assistantContext} blocked={assistantBlocked} dirty={assistantDirty} onSettings={() => openTab({ id: 'settings', kind: 'settings' })} onNavigate={navigateAssistant} t={t}/> : <div className="padded"><p className="muted">{t('AI_ANALYSIS_REQUIRED')}</p><button onClick={() => openTab({ id: 'settings', kind: 'settings' })}>{t('aiSettings')}</button></div>}</div><div hidden={rightPanel !== 'inspector'}>{inspectorPanel}</div></aside> : view ? inspectorPanel : null)}</>}</div><div className="problems-heading"><button onClick={() => setShowProblems(v => !v)}>{showProblems ? '⌄' : '›'} {t('problems')} <span>{problems.length}</span></button><span>PYTHON ANALYSIS</span></div>{showProblems && <div className="problems-dock"><Problems problems={problems} onSource={openSource} t={t}/></div>}</main></div><footer className="statusbar"><span><i className="signal"/> {connection === 'ready' ? 'Python bridge' : t('waiting')}</span><span>{session ? t(session.status) : '—'}</span><span className="status-path">{session?.projectPath}</span><span>{language === 'en' ? 'EN' : '中文'} · UTF-8</span></footer>{sourceDecision && <div className="source-dialog-backdrop"><section className="source-dialog" role="dialog" aria-modal="true" aria-labelledby="source-dialog-title"><h2 id="source-dialog-title">{t('unsavedSource')}</h2><p>{t('unsavedSourcePrompt')}</p><ul>{sourceDecision.keys.map(key => <li key={key}>{documents.get(key)?.filePath}</li>)}</ul><div className="source-dialog-actions"><button className="primary" disabled={guardSaving} onClick={() => sourceDecision.resolve('save')}>{guardSaving ? t('savingSource') : t('saveSource')}</button><button disabled={guardSaving} onClick={() => sourceDecision.resolve('discard')}>{t('discardSource')}</button><button autoFocus disabled={guardSaving} onClick={() => sourceDecision.resolve('cancel')}>{t('cancelSource')}</button></div></section></div>}</div></AssistantRuntimeBoundary>;
}
