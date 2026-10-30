import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AssistantSettings from './AssistantSettings';
import { AssistantStore } from '../assistant/store';
import { translator } from '../i18n';
import { ok } from '../testFixtures';
import type { ConnectionPoll } from '../assistant/types';

afterEach(() => { cleanup(); delete window.pywebview; vi.useRealTimers(); });
it('keeps the key in a password field only and clears its input even on configuration failure', async () => {
  const configure = vi.fn().mockResolvedValue({ ok: false, schemaVersion: 1,
    error: { code: 'AI_NOT_CONFIGURED', message: 'not configured' } });
  window.pywebview = { api: { assistant_get_settings: vi.fn().mockResolvedValue(ok({ available: false,
    enabled: false, baseUrl: 'https://provider.invalid/v1', model: 'test', keySet: false,
    configRevision: 0, issue: null })), assistant_configure: configure } };
  const store = new AssistantStore();
  render(<AssistantSettings store={store} onChanged={vi.fn()} t={translator('en')}/>);
  const key = await screen.findByLabelText('API key (memory only)');
  await waitFor(() => expect((screen.getByLabelText('Model') as HTMLInputElement).value).toBe('test'));
  expect(key.getAttribute('type')).toBe('password');
  fireEvent.change(key, { target: { value: 'synthetic-test-credential' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save AI settings' }));
  await waitFor(() => expect(configure).toHaveBeenCalledOnce());
  await waitFor(() => expect((key as HTMLInputElement).value).toBe(''));
  expect(screen.queryByText('synthetic-test-credential')).toBeNull();
  expect(localStorage.length).toBe(0);
  store.dispose();
});

it('hides old connection results and late polls after configuration changes', async () => {
  const settings = { available: true, enabled: true, baseUrl: 'https://provider.invalid/v1', model: 'test', keySet: true, configRevision: 1, issue: null };
  window.pywebview = { api: { assistant_get_settings: vi.fn(async () => ok(settings)),
    assistant_configure: vi.fn(async () => ok({ ...settings, configRevision: 2 })) } };
  const response: ConnectionPoll = { kind: 'connection', jobId: 'conn-1', projectEpoch: null,
    analysisGeneration: null, conversationId: null, conversationRevision: null, configRevision: 1, consentRevision: null,
    status: 'Running', cancelRequested: false, cancelAccepted: false, cancellation: 'none', dispatchClosed: false,
    resources: 'held', resourcesReleased: false, issue: null, seq: 1, reset: true, events: [], evictedConversationIds: [], connectionResult: null,
    usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0, toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
      observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } } };
  const poll = vi.fn(async () => response);
  const store = new AssistantStore({ getState: vi.fn(), start: vi.fn(), cancel: vi.fn(), clearConversation: vi.fn(), newConversation: vi.fn(),
    resolveEvidence: vi.fn(), testConnection: vi.fn(async () => ({ kind: 'connection' as const, jobId: 'conn-1', status: 'Queued' as const, scope: null, conversationId: null })), poll });
  render(<AssistantSettings store={store} onChanged={vi.fn()} t={translator('en')}/>);
  await waitFor(() => expect((screen.getByLabelText('Model') as HTMLInputElement).value).toBe('test'));
  vi.useFakeTimers();
  await act(async () => { await store.testConnection(1); await vi.advanceTimersByTimeAsync(250); });
  expect(screen.getByText('Running')).toBeTruthy();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save AI settings' })); });
  expect(screen.queryByText('Running')).toBeNull();
  poll.mockResolvedValue({ ...response, seq: 2, status: 'Completed', dispatchClosed: true,
    resources: 'released', resourcesReleased: true, connectionResult: { toolSupported: true, structuredOutputSupported: true } });
  await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  expect(screen.queryByText('Completed')).toBeNull();
  await act(async () => { store.dispose(); });
});
