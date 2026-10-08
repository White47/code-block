import { act, cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useEffect, useSyncExternalStore, type ReactNode } from 'react';
import type { Edge, Node, ReactFlowInstance } from '@xyflow/react';
import GraphEditor, { verticalPositions } from './GraphEditor';
import { graph, selectedNode, viewState } from '../testFixtures';
import { translator } from '../i18n';
const flow = vi.hoisted(() => ({
  fitView: vi.fn(), autoFit: vi.fn(), setViewport: vi.fn(), getNodes: vi.fn(), getNodesBounds: vi.fn(),
  getViewport: vi.fn(), getInternalNode: vi.fn(),
  initialized: true, width: 800, height: 600, epoch: 0, nodes: [] as Node[],
  listeners: new Set<() => void>(), viewportInitialized: true,
}));
function subscribe(listener: () => void) { flow.listeners.add(listener); return () => { flow.listeners.delete(listener); }; }
function snapshot() { return flow.epoch; }
function measuredNodes() {
  return flow.nodes.map(node => {
    const parent = node.parentId ? flow.nodes.find(item => item.id === node.parentId) : undefined;
    return { ...node, measured: flow.initialized ? {
      width: Number(node.style?.width ?? 230), height: Number(node.style?.height ?? 110),
    } : {}, internals: { positionAbsolute: {
      x: node.position.x + (parent?.position.x ?? 0), y: node.position.y + (parent?.position.y ?? 0),
    } } };
  });
}
function storeState() {
  return { width: flow.width, height: flow.height, panZoom: {}, nodes: measuredNodes(),
    nodeLookup: new Map(measuredNodes().map(node => [node.id, node])), transform: [32, 56, .7],
    viewportInitialized: flow.width > 0 && flow.height > 0 };
}
async function publishMeasurement(initialized: boolean, width: number, height: number) {
  await act(async () => {
    flow.initialized = initialized; flow.width = width; flow.height = height;
    flow.epoch++; flow.listeners.forEach(listener => listener());
  });
}
vi.mock('@xyflow/react', async importOriginal => {
  const original = await importOriginal<typeof import('@xyflow/react')>();
  return { ...original,
    getViewportForBounds: (...args: Parameters<typeof original.getViewportForBounds>) => flow.autoFit(...args), Background: () => null, Controls: () => null, Handle: () => null, BaseEdge: () => null, EdgeLabelRenderer: ({ children }: { children: ReactNode }) => children,
    ReactFlowProvider: ({ children }: { children: ReactNode }) => children,
    useNodesInitialized: () => { useSyncExternalStore(subscribe, snapshot); return flow.initialized && flow.nodes.length > 0; },
    useReactFlow: () => flow,
    useStore: (selector: (state: ReturnType<typeof storeState>) => unknown) => {
      useSyncExternalStore(subscribe, snapshot); return selector(storeState());
    },
    useStoreApi: () => ({ getState: storeState, subscribe }),
    ReactFlow: function Canvas({ nodes, edges, onInit, edgeTypes, children }: { nodes: Node[]; edges: Edge[]; onInit: (instance: ReactFlowInstance) => void; children?: ReactNode; edgeTypes: Record<string, (props: Record<string, unknown>) => ReactNode> }) {
      flow.nodes = nodes;
      useEffect(() => { onInit(flow as unknown as ReactFlowInstance); }, []);
      return <div>{children}{nodes.map(n => <div key={n.id} data-testid={n.id} data-x={n.position.x} data-y={n.position.y} className={n.className}/>)}{edges.map(e => { const EdgeComponent = edgeTypes[e.type!]; return <EdgeComponent key={e.id} {...e} sourceX={0} sourceY={0} targetX={0} targetY={100} sourcePosition={original.Position.Bottom} targetPosition={original.Position.Top}/>; })}</div>;
    },
  };
});
afterEach(() => {
  cleanup();
  expect(flow.fitView, 'automatic camera must never queue a fitView').not.toHaveBeenCalled();
  for (const [index, args] of flow.autoFit.mock.calls.entries()) {
    expect(args.slice(3)).toEqual([.25, 1, .16]);
    expect(flow.setViewport).toHaveBeenCalledWith(flow.autoFit.mock.results[index].value, { duration: 0 });
  }
  vi.useRealTimers(); vi.restoreAllMocks();
});
beforeEach(async () => {
  flow.fitView.mockReset(); flow.autoFit.mockReset(); flow.setViewport.mockReset().mockResolvedValue(true);
  const original = await vi.importActual<typeof import('@xyflow/react')>('@xyflow/react');
  flow.autoFit.mockImplementation(original.getViewportForBounds);
  flow.initialized = true; flow.width = 800; flow.height = 600; flow.nodes = [];
  flow.listeners.clear(); flow.epoch++;
  flow.getNodes.mockImplementation(measuredNodes);
  flow.getViewport.mockReturnValue({ x: 32, y: 56, zoom: .7 });
  flow.getInternalNode.mockImplementation(id => measuredNodes().find(node => node.id === id));
  flow.getNodesBounds.mockImplementation(() => {
    const nodes = measuredNodes();
    const x = Math.min(...nodes.map(node => node.internals.positionAbsolute.x));
    const y = Math.min(...nodes.map(node => node.internals.positionAbsolute.y));
    return { x, y, width: Math.max(...nodes.map(node => node.internals.positionAbsolute.x + (node.measured.width ?? 0))) - x,
      height: Math.max(...nodes.map(node => node.internals.positionAbsolute.y + (node.measured.height ?? 0))) - y };
  });
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => ({
    x: 0, y: 0, top: 0, left: 0, right: flow.width, bottom: flow.height,
    width: flow.width, height: flow.height, toJSON: () => ({}),
  }));
});
const props = { graph, viewState, details: {}, selectedId: 'layer',
  cameraContext: 'project-session-a:Architecture:model',
  onSelect: vi.fn(), onDetail: vi.fn(), onMove: vi.fn(), onViewport: vi.fn(), onCloseIsland: vi.fn(), t: translator('en') };
