import { describe, expect, it } from 'vitest';
import { initialState, layoutOf, reducer, sourceTab, viewTab, workspaceFromPython } from './state';
import type { Anchor, ViewState } from '../types';
const anchor: Anchor = { filePath: '模型.py', startLine: 20, endLine: 22, startColumn: null, endColumn: null };
const backend: ViewState = { activeView: null, selectedId: null, openTabs: [], graphRoots: {}, expandedIds: [], positions: {}, positionsByView: {}, zoom: 1, pan: [0, 0], viewportByView: {}, detailIslands: [], filters: [] };
describe('workspace transitions and Python persistence', () => {
  it('closing Source returns to its Architecture origin with reverse cached tab order', () => {
    const cached: ViewState = { ...backend, activeView: 'Architecture', selectedId: 'projection:layer',
      openTabs: ['Architecture', 'Overview'], graphRoots: { Architecture: 'model' } };
    const graph = workspaceFromPython(cached);
    const source = sourceTab(anchor);
    const opened = reducer(graph, { type: 'open', tab: source });
    expect(opened.tabs.map(tab => tab.id)).toEqual(['Architecture', 'Overview', source.id]);
    const closed = reducer(opened, { type: 'close', id: source.id });
    expect(closed.active).toBe('Architecture');
    expect(closed.selectedId).toBeNull();
    expect(closed.roots).toEqual({ Architecture: 'model' });
    expect(closed.tabs.map(tab => tab.id)).toEqual(cached.openTabs);
    expect(cached.selectedId).toBe('projection:layer');
  });

  it.each([sourceTab(anchor), viewTab('Overview'), { id: 'settings', kind: 'settings' } as const])(
    'serializes non-graph tab $kind with null selection using the unchanged layout schema', tab => {
      const cached: ViewState = { ...backend, activeView: 'Architecture', selectedId: 'projection:layer',
        openTabs: ['Architecture', 'Overview'], graphRoots: { Architecture: 'model' } };
      const graph = workspaceFromPython(cached);
      const opened = reducer(graph, { type: 'open', tab });
      expect(opened.selectedId).toBe('projection:layer');
      const layout = layoutOf(opened);
      expect(layout).toEqual({
        activeView: tab.kind === 'Overview' ? 'Overview' : null,
        selectedId: null,
        openTabs: [...opened.tabs.filter(item => item.id !== opened.active).map(item => item.id), tab.id],
        graphRoots: { Architecture: 'model' },
      });
      expect(JSON.parse(JSON.stringify(layout))).toEqual(layout);
      expect(workspaceFromPython({ ...cached, ...layout }).roots).toEqual(cached.graphRoots);
      expect(graph.selectedId).toBe('projection:layer');
      expect(cached.openTabs).toEqual(['Architecture', 'Overview']);
    });

  it('clears a previous graph selection when opening a different graph', () => { const state = reducer(initialState, { type: 'reveal', view: 'Architecture', root: 'model', selected: 'layer' }); const next = reducer(state, { type: 'open', tab: viewTab('Execution') }); expect(next.selectedId).toBeNull(); expect(next.roots.Architecture).toBe('model'); });
  it('opens Overview, Architecture, Execution and Source without duplicate tabs', () => { let state = initialState; for (const view of ['Overview', 'Architecture', 'Execution'] as const) state = reducer(state, { type: 'open', tab: viewTab(view) }); state = reducer(state, { type: 'open', tab: sourceTab(anchor) }); state = reducer(state, { type: 'open', tab: sourceTab(anchor) }); expect(state.tabs).toHaveLength(4); expect(state.active).toBe(sourceTab(anchor).id); });
  it('opens source to side without replacing the graph', () => { const graph = reducer(initialState, { type: 'open', tab: viewTab('Architecture') }); const state = reducer(graph, { type: 'side', anchor }); expect(state.active).toBe('Architecture'); expect(state.side).toEqual(anchor); expect(state.tabs).toEqual(graph.tabs); });
  it('closing the active tab activates its left neighbor and closing final tab is safe', () => { const state = reducer(initialState, { type: 'open', tab: viewTab('Execution') }); const closed = reducer(state, { type: 'close', id: 'Execution' }); expect(closed.active).toBe('Overview'); expect(reducer(closed, { type: 'close', id: 'Overview' })).toMatchObject({ tabs: [], active: null }); });
  it('uses Python reveal destination rather than guessing from result kind', () => { const state = reducer(initialState, { type: 'reveal', view: 'DataFlow', root: 'python-root', selected: 'python-selected' }); expect(state).toMatchObject({ active: 'DataFlow', selectedId: 'python-selected', roots: { DataFlow: 'python-root' } }); });
  it('restores full source anchors and active source from Python across origins', () => { const state = reducer(reducer(initialState, { type: 'open', tab: viewTab('Architecture') }), { type: 'open', tab: sourceTab(anchor) }); const layout = layoutOf(state); const restored = workspaceFromPython({ ...backend, ...layout }); expect(restored.active).toBe(sourceTab(anchor).id); expect(restored.tabs.find(t => t.kind === 'source')?.anchor).toEqual(anchor); });
  it('restores graph roots and selected node', () => { const restored = workspaceFromPython({ ...backend, openTabs: ['Overview', 'Architecture'], activeView: 'Architecture', graphRoots: { Architecture: 'model' }, selectedId: 'layer' }); expect(restored).toMatchObject({ active: 'Architecture', roots: { Architecture: 'model' }, selectedId: 'layer' }); });
  it('drops corrupted source anchor records without crashing', () => { const restored = workspaceFromPython({ ...backend, openTabs: ['Overview', 'source:invalid', 'source:%7B%7D'] }); expect(restored.tabs.map(t => t.id)).toEqual(['Overview']); });
});
