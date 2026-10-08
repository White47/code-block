import { useEffect, useMemo, useRef, useState } from 'react';
import { Background, BaseEdge, Controls, EdgeLabelRenderer, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, applyNodeChanges, getSmoothStepPath, getViewportForBounds, useNodesInitialized, useReactFlow, useStore, useStoreApi, type Edge, type EdgeProps, type Node, type NodeProps, type ReactFlowInstance, type Viewport } from '@xyflow/react';
import type { Graph, Island, ViewKind, ViewState, VisibleNode } from '../../types';
import type { Translate } from '../i18n';
import { viewLabel } from './EditorTabs';
import { definitionBreadcrumb, graphLabel } from '../graphLabels';
import { cardSize, layoutCards, overlaps, type Rect } from './canvasLayout';
import { routeEdge, type CanvasRoute } from './canvasRouting';
type GraphNode = Node<{ item: VisibleNode; detailSource: string | null; index: number; roleLabel: string; breadcrumb: string; projectionLabel: string }, 'code'>;
type IslandNode = Node<{ title: string; close: () => void }, 'island'>;
type CanvasNode = GraphNode | IslandNode;
function geometrySignature(node: CanvasNode): string {
  const geometry = [node.type, node.parentId, node.style?.width, node.style?.height];
  if (node.type === 'code') {
    const { item, index } = node.data;
    return JSON.stringify([...geometry, String(index + 1).padStart(2, '0'), node.data.roleLabel, node.data.projectionLabel,
      item.displayName, item.codeName ?? item.sourceAnchors[0]?.filePath.split(/[\\/]/).pop() ?? item.id,
      item.metadata.shape ? String(item.metadata.shape) : null]);
  }
  return JSON.stringify([...geometry, node.data.title]);
}
const edgeColors: Record<string, string> = { CONTAINS: '#687e90', CALLS: '#6fafff', CONTROL_FLOW: '#4dddc3', BRANCH_TRUE: '#6bce95', BRANCH_FALSE: '#f6ad69', LOOP_BACK: '#f6ad69', DATA_FLOW: '#67bcff', GRADIENT_FLOW: '#e897cb', READS: '#b4a2ed', WRITES: '#b4a2ed' };
function CodeNode({ data, selected }: NodeProps<GraphNode>) {
  const role = data.item.role ?? 'Symbol';
  return <div className={`code-node ${selected ? 'selected' : ''}`} data-role={role}><Handle type="target" position={Position.Top}/><div className="node-meta"><span>{String(data.index + 1).padStart(2, '0')}</span><span title={role}>{data.roleLabel}</span><i className={`signal ${data.item.confidence === 'Exact' ? '' : 'amber'}`}/></div><strong>{data.item.displayName}</strong><code>{data.item.codeName ?? data.item.sourceAnchors[0]?.filePath.split(/[\\/]/).pop() ?? data.item.id}</code>{data.projectionLabel && <small className="definition-badge" title={data.breadcrumb}>{data.projectionLabel}</small>}{data.item.metadata.shape && <small>[{String(data.item.metadata.shape)}]</small>}<Handle type="source" position={Position.Bottom}/></div>;
}
function DetailIsland({ data }: NodeProps<IslandNode>) { return <div className="detail-island"><div className="island-heading"><span>◇ {data.title}</span><button className="nodrag" onClick={data.close} aria-label="Close detail island">×</button></div></div>; }
function SemanticEdge(props: EdgeProps) {
  const route = props.data?.route as CanvasRoute | undefined;
  const [defaultPath, defaultX, defaultY] = getSmoothStepPath({ ...props, borderRadius: 12 });
  const { path, x, y } = route ?? { path: defaultPath, x: defaultX, y: defaultY };
  return <><BaseEdge id={props.id} path={path} markerEnd={props.markerEnd} style={props.style}/><EdgeLabelRenderer><div className="edge-label nodrag nopan" title={route?.fallback ? String(props.data?.fallbackMessage) : undefined} style={{ transform: `translate(-50%, -50%) translate(${x}px, ${y}px)` }}>{props.label}{route?.fallback ? ' ⚠' : ''}</div></EdgeLabelRenderer></>;
}
const nodeTypes = { code: CodeNode, island: DetailIsland };
const edgeTypes = { semantic: SemanticEdge };
// Public deterministic layout entry point; measurements refine this in GraphCanvas.
export function verticalPositions(graph: Graph): Record<string, [number, number]> {
  return layoutCards(graph);
}
function edgesFor(graph: Graph, t: Translate, prefix = ''): Edge[] { return graph.edges.map((edge, i) => ({ id: `${prefix}edge:${i}`, source: prefix + edge.source, target: prefix + edge.target, type: 'semantic', label: graphLabel(edge.type, t), style: { stroke: edgeColors[edge.type] ?? '#8192a8', strokeWidth: 1.7, strokeDasharray: edge.type === 'CONTAINS' ? '4 5' : edge.type === 'LOOP_BACK' ? '7 4' : edge.confidence === 'Probable' ? '3 4' : undefined }, markerEnd: { type: MarkerType.ArrowClosed, color: edgeColors[edge.type] ?? '#8192a8' }, labelStyle: { fill: '#a4b3c5', fontSize: 10 }, labelBgStyle: { fill: '#111820', fillOpacity: 0.92 } })); }
type GraphEditorProps = {
  graph: Graph; viewState: ViewState | null; details: Record<string, Graph>; selectedId: string | null;
  cameraContext?: string; restoreViewport?: boolean;
  onSelect: (node: VisibleNode, detailSource: string | null) => void; onDetail: (node: VisibleNode) => void;
  onMove: (id: string, position: { x: number; y: number }, island: boolean) => void;
  onViewport: (view: ViewKind, viewport: Viewport) => void; onCloseIsland: (id: string) => void; t: Translate;
};
export default function GraphEditor(props: GraphEditorProps) {
  return <ReactFlowProvider><GraphCanvas {...props}/></ReactFlowProvider>;
}
function GraphCanvas({ graph, viewState, details, selectedId, cameraContext = JSON.stringify([graph.viewKind, graph.rootIds]), restoreViewport, onSelect, onDetail, onMove, onViewport, onCloseIsland, t }: GraphEditorProps) {
  const [nodes, setNodes] = useState<CanvasNode[]>([]);
  const [instance, setInstance] = useState<ReactFlowInstance<CanvasNode> | null>(null);
  const islands = useMemo(() => viewState?.detailIslands.filter(i => i.viewKind === graph.viewKind) ?? [], [viewState?.detailIslands, graph.viewKind]);
  const initialViewport = { x: 30, y: 30, zoom: 0.8 };
  const localPositions = useRef({ context: cameraContext, main: {} as Record<string, [number, number]>, islands: {} as Record<string, [number, number]> });
  const currentContext = useRef(cameraContext); currentContext.current = cameraContext;
  const routeCache = useRef({ geometry: '', routes: new Map<string, CanvasRoute>() });
  function manualPositions() {
    if (localPositions.current.context !== cameraContext) localPositions.current = { context: cameraContext, main: {}, islands: {} };
    return localPositions.current;
  }
  function reflow(batch: CanvasNode[]): CanvasNode[] {
    const byId = new Map(batch.map(n => [n.id, n]));
    const size = (id: string) => { const n = byId.get(id); return { width: n?.measured?.width ?? cardSize.width, height: n?.measured?.height ?? cardSize.height }; };
    const local = manualPositions();
    const backendSaved = viewState?.positionsByView[graph.viewKind] ?? viewState?.positions ?? {};
    for (const [id, point] of Object.entries(local.main)) {
      if (backendSaved[id]?.[0] === point[0] && backendSaved[id]?.[1] === point[1]) delete local.main[id];
    }
    for (const island of islands) {
      const point = local.islands[island.id];
      if (point && island.position?.[0] === point[0] && island.position?.[1] === point[1]) delete local.islands[island.id];
    }
    const saved = { ...backendSaved, ...local.main };
    const result = batch.map(n => ({ ...n }));
    const rectangles: Rect[] = [];
    for (const island of islands) {
      const detail = details[island.id]; const parentId = `island:${island.id}`;
      const parent = result.find(n => n.id === parentId); if (!detail || !parent) continue;
      const points = layoutCards(detail, new Map(detail.nodes.map(n => [n.id, size(`${parentId}:${n.id}`)])));
      const minX = Math.min(0, ...Object.values(points).map(p => p[0])); const minY = Math.min(0, ...Object.values(points).map(p => p[1]));
      let width = 320; let height = 220;
      for (const child of result.filter(n => n.parentId === parentId)) {
        const point = points[child.id.slice(parentId.length + 1)];
        if (!point) continue;
        child.position = { x: point[0] - minX + 25, y: point[1] - minY + 55 };
        const dimensions = size(child.id);
        width = Math.max(width, child.position.x + dimensions.width + 25);
        height = Math.max(height, child.position.y + dimensions.height + 25);
      }
      parent.style = { ...parent.style, width, height };
      if (parent.measured) parent.measured = { width, height };
    }
    // All saved islands are obstacles even if they appear later in the list.
    for (const island of islands.filter(i => local.islands[i.id] ?? i.position)) {
      const parent = result.find(n => n.id === `island:${island.id}`); if (!parent) continue;
      parent.position = toXY(local.islands[island.id] ?? island.position!);
      rectangles.push({ ...parent.position, width: Number(parent.style!.width), height: Number(parent.style!.height) });
    }
    const positions = layoutCards(graph, new Map(graph.nodes.map(n => [n.id, size(n.id)])), saved, rectangles);
    for (const n of result.filter(n => n.type === 'code' && !n.parentId)) {
      if (positions[n.id]) n.position = toXY(positions[n.id]);
      rectangles.push({ ...n.position, ...size(n.id) });
    }
    for (const island of islands.filter(i => !(local.islands[i.id] ?? i.position))) {
      const parent = result.find(n => n.id === `island:${island.id}`); if (!parent) continue;
      const rect = { x: 520, y: 80, width: Number(parent.style!.width), height: Number(parent.style!.height) };
      for (let attempt = 0; attempt <= rectangles.length; attempt++) {
        const hits = rectangles.filter(other => overlaps(rect, other, 45)); if (!hits.length) break;
        rect.x = Math.max(...hits.map(other => other.x + other.width + 45));
      }
      parent.position = { x: rect.x, y: rect.y }; rectangles.push(rect);
    }
    return result;
  }
  useEffect(() => {
    const positions = verticalPositions(graph); const saved = viewState?.positionsByView[graph.viewKind] ?? viewState?.positions ?? {};
    const neighbors = new Set(graph.edges.filter(e => e.source === selectedId || e.target === selectedId).flatMap(e => [e.source, e.target]));
    const main: CanvasNode[] = graph.nodes.map((item, index) => ({ id: item.id, type: 'code', position: toXY(saved[item.id] ?? positions[item.id]), data: { item, detailSource: null, index, roleLabel: graphLabel(item.role ?? 'Symbol', t), breadcrumb: definitionBreadcrumb(item, t), projectionLabel: item.metadata.definitionProjection === true ? t('definitionExpanded') : '' }, selected: selectedId === item.id, className: selectedId ? neighbors.has(item.id) || item.id === selectedId ? 'neighbor-node' : 'dimmed-node' : '' }));
    islands.forEach((island, islandIndex) => {
      const detail = details[island.id]; if (!detail) return;
      const points = verticalPositions(detail); const minX = Math.min(0, ...Object.values(points).map(p => p[0])); const minY = Math.min(0, ...Object.values(points).map(p => p[1]));
      const width = Math.max(320, ...Object.values(points).map(p => p[0] - minX + 300)); const height = Math.max(220, ...Object.values(points).map(p => p[1] - minY + 180));
      const parent = `island:${island.id}`;
      main.push({ id: parent, type: 'island', position: toXY(island.position ?? [520 + islandIndex * 80, 80 + islandIndex * 50]), data: { title: graph.nodes.find(n => n.id === island.sourceNodeId)?.displayName ?? island.sourceNodeId, close: () => onCloseIsland(island.id) }, style: { width, height }, zIndex: -1 });
      const detailNeighbors = new Set(detail.edges.filter(e => e.source === selectedId || e.target === selectedId).flatMap(e => [e.source, e.target]));
      detail.nodes.forEach((item, index) => main.push({ id: `${parent}:${item.id}`, type: 'code', parentId: parent, extent: 'parent', draggable: false, position: { x: points[item.id][0] - minX + 25, y: points[item.id][1] - minY + 55 }, data: { item, detailSource: island.sourceNodeId, index, roleLabel: graphLabel(item.role ?? 'Symbol', t), breadcrumb: definitionBreadcrumb(item, t), projectionLabel: item.metadata.definitionProjection === true ? t('definitionExpanded') : '' }, selected: selectedId === item.id, className: selectedId && detail.nodes.some(n => n.id === selectedId) ? detailNeighbors.has(item.id) || item.id === selectedId ? 'neighbor-node' : 'dimmed-node' : '' }));
    });
    setNodes(current => {
      const previousById = new Map(current.map(node => [node.id, node]));
      return reflow(main.map(node => {
        const previous = previousById.get(node.id);
        if (node.type === 'island' && previous?.type === 'island' && previous.data.title === node.data.title && previous.measured) {
          return { ...node, measured: { ...previous.measured } };
        }
        // Dimension changes from React Flow are stored in this controlled state.
        // Preserve them across selection/viewport saves so unchanged cards stay
        // visible between the two clicks; changed content must be measured again.
        if (!previous?.measured?.width || !previous.measured.height
          || geometrySignature(previous) !== geometrySignature(node)) return node;
        return { ...node, measured: { ...previous.measured } };
      }));
    });
  }, [graph, cameraContext, viewState?.positionsByView, viewState?.positions, islands, details, selectedId, onCloseIsland, t]);
  const edges = useMemo(() => {
    const byId = new Map(nodes.map(n => [n.id, n]));
    const bounds = new Map(nodes.filter(n => !n.hidden).map(n => {
      const parent = n.parentId ? byId.get(n.parentId) : undefined;
      return [n.id, { x: n.position.x + (parent?.position.x ?? 0), y: n.position.y + (parent?.position.y ?? 0), width: n.measured?.width ?? Number(n.style?.width ?? cardSize.width), height: n.measured?.height ?? Number(n.style?.height ?? cardSize.height) }] as const;
    }));
    const channels = new Map<string, number>();
    const geometry = JSON.stringify([cameraContext, [...bounds], nodes.map(n => [n.id, n.parentId]),
      graph.edges.map(e => [e.source, e.target]), islands.map(i => [i.id, details[i.id]?.edges.map(e => [e.source, e.target])])]);
    if (routeCache.current.geometry !== geometry) routeCache.current = { geometry, routes: new Map() };
    // A graph-wide cap also bounds dense relation sets; no edge is dropped.
    const routingBudget = { remaining: 120000 };
    return [...edgesFor(graph, t), ...islands.flatMap(i => details[i.id] ? edgesFor(details[i.id], t, `island:${i.id}:`) : [])].map(e => {
      const key = JSON.stringify([e.source, e.target].sort()); const channel = channels.get(key) ?? 0; channels.set(key, channel + 1);
      const source = bounds.get(e.source); const target = bounds.get(e.target);
      const ownParent = byId.get(e.source)?.parentId === byId.get(e.target)?.parentId ? byId.get(e.source)?.parentId : undefined;
      const obstacles = [...bounds].filter(([id]) => id !== e.source && id !== e.target && id !== ownParent).map(([, rect]) => rect);
      const routeKey = JSON.stringify([e.source, e.target, channel]);
      let route = routeCache.current.routes.get(routeKey);
      if (!route && source && target) {
        route = routeEdge(source, target, obstacles, channel, routingBudget);
        routeCache.current.routes.set(routeKey, route);
      }
      return { ...e, data: { route, fallbackMessage: t('routingFallback') }, style: { ...e.style, opacity: selectedId && ![e.source, e.target].some(id => id === selectedId || id.endsWith(`:${selectedId}`)) ? .6 : 1 } };
    });
  }, [graph, cameraContext, islands, details, nodes, selectedId, t]);
  const loadedIslandKey = nodes.filter(n => n.type === 'island').map(n => n.id.slice(7)).join('|');
  return <section className="graph-editor"><div className="graph-toolbar"><span><i className="signal"/> {viewLabel(graph.viewKind, t)}</span><small>{graph.nodes.length} {t('nodes')} · {graph.edges.length} {t('edges')} · {Object.values(graph.hiddenCounts).reduce((a, b) => a + b, 0)} {t('hidden')}</small><button onClick={() => { void instance?.fitView({ padding: .16, maxZoom: 1 }); }}>{t('fit')}</button></div><div className="flow-container"><ReactFlow<CanvasNode> nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} defaultViewport={initialViewport} minZoom={0.25} maxZoom={2.5} onNodesChange={changes => { if (currentContext.current !== cameraContext) return; const local = manualPositions(); for (const change of changes) { if (change.type !== 'position' || !change.position) continue; const node = nodes.find(n => n.id === change.id); const point: [number, number] = [change.position.x, change.position.y]; if (node?.type === 'island') local.islands[node.id.slice(7)] = point; else if (node?.type === 'code' && !node.parentId) local.main[node.id] = point; } setNodes(current => changes.some(change => change.type === 'dimensions') ? reflow(applyNodeChanges(changes, current)) : applyNodeChanges(changes, current)); }} onNodeClick={(_, node) => { if (node.type === 'code') onSelect(node.data.item, node.data.detailSource); }} onNodeDoubleClick={(_, node) => { if (node.type === 'code' && !node.data.detailSource) onDetail(node.data.item); }} onNodeDragStop={(_, node) => { if (currentContext.current !== cameraContext) return; const local = manualPositions(); const point: [number, number] = [node.position.x, node.position.y]; if (node.type === 'island') { local.islands[node.id.slice(7)] = point; onMove(node.id.slice(7), node.position, true); } else if (node.type === 'code' && !node.data.detailSource) { local.main[node.id] = point; onMove(node.id, node.position, false); } }} onMoveEnd={(_, viewport) => onViewport(graph.viewKind, viewport)} onInit={setInstance} proOptions={{ hideAttribution: true }}><Camera context={cameraContext} graph={graph} viewState={viewState} restoreViewport={restoreViewport} loadedIslandKey={loadedIslandKey}/><Background gap={24} size={1} color="#26323e"/><Controls showInteractive={false}/></ReactFlow><div className="graph-legend">{graph.legend.map(item => <span key={item.type} title={item.label}><i style={{ background: edgeColors[item.type] ?? '#8192a8' }} className={item.style.includes('dash') ? 'dashed' : ''}/>{graphLabel(item.type, t)}</span>)}<span><i className="node-legend"/> {t('nodes')}</span></div>{graph.issues.length > 0 && <div className="graph-issues">{graph.issues.map(i => <p key={`${i.code}:${i.message}`}>{i.code}: {i.message}</p>)}</div>}</div></section>;
}
// Camera work belongs to semantic context, not query revisions or elapsed time.
function Camera({ context, graph, viewState, restoreViewport, loadedIslandKey }: {
  context: string; graph: Graph; viewState: ViewState | null; restoreViewport?: boolean; loadedIslandKey: string;
}) {
  const flow = useReactFlow<CanvasNode>();
  const store = useStoreApi<CanvasNode>();
  const initialized = useNodesInitialized();
  const width = useStore(state => state.width); const height = useStore(state => state.height);
  const measurements = useStore(state => JSON.stringify([...state.nodeLookup.values()].map(node =>
    [node.id, node.measured.width, node.measured.height, node.internals.positionAbsolute.x, node.internals.positionAbsolute.y])));
  const mounted = useRef(false);
  const [, refresh] = useState(0);
  const state = useRef({ context, changed: false, handled: false,
    mainIds: new Set(graph.nodes.map(n => n.id)),
    inFlight: null as { started: boolean } | null, attempted: new Set<string>(),
    initialIslands: new Set(viewState?.detailIslands.filter(i => i.viewKind === graph.viewKind).map(i => i.id)),
    revealed: new Set<string>() });
  const mainIds = JSON.stringify(graph.nodes.map(n => n.id));
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    if (state.current.context !== context) state.current = { context, changed: true, handled: false,
      mainIds: new Set(graph.nodes.map(n => n.id)),
      inFlight: null, attempted: new Set(),
      initialIslands: new Set(viewState?.detailIslands.filter(i => i.viewKind === graph.viewKind).map(i => i.id)), revealed: new Set() };
    const camera = state.current;
    if (camera.inFlight || !initialized || !flow.viewportInitialized || width <= 0 || height <= 0 || !graph.nodes.length) return;
    // Controlled nodes can lag the incoming graph by one render.
    const actualMain = flow.getNodes().filter(n => n.type === 'code' && !n.parentId).map(n => n.id);
    if (JSON.stringify(actualMain) !== mainIds) return;
    const loaded = loadedIslandKey ? loadedIslandKey.split('|') : [];
    const newlyOpened = loaded.filter(id => !camera.initialIslands.has(id) && !camera.revealed.has(id));
    const structural = graph.viewKind === 'Architecture' || graph.viewKind === 'ProjectStructure';
    const newlyAdded = structural ? graph.nodes.filter(n => !camera.mainIds.has(n.id)).map(n => n.id) : [];
    if (camera.handled && !newlyOpened.length && !newlyAdded.length) { camera.mainIds = new Set(graph.nodes.map(n => n.id)); return; }
    const attempt = JSON.stringify([camera.handled, newlyOpened, newlyAdded, width, height, measurements]);
    if (camera.attempted.has(attempt)) return;
    const operation = { started: false };
    camera.inFlight = operation;
    // A microtask lets an unmount/context change cancel pending work, without
    // introducing a measurement timer or an old-context camera animation.
    void Promise.resolve().then(async () => {
      if (!mounted.current || state.current !== camera || camera.inFlight !== operation) return;
      const current = store.getState();
      const measuredNodes = flow.getNodes().filter(node => !node.hidden);
      // Read readiness again at invocation, not just when the effect scheduled.
      // The bounds helper resolves parented nodes through the internal lookup.
      if (current.width <= 0 || current.height <= 0 || !current.panZoom || !measuredNodes.length
        || JSON.stringify(measuredNodes.filter(node => node.type === 'code' && !node.parentId).map(node => node.id)) !== mainIds
        || measuredNodes.filter(node => node.type === 'island').map(node => node.id.slice(7)).join('|') !== loadedIslandKey
        || measuredNodes.some(node => {
          const measured = flow.getInternalNode(node.id);
          return !measured || !(measured.measured.width! > 0) || !(measured.measured.height! > 0);
        })) {
        camera.inFlight = null;
        return;
      }
      const saved = viewState?.viewportByView[graph.viewKind];
      const rootMatches = restoreViewport ?? (viewState?.graphRoots[graph.viewKind] === (graph.rootIds[0] ?? null));
      const viewport = saved ? { x: saved.pan[0], y: saved.pan[1], zoom: saved.zoom } : null;
      const valid = viewport && Number.isFinite(viewport.x) && Number.isFinite(viewport.y)
        && Number.isFinite(viewport.zoom) && viewport.zoom >= .25 && viewport.zoom <= 2.5;
      const visible = valid && measuredNodes.some(n => {
        if (n.type !== 'code') return false;
        const measured = flow.getInternalNode(n.id);
        if (!measured?.measured.width || !measured.measured.height) return false;
        const position = measured.internals.positionAbsolute;
        const x = position.x * viewport.zoom + viewport.x; const y = position.y * viewport.zoom + viewport.y;
        return x < current.width && y < current.height && x + measured.measured.width * viewport.zoom > 0 && y + measured.measured.height * viewport.zoom > 0;
      });
      const restoring = !camera.handled && !camera.changed && rootMatches && visible && !newlyOpened.length && !newlyAdded.length;
      operation.started = true;
      camera.attempted.add(attempt);
      try {
        const target = restoring ? viewport! : getViewportForBounds(
          flow.getNodesBounds(measuredNodes), current.width, current.height, .25, 1, .16);
        // fitView queues work against a later node batch. A zero-duration viewport
        // write applies these measured bounds now, before a new context can render.
        if (!mounted.current || state.current !== camera || camera.inFlight !== operation) return;
        const result = await flow.setViewport(target, { duration: 0 });
        if (!mounted.current || state.current !== camera) return;
        if (result === true) {
          camera.handled = true;
          camera.mainIds = new Set(graph.nodes.map(n => n.id));
          loaded.forEach(id => camera.revealed.add(id));
        }
      } catch {
        // A failed operation stays unhandled; retry only for new readiness or islands.
      } finally {
        if (camera.inFlight === operation) camera.inFlight = null;
        if (mounted.current && state.current === camera) refresh(value => value + 1);
      }
    });
    return () => {
      // Same-context viewport saves must not discard an operation already underway.
      if (!operation.started && camera.inFlight === operation) camera.inFlight = null;
    };
    // Recheck controlled nodes after every commit, including the render in which
    // GraphCanvas catches up with an incoming graph or selection change.
  });
  return null;
}
function toXY(point: Island['position'] & [number, number]) { return { x: point[0], y: point[1] }; }