const siblingGraph = {
  ...graph,
  nodes: ['left', 'middle', 'right'].map(id => ({ ...selectedNode, id })),
  layoutHints: { left: [-210, 155], middle: [0, 155], right: [210, 155] } as Record<string, [number, number]>,
};
it('keeps automatic sibling cards apart while preserving their hinted vertical layer', () => {
  const positions = verticalPositions(siblingGraph);
  // Cards may be up to 230px wide; the 210px backend pitch used to overlap.
  for (const [left, right] of [['left', 'middle'], ['middle', 'right']]) {
    expect(positions[right][0] - positions[left][0]).toBe(280);
    expect(positions[left][0] + 230).toBeLessThan(positions[right][0]);
  }
  expect(Object.values(positions).map(p => p[1])).toEqual([155, 155, 155]);
  expect(siblingGraph.layoutHints.left).toEqual([-210, 155]);
});
it('uses the same automatic spacing inside detail islands and exactly restores the island position', async () => {
  const island = { id: 'siblings', viewKind: 'Architecture' as const, sourceNodeId: 'layer', position: [-41.25, 72.5] as [number, number] };
  render(<GraphEditor {...props} graph={siblingGraph} viewState={{ ...viewState, detailIslands: [island] }} details={{ siblings: siblingGraph }}/>);
  await waitFor(() => expect(document.querySelector('[data-testid="island:siblings:right"]')).not.toBeNull());
  const x = (id: string) => Number(document.querySelector(`[data-testid="${id}"]`)?.getAttribute('data-x'));
  for (const [left, right] of [['left', 'middle'], ['middle', 'right']]) {
    expect(x(right) - x(left)).toBe(280);
    expect(x(`island:siblings:${right}`) - x(`island:siblings:${left}`)).toBe(280);
    expect(x(`island:siblings:${left}`) + 230).toBeLessThan(x(`island:siblings:${right}`));
  }
  expect(x('island:siblings')).toBe(-41.25);
  expect(document.querySelector('[data-testid="island:siblings"]')?.getAttribute('data-y')).toBe('72.5');
});
it('restores manual positions exactly without scaling or separating them', async () => {
  const manual = { left: [-13.75, 48.5], middle: [0, 0] } as Record<string, [number, number]>;
  render(<GraphEditor {...props} graph={siblingGraph} viewState={{ ...viewState, positions: { left: [900, 900] }, positionsByView: { Architecture: manual } }}/>);
  await waitFor(() => expect(document.querySelector('[data-testid="left"]')).not.toBeNull());
  for (const [id, [x, y]] of Object.entries(manual)) {
    const node = document.querySelector(`[data-testid="${id}"]`)!;
    expect(Number(node.getAttribute('data-x'))).toBe(x);
    expect(Number(node.getAttribute('data-y'))).toBe(y);
  }
  expect(Number(document.querySelector('[data-testid="right"]')?.getAttribute('data-x'))).toBe(280);
});
it('also restores legacy flat manual positions exactly', async () => {
  render(<GraphEditor {...props} graph={siblingGraph} viewState={{ ...viewState, positionsByView: {}, positions: { middle: [17.125, -9.75] } }}/>);
  await waitFor(() => expect(document.querySelector('[data-testid="middle"]')).not.toBeNull());
  const node = document.querySelector('[data-testid="middle"]')!;
  expect(node.getAttribute('data-x')).toBe('17.125');
  expect(node.getAttribute('data-y')).toBe('-9.75');
});
it('retains saved viewport and does not auto fit restored islands', async () => { const island = { id: 'saved', viewKind: 'Architecture' as const, sourceNodeId: 'layer', position: [520, 50] as [number, number] }; const p = { ...props, viewState: { ...viewState, detailIslands: [island] } }; const { rerender } = render(<GraphEditor {...p}/>); await waitFor(() => expect(flow.setViewport).toHaveBeenCalledWith({ x: 32, y: 56, zoom: .7 }, { duration: 0 })); rerender(<GraphEditor {...p} details={{ saved: graph }}/>); await waitFor(() => expect(document.querySelector('[data-testid="island:saved"]')).not.toBeNull()); await new Promise(resolve => setTimeout(resolve, 100)); expect(flow.autoFit).not.toHaveBeenCalled(); });
it('does not restore a viewport belonging to a different semantic root after canvas remount', async () => {
  // Semantic identity regression; measured rectangles below are supplied by
  // the lower-level flow rig. Actual browser measurement remains native QA.
  const first = render(<GraphEditor {...props} key="Architecture:model"/>);
  await waitFor(() => expect(flow.setViewport).toHaveBeenCalledWith({ x: 32, y: 56, zoom: .7 }, { duration: 0 }));
  first.unmount();
  flow.setViewport.mockClear();
  flow.autoFit.mockClear();
  const next = { ...graph, rootIds: ['different-model'],
    nodes: [{ ...selectedNode, id: 'different-layer' }] };
  // viewportByView is stored only by view kind; graphRoots identifies the
  // context that produced this saved pan, rather than the React mount key.
  const nextProps = { ...props, cameraContext: 'project-session-a:Architecture:different-model' };
  render(<GraphEditor {...nextProps} key="Architecture:different-model" graph={next} selectedId={null}/>);
  await waitFor(() => expect(document.querySelector('[data-testid="different-layer"]')).not.toBeNull());
  expect(flow.setViewport, 'old model pan must not be restored into the different model graph').not.toHaveBeenCalledWith({ x: 32, y: 56, zoom: .7 }, { duration: 0 });
  await waitFor(() => expect(flow.autoFit).toHaveBeenCalledOnce());
});
it('fits a changed project-session cameraContext even when view and root IDs are identical', async () => {
  vi.useFakeTimers();
  const { rerender } = render(<GraphEditor {...props}/>);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(flow.setViewport).toHaveBeenCalledWith({ x: 32, y: 56, zoom: .7 }, { duration: 0 });
  flow.autoFit.mockClear(); flow.setViewport.mockClear();
  const nextProps = { ...props, cameraContext: 'project-session-b:Architecture:model' };
  rerender(<GraphEditor {...nextProps}/>);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(flow.autoFit).toHaveBeenCalledOnce();
  expect(flow.setViewport).not.toHaveBeenCalledWith({ x: 32, y: 56, zoom: .7 }, { duration: 0 });
});
it('fits a changed semantic root context without requiring a React remount', async () => {
  vi.useFakeTimers();
  const { rerender } = render(<GraphEditor {...props}/>);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  flow.autoFit.mockClear(); flow.setViewport.mockClear();
  const nextProps = { ...props, cameraContext: 'project-session-a:Architecture:other-model',
    graph: { ...graph, rootIds: ['other-model'], nodes: [{ ...selectedNode, id: 'other-layer' }] },
    viewState: { ...viewState, graphRoots: { Architecture: 'other-model' } }, selectedId: null };
  rerender(<GraphEditor {...nextProps}/>);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(flow.autoFit).toHaveBeenCalledOnce();
  expect(flow.setViewport).not.toHaveBeenCalledWith({ x: 32, y: 56, zoom: .7 }, { duration: 0 });
});
it('fits once after a newly opened island graph is actually loaded', async () => { const { rerender } = render(<GraphEditor {...props}/>); const island = { id: 'new', viewKind: 'Architecture' as const, sourceNodeId: 'layer', position: [520, 50] as [number, number] }; const p = { ...props, viewState: { ...viewState, detailIslands: [island] } }; rerender(<GraphEditor {...p}/>); expect(flow.autoFit).not.toHaveBeenCalled(); rerender(<GraphEditor {...p} details={{ new: graph }}/>); await waitFor(() => expect(flow.autoFit).toHaveBeenCalledOnce()); rerender(<GraphEditor {...p} details={{ new: graph }} viewState={{ ...p.viewState, viewportByView: { Architecture: { pan: [70, 90], zoom: .6 } } }}/>); await new Promise(resolve => setTimeout(resolve, 100)); expect(flow.autoFit).toHaveBeenCalledOnce(); });
it('fits the first graph when there is no persisted viewport', async () => { render(<GraphEditor {...props} viewState={{ ...viewState, viewportByView: {} }}/>); await waitFor(() => expect(flow.autoFit).toHaveBeenCalledOnce()); expect(flow.setViewport).not.toHaveBeenCalledWith({ x: 32, y: 56, zoom: .7 }, { duration: 0 }); });
it('waits for actual initialized nodes and a nonzero container, not an elapsed camera delay', async () => {
  vi.useFakeTimers();
  flow.initialized = false; flow.width = 0; flow.height = 0;
  render(<GraphEditor {...props} viewState={{ ...viewState, viewportByView: {} }}/>);
  // Advancing time is an adversarial probe, not simulated measurement.
  // Only publishMeasurement changes the readiness facts.
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(flow.autoFit).not.toHaveBeenCalled();
  await publishMeasurement(true, 0, 0);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(flow.autoFit).not.toHaveBeenCalled();
  await publishMeasurement(true, 800, 600);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(flow.autoFit).toHaveBeenCalledOnce();
  await publishMeasurement(true, 800, 600);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(flow.autoFit).toHaveBeenCalledOnce();
});
it.each([
  { width: 0, height: 600 },
  { width: 800, height: 0 },
])('rechecks $width x $height dimensions at the camera microtask before any fit or pan', async ({ width, height }) => {
  render(<GraphEditor {...props} viewState={{ ...viewState, viewportByView: {} }}/>);
  // The effect saw a ready canvas. Change the store before its microtask runs,
  // without publishing a render that would cancel the pending operation.
  flow.width = width; flow.height = height;
  await act(async () => {});
  expect(flow.autoFit).not.toHaveBeenCalled();
  expect(flow.setViewport).not.toHaveBeenCalled();
  await publishMeasurement(true, 800, 600);
  expect(flow.autoFit).toHaveBeenCalledOnce();
  expect(flow.setViewport).toHaveBeenCalledOnce();
});
it.each([
  { name: 'offscreen', pan: [100000, -100000] as [number, number], zoom: .7 },
  { name: 'zero zoom', pan: [32, 56] as [number, number], zoom: 0 },
  { name: 'nonfinite pan', pan: [Number.NaN, 56] as [number, number], zoom: .7 },
])('fits instead of restoring a $name saved viewport in the same context', async ({ pan, zoom }) => {
  render(<GraphEditor {...props} viewState={{ ...viewState,
    viewportByView: { Architecture: { pan, zoom } } }}/>);
  await waitFor(() => expect(document.querySelector('[data-testid="layer"]')).not.toBeNull());
  expect(flow.setViewport).not.toHaveBeenCalledWith({ x: 32, y: 56, zoom: .7 }, { duration: 0 });
  await waitFor(() => expect(flow.autoFit).toHaveBeenCalledOnce());
});
it('does not mistake the empty gap between offscreen node bounds for a visible node', async () => {
  const separated = { ...graph, nodes: [
    { ...selectedNode, id: 'far-left' }, { ...selectedNode, id: 'far-right' },
  ] };
  render(<GraphEditor {...props} graph={separated} selectedId={null} viewState={{ ...viewState,
    positionsByView: { Architecture: { 'far-left': [-3000, 50], 'far-right': [3000, 50] } },
    viewportByView: { Architecture: { pan: [0, 0], zoom: 1 } } }}/ >);
  await waitFor(() => expect(document.querySelector('[data-testid="far-right"]')).not.toBeNull());
  // Aggregate bbox intersects the 800x600 canvas, but neither 230x110 card
  // intersects it. Visibility must check actual measured node rectangles.
  expect(flow.setViewport).not.toHaveBeenCalledWith({ x: 32, y: 56, zoom: .7 }, { duration: 0 });
  await waitFor(() => expect(flow.autoFit).toHaveBeenCalledOnce());
});
it('restores a same-context viewport when only part of a measured card remains visible', async () => {
  vi.useFakeTimers();
  render(<GraphEditor {...props} viewState={{ ...viewState,
    viewportByView: { Architecture: { pan: [-150, 0], zoom: .7 } } }}/ >);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(flow.setViewport).toHaveBeenCalledWith({ x: -150, y: 0, zoom: .7 }, { duration: 0 });
  expect(flow.autoFit).not.toHaveBeenCalled();
});
it('does not fit or schedule repeated camera work for an empty graph', async () => {
  vi.useFakeTimers();
  render(<GraphEditor {...props} graph={{ ...graph, nodes: [], edges: [] }}
    viewState={{ ...viewState, viewportByView: {} }}/>);
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(flow.autoFit).not.toHaveBeenCalled();
  expect(flow.setViewport).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});
