import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ComponentType, ReactNode } from 'react';
import type { Edge, EdgeProps, Node, NodeChange, ReactFlowProps } from '@xyflow/react';
import GraphEditor, { verticalPositions } from './GraphEditor';
import { graph, selectedNode, viewState } from '../testFixtures';
import { translator } from '../i18n';
import type { Graph, Island, Point } from '../../types';

// Capture the controlled props, not a second implementation of the layout.
// Keep applyNodeChanges, getSmoothStepPath and the production edgeTypes real.
const captureReactFlow = vi.hoisted(() => ({ props: null as ReactFlowProps | null }));
vi.mock('@xyflow/react', async importOriginal => {
  const original = await importOriginal<typeof import('@xyflow/react')>();
  const state = () => {
    const nodes = captureReactFlow.props?.nodes ?? [];
    return { width: 800, height: 600, panZoom: {},
      nodeLookup: new Map(nodes.map(node => [node.id, {
        ...node, measured: node.measured ?? {},
        internals: { positionAbsolute: absolutePosition(node, nodes) },
      }])),
    };
  };
  return { ...original,
    Background: () => null, Controls: () => null, Handle: () => null,
    ReactFlowProvider: ({ children }: { children: ReactNode }) => children,
    // Camera initialization is outside this layout/routing suite.
    useNodesInitialized: () => false,
    useReactFlow: () => ({ getNodes: () => captureReactFlow.props?.nodes ?? [],
      getInternalNode: (id: string) => state().nodeLookup.get(id),
      viewportInitialized: false }),
    useStore: (selector: (value: ReturnType<typeof state>) => unknown) => selector(state()),
    useStoreApi: () => ({ getState: state }),
    BaseEdge: ({ id, path }: { id: string; path: string }) => <path data-edge-id={id} d={path}/>,
    EdgeLabelRenderer: ({ children }: { children: ReactNode }) => <>{children}</>,
    ReactFlow: function Canvas(props: ReactFlowProps) {
      captureReactFlow.props = props;
      const nodes = props.nodes ?? [];
      return <div>{props.children}<svg>{(props.edges ?? []).map(edge => {
        const source = nodes.find(node => node.id === edge.source);
        const target = nodes.find(node => node.id === edge.target);
        if (!source || !target) return null;
        const from = absolutePosition(source, nodes);
        const to = absolutePosition(target, nodes);
        const Component = props.edgeTypes?.[edge.type!] as ComponentType<EdgeProps>;
        return <Component key={edge.id} {...edge} markerStart={undefined} markerEnd={undefined}
          sourceX={from.x + (source.measured?.width ?? 230) / 2}
          sourceY={from.y + (source.measured?.height ?? 110)}
          targetX={to.x + (target.measured?.width ?? 230) / 2} targetY={to.y}
          sourcePosition={original.Position.Bottom} targetPosition={original.Position.Top}/>;
      })}</svg></div>;
    },
  };
});

beforeEach(() => { captureReactFlow.props = null; });
afterEach(cleanup);

const props = { graph, viewState, details: {}, selectedId: null,
  onSelect: vi.fn(), onDetail: vi.fn(), onMove: vi.fn(), onViewport: vi.fn(),
  onCloseIsland: vi.fn(), t: translator('en') };
function relation(source: string, target: string, type = 'DATA_FLOW'): Graph['edges'][number] {
  return { source, target, type, confidence: 'Exact', sourceAnchors: [],
    codeEdgeIds: [], semanticComponentIds: [] };
}
function chain(): Graph {
  return { ...graph, nodes: ['embedding', 'blocks', 'head'].map(id => ({
    ...selectedNode, id, displayName: id, metadata: { shape: '[B, T, C]' },
  })), edges: [relation('embedding', 'blocks'), relation('blocks', 'head')],
  layoutHints: { embedding: [0, 0], blocks: [0, 112], head: [0, 224] } };
}
function nodes(): Node[] { return captureReactFlow.props!.nodes!; }
function node(id: string): Node {
  const result = nodes().find(item => item.id === id);
  expect(result, `GraphEditor must supply ${id}`).toBeDefined();
  return result!;
}
function absolutePosition(item: Node, all: Node[]): { x: number; y: number } {
  const parent = item.parentId ? all.find(candidate => candidate.id === item.parentId) : undefined;
  const origin = parent ? absolutePosition(parent, all) : { x: 0, y: 0 };
  return { x: origin.x + item.position.x, y: origin.y + item.position.y };
}
async function measure(sizes: Record<string, { width: number; height: number }>) {
  await act(async () => {
    captureReactFlow.props!.onNodesChange!(Object.entries(sizes).map(([id, dimensions]) => ({
      id, type: 'dimensions', dimensions,
    } satisfies NodeChange)));
  });
  for (const [id, size] of Object.entries(sizes)) expect(node(id).measured).toMatchObject(size);
}
type Rect = { x: number; y: number; width: number; height: number };
function rectangle(id: string): Rect {
  const item = node(id);
  return { ...absolutePosition(item, nodes()),
    width: item.measured?.width ?? Number(item.style?.width ?? 230),
    height: item.measured?.height ?? Number(item.style?.height ?? 110) };
}
function overlaps(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.width && a.x + a.width > b.x
    && a.y < b.y + b.height && a.y + a.height > b.y;
}
function pathFor(edge: Edge): string {
  const path = document.querySelector(`[data-edge-id="${edge.id}"]`)?.getAttribute('d');
  expect(path, `rendered path for ${edge.id}`).toBeTruthy();
  return path!;
}

