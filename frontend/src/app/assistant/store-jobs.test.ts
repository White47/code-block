import { afterEach, expect, it, vi } from 'vitest';
import { AssistantStore } from './store';
import type { AssistantAnswer, AssistantPoll, AssistantState, ConnectionCancelReceipt,
  ConnectionPoll, NavigationPoll, StateMessageBinding } from './types';

afterEach(() => { vi.useRealTimers(); });
const usage = { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0, toolCalls: 0,
  sourceCharacters: 0, fileCount: 0 }, observed: { inputTokens: null, outputTokens: null, basis: 'unknown' as const } };
const lifecycle = { status: 'Running' as const, cancelRequested: false, cancelAccepted: false,
  cancellation: 'none' as const, dispatchClosed: false, resources: 'held' as const,
  resourcesReleased: false, issue: null, seq: 0 };
const answer: AssistantAnswer = {
  sections: (['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns'] as const)
    .map(kind => ({ kind, claims: kind === 'summary' ? [{ text: 'Saved code', basis: 'inference' as const,
      evidenceIds: ['evidence-1'], relationEvidenceId: null }] : [] })),
  evidence: [{ evidenceId: 'evidence-1', label: 'Saved file', confidence: 'Exact', truncated: false,
    sourceAvailable: true, graphAvailable: false, unavailableReason: 'source_only' }],
  relationFacts: [], incomplete: false, coverageGaps: [], checkedAt: '2026-10-19T00:00:00Z',
};
function state(): AssistantState {
  const common: StateMessageBinding = { jobId: 'original-job', projectEpoch: 'epoch-1', analysisGeneration: 1,
    conversationRevision: 0, configRevision: 1, consentRevision: 1, seq: 1, stale: false };
  return { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    conversations: [{ conversationId: 'chat-1', conversationRevision: 0, messages: [
      { ...common, type: 'question', text: 'Explain' }, { ...common, type: 'validated_answer', answer }] }],
    grant: { consentRevision: 1, origin: 'https://provider.invalid:443', allowedFiles: ['app.py'], enabled: true }, activeJobs: [] };
}
function native() {
  return { getState: vi.fn(async () => state()), start: vi.fn(),
    poll: vi.fn<() => Promise<AssistantPoll>>(), cancel: vi.fn(), clearConversation: vi.fn(),
    newConversation: vi.fn(async () => ({ conversationId: 'chat-2', evictedConversationIds: ['chat-1'] })),
    testConnection: vi.fn(async () => ({ kind: 'connection' as const, jobId: 'connection-1', status: 'Queued' as const,
      scope: null, conversationId: null })),
    resolveEvidence: vi.fn(async () => ({ kind: 'navigation' as const, jobId: 'navigation-1', status: 'Queued' as const,
      scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'navigation-request' }, conversationId: 'chat-1' })) };
}

it('creates and selects a new bounded conversation from the native eviction receipt without a model send', async () => {
  const api = native(), store = new AssistantStore(api);
  await store.restore('epoch-1'); await store.newConversation();
  expect(store.getSnapshot().selectedConversationId).toBe('chat-2');
  expect(store.getSnapshot().state?.conversations).toEqual([{ conversationId: 'chat-2', conversationRevision: 0, messages: [] }]);
  expect(api.start).not.toHaveBeenCalled();
  store.dispose();
});

it('runs source-free connection jobs without a project and polls terminal cleanup until released', async () => {
  vi.useFakeTimers();
  const api = native(), store = new AssistantStore(api, () => 'request-1');
  const common: ConnectionCancelReceipt = { ...lifecycle, kind: 'connection', jobId: 'connection-1',
    projectEpoch: null, analysisGeneration: null, conversationId: null, conversationRevision: null,
    configRevision: 4, consentRevision: null };
  const result: ConnectionPoll = { ...common, status: 'Completed', dispatchClosed: true, resources: 'closing', seq: 1,
    usage, connectionResult: { toolSupported: true, structuredOutputSupported: true }, reset: true, events: [], evictedConversationIds: [] };
  api.poll.mockResolvedValue(result);
  await store.testConnection(4);
  expect(api.testConnection).toHaveBeenCalledExactlyOnceWith({ configRevision: 4, requestId: 'request-1' });
  await vi.advanceTimersByTimeAsync(250);
  expect(store.getSnapshot().jobs[0].resourcesReleased).toBe(false);
  api.poll.mockResolvedValue({ ...result, seq: 2, resources: 'released', resourcesReleased: true });
  await vi.advanceTimersByTimeAsync(250);
  expect(store.getSnapshot().connection?.connectionResult?.toolSupported).toBe(true);
  expect(api.start).not.toHaveBeenCalled();
  store.dispose();
});

