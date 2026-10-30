import { afterEach, expect, it, vi } from 'vitest';
import { assistantClient } from './client';
import type { AssistantStartRequest } from './types';

afterEach(() => { delete window.pywebview; });

const request = (): AssistantStartRequest => ({
  projectEpoch: 'epoch-1', analysisGeneration: 2, conversationId: 'conversation-1',
  requestId: 'request-1', configRevision: 3, consentRevision: 4,
  question: 'Explain this', context: { kind: 'question' },
});

it('counts question limits as Unicode characters without losing joined emoji', async () => {
  const input = { ...request(), question: '🧑‍💻'.repeat(500) };
  const result = { jobId: 'job-1', status: 'Queued' as const, kind: 'explanation' as const,
    scope: { projectEpoch: 'epoch-1', analysisGeneration: 2, requestId: 'request-1' },
    conversationId: 'conversation-1' };
  const start = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: result }));
  window.pywebview = { api: { assistant_start: start } };
  await expect(assistantClient.start(input)).resolves.toEqual(result);
  expect(start).toHaveBeenCalledExactlyOnceWith(input);
});

it('rejects malformed text and noncanonical source paths before native dispatch', async () => {
  const start = vi.fn();
  window.pywebview = { api: { assistant_start: start } };
  for (const question of ['bad\uD800', 'bad\u0007', 'bad\rtext', 'x'.repeat(2001)]) {
    await expect(assistantClient.start({ ...request(), question }))
      .rejects.toMatchObject({ code: 'AI_INVALID_INPUT' });
  }
  for (const filePath of ['/app.py', '../app.py', 'pkg//app.py', 'C:\\app.py',
    'pkg/./app.py', 'pkg/app.py ', 'NUL.py', 'bad\uD800.py']) {
    await expect(assistantClient.start({ ...request(),
      context: { kind: 'source', filePath, anchor: null, mode: 'file' } }))
      .rejects.toMatchObject({ code: 'AI_INVALID_INPUT' });
  }
  expect(start).not.toHaveBeenCalled();
});
