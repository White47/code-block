import { afterEach, expect, it, vi } from 'vitest';
import { assistantClient } from './client';
import { ok } from '../testFixtures';

afterEach(() => { delete window.pywebview; });
it('accepts only a sorted candidate list bound to the requested project and analysis', async () => {
  expect(typeof assistantClient.getConsentCandidates).toBe('function');
  const request = { projectEpoch: 'epoch-1', analysisGeneration: 1 };
  const candidates = vi.fn(async () => ok({ ...request, files: ['a.py', 'b.py'] }));
  window.pywebview = { api: { assistant_get_consent_candidates: candidates } };
  expect(await assistantClient.getConsentCandidates(request)).toEqual({ ...request, files: ['a.py', 'b.py'] });
  candidates.mockResolvedValueOnce(ok({ ...request, analysisGeneration: 2, files: ['a.py'] }));
  await expect(assistantClient.getConsentCandidates(request)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
  candidates.mockResolvedValueOnce(ok({ ...request, files: ['b.py', 'a.py'] }));
  await expect(assistantClient.getConsentCandidates(request)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
});
