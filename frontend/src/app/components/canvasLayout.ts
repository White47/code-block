import type { Graph } from '../../types';

export type Size = { width: number; height: number };
export type Rect = Size & { x: number; y: number };
export const cardSize = { width: 230, height: 110 };
export function overlaps(a: Rect, b: Rect, gap = 0): boolean {
  return a.x < b.x + b.width + gap && a.x + a.width + gap > b.x
    && a.y < b.y + b.height + gap && a.y + a.height + gap > b.y;
}
// Hints define ordering, not a pixel pitch. Each row uses the measured maximum.
export function layoutCards(graph: Graph, sizes: Map<string, Size> = new Map(), saved: Record<string, [number, number]> = {}, fixedObstacles: Rect[] = []): Record<string, [number, number]> {
  const incoming = new Map(graph.nodes.map(n => [n.id, 0]));
  const outgoing = new Map<string, string[]>();
  for (const e of graph.edges) {
    if (e.type === 'LOOP_BACK' || e.source === e.target || !incoming.has(e.source) || !incoming.has(e.target)) continue;
    incoming.set(e.target, incoming.get(e.target)! + 1);
    outgoing.set(e.source, [...(outgoing.get(e.source) ?? []), e.target]);
  }
  const levels = new Map<string, number>();
  const queue = graph.nodes.filter(n => incoming.get(n.id) === 0).map(n => n.id);
  queue.forEach(id => levels.set(id, 0));
  for (let i = 0; i < queue.length; i++) for (const target of outgoing.get(queue[i]) ?? []) {
    levels.set(target, Math.max(levels.get(target) ?? 0, (levels.get(queue[i]) ?? 0) + 1));
    incoming.set(target, incoming.get(target)! - 1);
    if (!incoming.get(target)) queue.push(target);
  }
  const rows = new Map<number, string[]>();
  graph.nodes.forEach((n, i) => {
    const row = graph.layoutHints[n.id]?.[1] ?? (levels.get(n.id) ?? i) * 155;
    rows.set(row, [...(rows.get(row) ?? []), n.id]);
  });
  const result: Record<string, [number, number]> = {};
  const occupied: Rect[] = [...fixedObstacles, ...Object.entries(saved).filter(([id]) => incoming.has(id)).map(([id, [x, y]]) => ({ x, y, ...(sizes.get(id) ?? cardSize) }))];
  let y = 50;
  for (const [hintY, ids] of [...rows].sort((a, b) => a[0] - b[0])) {
    y = Math.max(y, hintY);
    let x = 60;
    let height = 0;
    ids.sort((a, b) => (graph.layoutHints[a]?.[0] ?? 0) - (graph.layoutHints[b]?.[0] ?? 0));
    for (const id of ids) {
      const size = sizes.get(id) ?? cardSize;
      height = Math.max(height, size.height);
      if (saved[id]) { result[id] = saved[id]; continue; }
      x = Math.max(x, (graph.layoutHints[id]?.[0] ?? 0) * 280 / 210);
      const rect = { x, y, ...size };
      // Moving right monotonically clears all pinned and already placed cards.
      for (let attempt = 0; attempt <= occupied.length; attempt++) {
        const hits = occupied.filter(other => overlaps(rect, other, 40));
        if (!hits.length) break;
        rect.x = Math.max(...hits.map(other => other.x + other.width + 40));
      }
      result[id] = [rect.x, rect.y]; occupied.push(rect); x = rect.x + size.width + 50;
    }
    y += height + 45;
  }
  return result;
}
