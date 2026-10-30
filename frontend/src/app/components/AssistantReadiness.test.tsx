import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AssistantPanel from './AssistantPanel.test-harness';
import { AssistantStore } from '../assistant/store';
import { translator } from '../i18n';
import type { AssistantSettings, AssistantStartRequest, AssistantState } from '../assistant/types';

afterEach(cleanup);
const settings: AssistantSettings = { available: true, enabled: true, keySet: true,
  baseUrl: 'https://provider.invalid/v1', model: 'test', configRevision: 1, issue: null };
function setup(granted: boolean) {
  const state: AssistantState = { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    grant: granted ? { enabled: true, origin: 'https://provider.invalid:443',
      allowedFiles: ['app.py'], consentRevision: 1 } : null, conversations: [], activeJobs: [] };
  const api = { getState: vi.fn(async () => state),
    newConversation: vi.fn(async () => ({ conversationId: 'chat-1', evictedConversationIds: [] })),
    start: vi.fn(async (request: AssistantStartRequest) => ({ kind: 'explanation' as const, jobId: 'job-1', status: 'Queued' as const,
      scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'request-1' }, conversationId: request.conversationId })),
    poll: vi.fn(), cancel: vi.fn(), clearConversation: vi.fn(), testConnection: vi.fn(), resolveEvidence: vi.fn() };
  return { state, api, store: new AssistantStore(api, () => 'request-1') };
}
it('creates the first local conversation only when the user explicitly sends a ready question', async () => {
  const { api, store } = setup(true); await store.restore('epoch-1');
  render(<AssistantPanel store={store} settings={settings} context={{ kind: 'question' }}
    blocked={false} dirty={false} onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  expect(api.newConversation).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Ask about the project'), { target: { value: 'Explain this project' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await waitFor(() => expect(api.start).toHaveBeenCalledOnce());
  expect(api.newConversation).toHaveBeenCalledOnce();
  expect(api.start.mock.calls[0][0]).toMatchObject({ conversationId: 'chat-1', question: 'Explain this project' });
  store.dispose();
});
it('explains the missing project authorization instead of silently disabling Send', async () => {
  const { api, store } = setup(false); await store.restore('epoch-1');
  render(<AssistantPanel store={store} settings={settings} context={{ kind: 'question' }}
    blocked={false} dirty={false} onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  expect(screen.getByText("Authorize this project's code before sending a question.")).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(true);
  expect(api.start).not.toHaveBeenCalled(); store.dispose();
});