function navigation(): NavigationPoll {
  return { ...lifecycle, kind: 'navigation', jobId: 'navigation-1', projectEpoch: 'epoch-1', analysisGeneration: 1,
    conversationId: 'chat-1', conversationRevision: 0, configRevision: 1, consentRevision: 1,
    status: 'Completed', dispatchClosed: true, resources: 'released', resourcesReleased: true, seq: 1,
    action: 'source', evidenceId: 'evidence-1', reset: true, events: [], evictedConversationIds: [], target: {
      evidenceId: 'evidence-1', scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'original-question' },
      binding: { projectEpoch: 'epoch-1', analysisGeneration: 1 }, action: 'source',
      astAnchor: { filePath: 'app.py', startLine: 1, startColumn: null, endLine: 1, endColumn: null },
      monacoRange: null, reveal: null, checkedAt: '2026-10-19T00:00:00Z', conversationId: 'chat-1',
      conversationRevision: 0, configRevision: 1, consentRevision: 1 } };
}

it('admits navigation only from a validated local card and never upgrades source-only evidence to a graph link', async () => {
  vi.useFakeTimers();
  const api = native(), store = new AssistantStore(api);
  await store.restore('epoch-1'); await store.openPanel();
  await expect(store.resolveEvidence('evidence-1', 'graph')).rejects.toMatchObject({ code: 'AI_RESULT_UNAVAILABLE' });
  api.poll.mockResolvedValue(navigation());
  await store.resolveEvidence('evidence-1', 'source'); await vi.advanceTimersByTimeAsync(250);
  expect(store.getSnapshot().navigation?.target?.scope.requestId).toBe('original-question');
  expect(api.resolveEvidence).toHaveBeenCalledTimes(1);
  expect(api.start).not.toHaveBeenCalled();
  store.dispose();
});

it('does not publish a navigation target that arrives after an analysis-generation change', async () => {
  vi.useFakeTimers();
  const api = native(), store = new AssistantStore(api);
  let finish!: (value: AssistantPoll) => void;
  api.poll.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await store.restore('epoch-1'); await store.openPanel(); await store.resolveEvidence('evidence-1', 'source');
  await vi.advanceTimersByTimeAsync(250);
  const next = state(); next.analysisGeneration = 2; next.conversations[0].messages.forEach(message => { message.stale = true; });
  api.getState.mockResolvedValue(next); await store.restore('epoch-1');
  finish(navigation()); await vi.advanceTimersByTimeAsync(0);
  expect(store.getSnapshot().navigation).toBeNull();
  store.dispose();
});

it('still cancels navigation explicitly admitted by the panel and waits for its resource release', async () => {
  vi.useFakeTimers();
  const api = native(), store = new AssistantStore(api);
  api.cancel.mockResolvedValue({ ...lifecycle, kind: 'navigation', jobId: 'navigation-1', projectEpoch: 'epoch-1',
    analysisGeneration: 1, conversationId: 'chat-1', conversationRevision: 0, configRevision: 1, consentRevision: 1,
    seq: 1, cancelRequested: true, cancellation: 'requested', resources: 'closing' });
  await store.restore('epoch-1'); await store.openPanel();
  await store.resolveEvidence('evidence-1', 'source'); await store.closePanel();
  expect(api.cancel).toHaveBeenCalledExactlyOnceWith({ jobId: 'navigation-1', projectEpoch: 'epoch-1' });
  expect(store.getSnapshot().jobs[0].resourcesReleased).toBe(false);
  api.poll.mockResolvedValue({ ...navigation(), seq: 2, status: 'Cancelled', target: null,
    cancelRequested: true, cancelAccepted: true, cancellation: 'confirmed' });
  await vi.advanceTimersByTimeAsync(250);
  expect(store.getSnapshot().jobs[0].resourcesReleased).toBe(true);
  expect(api.start).not.toHaveBeenCalled();
  store.dispose();
});
