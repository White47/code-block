import { useEffect, useMemo, useRef, useState } from 'react';
import { Background, BaseEdge, Controls, EdgeLabelRenderer, Handle, MarkerType, Position, ReactFlow, applyNodeChanges, getSmoothStepPath, type Edge, type EdgeProps, type Node, type NodeProps, type ReactFlowInstance, type Viewport } from '@xyflow/react';
import type { Graph, Island, ViewKind, ViewState, VisibleNode } from '../../types';
import type { Translate } from '../i18n';
import { viewLabel } from './EditorTabs';
type GraphNode = Node<{ item: VisibleNode; detailSource: string | null; index: number }, 'code'>;
type IslandNode = Node<{ title: string; close: () => void }, 'island'>;
type CanvasNode = GraphNode | IslandNode;
const edgeColors: Record<string, string> = { CONTAINS: '#687e90', CALLS: '#6fafff', CONTROL_FLOW: '#4dddc3', BRANCH_TRUE: '#6bce95', BRANCH_FALSE: '#f6ad69', LOOP_BACK: '#f6ad69', DATA_FLOW: '#67bcff', GRADIENT_FLOW: '#e897cb', READS: '#b4a2ed', WRITES: '#b4a2ed' };
function CodeNode({ data, selected }: NodeProps<GraphNode>) {
  const role = data.item.role ?? 'Symbol';
  return <div className={`code-node ${selected ? 'selected' : ''}`} data-role={role}><Handle type="target" position={Position.Top}/><div className="node-meta"><span>{String(data.index + 1).padStart(2, '0')}</span><span>{role}</span><i className={`signal ${data.item.confidence === 'Exact' ? '' : 'amber'}`}/></div><strong>{data.item.displayName}</strong><code>{data.item.codeName ?? data.item.sourceAnchors[0]?.filePath.split(/[\\/]/).pop() ?? data.item.id}</code>{data.item.metadata.shape && <small>[{String(data.item.metadata.shape)}]</small>}<Handle type="source" position={Position.Bottom}/></div>;
}
function DetailIsland({ data }: NodeProps<IslandNode>) { return <div className="detail-island"><div className="island-heading"><span>◇ {data.title}</span><button className="nodrag" onClick={data.close} aria-label="Close detail island">×</button></div></div>; }
function SemanticEdge(props: EdgeProps) { const [path, x, y] = getSmoothStepPath({ ...props, borderRadius: 12 }); return <><BaseEdge id={props.id} path={path} markerEnd={props.markerEnd} style={props.style}/><EdgeLabelRenderer><div className="edge-label nodrag nopan" style={{ transform: `translate(-50%, -50%) translate(${x}px, ${y}px)` }}>{props.label}</div></EdgeLabelRenderer></>; }
const nodeTypes = { code: CodeNode, island: DetailIsland };
const edgeTypes = { semantic: SemanticEdge };
// Python hints use a 210px column pitch. Expand automatic X coordinates to
// 280px so 220–230px cards have room; saved user positions bypass this function.
const AUTO_HINT_X_SCALE = 280 / 210;
export function verticalPositions(graph: Graph): Record<string, [number, number]> {
  const levels = new Map<string, number>(); const ids = new Set(graph.nodes.map(n => n.id));
  const incoming = new Map(graph.nodes.map(n => [n.id, 0]));
  graph.edges.filter(e => e.type !== 'LOOP_BACK' && e.source !== e.target).forEach(e => { if (ids.has(e.source) && ids.has(e.target)) incoming.set(e.target, (incoming.get(e.target) ?? 0) + 1); });
  const queue = graph.nodes.filter(n => incoming.get(n.id) === 0).map(n => n.id); queue.forEach(id => levels.set(id, 0));
  for (let cursor = 0; cursor < queue.length; cursor++) { const id = queue[cursor]; graph.edges.filter(e => e.source === id && e.type !== 'LOOP_BACK' && e.target !== id && ids.has(e.target)).forEach(e => { levels.set(e.target, Math.max(levels.get(e.target) ?? 0, (levels.get(id) ?? 0) + 1)); const remaining = (incoming.get(e.target) ?? 0) - 1; incoming.set(e.target, remaining); if (remaining === 0) queue.push(e.target); }); }
  const columns = new Map<number, number>();
  return Object.fromEntries(graph.nodes.map((n, i) => { const level = levels.get(n.id) ?? i; const column = columns.get(level) ?? 0; columns.set(level, column + 1); const hint = graph.layoutHints[n.id]; return [n.id, hint ? [hint[0] * AUTO_HINT_X_SCALE, hint[1]] : [column * 280 + 60, level * 155 + 50]]; }));
}
function edgesFor(graph: Graph, prefix = ''): Edge[] { return graph.edges.map((edge, i) => ({ id: `${prefix}edge:${i}`, source: prefix + edge.source, target: prefix + edge.target, type: 'semantic', label: edge.type, style: { stroke: edgeColors[edge.type] ?? '#8192a8', strokeWidth: 1.7, strokeDasharray: edge.type === 'CONTAINS' ? '4 5' : edge.type === 'LOOP_BACK' ? '7 4' : edge.confidence === 'Probable' ? '3 4' : undefined }, markerEnd: { type: MarkerType.ArrowClosed, color: edgeColors[edge.type] ?? '#8192a8' }, labelStyle: { fill: '#a4b3c5', fontSize: 10 }, labelBgStyle: { fill: '#111820', fillOpacity: 0.92 } })); }
export default function GraphEditor({ graph, viewState, details, selectedId, onSelect, onDetail, onMove, onViewport, onCloseIsland, t }: {
  graph: Graph; viewState: ViewState | null; details: Record<string, Graph>; selectedId: string | null;
  onSelect: (node: VisibleNode, detailSource: string | null) => void; onDetail: (node: VisibleNode) => void;
  onMove: (id: string, position: { x: number; y: number }, island: boolean) => void;
  onViewport: (view: ViewKind, viewport: Viewport) => void; onCloseIsland: (id: string) => void; t: Translate;
}) {
  const [nodes, setNodes] = useState<CanvasNode[]>([]);
  const [instance, setInstance] = useState<ReactFlowInstance<CanvasNode> | null>(null);
  const initialIslandIds = useRef(new Set(viewState?.detailIslands.filter(i => i.viewKind === graph.viewKind).map(i => i.id)));
  const fittedIslands = useRef(new Set<string>()); const fittedMain = useRef(false);
  const restoredViewport = useRef(Boolean(viewState?.viewportByView[graph.viewKind]));
  const islands = useMemo(() => viewState?.detailIslands.filter(i => i.viewKind === graph.viewKind) ?? [], [viewState?.detailIslands, graph.viewKind]);
  const initialViewport = useMemo(() => { const v = viewState?.viewportByView[graph.viewKind]; return v ? { x: v.pan[0], y: v.pan[1], zoom: v.zoom } : { x: 30, y: 30, zoom: 0.8 }; }, [graph.viewKind, viewState?.viewportByView]);
  useEffect(() => {
    const positions = verticalPositions(graph); const saved = viewState?.positionsByView[graph.viewKind] ?? viewState?.positions ?? {};
    const neighbors = new Set(graph.edges.filter(e => e.source === selectedId || e.target === selectedId).flatMap(e => [e.source, e.target]));
    const main: CanvasNode[] = graph.nodes.map((item, index) => ({ id: item.id, type: 'code', position: toXY(saved[item.id] ?? positions[item.id]), data: { item, detailSource: null, index }, selected: selectedId === item.id, className: selectedId ? neighbors.has(item.id) || item.id === selectedId ? 'neighbor-node' : 'dimmed-node' : '' }));
    islands.forEach((island, islandIndex) => {
      const detail = details[island.id]; if (!detail) return;
      const points = verticalPositions(detail); const minX = Math.min(0, ...Object.values(points).map(p => p[0])); const minY = Math.min(0, ...Object.values(points).map(p => p[1]));
      const width = Math.max(320, ...Object.values(points).map(p => p[0] - minX + 300)); const height = Math.max(220, ...Object.values(points).map(p => p[1] - minY + 180));
      const parent = `island:${island.id}`;
      main.push({ id: parent, type: 'island', position: toXY(island.position ?? [520 + islandIndex * 80, 80 + islandIndex * 50]), data: { title: graph.nodes.find(n => n.id === island.sourceNodeId)?.displayName ?? island.sourceNodeId, close: () => onCloseIsland(island.id) }, style: { width, height }, zIndex: -1 });
      const detailNeighbors = new Set(detail.edges.filter(e => e.source === selectedId || e.target === selectedId).flatMap(e => [e.source, e.target]));
      detail.nodes.forEach((item, index) => main.push({ id: `${parent}:${item.id}`, type: 'code', parentId: parent, extent: 'parent', draggable: false, position: { x: points[item.id][0] - minX + 25, y: points[item.id][1] - minY + 55 }, data: { item, detailSource: island.sourceNodeId, index }, selected: selectedId === item.id, className: selectedId && detail.nodes.some(n => n.id === selectedId) ? detailNeighbors.has(item.id) || item.id === selectedId ? 'neighbor-node' : 'dimmed-node' : '' }));
    });
    setNodes(main);
  }, [graph, viewState?.positionsByView, viewState?.positions, islands, details, selectedId, onCloseIsland]);
  const edges = useMemo(() => [...edgesFor(graph), ...islands.flatMap(i => details[i.id] ? edgesFor(details[i.id], `island:${i.id}:`) : [])].map(e => ({ ...e, style: { ...e.style, opacity: selectedId && ![e.source, e.target].some(id => id === selectedId || id.endsWith(`:${selectedId}`)) ? .25 : 1 } })), [graph, islands, details, selectedId]);
  const loadedIslandKey = nodes.filter(n => n.type === 'island').map(n => n.id.slice(7)).join('|');
  useEffect(() => {
    if (!instance || !nodes.length) return;
    const loaded = loadedIslandKey ? loadedIslandKey.split('|') : [];
    const newlyOpened = loaded.filter(id => (!restoredViewport.current || !initialIslandIds.current.has(id)) && !fittedIslands.current.has(id));
    if ((fittedMain.current || restoredViewport.current) && !newlyOpened.length) return;
    const timer = setTimeout(() => { fittedMain.current = true; newlyOpened.forEach(id => fittedIslands.current.add(id)); void instance.fitView({ padding: .16, maxZoom: 1, duration: 180 }); }, 80);
    return () => clearTimeout(timer);
  }, [instance, loadedIslandKey, nodes.length]);
  return <section className="graph-editor"><div className="graph-toolbar"><span><i className="signal"/> {viewLabel(graph.viewKind, t)}</span><small>{graph.nodes.length} {t('nodes')} · {graph.edges.length} {t('edges')} · {Object.values(graph.hiddenCounts).reduce((a, b) => a + b, 0)} {t('hidden')}</small><button onClick={() => { void instance?.fitView({ padding: .16, maxZoom: 1 }); }}>{t('fit')}</button></div><div className="flow-container"><ReactFlow<CanvasNode> nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} defaultViewport={initialViewport} minZoom={0.25} maxZoom={2.5} onNodesChange={changes => setNodes(current => applyNodeChanges(changes, current))} onNodeClick={(_, node) => { if (node.type === 'code') onSelect(node.data.item, node.data.detailSource); }} onNodeDoubleClick={(_, node) => { if (node.type === 'code' && !node.data.detailSource) onDetail(node.data.item); }} onNodeDragStop={(_, node) => { if (node.type === 'island') onMove(node.id.slice(7), node.position, true); else if (node.type === 'code' && !node.data.detailSource) onMove(node.id, node.position, false); }} onMoveEnd={(_, viewport) => onViewport(graph.viewKind, viewport)} onInit={flow => { setInstance(flow); if (viewState?.viewportByView[graph.viewKind]) void flow.setViewport(initialViewport); }} proOptions={{ hideAttribution: true }}><Background gap={24} size={1} color="#26323e"/><Controls showInteractive={false}/></ReactFlow><div className="graph-legend">{graph.legend.map(item => <span key={item.type} title={item.label}><i style={{ background: edgeColors[item.type] ?? '#8192a8' }} className={item.style.includes('dash') ? 'dashed' : ''}/>{item.type}</span>)}<span><i className="node-legend"/> {t('nodes')}</span></div>{graph.issues.length > 0 && <div className="graph-issues">{graph.issues.map(i => <p key={`${i.code}:${i.message}`}>{i.code}: {i.message}</p>)}</div>}</div></section>;
}
function toXY(point: Island['position'] & [number, number]) { return { x: point[0], y: point[1] }; }
