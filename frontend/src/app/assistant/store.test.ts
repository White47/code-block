import { afterEach, expect, it, vi } from 'vitest';
import { BridgeError } from '../../api/bridge';
import { AssistantStore } from './store';
import type { AssistantContext, AssistantState, ExplanationCancelReceipt, ExplanationPoll } from './types';

afterEach(() => { vi.useRealTimers(); });

function state(): AssistantState {
  return { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    conversations: [{ conversationId: 'chat-1', conversationRevision: 0, messages: [] }],
    grant: { consentRevision: 1, origin: 'https://provider.invalid:443', allowedFiles: [], enabled: true },
    activeJobs: [] };
}

function receipt(extra: Partial<ExplanationCancelReceipt> = {}): ExplanationCancelReceipt {
  return { kind: 'explanation', jobId: 'job-1', projectEpoch: 'epoch-1', analysisGeneration: 1,
    conversationId: 'chat-1', conversationRevision: 0, configRevision: 1, consentRevision: 1,
    status: 'Running', cancelRequested: false, cancelAccepted: false, cancellation: 'none',
    dispatchClosed: false, resources: 'held', resourcesReleased: false, issue: null, seq: 0, ...extra };
}

function poll(extra: Partial<ExplanationPoll> = {}): ExplanationPoll {
  return { ...receipt(), kind: 'explanation', displayText: '', answer: null, usage: {
    reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0, toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
    observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } },
    reset: false, events: [], evictedConversationIds: [], ...extra };
}

function native() {
  return { getState: vi.fn(async () => state()),
    start: vi.fn(async () => ({ kind: 'explanation' as const, jobId: 'job-1', status: 'Queued' as const,
      scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'request-1' }, conversationId: 'chat-1' })),
    poll: vi.fn(async () => poll()), cancel: vi.fn(async () => receipt({ cancelRequested: true, cancellation: 'requested' })),
    clearConversation: vi.fn(async () => ({ cleared: true as const, conversationRevision: 1 })),
    newConversation: vi.fn(async () => ({ conversationId: 'chat-2', evictedConversationIds: [] })),
    resolveEvidence: vi.fn(), testConnection: vi.fn() };
}

it('restores and reopens without sending; explicit send and close wait for actual cancellation release', async () => {
  vi.useFakeTimers();
  const api = native();
  const store = new AssistantStore(api, () => 'request-1');
  await store.restore('epoch-1');
  await store.openPanel();
  expect(api.start).not.toHaveBeenCalled();
  await store.send('Explain the project', { kind: 'question' });
  expect(api.start).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(250);
  api.poll.mockResolvedValue(poll({ seq: 2, status: 'Failed', dispatchClosed: true,
    resources: 'closing', cancelRequested: true, cancellation: 'requested', displayText: 'Checking...' }));
  await store.closePanel();
  expect(api.cancel).toHaveBeenCalledExactlyOnceWith({ jobId: 'job-1', projectEpoch: 'epoch-1' });
  await vi.advanceTimersByTimeAsync(250);
  expect(store.getSnapshot().jobs[0].resourcesReleased).toBe(false);
  api.poll.mockResolvedValue(poll({ seq: 3, status: 'Cancelled', dispatchClosed: true,
    resources: 'released', resourcesReleased: true, cancelRequested: true, cancelAccepted: true,
    cancellation: 'confirmed', displayText: 'Checking...' }));
  await vi.advanceTimersByTimeAsync(250);
  const calls = api.poll.mock.calls.length;
  await vi.advanceTimersByTimeAsync(2000);
  expect(api.poll).toHaveBeenCalledTimes(calls);
  expect(store.getSnapshot().state?.conversations[0].messages.map(message => message.type))
    .toEqual(['question', 'provisional']);
  await store.openPanel();
  expect(api.start).toHaveBeenCalledTimes(1);
  store.dispose();
});