// Sample the actual SVG path produced by the production edge component.
// M/L/Q/C cover the current smooth-step path and straight/bezier alternatives.
function pathPoints(path: string): Point[] {
  const tokens = path.match(/[MLQCmlqc]|[-+]?(?:\d*\.)?\d+(?:e[-+]?\d+)?/gi)!;
  const points: Point[] = [];
  let cursor = 0;
  let current: Point = [0, 0];
  while (cursor < tokens.length) {
    const command = tokens[cursor++];
    expect(['M', 'L', 'Q', 'C'], 'absolute SVG routing commands').toContain(command);
    const pair = (): Point => [Number(tokens[cursor++]), Number(tokens[cursor++])];
    if (command === 'M' || command === 'L') { current = pair(); points.push(current); continue; }
    const start = current;
    const first = pair();
    const second = pair();
    const end = command === 'C' ? pair() : second;
    for (let step = 1; step <= 32; step++) {
      const t = step / 32; const s = 1 - t;
      points.push(command === 'Q'
        ? [s * s * start[0] + 2 * s * t * first[0] + t * t * end[0],
          s * s * start[1] + 2 * s * t * first[1] + t * t * end[1]]
        : [s ** 3 * start[0] + 3 * s * s * t * first[0] + 3 * s * t * t * second[0] + t ** 3 * end[0],
          s ** 3 * start[1] + 3 * s * s * t * first[1] + 3 * s * t * t * second[1] + t ** 3 * end[1]]);
    }
    current = end;
  }
  return points;
}
function entersRectangle(points: Point[], rect: Rect): boolean {
  // Clip each segment against the open interior (touching the border is allowed).
  const epsilon = 1e-6;
  return points.slice(1).some((end, index) => {
    const start = points[index];
    let low = 0; let high = 1;
    for (const [axis, minimum, maximum] of [
      [0, rect.x + epsilon, rect.x + rect.width - epsilon],
      [1, rect.y + epsilon, rect.y + rect.height - epsilon],
    ]) {
      const delta = end[axis] - start[axis];
      if (delta === 0) { if (start[axis] < minimum || start[axis] > maximum) return false; }
      else {
        const first = (minimum - start[axis]) / delta;
        const second = (maximum - start[axis]) / delta;
        low = Math.max(low, Math.min(first, second));
        high = Math.min(high, Math.max(first, second));
        if (low > high) return false;
      }
    }
    return true;
  });
}
type Segment = [Point, Point];
function segments(points: Point[]): Segment[] {
  return points.slice(1).map((end, index): Segment => [points[index], end])
    .filter(([start, end]) => Math.hypot(end[0] - start[0], end[1] - start[1]) > 1e-6);
}
function distanceToSegment(point: Point, [start, end]: Segment): number {
  const dx = end[0] - start[0]; const dy = end[1] - start[1];
  const t = Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(point[0] - start[0] - t * dx, point[1] - start[1] - t * dy);
}
function hasSeparatedBody(first: Point[], second: Point[]): boolean {
  // Segmentation/zero-length changes cannot create geometric separation.
  // Permit a 24px shared access stub at each endpoint; require an actual
  // nonzero body segment with a point off the other route's entire geometry.
  const differs = (route: Point[], other: Point[]) => segments(route).some(([start, end]) => {
    const middle: Point = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
    if (Math.hypot(middle[0] - route[0][0], middle[1] - route[0][1]) <= 24
      || Math.hypot(middle[0] - route.at(-1)![0], middle[1] - route.at(-1)![1]) <= 24) return false;
    return segments(other).every(segment => distanceToSegment(middle, segment) > .5);
  });
  return differs(first, second) || differs(second, first);
}
function expectLegalEndpoints(points: Point[], sourceId: string, targetId: string) {
  const source = rectangle(sourceId); const target = rectangle(targetId);
  expect(points.length).toBeGreaterThan(1);
  expect(points.every(point => point.every(Number.isFinite))).toBe(true);
  expect(points[0]).toEqual([source.x + source.width / 2, source.y + source.height]);
  expect(points.at(-1)).toEqual([target.x + target.width / 2, target.y]);
  expect.soft(entersRectangle(points, source), 'path must leave the source through its bottom handle').toBe(false);
  expect.soft(entersRectangle(points, target), 'path must enter the target through its top handle').toBe(false);
}

