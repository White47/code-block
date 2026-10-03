import { cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useEffect, type ReactNode } from 'react';
import type { Edge, Node, ReactFlowInstance } from '@xyflow/react';
import GraphEditor, { verticalPositions } from './GraphEditor';
import { graph, selectedNode, viewState } from '../testFixtures';
import { translator } from '../i18n';
const flow = vi.hoisted(() => ({ fitView: vi.fn(), setViewport: vi.fn() }));
vi.mock('@xyflow/react', async importOriginal => {
  const original = await importOriginal<typeof import('@xyflow/react')>();
  return { ...original, Background: () => null, Controls: () => null, Handle: () => null, BaseEdge: () => null, EdgeLabelRenderer: ({ children }: { children: ReactNode }) => children,
    ReactFlow: function Canvas({ nodes, edges, onInit, edgeTypes }: { nodes: Node[]; edges: Edge[]; onInit: (instance: ReactFlowInstance) => void; edgeTypes: Record<string, (props: Record<string, unknown>) => ReactNode> }) {
      useEffect(() => { onInit(flow as unknown as ReactFlowInstance); }, []);
      return <div>{nodes.map(n => <div key={n.id} data-testid={n.id} data-x={n.position.x} data-y={n.position.y} className={n.className}/>)}{edges.map(e => { const EdgeComponent = edgeTypes[e.type!]; return <EdgeComponent key={e.id} {...e} sourceX={0} sourceY={0} targetX={0} targetY={100} sourcePosition={original.Position.Bottom} targetPosition={original.Position.Top}/>; })}</div>;
    },
  };
});
afterEach(cleanup); beforeEach(() => { flow.fitView.mockClear(); flow.setViewport.mockClear(); });
const props = { graph, viewState, details: {}, selectedId: 'layer', onSelect: vi.fn(), onDetail: vi.fn(), onMove: vi.fn(), onViewport: vi.fn(), onCloseIsland: vi.fn(), t: translator('en') };
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
it('retains saved viewport and does not auto fit restored islands', async () => { const island = { id: 'saved', viewKind: 'Architecture' as const, sourceNodeId: 'layer', position: [520, 50] as [number, number] }; const p = { ...props, viewState: { ...viewState, detailIslands: [island] } }; const { rerender } = render(<GraphEditor {...p}/>); await waitFor(() => expect(flow.setViewport).toHaveBeenCalledWith({ x: 32, y: 56, zoom: .7 })); rerender(<GraphEditor {...p} details={{ saved: graph }}/>); await waitFor(() => expect(document.querySelector('[data-testid="island:saved"]')).not.toBeNull()); await new Promise(resolve => setTimeout(resolve, 100)); expect(flow.fitView).not.toHaveBeenCalled(); });
it('fits once after a newly opened island graph is actually loaded', async () => { const { rerender } = render(<GraphEditor {...props}/>); const island = { id: 'new', viewKind: 'Architecture' as const, sourceNodeId: 'layer', position: [520, 50] as [number, number] }; const p = { ...props, viewState: { ...viewState, detailIslands: [island] } }; rerender(<GraphEditor {...p}/>); expect(flow.fitView).not.toHaveBeenCalled(); rerender(<GraphEditor {...p} details={{ new: graph }}/>); await waitFor(() => expect(flow.fitView).toHaveBeenCalledOnce()); rerender(<GraphEditor {...p} details={{ new: graph }} viewState={{ ...p.viewState, viewportByView: { Architecture: { pan: [70, 90], zoom: .6 } } }}/>); await new Promise(resolve => setTimeout(resolve, 100)); expect(flow.fitView).toHaveBeenCalledOnce(); });
it('fits the first graph when there is no persisted viewport', async () => { render(<GraphEditor {...props} viewState={{ ...viewState, viewportByView: {} }}/>); await waitFor(() => expect(flow.fitView).toHaveBeenCalledOnce()); expect(flow.setViewport).not.toHaveBeenCalled(); });
it('shows branch relation labels and highlights only the selected node and its one-hop neighbor', async () => { const g = { ...graph, nodes: [selectedNode, { ...selectedNode, id: 'neighbor' }, { ...selectedNode, id: 'unrelated' }], edges: [{ source: 'layer', target: 'neighbor', type: 'BRANCH_TRUE', confidence: 'Exact', sourceAnchors: [], semanticComponentIds: [], codeEdgeIds: [] }] }; render(<GraphEditor {...props} graph={g}/>); await waitFor(() => expect(document.querySelector('[data-testid="neighbor"]')?.className).toBe('neighbor-node')); expect(document.querySelector('[data-testid="unrelated"]')?.className).toBe('dimmed-node'); expect(document.querySelector('.edge-label')?.textContent).toBe('BRANCH_TRUE'); });
it('places downstream nodes below their input and handles a loop deterministically', () => { const g = { ...graph, nodes: [selectedNode, { ...selectedNode, id: 'downstream' }], edges: [{ source: 'layer', target: 'downstream', type: 'CONTROL_FLOW', confidence: 'Exact', sourceAnchors: [], semanticComponentIds: [], codeEdgeIds: [] }, { source: 'downstream', target: 'layer', type: 'LOOP_BACK', confidence: 'Exact', sourceAnchors: [], semanticComponentIds: [], codeEdgeIds: [] }] }; const positions = verticalPositions(g); expect(positions.downstream[1]).toBeGreaterThan(positions.layer[1]); expect(verticalPositions(g)).toEqual(positions); });
