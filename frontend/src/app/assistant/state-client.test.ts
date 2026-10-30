import { afterEach, expect, it, vi } from 'vitest';
import { assistantClient } from './client';

afterEach(() => { delete window.pywebview; });

function state() {
  const binding = { jobId: 'job-1', projectEpoch: 'epoch-1', analysisGeneration: 1,
    conversationRevision: 0, configRevision: 1, consentRevision: 1, seq: 3, stale: false };
  return { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    conversations: [{ conversationId: 'chat-1', conversationRevision: 0, messages: [
      { ...binding, type: 'question', text: 'What does this do?' },
      { ...binding, type: 'provisional', text: 'Checking...' },
    ] }], grant: { consentRevision: 1, origin: 'https://provider.invalid:443',
      allowedFiles: ['app.py'], enabled: true },
    activeJobs: [{ kind: 'explanation', jobId: 'job-1', projectEpoch: 'epoch-1',
      analysisGeneration: 1, conversationId: 'chat-1', conversationRevision: 0,
      configRevision: 1, consentRevision: 1, seq: 3, status: 'Running',
      cancelRequested: false, cancelAccepted: false, cancellation: 'none',
      dispatchClosed: false, resources: 'held', resourcesReleased: false, issue: null }] };
}

function native(value: unknown) {
  const getState = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: value }));
  window.pywebview = { api: { assistant_get_state: getState } };
  return getState;
}

it('restores bounded project displays with their original job cursor without sending', async () => {
  const original = state();
  original.conversations[0].messages[1].text = 'Checking...\r\n<img src="https://invalid.invalid/a">\u0000';
  const getState = native(original);
  await expect(assistantClient.getState({ projectEpoch: 'epoch-1' })).resolves.toEqual(original);
  expect(getState).toHaveBeenCalledExactlyOnceWith({ projectEpoch: 'epoch-1' });
  const empty = { projectEpoch: 'epoch-1', analysisGeneration: null, configRevision: 0,
    conversations: [], grant: null, activeJobs: [] };
  native(empty);
  await expect(assistantClient.getState({ projectEpoch: 'epoch-1' })).resolves.toEqual(empty);
});

it('rejects injected fields and inconsistent recovery identity or cursor', async () => {
  const injected = { ...state(), sourceText: 'not permitted' };
  const mismatched = state();
  mismatched.conversations[0].messages[1].conversationRevision = 1;
  const cursor = state();
  cursor.activeJobs[0].seq = 4;
  const foreign = { ...state(), projectEpoch: 'foreign-project' };
  for (const value of [injected, mismatched, cursor, foreign]) {
    native(value);
    await expect(assistantClient.getState({ projectEpoch: 'epoch-1' }))
      .rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
  }
});

it('accepts grant paths sorted by Python Unicode code points, not UTF-16 units', async () => {
  const original = state();
  original.grant.allowedFiles = ['\uE000.py', '😀.py'];
  native(original);
  await expect(assistantClient.getState({ projectEpoch: 'epoch-1' })).resolves.toEqual(original);
});

it('rejects a trailing newline in an opaque project identity before calling native', async () => {
  const getState = native(state());
  await expect(assistantClient.getState({ projectEpoch: 'epoch-1\n' }))
    .rejects.toMatchObject({ code: 'AI_INVALID_INPUT' });
  expect(getState).not.toHaveBeenCalled();
});

it('rejects the complete oversized envelope instead of truncating displays', async () => {
  const original = state();
  const oversized = { ...original, activeJobs: [], conversations: Array.from({ length: 10 }, (_, index) => ({
    conversationId: `chat-${index}`, conversationRevision: 0,
    messages: Array.from({ length: 4 }, (_, turn) => original.conversations[0].messages.map(message => ({
      ...message, jobId: `job-${index}-${turn}`, stale: true,
      text: message.type === 'question' ? 'question' : 'x'.repeat(64 * 1024),
    }))).flat(),
  })) };
  native(oversized);
  await expect(assistantClient.getState({ projectEpoch: 'epoch-1' }))
    .rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
});
