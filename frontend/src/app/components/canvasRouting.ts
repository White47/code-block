import type { Rect } from './canvasLayout';
type Point = { x: number; y: number };
export type CanvasRoute = { path: string; x: number; y: number; fallback: boolean };
export type RoutingBudget = { remaining: number };
function blocked(a: Point, b: Point, obstacles: Rect[]): boolean {
  return obstacles.some(r => a.x === b.x
    ? a.x > r.x && a.x < r.x + r.width && Math.max(a.y, b.y) > r.y && Math.min(a.y, b.y) < r.y + r.height
    : a.y > r.y && a.y < r.y + r.height && Math.max(a.x, b.x) > r.x && Math.min(a.x, b.x) < r.x + r.width);
}
function routeResult(points: Point[], fallback: boolean): CanvasRoute {
  let length = 0;
  const lengths = points.slice(1).map((p, i) => { const value = Math.abs(p.x - points[i].x) + Math.abs(p.y - points[i].y); length += value; return value; });
  let remaining = length / 2; let label = points[0];
  for (let i = 0; i < lengths.length; i++) {
    if (remaining <= lengths[i]) {
      const ratio = lengths[i] ? remaining / lengths[i] : 0;
      label = { x: points[i].x + (points[i + 1].x - points[i].x) * ratio, y: points[i].y + (points[i + 1].y - points[i].y) * ratio }; break;
    }
    remaining -= lengths[i];
  }
  return { path: points.map((p, i) => `${i ? 'L' : 'M'} ${p.x} ${p.y}`).join(' '), ...label, fallback };
}
// Compressed orthogonal grid; bounded A* keeps pathological manual overlaps finite.
export function routeEdge(source: Rect, target: Rect, obstacles: Rect[], channel: number, budget: RoutingBudget = { remaining: 12000 }): CanvasRoute {
  const start = { x: source.x + source.width / 2, y: source.y + source.height };
  const end = { x: target.x + target.width / 2, y: target.y };
  const clearance = 16 + channel * 9;
  const all = [...obstacles, source, target];
  const portDistance = (p: Point, direction: 1 | -1) => {
    let available = clearance;
    for (const r of all) {
      if (p.x <= r.x || p.x >= r.x + r.width) continue;
      const gap = direction === 1 ? r.y - p.y : p.y - (r.y + r.height);
      if (gap >= 0) available = Math.min(available, gap / 2);
    }
    return available;
  };
  const sourcePort = { x: start.x, y: start.y + portDistance(start, 1) };
  const targetPort = { x: end.x, y: end.y - portDistance(end, -1) };
  const offset = channel * 18;
  const a = { x: start.x + offset, y: sourcePort.y };
  const b = { x: end.x + offset, y: targetPort.y };
  const fallback = () => {
    const outside = Math.max(...all.map(r => r.x + r.width), start.x, end.x) + clearance;
    return routeResult([start, sourcePort, a, { x: outside, y: a.y }, { x: outside, y: b.y }, b, targetPort, end], true);
  };
  if (budget.remaining <= 0) return fallback();
  const accessBlocked = blocked(start, sourcePort, obstacles) || blocked(sourcePort, a, all)
    || blocked(b, targetPort, all) || blocked(targetPort, end, obstacles);
  const finish = (points: Point[]) => routeResult([start, sourcePort, ...points, targetPort, end], accessBlocked);
  // Most measured layouts have a clear row or column corridor. Check actual
  // segments before allocating a grid, including every visible obstacle.
  const candidates: Point[][] = [
    [a, { x: a.x, y: b.y }, b], [a, { x: b.x, y: a.y }, b],
  ];
  const lanes = [...new Set(all.flatMap(r => [r.x - clearance, r.x + r.width + clearance]))]
    .sort((l, r) => (Math.abs(l - a.x) + Math.abs(l - b.x)) - (Math.abs(r - a.x) + Math.abs(r - b.x)));
  for (const x of lanes.slice(0, 24)) candidates.push([a, { x, y: a.y }, { x, y: b.y }, b]);
  for (const points of candidates) {
    if (budget.remaining <= 0) return fallback();
    budget.remaining--;
    if (points.slice(1).every((p, i) => !blocked(points[i], p, all))) return finish(points);
  }
  const rows = [...new Set(all.flatMap(r => [r.y - clearance, r.y + r.height + clearance]))]
    .sort((l, r) => Math.abs(l - b.y) - Math.abs(r - b.y));
  for (const x of lanes.slice(0, 8)) for (const y of rows.slice(0, 8)) {
    if (budget.remaining <= 0) return fallback();
    budget.remaining--;
    const points = [a, { x, y: a.y }, { x, y }, { x: b.x, y }, b];
    if (points.slice(1).every((p, i) => !blocked(points[i], p, all))) return finish(points);
  }
  const xs = [...new Set([a.x, b.x, ...all.flatMap(r => [r.x - clearance, r.x + r.width + clearance])])].sort((l, r) => l - r);
  const ys = [...new Set([a.y, b.y, ...all.flatMap(r => [r.y - clearance, r.y + r.height + clearance])])].sort((l, r) => l - r);
  const cols = xs.length;
  const first = ys.indexOf(a.y) * cols + xs.indexOf(a.x);
  const last = ys.indexOf(b.y) * cols + xs.indexOf(b.x);
  const point = (id: number): Point => ({ x: xs[id % cols], y: ys[Math.floor(id / cols)] });
  const distance = new Map<number, number>([[first, 0]]);
  const previous = new Map<number, number>();
  const vertical = new Map<number, Rect[]>(); const horizontal = new Map<number, Rect[]>();
  const segmentBlocked = (p: Point, q: Point) => {
    const cache = p.x === q.x ? vertical : horizontal;
    const coordinate = p.x === q.x ? p.x : p.y;
    let matching = cache.get(coordinate);
    if (!matching) {
      matching = all.filter(r => p.x === q.x ? p.x > r.x && p.x < r.x + r.width : p.y > r.y && p.y < r.y + r.height);
      cache.set(coordinate, matching);
    }
    return blocked(p, q, matching);
  };
  const heap: { id: number; score: number; cost: number }[] = [];
  const push = (entry: typeof heap[number]) => {
    heap.push(entry); let i = heap.length - 1;
    while (i > 0) { const parent = (i - 1) >> 1; if (heap[parent].score <= entry.score) break; heap[i] = heap[parent]; i = parent; } heap[i] = entry;
  };
  const pop = () => {
    const head = heap[0]; const tail = heap.pop()!;
    if (heap.length) { let i = 0; while (i * 2 + 1 < heap.length) { let child = i * 2 + 1; if (child + 1 < heap.length && heap[child + 1].score < heap[child].score) child++; if (heap[child].score >= tail.score) break; heap[i] = heap[child]; i = child; } heap[i] = tail; } return head;
  };
  push({ id: first, score: 0, cost: 0 });
  let found = false;
  for (let count = 0; heap.length && count < 12000 && budget.remaining > 0; count++) {
    budget.remaining--;
    const current = pop(); if (current.cost !== distance.get(current.id)) continue;
    if (current.id === last) { found = true; break; }
    const p = point(current.id); const column = current.id % cols; const row = Math.floor(current.id / cols);
    const neighbors = [column > 0 ? current.id - 1 : -1, column + 1 < cols ? current.id + 1 : -1, row > 0 ? current.id - cols : -1, row + 1 < ys.length ? current.id + cols : -1];
    for (const next of neighbors) {
      if (next < 0) continue; const q = point(next);
      if (segmentBlocked(p, q)) continue;
      const cost = current.cost + Math.abs(q.x - p.x) + Math.abs(q.y - p.y);
      if (cost >= (distance.get(next) ?? Infinity)) continue;
      distance.set(next, cost); previous.set(next, current.id);
      push({ id: next, cost, score: cost + Math.abs(q.x - b.x) + Math.abs(q.y - b.y) });
    }
  }
  if (found) {
    const points = [b]; let id = last;
    while (id !== first) { id = previous.get(id)!; points.push(point(id)); }
    return finish(points.reverse());
  }
  // Still show every relation when pinned overlaps block a port or budget runs out.
  return fallback();
}
