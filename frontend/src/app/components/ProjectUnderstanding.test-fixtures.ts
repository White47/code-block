import type { EffectiveUnderstanding } from '../assistant/project-understanding';

export function understandingFixture(): EffectiveUnderstanding {
  return { reference: { schemaVersion: 1, artifactId: 'artifact-1', revision: 1,
    projectEpoch: 'epoch-1', analysisGeneration: 1, conversationId: 'owner', conversationRevision: 0,
    jobId: 'job-1', configRevision: 1, consentRevision: 1, coverage: { indexedFileCount: 12,
      authorizedFileCount: 8, metadataFileCount: 4, sourceFileCount: 2, gapCodes: ['representative_sample'] } },
  answer: { sections: (['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns'] as const)
    .map(kind => ({ kind, claims: [{ text: `Detailed ${kind} explanation`, basis: 'inference' as const,
      evidenceIds: [kind === 'summary' ? 'source-1' : 'source-2'], relationEvidenceId: null }] })),
    evidence: ['source-1', 'source-2'].map(evidenceId => ({ evidenceId, label: `Saved ${evidenceId}`,
      confidence: 'Exact' as const, sourceAvailable: true, graphAvailable: false,
      unavailableReason: 'source_only' as const, truncated: false })),
    relationFacts: [{ evidenceId: 'source-2', sourceId: 'caller', targetId: 'callee', type: 'CALLS',
      confidence: 'Exact', candidateTargets: [], category: 'call' }], incomplete: true,
    coverageGaps: ['Some source ranges were omitted.'], checkedAt: '2026-10-29T00:00:00Z' } };
}
