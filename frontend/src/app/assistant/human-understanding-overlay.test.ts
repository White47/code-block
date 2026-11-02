import { describe, expect, it } from 'vitest';
import type { HumanUnderstanding, SummaryRelation, SummaryStep } from '../../types';
import type { EffectiveUnderstanding } from './project-understanding';
import { projectHumanUnderstandingEnhancement, summaryContextKey, summaryRelationKey } from './human-understanding-overlay';

const anchor = { filePath: 'main.py', startLine: 3, endLine: 3, startColumn: null, endColumn: null };
const step = (id: string): SummaryStep => ({ id, kind: 'task', title: `Friendly ${id}`, basis: 'unknown', confidence: 'Probable',
  ownerSymbolIds: [`owner:${id}`], codeNodeIds: [`code:${id}`], evidenceIds: [`fact:${id}`], sourceAnchors: [anchor], sourceLocationCount: 1 });
const relation: SummaryRelation = { source: 'a', target: 'b', kind: 'control_next', confidence: 'Probable',
  evidenceIds: ['static-path'], codeNodeIds: ['code:a', 'code:b'], sourceAnchors: [anchor] };
const context = { kind: 'task_flow', id: 'flow' } as const;
function understanding(): HumanUnderstanding {
  return { status: 'partial', responsibilities: [], omittedResponsibilityCount: 0, omittedFactCount: 0, discardedFactCount: 0,
    flows: [{ id: 'flow', title: 'Report', entrySymbolId: 'entry', steps: [step('a'), step('b')], relations: [relation], incomplete: false, omittedCount: 0 }] };
}
function model(): EffectiveUnderstanding {
  return { reference: { schemaVersion: 1, artifactId: 'artifact', revision: 2, projectEpoch: 'epoch', analysisGeneration: 1,
    configRevision: 1, consentRevision: 1, conversationId: 'chat', conversationRevision: 0, jobId: 'job',
    coverage: { indexedFileCount: 4, authorizedFileCount: 2, metadataFileCount: 2, sourceFileCount: 2, gapCodes: ['representative_sample'] } },
  answer: { sections: [{ kind: 'steps', claims: [{ text: 'Read saved scores before computing the report.', basis: 'inference', evidenceIds: ['e-rel'], relationEvidenceId: 'e-rel' }] }],
    evidence: [{ evidenceId: 'e-rel', label: 'Validated path', confidence: 'Probable', truncated: false, sourceAvailable: true, graphAvailable: true, unavailableReason: 'navigation_check_required' }],
    relationFacts: [{ evidenceId: 'e-rel', sourceId: 'code:a', targetId: 'code:b', type: 'CONTROL_FLOW', category: 'control', confidence: 'Probable', candidateTargets: [] }],
    incomplete: true, coverageGaps: ['Representative source sample'], checkedAt: '2026-11-01T00:00:00Z' } };
}

describe('trusted human understanding annotations', () => {
  it('projects only exact current relation evidence onto existing summary contexts', () => {
    const original = understanding(); const before = structuredClone(original);
    const result = projectHumanUnderstandingEnhancement(original, model())!;
    const current = result.byContext[summaryContextKey(context)];
    expect(current.nodeAnnotations.a[0]).toMatchObject({ itemId: 'steps:0', basis: 'ai_inference', evidenceIds: ['e-rel'] });
    expect(current.nodeAnnotations.b).toEqual(current.nodeAnnotations.a);
    expect(current.relationAnnotations[summaryRelationKey(relation)]).toEqual(current.nodeAnnotations.a);
    expect(result.projectAnnotations).toEqual([]);
    expect(result).toMatchObject({ artifactId: 'artifact', revision: 2, incomplete: true });
    expect(original).toEqual(before);
  });

  it('keeps ambiguous, unverified, reversed and name-only claims at project scope', () => {
    const variants: ((h: HumanUnderstanding, m: EffectiveUnderstanding) => void)[] = [
      (_, m) => { m.answer.relationFacts[0].sourceId = 'Friendly a'; },
      (h, m) => { h.flows![0].steps[0].codeName = 'display-id'; m.answer.relationFacts[0].sourceId = 'display-id'; },
      (h, m) => { h.flows![0].steps.forEach(node => node.ownerSymbolIds.push('shared')); m.answer.relationFacts[0].sourceId = 'shared'; },
      (_, m) => { Object.assign(m.answer.relationFacts[0], { sourceId: 'code:b', targetId: 'code:a' }); },
      (_, m) => { Object.assign(m.answer.relationFacts[0], { category: 'call', type: 'CALLS' }); },
      (_, m) => { m.answer.relationFacts[0].confidence = 'Unresolved'; },
      (_, m) => { m.answer.evidence = []; },
      (_, m) => { m.answer.relationFacts = []; },
      (_, m) => { m.answer.sections[0].claims[0].basis = 'unknown'; },
    ];
    for (const change of variants) {
      const h = understanding(); const m = model(); change(h, m);
      const result = projectHumanUnderstandingEnhancement(h, m)!;
      expect(result.byContext[summaryContextKey(context)].nodeAnnotations).toEqual({});
      expect(result.byContext[summaryContextKey(context)].relationAnnotations).toEqual({});
      expect(result.projectAnnotations).toHaveLength(1);
    }
    expect(projectHumanUnderstandingEnhancement(understanding(), null)).toBeNull();
  });

  it('bounds annotations and never returns a replacement static graph for sparse repair', () => {
    const h = understanding(); const original = structuredClone(h); const m = model();
    m.answer.sections[0].claims = Array.from({ length: 40 }, (_, index) => ({ ...model().answer.sections[0].claims[0], text: `Explanation ${index}` }));
    const result = projectHumanUnderstandingEnhancement(h, m)!;
    expect(result.byContext[summaryContextKey(context)].nodeAnnotations.a).toHaveLength(32);
    expect(result.byContext[summaryContextKey(context)].omittedCount).toBe(8);
    expect(result).not.toHaveProperty('steps'); expect(result).not.toHaveProperty('relations');
    m.answer.relationFacts = [];
    m.answer.sections[0].claims = Array.from({ length: 70 }, () => model().answer.sections[0].claims[0]);
    const notes = projectHumanUnderstandingEnhancement(h, m)!;
    expect(notes.projectAnnotations).toHaveLength(64); expect(notes.omittedProjectCount).toBe(6);
    m.answer.sections[0].claims = [];
    expect(projectHumanUnderstandingEnhancement(h, m)!.byContext[summaryContextKey(context)].nodeAnnotations).toEqual({});
    expect(h).toEqual(original);
  });
});