it('keeps one poll in flight and treats reset displayText as a replacement', async () => {
  vi.useFakeTimers();
  const api = native();
  let finish!: (value: ExplanationPoll) => void;
  api.poll.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const store = new AssistantStore(api, () => 'request-1');
  await store.restore('epoch-1'); await store.openPanel();
  await store.send('Explain', { kind: 'question' });
  await vi.advanceTimersByTimeAsync(1000);
  expect(api.poll).toHaveBeenCalledTimes(1);
  finish(poll({ seq: 1, displayText: 'old partial' }));
  await vi.advanceTimersByTimeAsync(0);
  api.poll.mockResolvedValue(poll({ seq: 5, reset: true, displayText: '<script>inert text</script>' }));
  await vi.advanceTimersByTimeAsync(250);
  expect(store.getSnapshot().state?.conversations[0].messages.at(-1))
    .toMatchObject({ type: 'provisional', text: '<script>inert text</script>', seq: 5 });
  store.dispose();
});

it('does not let a late poll roll back the cursor of an authoritative restored job', async () => {
  vi.useFakeTimers();
  const api = native();
  let finish!: (value: ExplanationPoll) => void;
  api.poll.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const store = new AssistantStore(api, () => 'request-1');
  await store.restore('epoch-1'); await store.openPanel();
  await store.send('Explain', { kind: 'question' });
  await vi.advanceTimersByTimeAsync(250);
  const restored = state();
  restored.activeJobs = [receipt({ seq: 5 })];
  restored.conversations[0].messages = [{ ...store.getSnapshot().state!.conversations[0].messages[0], seq: 5 }];
  api.getState.mockResolvedValue(restored);
  await store.restore('epoch-1');
  finish(poll({ seq: 2, displayText: 'obsolete partial' }));
  await vi.advanceTimersByTimeAsync(0);
  expect(store.getSnapshot().jobs[0].seq).toBe(5);
  expect(store.getSnapshot().state?.conversations[0].messages).toHaveLength(1);
  store.dispose();
});

it('a start timeout is ambiguous; only explicit recovery reuses the identical payload and ID', async () => {
  vi.useFakeTimers();
  const api = native();
  api.start.mockRejectedValueOnce(new BridgeError('BRIDGE_TIMEOUT', 'Synthetic timeout'));
  const ids = vi.fn(() => 'request-1');
  const store = new AssistantStore(api, ids);
  await store.restore('epoch-1'); await store.openPanel();
  const context = { kind: 'question' as const };
  await expect(store.send('Explain', context)).rejects.toMatchObject({ code: 'BRIDGE_TIMEOUT' });
  await vi.advanceTimersByTimeAsync(2000); await store.openPanel();
  expect(api.start).toHaveBeenCalledTimes(1);
  expect(store.getSnapshot().startRecoveryRequired).toBe(true);
  await store.recoverStart();
  expect(api.start.mock.calls[1]).toEqual(api.start.mock.calls[0]);
  expect(ids).toHaveBeenCalledTimes(1);
  store.dispose();
});

it('replaces provisional content with a closed final answer instead of leaking the question text field', async () => {
  vi.useFakeTimers();
  const api = native();
  const store = new AssistantStore(api, () => 'request-1');
  await store.restore('epoch-1'); await store.openPanel(); await store.send('Explain', { kind: 'question' });
  api.poll.mockResolvedValue(poll({ seq: 1, displayText: 'partial' }));
  await vi.advanceTimersByTimeAsync(250);
  const answer: NonNullable<ExplanationPoll['answer']> = { sections:
    (['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns'] as const)
      .map(kind => ({ kind, claims: [] })), evidence: [], relationFacts: [],
    incomplete: false, coverageGaps: [], checkedAt: '2026-10-19T00:00:00Z' };
  api.poll.mockResolvedValue(poll({ seq: 2, status: 'Completed', dispatchClosed: true,
    resources: 'released', resourcesReleased: true, answer }));
  await vi.advanceTimersByTimeAsync(250);
  const messages = store.getSnapshot().state!.conversations[0].messages;
  expect(messages.map(message => message.type)).toEqual(['question', 'validated_answer']);
  expect(messages[1]).not.toHaveProperty('text');
  store.dispose();
});

