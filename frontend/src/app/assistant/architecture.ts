import type { AssistantAnswer } from './types';

export interface AIArchitectureNode {
  id: string;
  section: 'root' | AssistantAnswer['sections'][number]['kind'];
  title: string;
  description: string;
  basis: 'inference' | 'unknown';
  evidenceIds: string[];
  position: { x: number; y: number };
}
export interface AIArchitectureEdge { id: string; source: string; target: string; basis: 'inference'; viaGutter: boolean }

/** Display-only explanation. IDs are never static graph IDs or source handles. */
export function buildAIArchitecture(answer: AssistantAnswer, limit = 100) {
  const maximum = Number.isSafeInteger(limit) ? Math.max(1, Math.min(100, limit)) : 100;
  const evidence = new Set(answer.evidence.map(item => item.evidenceId));
  const claims = (['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns'] as const).flatMap(kind =>
    (answer.sections.find(section => section.kind === kind)?.claims ?? []).map((claim, index) => ({ kind, claim, index })));
  const chosen = claims.slice(0, maximum - 1);
  const nodes: AIArchitectureNode[] = [{ id: 'ai:root', section: 'root', title: '', description: '',
    basis: 'inference', evidenceIds: [], position: { x: 340, y: 0 } }];
  const edges: AIArchitectureEdge[] = [];
  let moduleIndex = 0;
  const moduleCount = chosen.filter(item => item.kind === 'summary').length;
  let flowIndex = 0;
  let previousStep: string | null = null;
  for (const { kind, claim, index } of chosen) {
    const id = `ai:${kind}:${index}`;
    const position = kind === 'summary'
      ? { x: (moduleIndex % 3) * 340, y: 150 + Math.floor(moduleIndex++ / 3) * 150 }
      : { x: 340, y: 200 + Math.ceil(moduleCount / 3) * 150 + flowIndex++ * 150 };
    nodes.push({ id, section: kind, title: claim.text.split(/[\n。！？.!?]/u)[0].slice(0, 64) || claim.text.slice(0, 64),
      description: claim.text, basis: claim.basis, evidenceIds: [...new Set(claim.evidenceIds.filter(value => evidence.has(value)))], position });
    edges.push({ id: `ai-edge:${id}`, source: kind === 'steps' && previousStep ? previousStep : 'ai:root',
      target: id, basis: 'inference', viaGutter: kind !== 'summary' && !(kind === 'steps' && previousStep) });
    if (kind === 'steps') previousStep = id;
  }
  return { nodes, edges, omitted: claims.length - chosen.length };
}