it('rejects fully coincident parallel geometry even with different breakpoints and zero-length segments', () => {
  const straight = pathPoints('M 115 120 L 115 420');
  const segmented = pathPoints('M 115 120 L 115 136 L 115 136 L 115 260 L 115 395 L 115 420');
  expect(hasSeparatedBody(straight, segmented)).toBe(false);
  const separate = pathPoints('M 115 120 L 115 140 L 145 140 L 145 400 L 115 400 L 115 420');
  expect(hasSeparatedBody(straight, separate)).toBe(true);
});

it('keeps public verticalPositions ordered and leaves backend hints unchanged', () => {
  const input = chain();
  const hints = structuredClone(input.layoutHints);
  const positions = verticalPositions(input);
  expect(positions.blocks[1]).toBeGreaterThan(positions.embedding[1]);
  expect(positions.head[1]).toBeGreaterThan(positions.blocks[1]);
  expect(verticalPositions(input)).toEqual(positions);
  expect(input.layoutHints).toEqual(hints);
});

it('places measured nanoGPTB cards below the previous card despite a 112px backend pitch', async () => {
  const input = chain();
  render(<GraphEditor {...props} graph={input}/>);
  await measure({ embedding: { width: 230, height: 180 }, blocks: { width: 230, height: 260 },
    head: { width: 230, height: 150 } });
  for (const [before, after] of [['embedding', 'blocks'], ['blocks', 'head']]) {
    const previous = rectangle(before);
    expect(node(after).position.y, `${after} must clear measured ${before}`)
      .toBeGreaterThan(previous.y + previous.height);
  }
  expect(input.layoutHints.blocks).toEqual([0, 112]);
  expect(props.onMove).not.toHaveBeenCalled();
});

it('reflows automatic downstream cards when an already measured card grows', async () => {
  render(<GraphEditor {...props} graph={chain()}/>);
  await measure({ embedding: { width: 230, height: 80 }, blocks: { width: 230, height: 80 },
    head: { width: 230, height: 80 } });
  const before = node('head').position.y;
  await measure({ blocks: { width: 230, height: 300 } });
  expect(node('head').position.y).toBeGreaterThan(before);
  expect(node('head').position.y).toBeGreaterThan(node('blocks').position.y + 300);
});

it('separates same-layer automatic cards using measured widths', async () => {
  const input: Graph = { ...graph, nodes: ['left', 'right'].map(id => ({ ...selectedNode, id })),
    layoutHints: { left: [0, 112], right: [210, 112] } };
  render(<GraphEditor {...props} graph={input}/>);
  await measure({ left: { width: 360, height: 180 }, right: { width: 330, height: 180 } });
  expect(overlaps(rectangle('left'), rectangle('right')), 'measured sibling rectangles').toBe(false);
});

it.each(['per-view', 'legacy'] as const)('preserves %s saved manual coordinates after measurements and selection', async mode => {
  const manual: Record<string, Point> = { embedding: [-13.75, 48.5], blocks: [0, 0], head: [17.125, -9.75] };
  const state = { ...viewState, positions: mode === 'legacy' ? manual : { embedding: [900, 900] as Point },
    positionsByView: mode === 'per-view' ? { Architecture: manual } : {} };
  const input = chain();
  const { rerender } = render(<GraphEditor {...props} graph={input} viewState={state}/>);
  await measure({ embedding: { width: 350, height: 300 }, blocks: { width: 350, height: 300 },
    head: { width: 350, height: 300 } });
  rerender(<GraphEditor {...props} graph={input} viewState={state} selectedId="blocks"/>);
  for (const [id, [x, y]] of Object.entries(manual)) expect(node(id).position).toEqual({ x, y });
});

