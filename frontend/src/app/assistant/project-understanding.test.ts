import { afterEach, expect, it, vi } from 'vitest';
import { assistantClient } from './client';
import { effectiveProjectUnderstanding } from './project-understanding';
import type { AssistantAnswer, AssistantState } from './types';

afterEach(() => { delete window.pywebview; });
function state(): AssistantState {
  const answer: AssistantAnswer = { sections: (['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns'] as const)
    .map(kind => ({ kind, claims: [] })), evidence: [], relationFacts: [], incomplete: true, coverageGaps: [], checkedAt: '2026-10-28T00:00:00Z' };
  const reference = { schemaVersion: 1 as const, artifactId: 'artifact-1', revision: 1, projectEpoch: 'epoch-1', analysisGeneration: 1,
    conversationId: 'chat-1', conversationRevision: 0, jobId: 'job-1', configRevision: 1, consentRevision: 1,
    coverage: { indexedFileCount: 10, authorizedFileCount: 3, metadataFileCount: 2, sourceFileCount: 2,
      gapCodes: ['representative_sample'] as const } };
  return { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    grant: { enabled: true, origin: 'https://provider.invalid:443', allowedFiles: ['a.py', 'b.py', 'c.py'], consentRevision: 1 },
    conversations: [{ conversationId: 'chat-1', conversationRevision: 0, messages: [{ type: 'validated_answer', answer,
      jobId: 'job-1', projectEpoch: 'epoch-1', analysisGeneration: 1, conversationRevision: 0, configRevision: 1,
      consentRevision: 1, seq: 1, stale: false }] }],
    activeJobs: [], projectUnderstanding: { ...reference, coverage: { ...reference.coverage, gapCodes: ['representative_sample'] } } };
}
function native(value: unknown) {
  window.pywebview = { api: { assistant_get_state: vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: value })) } };
}
it('restores only the closed owned project reference and rejects wrong ownership, coverage and unknown fields', async () => {
  const original = state();
  native(original);
  const restored = await assistantClient.getState({ projectEpoch: 'epoch-1' });
  expect(effectiveProjectUnderstanding(restored)?.reference.artifactId).toBe('artifact-1');
  const wrongOwner = structuredClone(original); wrongOwner.projectUnderstanding!.conversationId = 'chat-2';
  const wrongCoverage = structuredClone(original); wrongCoverage.projectUnderstanding!.coverage.sourceFileCount = 4;
  const injected = { ...original, projectUnderstanding: { ...original.projectUnderstanding, rawSource: 'not allowed' } };
  for (const value of [wrongOwner, wrongCoverage, injected]) {
    native(value); await expect(assistantClient.getState({ projectEpoch: 'epoch-1' })).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
  }
  restored.conversations[0].messages[0].stale = true;
  expect(effectiveProjectUnderstanding(restored)).toBeNull();
  restored.conversations[0].messages = [];
  expect(effectiveProjectUnderstanding(restored)).toBeNull();
});
