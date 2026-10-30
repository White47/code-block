import { expect, it } from 'vitest';
import type { AssistantAnswer } from './types';

export function architectureAnswer(): AssistantAnswer {
  return { sections: [
    { kind: 'summary', claims: [{ text: '数据处理模块。解析输入文件。', basis: 'inference', evidenceIds: ['e1'], relationEvidenceId: null }] },
    { kind: 'steps', claims: [
      { text: '读取输入', basis: 'inference', evidenceIds: ['e1'], relationEvidenceId: null },
      { text: '处理结果', basis: 'unknown', evidenceIds: [], relationEvidenceId: null }] }],
    evidence: [{ evidenceId: 'e1', label: 'app.py', confidence: 'Exact', truncated: false,
      sourceAvailable: true, graphAvailable: false, unavailableReason: 'source_only' }],
    relationFacts: [], incomplete: false, coverageGaps: [], checkedAt: '2026-10-24T00:00:00Z' };
}
it('builds a bounded top-down interpretation without promoting claims into static facts', async () => {
  const implementations = import.meta.glob<typeof import('./architecture')>('./architecture.ts');
  expect(typeof implementations['./architecture.ts']).toBe('function');
  const { buildAIArchitecture } = await implementations['./architecture.ts']();
  const answer = architectureAnswer();
  answer.sections.push({ kind: 'unknowns', claims: [{ text: 'Dynamic dispatch is not confirmed', basis: 'unknown', evidenceIds: [], relationEvidenceId: null }] });
  const before = JSON.stringify(answer);
  const graph = buildAIArchitecture(answer);
  const module = graph.nodes.find(node => node.section === 'summary')!;
  const steps = graph.nodes.filter(node => node.section === 'steps');
  expect(graph.nodes.some(node => node.section === 'unknowns')).toBe(true);
  expect(graph.edges.some(edge => edge.target === steps[0].id)).toBe(true);
  expect(module.evidenceIds).toEqual(['e1']);
  expect(steps[1].position.y).toBeGreaterThan(steps[0].position.y);
  expect(graph.edges.every(edge => edge.basis === 'inference')).toBe(true);
  expect(steps[1].basis).toBe('unknown');
  expect(JSON.stringify(answer)).toBe(before);
  const bounded = buildAIArchitecture(answer, 2);
  expect(bounded.nodes).toHaveLength(2);
  expect(bounded.omitted).toBeGreaterThan(0);
  expect(bounded.edges.every(edge => bounded.nodes.some(node => node.id === edge.source)
    && bounded.nodes.some(node => node.id === edge.target))).toBe(true);
});