it('reflows measured cards inside a detail island and keeps them inside its rectangle', async () => {
  const island: Island = { id: 'detail', viewKind: 'Architecture', sourceNodeId: 'layer', position: null };
  render(<GraphEditor {...props} viewState={{ ...viewState, detailIslands: [island] }} details={{ detail: chain() }}/>);
  await measure({ 'island:detail:embedding': { width: 330, height: 180 },
    'island:detail:blocks': { width: 330, height: 260 }, 'island:detail:head': { width: 330, height: 300 } });
  expect.soft(overlaps(rectangle('island:detail:embedding'), rectangle('island:detail:blocks'))).toBe(false);
  expect.soft(overlaps(rectangle('island:detail:blocks'), rectangle('island:detail:head'))).toBe(false);
  const parent = rectangle('island:detail');
  for (const id of ['embedding', 'blocks', 'head']) {
    const child = rectangle(`island:detail:${id}`);
    expect.soft(child.x + child.width).toBeLessThanOrEqual(parent.x + parent.width);
    expect.soft(child.y + child.height).toBeLessThanOrEqual(parent.y + parent.height);
  }
});

it('places automatic detail island rectangles without overlapping each other or main cards', async () => {
  const islands: Island[] = ['a', 'b'].map(id => ({ id, viewKind: 'Architecture', sourceNodeId: 'layer', position: null }));
  render(<GraphEditor {...props} viewState={{ ...viewState, detailIslands: islands }} details={{ a: chain(), b: chain() }}/>);
  await measure({ layer: { width: 700, height: 500 } });
  expect.soft(overlaps(rectangle('island:a'), rectangle('island:b')), 'automatic islands').toBe(false);
  for (const id of ['island:a', 'island:b']) {
    expect.soft(overlaps(rectangle(id), rectangle('layer')), `${id} versus measured main card`).toBe(false);
  }
});

it('restores saved island coordinates exactly even when saved rectangles overlap', async () => {
  const islands: Island[] = ['a', 'b'].map(id => ({ id, viewKind: 'Architecture', sourceNodeId: 'layer', position: [-41.25, 72.5] }));
  render(<GraphEditor {...props} viewState={{ ...viewState, detailIslands: islands }} details={{ a: chain(), b: chain() }}/>);
  await measure({ 'island:a:embedding': { width: 450, height: 400 } });
  for (const id of ['island:a', 'island:b']) expect(node(id).position).toEqual({ x: -41.25, y: 72.5 });
});

it('moves automatic main cards clear of a fixed saved island after a late height measurement', async () => {
  const island: Island = { id: 'fixed', viewKind: 'Architecture', sourceNodeId: 'layer', position: [60, 300] };
  const onMove = vi.fn();
  render(<GraphEditor {...props} onMove={onMove}
    viewState={{ ...viewState, detailIslands: [island] }} details={{ fixed: graph }}/>);
  await measure({ layer: { width: 230, height: 110 } });
  expect(rectangle('island:fixed')).toEqual({ x: 60, y: 300, width: 340, height: 240 });
  expect(overlaps(rectangle('layer'), rectangle('island:fixed')), 'initial automatic placement is clear').toBe(false);

  await measure({ layer: { width: 230, height: 400 } });
  expect(node('island:fixed').position, 'saved island must not be displaced').toEqual({ x: 60, y: 300 });
  expect(overlaps(rectangle('layer'), rectangle('island:fixed')),
    'late measurement must not make an automatic main card intersect the saved island').toBe(false);
  expect(onMove).not.toHaveBeenCalled();
});

