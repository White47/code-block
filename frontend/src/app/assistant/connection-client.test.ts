import { afterEach, expect, it, vi } from 'vitest';
import { assistantClient } from './client';

afterEach(() => { delete window.pywebview; });

it('starts a connection probe only on explicit call with null project context', async () => {
  const request = { configRevision: 3, requestId: 'probe-1' };
  const receipt = { jobId: 'connection-1', kind: 'connection' as const, status: 'Queued' as const,
    scope: null, conversationId: null };
  const test = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: receipt }));
  window.pywebview = { api: { assistant_test_connection: test } };
  expect(test).not.toHaveBeenCalled();
  await expect(assistantClient.testConnection(request)).resolves.toEqual(receipt);
  expect(test).toHaveBeenCalledExactlyOnceWith(request);
});

it('rejects a project-bearing connection receipt and sanitizes provider failures', async () => {
  const test = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: {
    jobId: 'connection-1', kind: 'connection' as const, status: 'Queued' as const,
    scope: { projectEpoch: 'forged' }, conversationId: null,
  } }));
  window.pywebview = { api: { assistant_test_connection: test } };
  await expect(assistantClient.testConnection({ configRevision: 3, requestId: 'probe-1' }))
    .rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
  await expect(assistantClient.testConnection({ configRevision: -1, requestId: 'probe-2' }))
    .rejects.toMatchObject({ code: 'AI_INVALID_INPUT' });
  expect(test).toHaveBeenCalledTimes(1);
  window.pywebview = { api: { assistant_test_connection: vi.fn(async () => ({
    ok: false as const, schemaVersion: 1,
    error: { code: 'AI_AUTH_FAILED', message: 'private diagnostic' },
  })) } };
  await expect(assistantClient.testConnection({ configRevision: 3, requestId: 'probe-3' }))
    .rejects.toMatchObject({ code: 'AI_AUTH_FAILED', message: 'Assistant request could not be completed.' });
});
