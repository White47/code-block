import { StrictMode } from 'react';
import { act, cleanup, render } from '@testing-library/react';
import { useAui } from '@assistant-ui/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AssistantRuntimeBoundary, cancelBeforeLocalAbort } from './runtime';
import { AssistantStore } from './store';
import type { AssistantState, AssistantStartRequest, ExplanationCancelReceipt } from './types';

afterEach(() => { cleanup(); delete window.pywebview; vi.unstubAllGlobals(); vi.useRealTimers(); });
const ok = <T,>(data: T) => ({ ok: true as const, schemaVersion: 1 as const, data });
function setup() {
  const state: AssistantState = { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    grant: { consentRevision: 1, origin: 'https://provider.invalid:443', allowedFiles: [], enabled: true },
    conversations: [{ conversationId: 'chat-1', conversationRevision: 0, messages: [] }], activeJobs: [] };
  const start = vi.fn(async (request: AssistantStartRequest) => ok({ kind: 'explanation' as const,
    jobId: 'job-1', status: 'Queued' as const, conversationId: request.conversationId,
    scope: { projectEpoch: request.projectEpoch, analysisGeneration: request.analysisGeneration, requestId: request.requestId } }));
  const receipt: ExplanationCancelReceipt = { kind: 'explanation', jobId: 'job-1', projectEpoch: 'epoch-1',
    analysisGeneration: 1, conversationId: 'chat-1', conversationRevision: 0, configRevision: 1,
    consentRevision: 1, seq: 1, status: 'Running', cancelRequested: true, cancelAccepted: false,
    cancellation: 'requested', dispatchClosed: false, resources: 'closing', resourcesReleased: false, issue: null };
  const cancel = vi.fn(async () => ok(receipt));
  window.pywebview = { api: { assistant_get_state: vi.fn(async () => ok(state)), assistant_start: start,
    assistant_cancel: cancel } };
  return { state, start, cancel, receipt, store: new AssistantStore(undefined, () => 'request-1') };
}

it('real controlled runtime survives StrictMode/remount with no implicit sends, then sends only explicit text', async () => {
  vi.useFakeTimers();
  const { store, start, cancel } = setup();
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  await store.restore('epoch-1'); await store.openPanel();
  let runtime!: ReturnType<typeof useAui>;
  function Probe() { runtime = useAui(); return <span>Controlled assistant</span>; }
  const tree = <StrictMode><AssistantRuntimeBoundary store={store} getContext={() => ({ kind: 'question' })}>
    <Probe/>
  </AssistantRuntimeBoundary></StrictMode>;
  const first = render(tree);
  expect(start).not.toHaveBeenCalled(); first.unmount();
  const second = render(tree);
  expect(start).not.toHaveBeenCalled();
  await act(async () => { runtime.thread.append('Explain saved code'); });
  expect(start).toHaveBeenCalledTimes(1);
  expect(start.mock.calls[0][0].context).toEqual({ kind: 'question' });
  expect(runtime.thread.getState().messages[0].content).toEqual([{ type: 'text', text: 'Explain saved code' }]);
  await act(async () => { runtime.thread.cancelRun(); });
  expect(cancel).toHaveBeenCalledTimes(1);
  expect(runtime.thread.getState().isRunning).toBe(true);
  expect(store.getSnapshot().jobs[0].cancelAccepted).toBe(false);
  expect(fetch).not.toHaveBeenCalled();
  second.unmount(); store.dispose();
});

it('Python cancel receipt must arrive before local abort; abort never claims backend release', async () => {
  vi.useFakeTimers();
  const { store, cancel, receipt } = setup();
  await store.restore('epoch-1'); await store.openPanel(); await store.send('Explain', { kind: 'question' });
  let finish!: (value: ReturnType<typeof ok<ExplanationCancelReceipt>>) => void;
  cancel.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const local = new AbortController();
  const stopping = cancelBeforeLocalAbort(store, local);
  expect(local.signal.aborted).toBe(false);
  await act(async () => { await Promise.resolve(); });
  expect(cancel).toHaveBeenCalledTimes(1);
  finish(ok(receipt)); await stopping;
  expect(local.signal.aborted).toBe(true);
  expect(store.getSnapshot().jobs[0].resourcesReleased).toBe(false);
  store.dispose();
});

it('reopening restores an answer completed before close cancellation without resending', async () => {
  vi.useFakeTimers();
  const { store, state, start, cancel, receipt } = setup();
  await store.restore('epoch-1'); await store.openPanel(); await store.send('Explain', { kind: 'question' });
  const question = store.getSnapshot().state!.conversations[0].messages[0];
  const binding = { jobId: question.jobId, projectEpoch: question.projectEpoch,
    analysisGeneration: question.analysisGeneration, conversationRevision: question.conversationRevision,
    configRevision: question.configRevision, consentRevision: question.consentRevision, seq: 2, stale: false };
  state.conversations[0].messages = [{ ...binding, type: 'question', text: 'Explain' },
    { ...binding, type: 'validated_answer', answer: {
      sections: (['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns'] as const)
        .map(kind => ({ kind, claims: [] })), evidence: [], relationFacts: [], incomplete: false,
      coverageGaps: [], checkedAt: '2026-10-19T00:00:00Z' } }];
  cancel.mockResolvedValue(ok({ ...receipt, seq: 2, status: 'Completed', cancelRequested: false,
    cancellation: 'none', dispatchClosed: true, resources: 'released', resourcesReleased: true }));
  await store.closePanel(); await store.openPanel();
  expect(store.getSnapshot().state?.conversations[0].messages.at(-1)?.type).toBe('validated_answer');
  expect(start).toHaveBeenCalledTimes(1);
  store.dispose();
});

it('does not locally abort when cancellation delivery fails', async () => {
  vi.useFakeTimers();
  const { store, cancel } = setup();
  await store.restore('epoch-1'); await store.openPanel(); await store.send('Explain', { kind: 'question' });
  cancel.mockRejectedValue(new Error('Synthetic bridge failure'));
  const local = new AbortController();
  await expect(cancelBeforeLocalAbort(store, local)).resolves.toBe(false);
  expect(local.signal.aborted).toBe(false);
  expect(store.getSnapshot().jobs[0].resourcesReleased).toBe(false);
  store.dispose();
});

it('switching conversations replaces the library repository without carrying the previous thread history', async () => {
  vi.useFakeTimers();
  const { store, state, start } = setup();
  state.conversations[0].messages = [{ jobId: 'old-job', projectEpoch: 'epoch-1', analysisGeneration: 1,
    conversationRevision: 0, configRevision: 1, consentRevision: 1, seq: 1, stale: false,
    type: 'question', text: 'OLD THREAD ONLY' }];
  state.conversations.push({ conversationId: 'chat-2', conversationRevision: 0, messages: [] });
  await store.restore('epoch-1'); await store.openPanel();
  let runtime!: ReturnType<typeof useAui>;
  function Probe() { runtime = useAui(); return null; }
  render(<AssistantRuntimeBoundary store={store} getContext={() => ({ kind: 'question' })}><Probe/></AssistantRuntimeBoundary>);
  expect(runtime.thread.getState().messages).toHaveLength(1);
  await act(async () => { await store.selectConversation('chat-2'); });
  expect(runtime.thread.getState().messages).toHaveLength(0);
  expect(start).not.toHaveBeenCalled();
  store.dispose();
});