it.each([
  ['main', 'other'], ['main', 'all'], ['island', 'other'], ['island', 'all'],
] as const)('preserves an unpersisted %s drag through %s node measurements', async (kind, batch) => {
  const island: Island = { id: 'pending', viewKind: 'Architecture', sourceNodeId: 'embedding', position: null };
  const input = chain();
  const state = { ...viewState, detailIslands: [island] };
  const details = { pending: graph };
  const onMove = vi.fn();
  render(<GraphEditor {...props} graph={input} viewState={state} details={details} onMove={onMove}/>);
  const sizes = { embedding: { width: 230, height: 110 }, blocks: { width: 230, height: 110 },
    head: { width: 230, height: 110 }, 'island:pending:layer': { width: 230, height: 110 } };
  await measure(sizes);
  const id = kind === 'main' ? 'embedding' : 'island:pending';
  if (kind === 'main') expect(node(id).position).toEqual({ x: 60, y: 50 });
  const dragged = { x: 900, y: 700 };
  await act(async () => {
    captureReactFlow.props!.onNodesChange!([
      { id, type: 'position', position: dragged, dragging: true } satisfies NodeChange,
    ]);
  });
  expect(node(id).position, 'controlled position must first reflect the drag').toEqual(dragged);
  expect(node(id).dragging).toBe(true);
  expect(onMove, 'drag is still pending before drag-stop persistence').not.toHaveBeenCalled();
  expect(state.positions).toEqual({});
  expect(state.positionsByView).toEqual({});
  expect(island.position).toBeNull();

  await measure(batch === 'other'
    ? { blocks: { width: 230, height: 300 } }
    : { ...sizes, blocks: { width: 230, height: 300 } });
  expect(node(id).position, 'measurement reflow must preserve the pending user position').toEqual(dragged);
  expect(onMove).not.toHaveBeenCalled();
});

it.each(['main', 'island'] as const)('keeps a stopped %s drag while a save is pending and measurements arrive late', async kind => {
  const island: Island = { id: 'latency', viewKind: 'Architecture', sourceNodeId: 'layer', position: null };
  const state = { ...viewState, detailIslands: [island] };
  const details = { latency: graph };
  // Persistence is outside GraphEditor: unchanged incoming state represents
  // a backend save that has not yet returned its updated coordinates.
  const onMove = vi.fn();
  const { rerender } = render(<GraphEditor {...props} viewState={state} details={details} onMove={onMove}/>);
  const id = kind === 'main' ? 'layer' : 'island:latency';
  const dragged = { x: 900, y: 700 };
  await act(async () => {
    captureReactFlow.props!.onNodesChange!([
      { id, type: 'position', position: dragged, dragging: true } satisfies NodeChange,
    ]);
  });
  await act(async () => {
    captureReactFlow.props!.onNodesChange!([
      { id, type: 'position', position: dragged, dragging: false } satisfies NodeChange,
    ]);
    // GraphEditor only consumes the node; the unused synthetic event is a stub.
    captureReactFlow.props!.onNodeDragStop!(
      {} as Parameters<NonNullable<ReactFlowProps['onNodeDragStop']>>[0], node(id), [node(id)]);
  });
  expect(onMove).toHaveBeenCalledExactlyOnceWith(kind === 'main' ? 'layer' : 'latency', dragged, kind === 'island');
  expect(node(id).position).toEqual(dragged);
  expect(node(id).dragging).toBe(false);
  await measure({ layer: { width: 230, height: 400 }, 'island:latency:layer': { width: 330, height: 260 } });
  expect.soft(node(id).position, 'late first measurement during save latency').toEqual(dragged);
  rerender(<GraphEditor {...props} viewState={state} details={details} onMove={onMove} selectedId="layer"/>);
  expect.soft(node(id).position, 'selection rebuild before backend acknowledgement').toEqual(dragged);
  expect(onMove).toHaveBeenCalledTimes(1);
  expect(state.positions).toEqual({});
  expect(state.positionsByView).toEqual({});
  expect(island.position).toBeNull();
});

it.each(['main', 'island'] as const)('clears the %s local drag override when the semantic context changes', async kind => {
  const island: Island = { id: 'context', viewKind: 'Architecture', sourceNodeId: 'layer', position: null };
  const state = { ...viewState, detailIslands: [island] };
  const details = { context: graph };
  const onMove = vi.fn();
  const { rerender } = render(<GraphEditor {...props} cameraContext="project-a:model"
    viewState={state} details={details} onMove={onMove}/>);
  await measure({ layer: { width: 230, height: 110 }, 'island:context:layer': { width: 230, height: 110 } });
  const id = kind === 'main' ? 'layer' : 'island:context';
  const automatic = { ...node(id).position };
  await act(async () => {
    captureReactFlow.props!.onNodesChange!([
      { id, type: 'position', position: { x: 900, y: 700 }, dragging: true } satisfies NodeChange,
    ]);
  });
  expect(node(id).position).toEqual({ x: 900, y: 700 });
  // Reuse node IDs and equivalent graph data to catch overrides leaking
  // between projects even when the graph's shape has not changed.
  rerender(<GraphEditor {...props} cameraContext="project-b:model" graph={{ ...graph }}
    viewState={state} details={details} onMove={onMove}/>);
  expect(node(id).position, 'new semantic context restores its own automatic coordinates').toEqual(automatic);
  await measure({ layer: { width: 230, height: 180 } });
  expect(node(id).position).toEqual(automatic);
  expect(node(id).dragging).not.toBe(true);
  expect(onMove).not.toHaveBeenCalled();
});

