import { afterEach, expect, it, vi } from 'vitest';
import { AssistantStore } from './store';
import type { AssistantState, AssistantStartRequest, AssistantStartResult, ExplanationCancelReceipt, ExplanationPoll } from './types';

afterEach(() => { vi.useRealTimers(); });
const running: ExplanationCancelReceipt = { kind: 'explanation', jobId: 'job-1', projectEpoch: 'epoch-1',
  analysisGeneration: 1, conversationId: 'chat-1', conversationRevision: 0, configRevision: 1, consentRevision: 1,
  status: 'Running', cancelRequested: false, cancelAccepted: false, cancellation: 'none', dispatchClosed: false,
  resources: 'held', resourcesReleased: false, issue: null, seq: 0 };
function setup() {
  const state: AssistantState = { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    conversations: ['chat-1', 'chat-2'].map(conversationId => ({ conversationId, conversationRevision: 0, messages: [] })),
    grant: { consentRevision: 1, origin: 'https://provider.invalid:443', allowedFiles: ['app.py'], enabled: true }, activeJobs: [] };
  const poll: ExplanationPoll = { ...running, kind: 'explanation', displayText: 'Inspecting project', answer: null,
    usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0, toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
      observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } }, reset: true, events: [], evictedConversationIds: [] };
  const result: AssistantStartResult = { kind: 'explanation', jobId: 'job-1', status: 'Queued', conversationId: 'chat-1',
    scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'request-1' } };
  const api = { getState: vi.fn(async () => structuredClone(state)), start: vi.fn(async (request: AssistantStartRequest) => ({ ...result, conversationId: request.conversationId })),
    poll: vi.fn(async () => poll), cancel: vi.fn(async () => ({ ...running, cancelRequested: true, cancellation: 'requested' as const })),
    clearConversation: vi.fn(), newConversation: vi.fn(), resolveEvidence: vi.fn(), testConnection: vi.fn() };
  return { api, state, result, store: new AssistantStore(api, () => 'request-1') };
}

it('keeps project admission and polling owned by the project when the sidebar closes and chat changes', async () => {
  vi.useFakeTimers();
  const { api, result, store } = setup();
  let finish!: (value: AssistantStartResult) => void;
  api.start.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await store.restore('epoch-1'); await store.openPanel();
  const sent = store.send('Explain the whole project', { kind: 'project' });
  await store.closePanel(); await store.selectConversation('chat-2');
  finish(result); await sent; await vi.advanceTimersByTimeAsync(250);
  expect(store.getSnapshot().visible).toBe(false);
  expect(store.getSnapshot().selectedConversationId).toBe('chat-2');
  expect(store.getSnapshot().jobs[0]).toMatchObject({ purpose: 'project_understanding', cancelRequested: false });
  expect(store.getSnapshot().state?.conversations[0].messages.at(-1)).toMatchObject({ type: 'provisional', text: 'Inspecting project' });
  expect(api.start).toHaveBeenCalledTimes(1); expect(api.cancel).not.toHaveBeenCalled();
  await store.stop();
  expect(api.cancel).toHaveBeenCalledExactlyOnceWith({ jobId: 'job-1', projectEpoch: 'epoch-1' });
  store.dispose();
});

it('restores project ownership without resending and cancels it on authority loss', async () => {
  vi.useFakeTimers();
  const { api, state, store } = setup();
  state.activeJobs = [{ ...running, purpose: 'project_understanding' }];
  await store.restore('epoch-1'); await store.closePanel();
  expect(api.cancel).not.toHaveBeenCalled();
  expect(api.start).not.toHaveBeenCalled();
  state.configRevision = 2; state.grant = null;
  await store.restore('epoch-1');
  expect(api.cancel).toHaveBeenCalledExactlyOnceWith({ jobId: 'job-1', projectEpoch: 'epoch-1' });
  store.dispose();
});

it('applies an owner clear acknowledgment even if another chat was selected meanwhile', async () => {
  vi.useFakeTimers();
  const { api, store } = setup();
  let finish!: (value: { cleared: true; conversationRevision: number }) => void;
  api.clearConversation.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await store.restore('epoch-1');
  await store.send('Explain this project', { kind: 'project' });
  const cleared = store.clearConversation();
  await store.selectConversation('chat-2');
  finish({ cleared: true, conversationRevision: 1 }); await cleared;
  expect(store.getSnapshot().state?.conversations[0]).toMatchObject({ conversationRevision: 1, messages: [] });
  expect(store.getSnapshot().selectedConversationId).toBe('chat-2');
  expect(api.cancel).toHaveBeenCalledExactlyOnceWith({ projectEpoch: 'epoch-1', jobId: 'job-1' });
  store.dispose();
});

it.each([false, true])('captures project ownership before first-conversation creation (explicit Stop=%s)', async stopped => {
  vi.useFakeTimers();
  const { api, state, store, result } = setup();
  state.conversations = [];
  let finish!: (value: { conversationId: string; evictedConversationIds: string[] }) => void;
  api.newConversation.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  api.start.mockImplementation(async request => ({ ...result, conversationId: request.conversationId }));
  await store.restore('epoch-1');
  const sent = store.send('Explain whole project', { kind: 'project' });
  expect(store.getSnapshot()).toMatchObject({ sending: true, projectPending: true });
  await store.closePanel();
  expect(store.getSnapshot()).toMatchObject({ sending: true, projectPending: true });
  if (stopped) await store.stop();
  finish({ conversationId: 'first-chat', evictedConversationIds: [] });
  await sent;
  expect(api.start).toHaveBeenCalledTimes(stopped ? 0 : 1);
  if (!stopped) expect(store.getSnapshot().jobs[0]).toMatchObject({ purpose: 'project_understanding', cancelRequested: false });
  expect(store.getSnapshot()).toMatchObject({ sending: false, projectPending: false });
  store.dispose();
});

it('does not resurrect an owner and artifact from a restore captured before its clear acknowledgment', async () => {
  const { api, state, store } = setup();
  state.projectUnderstanding = { schemaVersion: 1, artifactId: 'old-artifact', revision: 1,
    projectEpoch: 'epoch-1', analysisGeneration: 1, conversationId: 'chat-1', conversationRevision: 0,
    jobId: 'old-project', configRevision: 1, consentRevision: 1, coverage: { indexedFileCount: 1,
      authorizedFileCount: 1, metadataFileCount: 1, sourceFileCount: 1, gapCodes: [] } };
  state.conversations[0].messages = [{ type: 'validated_answer', jobId: 'old-project', projectEpoch: 'epoch-1',
    analysisGeneration: 1, conversationRevision: 0, configRevision: 1, consentRevision: 1, seq: 1, stale: false,
    answer: { sections: (['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns'] as const)
      .map(kind => ({ kind, claims: [] })), evidence: [], relationFacts: [], incomplete: true,
      coverageGaps: [], checkedAt: '2026-10-28T00:00:00Z' } }];
  await store.restore('epoch-1');
  const old = structuredClone(state);
  let finish!: (value: AssistantState) => void;
  api.getState.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const restoring = store.restore('epoch-1');
  api.clearConversation.mockResolvedValueOnce({ cleared: true, conversationRevision: 1 });
  await store.clearConversation();
  finish(old); await restoring;
  expect(store.getSnapshot().state?.conversations[0]).toMatchObject({ conversationRevision: 1, messages: [] });
  expect(store.getSnapshot().state?.projectUnderstanding).toBeUndefined();
  store.dispose();
});
