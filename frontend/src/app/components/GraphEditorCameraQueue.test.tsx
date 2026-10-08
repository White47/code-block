/// <reference types="vite/client" />
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import * as React from 'react';
import * as flowLibrary from '@xyflow/react';
import { transpileModule, ScriptTarget } from 'typescript';
import { graph, selectedNode, viewState } from '../testFixtures';
import source from './GraphEditor.tsx?raw';
import GraphEditor from './GraphEditor';
import { translator } from '../i18n';

let capturedStore: ReturnType<typeof flowLibrary.useStoreApi> | undefined;
vi.mock('@xyflow/react', async importOriginal => {
  const original = await importOriginal<typeof import('@xyflow/react')>();
  const react = await import('react');
  function CaptureStore() { capturedStore = original.useStoreApi(); return null; }
  return { ...original, ReactFlowProvider: (props: React.ComponentProps<typeof original.ReactFlowProvider>) =>
    react.createElement(original.ReactFlowProvider, props,
      react.createElement(CaptureStore), props.children) };
});

// Keep the camera private in production, but execute its actual source against
// the installed React Flow hooks/store. Only the native panZoom boundary is a spy.
const cameraSource = source.slice(source.indexOf('function Camera('), source.indexOf('\nfunction toXY('));
const compiled = transpileModule(cameraSource, { compilerOptions: { target: ScriptTarget.ES2022 } });
const Camera = new Function('flowLibrary', 'reactLibrary', `
  const {useReactFlow, useNodesInitialized, useStore, useStoreApi, getViewportForBounds} = flowLibrary;
  const {useEffect, useMemo, useRef, useState} = reactLibrary;
  ${compiled.outputText}
  return Camera;
`)(flowLibrary, React) as React.ComponentType<{
  context: string; graph: typeof graph; viewState: typeof viewState; loadedIslandKey: string;
}>;

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('cannot let an old A fit queue move controlled B nodes while the canvas has zero dimensions', async () => {
  // Freeze React Flow batching: its real fitView must remain queued until the
  // controlled node update below, reproducing the installed library's race.
  vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
  let store!: ReturnType<typeof flowLibrary.useStoreApi>;
  const moves = vi.fn(async () => undefined);
  const a: flowLibrary.Node = { id: 'a', type: 'code', data: {}, position: { x: 0, y: 0 },
    measured: { width: 230, height: 110 } };
  const b: flowLibrary.Node = { ...a, id: 'b', position: { x: 1000, y: 0 } };
  const graphA = { ...graph, rootIds: ['a'], nodes: [{ ...selectedNode, id: 'a' }] };
  const graphB = { ...graph, rootIds: ['b'], nodes: [{ ...selectedNode, id: 'b' }] };
  const state = { ...viewState, viewportByView: {} };
  function Setup() {
    store = flowLibrary.useStoreApi();
    React.useEffect(() => {
      store.getState().nodeLookup.get('a')!.internals.handleBounds = { source: [], target: [] };
      store.setState({ width: 800, height: 600, nodesInitialized: true,
        panZoom: { setViewport: moves } as unknown as NonNullable<ReturnType<typeof store.getState>['panZoom']> });
    }, []);
    return null;
  }
  function Rig({ context, current }: { context: string; current: typeof graph }) {
    return <flowLibrary.ReactFlowProvider initialNodes={[a]} initialWidth={800} initialHeight={600}>
      <Setup/><Camera context={context} graph={current} viewState={state} loadedIslandKey=""/>
    </flowLibrary.ReactFlowProvider>;
  }
  const { rerender } = render(<Rig context="A" current={graphA}/>);
  await act(async () => {});
  const baselineMoves = moves.mock.calls.length;
  await act(async () => {
    store.setState({ width: 0, height: 0, nodesInitialized: false });
    rerender(<Rig context="B" current={graphB}/>);
  });
  await act(async () => { store.getState().setNodes([b]); });
  expect(moves.mock.calls.length, 'A must not move B through React Flow setNodes at zero size').toBe(baselineMoves);
  expect(store.getState().fitViewQueued, 'automatic camera work must never leave a library fit queue').toBe(false);
  expect(baselineMoves, 'ready A should already have fitted immediately').toBe(1);
  expect(moves).toHaveBeenCalledWith(
    flowLibrary.getViewportForBounds({ x: 0, y: 0, width: 230, height: 110 }, 800, 600, .25, 1, .16),
    { duration: 0 });
  await act(async () => {
    store.getState().nodeLookup.get('b')!.internals.handleBounds = { source: [], target: [] };
    store.setState({ width: 800, height: 600, nodesInitialized: true });
  });
  expect(moves).toHaveBeenCalledTimes(baselineMoves + 1);
  const expected = flowLibrary.getViewportForBounds({ x: 1000, y: 0, width: 230, height: 110 }, 800, 600, .25, 1, .16);
  expect(moves).toHaveBeenLastCalledWith(expected, { duration: 0 });
  expect(store.getState().fitViewQueued).toBe(false);
});