it('renders geometrically separated bodies for parallel relations with the same vertical endpoints', async () => {
  const input = chain();
  input.nodes = input.nodes.filter(item => item.id !== 'blocks');
  input.edges = [relation('embedding', 'head', 'DATA_FLOW'), relation('embedding', 'head', 'GRADIENT_FLOW')];
  render(<GraphEditor {...props} graph={input} viewState={{ ...viewState,
    positionsByView: { Architecture: { embedding: [0, 0], head: [0, 420] } } }}/>);
  await measure({ embedding: { width: 230, height: 140 }, head: { width: 230, height: 140 } });
  const edges = captureReactFlow.props!.edges!;
  expect(edges).toHaveLength(2);
  const first = pathPoints(pathFor(edges[0])); const second = pathPoints(pathFor(edges[1]));
  expectLegalEndpoints(first, 'embedding', 'head');
  expectLegalEndpoints(second, 'embedding', 'head');
  expect(hasSeparatedBody(first, second), 'parallel route bodies must separate geometrically, not merely use different SVG breakpoints').toBe(true);
});

it('routes an edge around the measured rectangle of an unrelated third node', async () => {
  const input = chain();
  input.edges = [relation('embedding', 'head')];
  const manual: Record<string, Point> = { embedding: [0, 0], blocks: [0, 180], head: [0, 420] };
  render(<GraphEditor {...props} graph={input} viewState={{ ...viewState, positionsByView: { Architecture: manual } }}/>);
  await measure({ embedding: { width: 230, height: 120 }, blocks: { width: 230, height: 160 },
    head: { width: 230, height: 120 } });
  const points = pathPoints(pathFor(captureReactFlow.props!.edges![0]));
  expectLegalEndpoints(points, 'embedding', 'head');
  expect(entersRectangle(points, rectangle('blocks')), 'actual edge path must not enter the third card').toBe(false);
});

it('routes a self-loop outside its measured card with legal bottom and top endpoints', async () => {
  const input: Graph = { ...graph, nodes: [{ ...selectedNode, id: 'loop' }, { ...selectedNode, id: 'obstacle' }],
    edges: [relation('loop', 'loop', 'LOOP_BACK')] };
  render(<GraphEditor {...props} graph={input} viewState={{ ...viewState,
    positionsByView: { Architecture: { loop: [0, 0], obstacle: [300, 0] } } }}/>);
  await measure({ loop: { width: 230, height: 180 }, obstacle: { width: 230, height: 180 } });
  const points = pathPoints(pathFor(captureReactFlow.props!.edges![0]));
  expectLegalEndpoints(points, 'loop', 'loop');
  expect(entersRectangle(points, rectangle('obstacle')), 'self-loop must avoid the third card').toBe(false);
  expect(points.some(([x]) => x < 0 || x > 230), 'loop must have an exterior side segment').toBe(true);
});

it('routes island edges in absolute coordinates around a third measured child', async () => {
  const detail = chain();
  detail.edges.push(relation('embedding', 'head'));
  const island: Island = { id: 'route', viewKind: 'Architecture', sourceNodeId: 'layer', position: [-41.25, 72.5] };
  render(<GraphEditor {...props} viewState={{ ...viewState, detailIslands: [island] }} details={{ route: detail }}/>);
  await measure({ 'island:route:embedding': { width: 230, height: 180 },
    'island:route:blocks': { width: 230, height: 260 }, 'island:route:head': { width: 230, height: 150 } });
  const edge = captureReactFlow.props!.edges!.find(item => item.source === 'island:route:embedding' && item.target === 'island:route:head')!;
  const points = pathPoints(pathFor(edge));
  expectLegalEndpoints(points, edge.source, edge.target);
  expect(entersRectangle(points, rectangle('island:route:blocks')), 'island edge must avoid the third child').toBe(false);
  expect(node('island:route').position).toEqual({ x: -41.25, y: 72.5 });
});