it('ignores an old canvas fit callback after that canvas has unmounted', async () => {
  vi.useFakeTimers();
  const first = render(<GraphEditor {...props} viewState={{ ...viewState, viewportByView: {} }}/>);
  first.unmount();
  render(<GraphEditor {...props}/>);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(flow.setViewport).toHaveBeenCalledWith({ x: 32, y: 56, zoom: .7 }, { duration: 0 });
  expect(flow.autoFit).not.toHaveBeenCalled();
});
it('keeps a visible same-context viewport across selection, expansion and collapse', async () => {
  vi.useFakeTimers();
  const { rerender } = render(<GraphEditor {...props}/>);
  const expanded = { ...graph, nodes: [...graph.nodes, { ...selectedNode, id: 'expanded-child' }] };
  rerender(<GraphEditor {...props} graph={expanded} selectedId="expanded-child"/>);
  rerender(<GraphEditor {...props} graph={graph} selectedId={null}/>);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(flow.setViewport).toHaveBeenCalledWith({ x: 32, y: 56, zoom: .7 }, { duration: 0 });
  expect(flow.autoFit).not.toHaveBeenCalled();
});
it('fits a new detail island exactly once only after its nodes are initialized', async () => {
  vi.useFakeTimers();
  const { rerender } = render(<GraphEditor {...props}/>);
  const island = { id: 'measured-island', viewKind: 'Architecture' as const,
    sourceNodeId: 'layer', position: [520, 50] as [number, number] };
  await publishMeasurement(false, 800, 600);
  const p = { ...props, viewState: { ...viewState, detailIslands: [island] }, details: { 'measured-island': graph } };
  rerender(<GraphEditor {...p}/>);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(flow.autoFit).not.toHaveBeenCalled();
  await publishMeasurement(true, 800, 600);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(flow.autoFit).toHaveBeenCalledOnce();
  rerender(<GraphEditor {...p} details={{ 'measured-island': { ...graph } }}/>);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(flow.autoFit).toHaveBeenCalledOnce();
});
it('shows readable branch labels, preserves the relation type and highlights only the selected node and its one-hop neighbor', async () => {
  const g = { ...graph, nodes: [selectedNode, { ...selectedNode, id: 'neighbor' }, { ...selectedNode, id: 'unrelated' }],
    edges: [{ source: 'layer', target: 'neighbor', type: 'BRANCH_TRUE', confidence: 'Exact', sourceAnchors: [], semanticComponentIds: [], codeEdgeIds: [] }] };
  render(<GraphEditor {...props} graph={g}/>);
  await waitFor(() => expect(document.querySelector('[data-testid="neighbor"]')?.className).toBe('neighbor-node'));
  expect(document.querySelector('[data-testid="unrelated"]')?.className).toBe('dimmed-node');
  expect(document.querySelector('.edge-label')?.textContent).toBe('True branch');
  expect(g.edges[0]).toEqual(expect.objectContaining({ type: 'BRANCH_TRUE', source: 'layer', target: 'neighbor' }));
  expect(verticalPositions(g).neighbor[1]).toBeGreaterThan(verticalPositions(g).layer[1]);
});
it('places downstream nodes below their input and handles a loop deterministically', () => { const g = { ...graph, nodes: [selectedNode, { ...selectedNode, id: 'downstream' }], edges: [{ source: 'layer', target: 'downstream', type: 'CONTROL_FLOW', confidence: 'Exact', sourceAnchors: [], semanticComponentIds: [], codeEdgeIds: [] }, { source: 'downstream', target: 'layer', type: 'LOOP_BACK', confidence: 'Exact', sourceAnchors: [], semanticComponentIds: [], codeEdgeIds: [] }] }; const positions = verticalPositions(g); expect(positions.downstream[1]).toBeGreaterThan(positions.layer[1]); expect(verticalPositions(g)).toEqual(positions); });
