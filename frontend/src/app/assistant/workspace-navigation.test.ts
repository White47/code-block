import { afterEach, expect, it, vi } from 'vitest';
import { currentWorkspaceNavigation, WorkspaceNavigation, type WorkspaceNavigationContext } from './workspace-navigation';
import { assistantClient } from './client';
import { AssistantStore } from './store';
import type { AssistantPoll, AssistantState, NavigationCancelReceipt, NavigationPoll } from './types';

afterEach(() => { vi.useRealTimers(); delete window.pywebview; });
it.each(['origin', 'replacement', 'clear'])('uses the artifact owner independently of sidebar/chat and rejects late %s', async invalidation => {
  vi.useFakeTimers();
  const reference = { schemaVersion: 1 as const, artifactId: 'artifact-1', revision: 1, projectEpoch: 'epoch-1',
    analysisGeneration: 1, conversationId: 'owner-chat', conversationRevision: 0, jobId: 'answer-1',
    configRevision: 1, consentRevision: 1, coverage: { indexedFileCount: 1, authorizedFileCount: 1,
      metadataFileCount: 1, sourceFileCount: 1, gapCodes: [] } };
  let context: WorkspaceNavigationContext | null = { origin: 'Overview', model: { reference, answer: {
    sections: [{ kind: 'summary', claims: [{ text: 'Module', basis: 'inference', evidenceIds: ['ev-1'], relationEvidenceId: null }] }],
    evidence: [{ evidenceId: 'ev-1', label: 'Saved source', confidence: 'Exact', truncated: false,
      sourceAvailable: true, graphAvailable: false, unavailableReason: 'source_only' }],
    relationFacts: [], incomplete: true, coverageGaps: [], checkedAt: '2026-10-28T00:00:00Z' } } };
  const state: AssistantState = { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    grant: { consentRevision: 1, enabled: true, origin: 'https://provider.invalid:443', allowedFiles: ['app.py'] },
    conversations: [{ conversationId: 'owner-chat', conversationRevision: 0, messages: [{ type: 'validated_answer',
      jobId: 'answer-1', projectEpoch: 'epoch-1', analysisGeneration: 1, conversationRevision: 0,
      configRevision: 1, consentRevision: 1, seq: 1, stale: false, answer: context.model.answer }] }],
    activeJobs: [], projectUnderstanding: reference };
  const identity = { kind: 'navigation' as const, jobId: 'nav-1', projectEpoch: 'epoch-1', analysisGeneration: 1,
    conversationId: 'owner-chat', conversationRevision: 0, configRevision: 1, consentRevision: 1, seq: 0,
    status: 'Running' as const, cancelRequested: false, cancelAccepted: false, cancellation: 'none' as const,
    dispatchClosed: false, resources: 'held' as const, resourcesReleased: false, issue: null };
  const result: NavigationPoll = { ...identity, status: 'Completed', dispatchClosed: true, resources: 'released', resourcesReleased: true, seq: 1,
    action: 'source', evidenceId: 'ev-1', reset: true, events: [], evictedConversationIds: [], target: {
      evidenceId: 'ev-1', action: 'source', scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'question-1' },
      binding: { projectEpoch: 'epoch-1', analysisGeneration: 1 }, astAnchor: { filePath: 'app.py', startLine: 1, endLine: 2, startColumn: null, endColumn: null },
      monacoRange: null, reveal: null, checkedAt: '2026-10-28T00:00:00Z', conversationId: 'owner-chat',
      conversationRevision: 0, configRevision: 1, consentRevision: 1 } };
  let finish!: (value: AssistantPoll) => void;
  const api = { resolveEvidence: vi.fn(async () => ({ kind: 'navigation' as const, jobId: 'nav-1', status: 'Queued' as const,
    scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'nav-request' }, conversationId: 'owner-chat' })),
    poll: vi.fn(() => new Promise<AssistantPoll>(resolve => { finish = resolve; })),
    cancel: vi.fn(async () => ({ ...identity, cancelRequested: true, cancelAccepted: true, cancellation: 'confirmed' as const,
      dispatchClosed: true, resources: 'released' as const, resourcesReleased: true, status: 'Cancelled' as const, seq: 1 })) };
  const consume = vi.fn();
  const navigation = new WorkspaceNavigation(() => currentWorkspaceNavigation(context, state), consume, api);
  await expect(navigation.resolve('summary:0', 'ev-1', 'graph')).rejects.toMatchObject({ code: 'AI_RESULT_UNAVAILABLE' });
  await navigation.resolve('summary:0', 'ev-1', 'source');
  expect(api.resolveEvidence).toHaveBeenCalledExactlyOnceWith({ projectEpoch: 'epoch-1', analysisGeneration: 1,
    conversationId: 'owner-chat', evidenceId: 'ev-1', action: 'source',
    understanding: { artifactId: 'artifact-1', revision: 1, itemId: 'summary:0' } });
  await vi.advanceTimersByTimeAsync(250);
  if (invalidation === 'origin') context = { ...context!, origin: 'Architecture' };
  else if (invalidation === 'clear') { state.conversations[0].conversationRevision = 1; state.conversations[0].messages = []; delete state.projectUnderstanding; }
  else {
    state.conversations[0].messages[0].jobId = 'answer-2';
    state.projectUnderstanding = { ...reference, artifactId: 'artifact-2', revision: 2, jobId: 'answer-2' };
  }
  navigation.invalidate(); finish(result); await vi.advanceTimersByTimeAsync(0);
  expect(consume).not.toHaveBeenCalled(); expect(api.cancel).toHaveBeenCalledOnce();
  context = null; navigation.dispose();
});