it('keeps measured real React Flow cards visible across selection without a second ResizeObserver measurement', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  // No resize callback can hide a dimensions reset by repairing it afterwards.
  function SelectableGraph() {
    const [selectedId, setSelectedId] = React.useState<string | null>(null);
    return <GraphEditor graph={graph} viewState={viewState} details={{}} selectedId={selectedId}
      onSelect={node => setSelectedId(node.id)} onDetail={vi.fn()} onMove={vi.fn()}
      onViewport={vi.fn()} onCloseIsland={vi.fn()} t={translator('en')}/>;
  }
  render(<SelectableGraph/>);
  await waitFor(() => expect(capturedStore?.getState().nodes.length).toBeGreaterThan(0));
  const store = capturedStore!;
  await act(async () => {
    store.getState().triggerNodeChanges(store.getState().nodes.map(node => ({
      id: node.id, type: 'dimensions' as const, dimensions: { width: 230, height: 110 },
    })));
  });
  const card = document.querySelector('.react-flow__node[data-id="layer"]') as HTMLElement;
  expect(card).not.toBeNull();
  expect(store.getState().nodeLookup.get('layer')?.measured).toEqual({ width: 230, height: 110 });
  expect(getComputedStyle(card).visibility).not.toBe('hidden');
  fireEvent.click(card);
  await waitFor(() => expect(store.getState().nodes.find(node => node.id === 'layer')?.selected).toBe(true));
  expect(store.getState().nodeLookup.get('layer')?.measured,
    'selection must preserve real dimensions without waiting for another resize').toEqual({ width: 230, height: 110 });
  expect(getComputedStyle(card).visibility, 'selected card must still receive the second click').not.toBe('hidden');
  for (const node of store.getState().nodes) {
    expect(store.getState().nodeLookup.get(node.id)?.measured).toEqual({ width: 230, height: 110 });
    const element = document.querySelector(`.react-flow__node[data-id="${node.id}"]`)!;
    expect(getComputedStyle(element).visibility).not.toBe('hidden');
  }
});

it('fits actual absolute detail-island bounds with zoom capped at one', async () => {
  const main: flowLibrary.Node = { id: 'main', type: 'code', data: {}, position: { x: 0, y: 0 },
    measured: { width: 230, height: 110 } };
  const parent: flowLibrary.Node = { id: 'island:detail', type: 'island', data: {}, position: { x: 1000, y: 1000 },
    measured: { width: 600, height: 400 } };
  const child: flowLibrary.Node = { ...main, id: 'island:detail:child', parentId: parent.id,
    position: { x: 25, y: 55 } };
  const nodes = [main, parent, child];
  let store!: ReturnType<typeof flowLibrary.useStoreApi>;
  let instance!: ReturnType<typeof flowLibrary.useReactFlow>;
  const moves = vi.fn(async () => undefined);
  function Setup() {
    store = flowLibrary.useStoreApi(); instance = flowLibrary.useReactFlow();
    React.useEffect(() => {
      for (const node of store.getState().nodeLookup.values()) node.internals.handleBounds = { source: [], target: [] };
      store.setState({ width: 800, height: 600, nodesInitialized: true,
        panZoom: { setViewport: moves } as unknown as NonNullable<ReturnType<typeof store.getState>['panZoom']> });
    }, []);
    return null;
  }
  render(<flowLibrary.ReactFlowProvider initialNodes={nodes} initialWidth={800} initialHeight={600}>
    <Setup/><Camera context="detail-bounds" graph={{ ...graph, nodes: [{ ...selectedNode, id: 'main' }] }}
      viewState={{ ...viewState, viewportByView: {} }} loadedIslandKey="detail"/>
  </flowLibrary.ReactFlowProvider>);
  await act(async () => {});
  expect(instance.getNodesBounds([child])).toEqual({ x: 1025, y: 1055, width: 230, height: 110 });
  const expected = flowLibrary.getViewportForBounds({ x: 0, y: 0, width: 1600, height: 1400 }, 800, 600, .25, 1, .16);
  expect(moves).toHaveBeenCalledExactlyOnceWith(expected, { duration: 0 });
  expect(expected.zoom).toBeLessThanOrEqual(1);
  expect(store.getState().fitViewQueued).toBe(false);
});
