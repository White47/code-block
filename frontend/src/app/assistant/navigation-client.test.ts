import { afterEach, expect, it, vi } from 'vitest';
import { assistantClient } from './client';

afterEach(() => { delete window.pywebview; });

it('starts an explicit navigation check without accepting a target in its receipt', async () => {
  const request = { projectEpoch: 'epoch-1', analysisGeneration: 2,
    conversationId: 'chat-1', evidenceId: 'evidence-1', action: 'source' as const };
  const receipt = { jobId: 'nav-1', kind: 'navigation' as const, status: 'Queued' as const,
    conversationId: 'chat-1',
    scope: { projectEpoch: 'epoch-1', analysisGeneration: 2, requestId: 'local-nav-1' } };
  const resolve = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: receipt }));
  window.pywebview = { api: { assistant_resolve_evidence: resolve } };
  await expect(assistantClient.resolveEvidence(request)).resolves.toEqual(receipt);
  expect(resolve).toHaveBeenCalledExactlyOnceWith(request);
  resolve.mockResolvedValueOnce({ ok: true, schemaVersion: 1, data: { ...receipt,
    scope: { ...receipt.scope, projectEpoch: 'other-epoch' } } });
  await expect(assistantClient.resolveEvidence(request)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
});
