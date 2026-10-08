import { act, cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import * as flowLibrary from '@xyflow/react';
import GraphEditor from './GraphEditor';
import { anchor, graph, selectedNode, viewState } from '../testFixtures';
import { translator } from '../i18n';
import type { Graph } from '../../types';

let capturedStore: ReturnType<typeof flowLibrary.useStoreApi> | undefined;
vi.mock('@xyflow/react', async importOriginal => {
  const original = await importOriginal<typeof import('@xyflow/react')>();
  const react = await import('react');
  function CaptureStore() { capturedStore = original.useStoreApi(); return null; }
  return { ...original, ReactFlowProvider: (props: ComponentProps<typeof original.ReactFlowProvider>) =>
    react.createElement(original.ReactFlowProvider, props, react.createElement(CaptureStore), props.children) };
});

function structuralGraph(depth: number): Graph {
  const nodes = [
    { ...selectedNode, id: 'model', displayName: 'Classifier' },
    { ...selectedNode, id: 'encoder', displayName: 'Encoder' },
    ...(depth > 1 ? [{ ...selectedNode, id: 'encoder-layers', displayName: 'Layers',
      codeName: 'model.Encoder.layers', sourceAnchors: [{ ...anchor, startLine: 35, endLine: 37 }],
      metadata: { definition_projection: true, instance_id: 'encoder' } }] : []),
  ];
  const queryContext = { controlMode: 'structure' as const, structureDepth: depth, canExpand: depth < 2,
    depthLimit: false, hasMore: false, budgetTruncated: false };
  return { ...graph, nodes, edges: [], rootIds: ['model'], queryContext };
}

function props(current: Graph) {
  return { graph: current, viewState: { ...viewState, selectedId: null,
    viewportByView: { Architecture: { zoom: 1, pan: [0, 0] as [number, number] } },
    positionsByView: { Architecture: { model: [50, 50] as [number, number], encoder: [50, 250] as [number, number],
      'encoder-layers': [50, 1500] as [number, number] } } },
    details: {}, selectedId: null as string | null, cameraContext: 'project-a:Architecture:model',
    onSelect: vi.fn(), onDetail: vi.fn(), onMove: vi.fn(), onViewport: vi.fn(), onCloseIsland: vi.fn(), t: translator('en') };
}

async function measure(store: ReturnType<typeof flowLibrary.useStoreApi>) {
  await act(async () => {
    store.getState().triggerNodeChanges(store.getState().nodes.map(node => ({ id: node.id, type: 'dimensions' as const,
      dimensions: { width: 230, height: 110 } })));
    for (const node of store.getState().nodeLookup.values()) node.internals.handleBounds = { source: [], target: [] };
    store.setState({ width: 800, height: 600 });
  });
}

afterEach(() => { cleanup(); capturedStore = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('reveals actual added structure only after measurement, once, and keeps selection and shrink from fitting again', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  const shallow = structuralGraph(1); const deep = structuralGraph(2);
  const initial = props(shallow);
  const { rerender } = render(<GraphEditor {...initial}/>);
  await waitFor(() => expect(capturedStore?.getState().nodes.map(node => node.id)).toEqual(['model', 'encoder']));
  const store = capturedStore!;
  const moves = vi.fn(async (viewport: flowLibrary.Viewport) => {
    store.setState({ transform: [viewport.x, viewport.y, viewport.zoom] });
  });
  await act(async () => {
    store.setState({ panZoom: { setViewport: moves } as unknown as NonNullable<ReturnType<typeof store.getState>['panZoom']> });
  });
  await measure(store);
  await waitFor(() => expect(moves).toHaveBeenCalledTimes(1));
  expect(moves).toHaveBeenLastCalledWith({ x: 0, y: 0, zoom: 1 }, { duration: 0 });
  rerender(<GraphEditor {...initial} graph={deep}/>);
  await waitFor(() => expect(store.getState().nodes.map(node => node.id)).toEqual(['model', 'encoder', 'encoder-layers']));
  expect(store.getState().nodeLookup.get('encoder-layers')?.measured.width).toBeUndefined();
  expect(moves).toHaveBeenCalledTimes(1);
  await measure(store);
  await waitFor(() => expect(moves).toHaveBeenCalledTimes(2));
  const target = moves.mock.calls.at(-1)![0];
  const node = store.getState().nodeLookup.get('encoder-layers')!;
  const absolute = node.internals.positionAbsolute;
  expect(absolute.x * target.zoom + target.x).toBeLessThan(800);
  expect(absolute.y * target.zoom + target.y).toBeLessThan(600);
  expect((absolute.x + node.measured.width!) * target.zoom + target.x).toBeGreaterThan(0);
  expect((absolute.y + node.measured.height!) * target.zoom + target.y).toBeGreaterThan(0);
  expect(store.getState().fitViewQueued).toBe(false);
  rerender(<GraphEditor {...initial} graph={deep} selectedId="encoder-layers"/>);
  await act(async () => {});
  expect(moves).toHaveBeenCalledTimes(2);
  rerender(<GraphEditor {...initial} graph={shallow}/>);
  await waitFor(() => expect(store.getState().nodes.map(node => node.id)).toEqual(['model', 'encoder']));
  expect(moves).toHaveBeenCalledTimes(2);
  expect(store.getState().fitViewQueued).toBe(false);
});
