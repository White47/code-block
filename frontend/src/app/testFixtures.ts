import type { Anchor, Graph, Overview, Session, ViewState, VisibleNode } from '../types';
export const anchor: Anchor = { filePath: 'model.py', startLine: 20, endLine: 22, startColumn: null, endColumn: null };
export const selectedNode: VisibleNode = { id: 'layer', displayName: 'Encoder', role: 'Layer', codeName: 'encoder', confidence: 'Exact', sourceAnchors: [anchor], codeNodeIds: ['layer'], semanticComponentIds: [], memberIds: [], metadata: {} };
export const graph: Graph = { viewKind: 'Architecture', nodes: [selectedNode], edges: [], rootIds: ['model'], layoutKind: 'vertical', layoutHints: {}, legend: [], hiddenCounts: {}, queryContext: {}, issues: [] };
export const viewState: ViewState = { activeView: 'Architecture', selectedId: 'layer', openTabs: ['Overview', 'Architecture'], graphRoots: { Architecture: 'model' }, expandedIds: [], positions: {}, positionsByView: {}, zoom: 1, pan: [0, 0], viewportByView: { Architecture: { zoom: .7, pan: [32, 56] } }, detailIslands: [], filters: [] };
export const session: Session & { hasAnalysis: boolean } = { projectPath: 'E:\\project-a', status: 'Ready', hasAnalysis: true, cacheIssue: null, outdatedReasons: [], recentProjects: ['E:\\project-b'] };
export const overview: Overview = { projectName: 'project-a', projectPath: session.projectPath!, language: 'Python', framework: null, models: [], entries: [], mainAreas: [], health: { filesParsed: 1, filesTotal: 1, symbolsResolved: 1, exact: 1, probable: 0, unresolved: 0, issueCount: 0 } };
export function ok<T>(data: T) { return { ok: true as const, schemaVersion: 1, data }; }
