import type { Anchor, Layout, ViewKind, ViewState } from '../types';
import { viewKinds } from '../types';
export interface Tab { id: string; kind: 'Overview' | 'graph' | 'source' | 'settings'; view?: ViewKind; anchor?: Anchor; returnTabId?: string }
export interface Workspace { tabs: Tab[]; active: string | null; selectedId: string | null; roots: Layout['graphRoots']; side: Anchor | null }
export const initialState: Workspace = { tabs: [{ id: 'Overview', kind: 'Overview' }], active: 'Overview', selectedId: null, roots: {}, side: null };
export type Action = { type: 'open'; tab: Tab } | { type: 'close'; id: string } | { type: 'select'; id: string | null } | { type: 'side'; anchor: Anchor | null } | { type: 'reveal'; view: ViewKind; root: string | null; selected: string | null } | { type: 'restore'; state: Workspace };
export function reducer(state: Workspace, action: Action): Workspace {
  switch (action.type) {
    case 'open': {
      const existing = state.tabs.find(t => t.id === action.tab.id);
      const tab = action.tab.kind === 'source' ? { ...action.tab, returnTabId: state.active !== action.tab.id ? state.active ?? undefined : existing?.returnTabId } : action.tab;
      return { ...state, tabs: existing ? state.tabs.map(t => t.id === tab.id ? tab : t) : [...state.tabs, tab].slice(-40), active: tab.id, selectedId: tab.kind === 'graph' && tab.id !== state.active ? null : state.selectedId };
    }
    case 'close': {
      const index = state.tabs.findIndex(t => t.id === action.id); const closing = state.tabs[index];
      const tabs = state.tabs.filter(t => t.id !== action.id);
      const origin = closing?.kind === 'source' ? tabs.find(t => t.id === closing.returnTabId) : undefined;
      const active = state.active === action.id ? origin?.id ?? tabs[Math.max(0, index - 1)]?.id ?? null : state.active;
      const next = tabs.find(t => t.id === active);
      return { ...state, tabs, active, selectedId: active !== state.active && next?.kind === 'graph' ? null : state.selectedId };
    }
    case 'select': return { ...state, selectedId: action.id };
    case 'side': return { ...state, side: action.anchor };
    case 'reveal': return { ...reducer(state, { type: 'open', tab: viewTab(action.view) }), selectedId: action.selected, roots: { ...state.roots, [action.view]: action.root } };
    case 'restore': return action.state;
  }
}
export function viewTab(view: ViewKind): Tab { return { id: view, kind: view === 'Overview' ? 'Overview' : 'graph', view }; }
export function sourceTab(anchor: Anchor): Tab { return { id: `source:${encodeURIComponent(JSON.stringify(anchor))}`, kind: 'source', anchor }; }
export function workspaceFromPython(state: ViewState): Workspace {
  const tabs: Tab[] = state.openTabs.flatMap(id => {
    if (viewKinds.includes(id as ViewKind)) return [viewTab(id as ViewKind)];
    if (id === 'settings') return [{ id, kind: 'settings' } as Tab];
    if (id.startsWith('source:')) { try { const anchor: unknown = JSON.parse(decodeURIComponent(id.slice(7))); if (validAnchor(anchor)) return [sourceTab(anchor)]; } catch { /* Invalid or legacy source tab has no safe anchor. */ } }
    return [];
  });
  if (!tabs.length && !state.openTabs.length && state.activeView === null) return { ...initialState, roots: state.graphRoots, selectedId: state.selectedId };
  return { tabs, active: tabs.find(t => t.view === state.activeView)?.id ?? tabs.at(-1)?.id ?? null, selectedId: state.selectedId, roots: state.graphRoots, side: null };
}
export function layoutOf(state: Workspace): Layout { const tab = state.tabs.find(t => t.id === state.active); return { activeView: tab?.view ?? (tab?.kind === 'Overview' ? 'Overview' : null), selectedId: tab?.kind === 'graph' ? state.selectedId : null, openTabs: [...state.tabs.filter(t => t.id !== state.active).map(t => t.id), ...(tab ? [tab.id] : [])], graphRoots: state.roots }; }
export function validAnchor(a: unknown): a is Anchor { if (!a || typeof a !== 'object') return false; const x = a as Anchor; return typeof x.filePath === 'string' && Number.isInteger(x.startLine) && x.startLine > 0 && Number.isInteger(x.endLine) && x.endLine >= x.startLine && (x.startColumn === null || Number.isInteger(x.startColumn)) && (x.endColumn === null || Number.isInteger(x.endColumn)); }