it('clearing advances the conversation revision before a late response can repopulate it', async () => {
  vi.useFakeTimers();
  const api = native();
  let finish!: (value: ExplanationPoll) => void;
  api.poll.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const store = new AssistantStore(api, () => 'request-1');
  await store.restore('epoch-1'); await store.openPanel(); await store.send('Explain', { kind: 'question' });
  await vi.advanceTimersByTimeAsync(250);
  await store.clearConversation();
  finish(poll({ seq: 1, displayText: 'obsolete partial' }));
  await vi.advanceTimersByTimeAsync(0);
  expect(store.getSnapshot().state?.conversations[0]).toMatchObject({ conversationRevision: 1, messages: [] });
  store.dispose();
});

it('publishes display and lifecycle together once per accepted poll', async () => {
  vi.useFakeTimers();
  const api = native();
  const store = new AssistantStore(api, () => 'request-1');
  await store.restore('epoch-1'); await store.openPanel(); await store.send('Explain', { kind: 'question' });
  const changed = vi.fn(); store.subscribe(changed);
  api.poll.mockResolvedValue(poll({ seq: 1, displayText: 'one batch' }));
  await vi.advanceTimersByTimeAsync(250);
  expect(changed).toHaveBeenCalledTimes(1);
  expect(store.getSnapshot().jobs[0].seq).toBe(1);
  expect(store.getSnapshot().state?.conversations[0].messages.at(-1)?.seq).toBe(1);
  store.dispose();
});

it('rejects draft-bearing contexts before retaining or submitting them', async () => {
  const api = native();
  const store = new AssistantStore(api, () => 'request-1');
  await store.restore('epoch-1'); await store.openPanel();
  await expect(store.send('Explain', { kind: 'question', sourceText: 'DRAFT' } as AssistantContext))
    .rejects.toMatchObject({ code: 'AI_INVALID_INPUT' });
  expect(api.start).not.toHaveBeenCalled();
  store.dispose();
});

it('rejects a foreign generation snapshot without displaying its provisional text', async () => {
  vi.useFakeTimers();
  const api = native();
  const store = new AssistantStore(api, () => 'request-1');
  await store.restore('epoch-1'); await store.openPanel(); await store.send('Explain', { kind: 'question' });
  api.poll.mockResolvedValue(poll({ seq: 1, analysisGeneration: 2, displayText: 'FOREIGN' }));
  await vi.advanceTimersByTimeAsync(250);
  expect(store.getSnapshot().issue?.code).toBe('INVALID_RESPONSE');
  expect(store.getSnapshot().state?.conversations[0].messages).toHaveLength(1);
  store.dispose();
});

it('bounds unresolved cleanup polling without inventing release and reopens with polling only', async () => {
  vi.useFakeTimers();
  const api = native();
  const store = new AssistantStore(api, () => 'request-1');
  await store.restore('epoch-1'); await store.openPanel(); await store.send('Explain', { kind: 'question' });
  api.poll.mockResolvedValue(poll({ seq: 1, status: 'Failed', dispatchClosed: true, resources: 'closing' }));
  await vi.advanceTimersByTimeAsync(100000);
  expect(api.poll).toHaveBeenCalledTimes(8);
  expect(store.getSnapshot().issue?.code).toBe('AI_NETWORK_TIMEOUT');
  expect(store.getSnapshot().jobs[0].resourcesReleased).toBe(false);
  await store.openPanel(); await vi.advanceTimersByTimeAsync(250);
  expect(api.poll).toHaveBeenCalledTimes(9);
  expect(api.start).toHaveBeenCalledTimes(1);
  store.dispose();
});

