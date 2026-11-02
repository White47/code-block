import type { HumanUnderstanding, SummaryContext, SummaryRelation, SummaryRelationKind, SummaryStep } from '../../types';
import type { AssistantAnswer, ProjectUnderstanding } from './types';
import { understandingItems, type EffectiveUnderstanding } from './project-understanding';

export interface AiAnnotation {
  itemId: string;
  section: AssistantAnswer['sections'][number]['kind'];
  text: string;
  basis: 'ai_inference' | 'unknown';
  evidenceIds: string[];
}
export interface ContextAnnotations {
  nodeAnnotations: Record<string, AiAnnotation[]>;
  relationAnnotations: Record<string, AiAnnotation[]>;
  omittedCount: number;
}
export interface HumanUnderstandingEnhancement {
  artifactId: string;
  revision: number;
  incomplete: boolean;
  coverage: ProjectUnderstanding['coverage'];
  coverageGaps: string[];
  byContext: Record<string, ContextAnnotations>;
  projectAnnotations: AiAnnotation[];
  omittedProjectCount: number;
}

export const summaryContextKey = (context: SummaryContext) => JSON.stringify([context.kind, context.id]);
/** Display relation identity, never an authority for native navigation. */
export const summaryRelationKey = (relation: SummaryRelation) => JSON.stringify([
  relation.source, relation.target, relation.kind, relation.sharingKind ?? null, relation.sharingAttributes ?? null,
  [...relation.evidenceIds].sort(), [...relation.codeNodeIds].sort(),
  relation.sourceAnchors.map(anchor => JSON.stringify(anchor)).sort(),
]);
const compatible: Record<AssistantAnswer['relationFacts'][number]['category'], readonly SummaryRelationKind[]> = {
  call: ['static_call', 'model_use'], control: ['control_next', 'branch_true', 'branch_false', 'loop_back'],
  data: ['data_artifact', 'shared'], structure: ['contains', 'shared'],
};

function contextBindings(steps: SummaryStep[], relations: SummaryRelation[], model: EffectiveUnderstanding) {
  const aliases = new Map<string, Set<string>>();
  for (const step of steps) for (const alias of [step.id, ...step.ownerSymbolIds, ...step.codeNodeIds]) {
    if (!aliases.has(alias)) aliases.set(alias, new Set());
    aliases.get(alias)!.add(step.id);
  }
  const unique = (alias: string) => {
    const candidates = aliases.get(alias);
    return candidates?.size === 1 ? [...candidates][0] : undefined;
  };
  const evidence = new Set(model.answer.evidence.map(card => card.evidenceId));
  const bindings = new Map<string, SummaryRelation[]>();
  for (const fact of model.answer.relationFacts) {
    if (!evidence.has(fact.evidenceId) || !['Exact', 'Probable'].includes(fact.confidence)) continue;
    const source = unique(fact.sourceId); const target = unique(fact.targetId);
    if (!source || !target) continue;
    const matches = relations.filter(edge => edge.source === source && edge.target === target && compatible[fact.category].includes(edge.kind));
    if (matches.length) bindings.set(fact.evidenceId, [...(bindings.get(fact.evidenceId) ?? []), ...matches]);
  }
  return bindings;
}

/** Annotation only: the effective artifact is checked by the caller; static graph inputs stay untouched. */
export function projectHumanUnderstandingEnhancement(understanding: HumanUnderstanding | null | undefined,
  model: EffectiveUnderstanding | null): HumanUnderstandingEnhancement | null {
  if (!model) return null;
  const contexts = [
    ...(understanding?.architecture ? [{ context: { kind: 'architecture', id: understanding.architecture.id } as SummaryContext,
      steps: understanding.architecture.areas, relations: understanding.architecture.relations }] : []),
    ...(understanding?.flows ?? []).map(flow => ({ context: { kind: 'task_flow', id: flow.id } as SummaryContext, steps: flow.steps, relations: flow.relations })),
  ].map(value => ({ ...value, bindings: contextBindings(value.steps, value.relations, model),
    nodes: new Map<string, Map<string, AiAnnotation>>(), edges: new Map<string, Map<string, AiAnnotation>>(), count: 0, omittedCount: 0 }));
  const projectAnnotations: AiAnnotation[] = [];
  let omittedProjectCount = 0;
  const evidence = new Set(model.answer.evidence.map(card => card.evidenceId));
  const add = (map: Map<string, Map<string, AiAnnotation>>, id: string, note: AiAnnotation) => {
    if (!map.has(id)) map.set(id, new Map());
    map.get(id)!.set(note.itemId, note);
  };
  for (const item of understandingItems(model)) {
    const ids = [...new Set([...item.claim.evidenceIds, ...(item.claim.relationEvidenceId ? [item.claim.relationEvidenceId] : [])])]
      .filter(id => evidence.has(id));
    const note: AiAnnotation = { itemId: item.id, section: item.section, text: item.claim.text,
      basis: item.claim.basis === 'unknown' ? 'unknown' : 'ai_inference', evidenceIds: ids };
    let matched = false;
    if (note.basis !== 'unknown') for (const current of contexts) {
      const edges = ids.flatMap(id => current.bindings.get(id) ?? []);
      if (!edges.length) continue;
      matched = true;
      if (current.count >= 32) { current.omittedCount++; continue; }
      current.count++;
      for (const edge of edges) {
        add(current.nodes, edge.source, note); add(current.nodes, edge.target, note);
        add(current.edges, summaryRelationKey(edge), note);
      }
    }
    if (!matched) {
      if (projectAnnotations.length < 64) projectAnnotations.push(note);
      else omittedProjectCount++;
    }
  }
  const record = (map: Map<string, Map<string, AiAnnotation>>) => Object.fromEntries([...map].map(([key, values]) => [key, [...values.values()]]));
  return { artifactId: model.reference.artifactId, revision: model.reference.revision, incomplete: model.answer.incomplete,
    coverage: model.reference.coverage, coverageGaps: [...model.answer.coverageGaps], projectAnnotations, omittedProjectCount,
    byContext: Object.fromEntries(contexts.map(current => [summaryContextKey(current.context), {
      nodeAnnotations: record(current.nodes), relationAnnotations: record(current.edges), omittedCount: current.omittedCount,
    }])) };
}