it('admits only the closed artifact navigation extension and rejects malformed input before native dispatch', async () => {
  const resolve = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: { kind: 'navigation',
    jobId: 'navigation-1', status: 'Queued', conversationId: 'owner',
    scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'request-1' } } }));
  window.pywebview = { api: { assistant_resolve_evidence: resolve } };
  const request = { projectEpoch: 'epoch-1', analysisGeneration: 1, conversationId: 'owner', evidenceId: 'ev-1',
    action: 'source' as const, understanding: { artifactId: 'artifact-1', revision: 1, itemId: 'summary:0' } };
  await assistantClient.resolveEvidence(request);
  expect(resolve).toHaveBeenCalledExactlyOnceWith(request);
  for (const raw of [null, { ...request, understanding: null }, { ...request, understanding: { ...request.understanding, revision: true } },
    { ...request, understanding: { ...request.understanding, itemId: 'summary:01' } },
    { ...request, understanding: { ...request.understanding, source: 'app.py' } }]) {
    await expect(Reflect.apply(assistantClient.resolveEvidence, assistantClient, [raw])).rejects.toMatchObject({ code: 'AI_INVALID_INPUT' });
  }
  expect(resolve).toHaveBeenCalledTimes(1);
});

it('does not let a sidebar observer cancel a restored main navigation job, while global Stop still closes it', async () => {
  vi.useFakeTimers();
  const job: NavigationCancelReceipt = { kind: 'navigation', jobId: 'main-nav', projectEpoch: 'epoch-1',
    analysisGeneration: 1, conversationId: 'owner', conversationRevision: 0, configRevision: 1, consentRevision: 1,
    seq: 0, status: 'Running', cancelRequested: false, cancelAccepted: false, cancellation: 'none', dispatchClosed: false,
    resources: 'held', resourcesReleased: false, issue: null };
  const state: AssistantState = { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    grant: { consentRevision: 1, enabled: true, origin: 'https://provider.invalid:443', allowedFiles: ['app.py'] },
    conversations: [{ conversationId: 'owner', conversationRevision: 0, messages: [] }], activeJobs: [job] };
  const api = { getState: vi.fn(async () => state), poll: vi.fn(), resolveEvidence: vi.fn(), start: vi.fn(),
    newConversation: vi.fn(), clearConversation: vi.fn(), testConnection: vi.fn(),
    cancel: vi.fn(async () => ({ ...job, status: 'Cancelled' as const, seq: 1, cancelRequested: true,
      cancelAccepted: true, cancellation: 'confirmed' as const, dispatchClosed: true, resources: 'released' as const, resourcesReleased: true })) };
  const store = new AssistantStore(api);
  await store.restore('epoch-1'); await store.closePanel();
  expect(api.cancel).not.toHaveBeenCalled();
  expect(store.getSnapshot().jobs[0].resourcesReleased).toBe(false);
  await store.stop();
  expect(api.cancel).toHaveBeenCalledExactlyOnceWith({ jobId: 'main-nav', projectEpoch: 'epoch-1' });
  expect(store.getSnapshot().jobs[0].resourcesReleased).toBe(true);
  store.dispose();
});