it('continues acknowledgement polling for backend-requested cancellation even when resources are released', async () => {
  vi.useFakeTimers();
  const api = native();
  const store = new AssistantStore(api, () => 'request-1');
  await store.restore('epoch-1'); await store.openPanel(); await store.send('Explain', { kind: 'question' });
  api.poll.mockResolvedValue(poll({ seq: 1, status: 'Failed', dispatchClosed: true, resources: 'released',
    resourcesReleased: true, cancelRequested: true, cancellation: 'requested' }));
  await vi.advanceTimersByTimeAsync(250);
  api.poll.mockResolvedValue(poll({ seq: 2, status: 'Failed', dispatchClosed: true, resources: 'released',
    resourcesReleased: true, cancelRequested: true, cancelAccepted: true, cancellation: 'confirmed' }));
  await vi.advanceTimersByTimeAsync(250);
  expect(api.poll).toHaveBeenCalledTimes(2);
  expect(store.getSnapshot().jobs[0].cancelAccepted).toBe(true);
  store.dispose();
});

it('cancels a late start receipt after clear instead of restoring the old-revision question', async () => {
  vi.useFakeTimers();
  const api = native();
  let finish!: (value: Awaited<ReturnType<typeof api.start>>) => void;
  api.start.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const store = new AssistantStore(api, () => 'request-1');
  await store.restore('epoch-1'); await store.openPanel();
  const sending = store.send('Explain', { kind: 'question' });
  await store.clearConversation();
  finish({ kind: 'explanation', jobId: 'job-1', status: 'Queued', conversationId: 'chat-1',
    scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'request-1' } });
  await sending;
  expect(store.getSnapshot().state?.conversations[0]).toMatchObject({ conversationRevision: 1, messages: [] });
  expect(api.cancel).toHaveBeenCalledTimes(1);
  store.dispose();
});

it('retains Stop intent while start awaits its receipt, including after close and reopen', async () => {
  vi.useFakeTimers();
  const api = native(); let finish!: (value: Awaited<ReturnType<typeof api.start>>) => void;
  api.start.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const store = new AssistantStore(api, () => 'request-1');
  await store.restore('epoch-1'); await store.openPanel();
  const sending = store.send('Explain', { kind: 'question' });
  expect(await store.stop()).toBe(false); // No native cancel receipt can exist yet.
  await store.closePanel(); await store.openPanel();
  finish({ kind: 'explanation', jobId: 'job-1', status: 'Queued', conversationId: 'chat-1',
    scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'request-1' } });
  await sending;
  expect(api.cancel).toHaveBeenCalledExactlyOnceWith({ jobId: 'job-1', projectEpoch: 'epoch-1' });
  expect(store.getSnapshot().jobs[0].resourcesReleased).toBe(false);
  store.dispose();
});

it.each(['project', 'configuration'] as const)('detaches ambiguous recovery after %s invalidation without a model resend', async changed => {
  const api = native(); api.start.mockRejectedValueOnce(new BridgeError('BRIDGE_TIMEOUT', 'Synthetic timeout'));
  const store = new AssistantStore(api, () => 'request-1');
  await store.restore('epoch-1'); await store.openPanel();
  await expect(store.send('Explain', { kind: 'question' })).rejects.toMatchObject({ code: 'BRIDGE_TIMEOUT' });
  const next = state();
  if (changed === 'project') next.projectEpoch = 'epoch-b';
  else { next.configRevision = 2; next.grant = null; }
  api.getState.mockResolvedValue(next); await store.restore(next.projectEpoch);
  expect(store.getSnapshot().startRecoveryRequired).toBe(false);
  expect(store.getSnapshot().sending).toBe(false);
  await expect(store.recoverStart()).rejects.toMatchObject({ code: 'AI_REQUEST_CONFLICT' });
  expect(api.start).toHaveBeenCalledOnce(); store.dispose();
});
