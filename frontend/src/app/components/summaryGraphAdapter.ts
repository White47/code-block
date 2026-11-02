import type { EdgePresentation, Graph, SummaryRelation, SummaryStep } from '../../types';
import type { Translate } from '../i18n';

const roles = { task: 'Task', decision: 'Branch', loop: 'Loop', result: 'Output' } as const;
const labelKeys = {
  control_next: 'summaryControl', branch_true: 'summaryBranchTrue', branch_false: 'summaryBranchFalse',
  loop_back: 'summaryLoopBack', contains: 'summaryContains', data_artifact: 'summaryArtifact',
  static_call: 'summaryStaticCall', model_use: 'summaryModelUse', shared: 'summarySharedModule',
} as const;

export function summaryRelationLabel(edge: SummaryRelation, t: Translate): string {
  const parameter = edge.kind === 'shared' && edge.sharingKind === 'parameter';
  let label = t(parameter ? 'summarySharedParameter' : labelKeys[edge.kind]);
  if (parameter && edge.sharingAttributes) label += ` ${[...new Set(edge.sharingAttributes)].join(' / ')}`;
  return label;
}

/** Presentation only. These edges are never native CodeGraph edges or query inputs. */
export function buildSummaryGraph(
  steps: SummaryStep[], relations: SummaryRelation[], names: ReadonlyMap<string, string>, t: Translate, columns: 1 | 2 | 3 = 3,
): { graph: Graph; edgePresentation: Record<string, EdgePresentation>; rejectedCount: number } {
  const counts = new Map<string, number>();
  steps.forEach(step => counts.set(step.id, (counts.get(step.id) ?? 0) + 1));
  const chosen = steps.filter(step => step.id && counts.get(step.id) === 1
    && step.sourceAnchors.length > 0 && step.ownerSymbolIds.length > 0 && step.evidenceIds.length > 0).slice(0, 16);
  const ids = new Set(chosen.map(step => step.id));
  const links = relations.filter(edge => ids.has(edge.source) && ids.has(edge.target)).slice(0, 32);
  const incoming = new Map(chosen.map(step => [step.id, 0]));
  const outgoing = new Map<string, string[]>();
  for (const edge of links) {
    if (edge.kind === 'loop_back' || edge.kind === 'shared' || edge.source === edge.target) continue;
    incoming.set(edge.target, incoming.get(edge.target)! + 1);
    outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge.target]);
  }
  const roots = chosen.filter(step => incoming.get(step.id) === 0).map(step => step.id);
  const levels = new Map(roots.map(id => [id, 0])); const queue = [...roots];
  for (let index = 0; index < queue.length; index++) for (const target of outgoing.get(queue[index]) ?? []) {
    levels.set(target, Math.max(levels.get(target) ?? 0, (levels.get(queue[index]) ?? 0) + 1));
    incoming.set(target, incoming.get(target)! - 1);
    if (incoming.get(target) === 0) queue.push(target);
  }
  let fallback = Math.max(0, ...levels.values()) + 1;
  const rows = new Map<number, string[]>(); const hints: Graph['layoutHints'] = {};
  for (const step of chosen) {
    const level = levels.get(step.id) ?? fallback++;
    rows.set(level, [...(rows.get(level) ?? []), step.id]);
  }
  let row = 0;
  for (const [, members] of [...rows].sort(([left], [right]) => left - right)) {
    members.forEach((id, index) => { hints[id] = [(index % columns) * 210, (row + Math.floor(index / columns)) * 155]; });
    row += Math.ceil(members.length / columns);
  }
  const presentation: Record<string, EdgePresentation> = {};
  const edges = links.map((edge, index) => {
    const parameter = edge.kind === 'shared' && edge.sharingKind === 'parameter';
    const label = summaryRelationLabel(edge, t);
    const type = `summary-${edge.kind.replaceAll('_', '-')}${edge.kind === 'shared' ? parameter ? '-parameter' : '-module' : ''}`;
    presentation[`edge:${index}`] = {
      label, color: edge.kind === 'data_artifact' ? '#67bcff' : edge.kind === 'shared' ? '#e897cb'
        : edge.kind === 'branch_false' || edge.kind === 'loop_back' ? '#eeb478' : '#4dddc3',
      dashed: edge.kind === 'contains' || edge.kind === 'static_call' || edge.kind === 'shared'
        || edge.confidence !== 'Exact',
    };
    presentation[type] = { ...presentation[`edge:${index}`], label: t(parameter ? 'summarySharedParameter' : labelKeys[edge.kind]) };
    return { source: edge.source, target: edge.target, type, confidence: edge.confidence,
      sourceAnchors: edge.sourceAnchors, codeEdgeIds: [], semanticComponentIds: [] };
  });
  const rejectedCount = steps.length - chosen.length + relations.length - links.length;
  return {
    graph: {
      viewKind: 'Overview', layoutKind: 'vertical', layoutHints: hints,
      nodes: chosen.map(step => ({ id: step.id, displayName: step.title,
        codeName: step.codeName ?? (step.ownerSymbolIds.map(id => names.get(id)).filter(Boolean).slice(0, 2).join(' / ')
          || step.sourceAnchors[0].filePath),
        role: roles[step.kind], confidence: step.confidence, sourceAnchors: step.sourceAnchors,
        codeNodeIds: step.codeNodeIds, semanticComponentIds: [], memberIds: [],
        metadata: { summary: true, basis: step.basis, sourceLocationCount: step.sourceLocationCount },
      })),
      edges, rootIds: roots.length ? roots : chosen.slice(0, 1).map(step => step.id),
      legend: [...new Map(edges.map((edge, index) => [edge.type,
        { type: edge.type, label: presentation[`edge:${index}`].label, style: 'summary' }])).values()],
      hiddenCounts: rejectedCount ? { summary: rejectedCount } : {}, queryContext: { controlMode: 'none' }, issues: [],
    }, edgePresentation: presentation, rejectedCount,
  };
}
