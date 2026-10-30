import { afterEach, expect, it, vi } from 'vitest';
import { assistantClient } from './client';

afterEach(() => { delete window.pywebview; });

it('accepts only an explicitly confirmed grant for the exact selected files', async () => {
  const request = { projectEpoch: 'epoch-1', analysisGeneration: 2, configRevision: 3,
    allowedFiles: ['pkg/app.py', 'model.py'], confirmed: true as const };
  const receipt = { consentRevision: 4, origin: 'https://example.invalid:443',
    allowedFiles: ['model.py', 'pkg/app.py'], enabled: true };
  const grant = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: receipt }));
  window.pywebview = { api: { assistant_grant_consent: grant } };
  await expect(assistantClient.grantConsent(request)).resolves.toEqual(receipt);
  expect(grant).toHaveBeenCalledExactlyOnceWith(request);
  grant.mockResolvedValueOnce({ ok: true, schemaVersion: 1,
    data: { ...receipt, allowedFiles: ['unselected.py'] } });
  await expect(assistantClient.grantConsent(request)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
  await expect(assistantClient.grantConsent({ ...request, allowedFiles: ['../app.py'] }))
    .rejects.toMatchObject({ code: 'AI_INVALID_INPUT' });
  expect(grant).toHaveBeenCalledTimes(2);
});
