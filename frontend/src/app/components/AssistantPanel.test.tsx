import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AssistantPanel from './AssistantPanel.test-harness';
import { AssistantStore } from '../assistant/store';
import { translator } from '../i18n';
import { ok } from '../testFixtures';
import type { AssistantAnswer, AssistantSettings, AssistantState, ExplanationCancelReceipt } from '../assistant/types';
import { BridgeError } from '../../api/bridge';

export const settings: AssistantSettings = { available: false, enabled: true, keySet: true,
  baseUrl: 'https://provider.invalid/v1', model: 'test', configRevision: 1, issue: null };
export function assistantState(): AssistantState {
  return { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1, grant: null,
    conversations: [{ conversationId: 'chat-1', conversationRevision: 0, messages: [] }], activeJobs: [] };
}
afterEach(() => { cleanup(); delete window.pywebview; vi.useRealTimers(); });
it('does not keep an older job failure visible after a newer same-scope job succeeds', async () => {
  const state = assistantState();
  state.grant = { enabled: true, origin: 'https://provider.invalid:443', allowedFiles: ['app.py'], consentRevision: 1 };
  const start = vi.fn();
  const store = new AssistantStore({ getState: vi.fn(async () => state), start, poll: vi.fn(),
    cancel: vi.fn(), clearConversation: vi.fn(), newConversation: vi.fn(), testConnection: vi.fn(), resolveEvidence: vi.fn() });
  await store.restore('epoch-1');
  const previous: ExplanationCancelReceipt = { kind: 'explanation', jobId: 'older-failure',
    projectEpoch: 'epoch-1', analysisGeneration: 1, conversationId: 'chat-1', conversationRevision: 0,
    configRevision: 1, consentRevision: 1, status: 'Failed', cancelRequested: false, cancelAccepted: false,
    cancellation: 'none', dispatchClosed: true, resources: 'released', resourcesReleased: true,
    issue: { code: 'AI_PROTOCOL_INCOMPATIBLE', message: 'Safe failure' }, seq: 1 };
  const snapshot = { ...store.getSnapshot(), jobs: [previous,
    { ...previous, jobId: 'newer-success', status: 'Completed' as const, issue: null }] };
  vi.spyOn(store, 'getSnapshot').mockReturnValue(snapshot);
  render(<AssistantPanel store={store} settings={{ ...settings, available: true }} context={{ kind: 'question' }}
    blocked={false} dirty={false} onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(start).not.toHaveBeenCalled();
  store.dispose();
});
it('does not display a failed job from a previous consent revision', async () => {
  const state = assistantState();
  state.grant = { enabled: true, origin: 'https://provider.invalid:443', allowedFiles: ['app.py'], consentRevision: 2 };
  const start = vi.fn();
  const store = new AssistantStore({ getState: vi.fn(async () => state), start, poll: vi.fn(),
    cancel: vi.fn(), clearConversation: vi.fn(), newConversation: vi.fn(), testConnection: vi.fn(), resolveEvidence: vi.fn() });
  await store.restore('epoch-1');
  const previous: ExplanationCancelReceipt = { kind: 'explanation', jobId: 'old-consent-failure',
    projectEpoch: 'epoch-1', analysisGeneration: 1, conversationId: 'chat-1', conversationRevision: 0,
    configRevision: 1, consentRevision: 1, status: 'Failed', cancelRequested: false, cancelAccepted: false,
    cancellation: 'none', dispatchClosed: true, resources: 'released', resourcesReleased: true,
    issue: { code: 'AI_PROTOCOL_INCOMPATIBLE', message: 'Safe failure' }, seq: 1 };
  const snapshot = { ...store.getSnapshot(), jobs: [previous] };
  vi.spyOn(store, 'getSnapshot').mockReturnValue(snapshot);
  render(<AssistantPanel store={store} settings={{ ...settings, available: true }} context={{ kind: 'question' }}
    blocked={false} dirty={false} onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(start).not.toHaveBeenCalled();
  store.dispose();
});
it('requires explicit checked consent to the displayed files and does not send when granting or reopening', async () => {
  const state = assistantState();
  const grant = vi.fn(async () => { state.grant = { enabled: true, origin: 'https://provider.invalid:443',
    allowedFiles: ['app.py'], consentRevision: 1 }; return ok(state.grant); });
  const start = vi.fn();
  window.pywebview = { api: { assistant_get_state: vi.fn(async () => ok(state)),
    assistant_grant_consent: grant, assistant_start: start } };
  const store = new AssistantStore(); await store.restore('epoch-1'); await store.openPanel();
  render(<AssistantPanel store={store} settings={settings} context={{ kind: 'question' }}
    blocked={false} dirty={false} onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  fireEvent.click(screen.getByRole('button', { name: 'Authorize files' }));
  fireEvent.change(screen.getByLabelText('Allowed Python files · one per line'), { target: { value: 'app.py' } });
  const submit = screen.getByRole('button', { name: 'Confirm authorization' });
  expect((submit as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(submit); expect(grant).not.toHaveBeenCalled();
  fireEvent.click(screen.getByLabelText('I approve sending the listed saved files and permitted context to this provider.'));
  fireEvent.click(submit); await waitFor(() => expect(grant).toHaveBeenCalledOnce());
  await store.closePanel(); await store.openPanel();
  expect(start).not.toHaveBeenCalled(); store.dispose();
});

it('renders seven inert sections and local cards, keeps stale explanations readable but disables navigation', async () => {
  const state = assistantState();
  const answer: AssistantAnswer = { sections: (['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns'] as const)
    .map(kind => ({ kind, claims: [{ text: '<img src="https://invalid.test/x">', basis: 'inference', evidenceIds: [], relationEvidenceId: null }] })),
    evidence: [{ evidenceId: 'ev-1', label: 'app.py', confidence: 'Exact', truncated: false,
      sourceAvailable: true, graphAvailable: false, unavailableReason: 'source_only' }], relationFacts: [],
    incomplete: true, coverageGaps: ['Dynamic target unresolved'], checkedAt: '2026-10-20T00:00:00Z' };
  state.conversations[0].messages = [{ type: 'validated_answer', answer, stale: true, jobId: 'job-1',
    projectEpoch: 'epoch-1', analysisGeneration: 1, conversationRevision: 0, configRevision: 1, consentRevision: 0, seq: 1 }];
  const store = new AssistantStore({ getState: vi.fn(async () => state), start: vi.fn(), poll: vi.fn(),
    cancel: vi.fn(), clearConversation: vi.fn(), newConversation: vi.fn(), testConnection: vi.fn(), resolveEvidence: vi.fn() });
  await store.restore('epoch-1');
  const { container } = render(<AssistantPanel store={store} settings={settings} context={{ kind: 'question' }}
    blocked={false} dirty={false} onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(7);
  expect(screen.getByText('Dynamic target unresolved')).toBeTruthy();
  expect(container.querySelector('img,a')).toBeNull();
  expect((screen.getByRole('button', { name: 'Open source' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: 'Reveal in graph' }) as HTMLButtonElement).disabled).toBe(true);
  store.dispose();
});

it('submits only explicit questions and provides Stop, Clear and New without auto sends', async () => {
  const state = assistantState(); state.grant = { enabled: true, origin: 'https://provider.invalid:443', allowedFiles: ['app.py'], consentRevision: 1 };
  const receipt = { kind: 'explanation' as const, jobId: 'job-1', projectEpoch: 'epoch-1', analysisGeneration: 1,
    conversationId: 'chat-1', conversationRevision: 0, configRevision: 1, consentRevision: 1,
    status: 'Running' as const, cancelRequested: true, cancelAccepted: false, cancellation: 'requested' as const,
    dispatchClosed: false, resources: 'closing' as const, resourcesReleased: false, issue: null, seq: 1 };
  const api = { getState: vi.fn(async () => state), start: vi.fn(async () => ({ kind: 'explanation' as const,
    jobId: 'job-1', status: 'Queued' as const, scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'request-1' }, conversationId: 'chat-1' })),
    poll: vi.fn(), cancel: vi.fn(async () => receipt), clearConversation: vi.fn(async () => ({ cleared: true as const, conversationRevision: 1 })),
    newConversation: vi.fn(async () => ({ conversationId: 'chat-2', evictedConversationIds: [] })), testConnection: vi.fn(), resolveEvidence: vi.fn() };
  const store = new AssistantStore(api, () => 'request-1'); await store.restore('epoch-1'); await store.openPanel();
  render(<AssistantPanel store={store} settings={{ ...settings, available: true }} context={{ kind: 'question' }}
    blocked={false} dirty={false} onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  fireEvent.change(screen.getByLabelText('Ask about the project'), { target: { value: 'Explain saved code' } });
  expect(api.start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await waitFor(() => expect(api.start).toHaveBeenCalledOnce());
  expect(api.start.mock.calls[0]).toEqual([{ projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    consentRevision: 1, requestId: 'request-1', conversationId: 'chat-1', question: 'Explain saved code', context: { kind: 'question' } }]);
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  await waitFor(() => expect(api.cancel).toHaveBeenCalled());
  expect(screen.getByText(/awaiting backend acknowledgement/)).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Clear conversation' }));
  await waitFor(() => expect(store.getSnapshot().state?.conversations[0].messages).toEqual([]));
  fireEvent.click(screen.getByRole('button', { name: 'New conversation' }));
  await waitFor(() => expect(store.getSnapshot().selectedConversationId).toBe('chat-2'));
  expect(api.start).toHaveBeenCalledOnce();
  await act(async () => { store.dispose(); });
});

it('shows ambiguous billing recovery and only reuses the original payload on an explicit recovery click', async () => {
  const state = assistantState(); state.grant = { enabled: true, origin: 'https://provider.invalid:443', allowedFiles: ['app.py'], consentRevision: 1 };
  const start = vi.fn().mockRejectedValueOnce(new BridgeError('BRIDGE_TIMEOUT', 'Synthetic timeout'))
    .mockResolvedValue({ kind: 'explanation', jobId: 'job-1', status: 'Queued',
      scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'request-1' }, conversationId: 'chat-1' });
  const store = new AssistantStore({ getState: vi.fn(async () => state), start, poll: vi.fn(), cancel: vi.fn(),
    clearConversation: vi.fn(), newConversation: vi.fn(), testConnection: vi.fn(), resolveEvidence: vi.fn() }, () => 'request-1');
  await store.restore('epoch-1'); await store.openPanel();
  render(<AssistantPanel store={store} settings={{ ...settings, available: true }} context={{ kind: 'question' }}
    blocked={false} dirty={false} onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  fireEvent.change(screen.getByLabelText('Ask about the project'), { target: { value: 'Explain' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await screen.findByText(/may have incurred charges/);
  expect(start).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Recover original request' }));
  await waitFor(() => expect(start).toHaveBeenCalledTimes(2));
  expect(start.mock.calls[1]).toEqual(start.mock.calls[0]);
  await act(async () => { store.dispose(); });
});

it('surfaces backend job errors as localized remedies rather than silently retiring a failed request', async () => {
  vi.useFakeTimers();
  const state = assistantState(); state.grant = { enabled: true, origin: 'https://provider.invalid:443', allowedFiles: ['app.py'], consentRevision: 1 };
  const store = new AssistantStore({ getState: vi.fn(async () => state), start: vi.fn(async () => ({ kind: 'explanation' as const,
    jobId: 'job-1', status: 'Queued' as const, scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'request-1' }, conversationId: 'chat-1' })),
    poll: vi.fn(async () => ({ kind: 'explanation' as const, jobId: 'job-1', projectEpoch: 'epoch-1', analysisGeneration: 1,
      conversationId: 'chat-1', conversationRevision: 0, configRevision: 1, consentRevision: 1, seq: 1, status: 'Failed' as const,
      cancelRequested: false, cancelAccepted: false, cancellation: 'none' as const, dispatchClosed: true,
      resources: 'released' as const, resourcesReleased: true, issue: { code: 'AI_AUTH_FAILED', message: 'fixed safe error' },
      usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0, toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
        observed: { inputTokens: null, outputTokens: null, basis: 'unknown' as const } }, displayText: '', answer: null, reset: true, events: [], evictedConversationIds: [] })),
    cancel: vi.fn(), clearConversation: vi.fn(), newConversation: vi.fn(), testConnection: vi.fn(), resolveEvidence: vi.fn() }, () => 'request-1');
  await store.restore('epoch-1'); await store.openPanel();
  render(<AssistantPanel store={store} settings={{ ...settings, available: true }} context={{ kind: 'question' }}
    blocked={false} dirty={false} onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  await act(async () => { await store.send('Explain', { kind: 'question' }); await vi.advanceTimersByTimeAsync(250); });
  expect(screen.getByRole('alert').textContent).toContain('Provider authentication failed; check the key.');
  await act(async () => { store.dispose(); });
});

it('ignores an old project consent completion after the current project has changed', async () => {
  const a = assistantState(); const b = assistantState(); b.projectEpoch = 'epoch-b'; b.conversations[0].conversationId = 'chat-b';
  const getState = vi.fn(async (request: { projectEpoch: string }) => ok(request.projectEpoch === 'epoch-b' ? b : a));
  let finish!: (value: ReturnType<typeof ok<{ enabled: boolean; origin: string; allowedFiles: string[]; consentRevision: number }>>) => void;
  const grant = vi.fn(() => new Promise<ReturnType<typeof ok<{ enabled: boolean; origin: string; allowedFiles: string[]; consentRevision: number }>>>(resolve => { finish = resolve; }));
  window.pywebview = { api: { assistant_get_state: getState, assistant_grant_consent: grant } };
  const store = new AssistantStore(); await store.restore('epoch-1'); await store.openPanel();
  render(<AssistantPanel store={store} settings={settings} context={{ kind: 'question' }} blocked={false} dirty={false}
    onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  fireEvent.click(screen.getByRole('button', { name: 'Authorize files' }));
  fireEvent.change(screen.getByLabelText('Allowed Python files · one per line'), { target: { value: 'app.py' } });
  fireEvent.click(screen.getByLabelText('I approve sending the listed saved files and permitted context to this provider.'));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm authorization' }));
  await waitFor(() => expect(grant).toHaveBeenCalledOnce());
  await act(async () => { await store.restore('epoch-b'); });
  await act(async () => { finish(ok({ enabled: true, origin: 'https://provider.invalid:443', allowedFiles: ['app.py'], consentRevision: 1 })); });
  expect(store.getSnapshot().state?.projectEpoch).toBe('epoch-b');
  expect(getState.mock.calls.filter(([request]) => request.projectEpoch === 'epoch-1')).toHaveLength(2);
  store.dispose();
});
