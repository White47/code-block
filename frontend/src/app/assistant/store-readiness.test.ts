import { expect, it, vi } from 'vitest';
import { AssistantStore } from './store';
import type { AssistantState } from './types';

it('does not send into a different project when first-conversation creation finishes late', async () => {
  let state: AssistantState = { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    grant: { enabled: true, origin: 'https://provider.invalid:443', allowedFiles: ['app.py'], consentRevision: 1 },
    conversations: [], activeJobs: [] };
  let finish!: (value: { conversationId: string; evictedConversationIds: string[] }) => void;
  const api = { getState: vi.fn(async () => state), start: vi.fn(), poll: vi.fn(), cancel: vi.fn(),
    clearConversation: vi.fn(), testConnection: vi.fn(), resolveEvidence: vi.fn(),
    newConversation: vi.fn(() => new Promise<{ conversationId: string; evictedConversationIds: string[] }>(resolve => { finish = resolve; })) };
  const store = new AssistantStore(api); await store.restore('epoch-1');
  const pending = expect(store.send('Explain the first project', { kind: 'question' }))
    .rejects.toMatchObject({ code: 'AI_ANALYSIS_STALE' });
  expect(api.newConversation).toHaveBeenCalledOnce();
  state = { ...state, projectEpoch: 'epoch-2', analysisGeneration: 2 };
  await store.restore('epoch-2');
  finish({ conversationId: 'old-chat', evictedConversationIds: [] });
  await pending;
  expect(api.start).not.toHaveBeenCalled();
  expect(store.getSnapshot().state?.conversations).toEqual([]);
  store.dispose();
});

it('does not submit after the panel closes while the first conversation is being created', async () => {
  const state: AssistantState = { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    grant: { enabled: true, origin: 'https://provider.invalid:443', allowedFiles: ['app.py'], consentRevision: 1 },
    conversations: [], activeJobs: [] };
  let finish!: (value: { conversationId: string; evictedConversationIds: string[] }) => void;
  const api = { getState: vi.fn(async () => state), start: vi.fn(), poll: vi.fn(), cancel: vi.fn(),
    clearConversation: vi.fn(), testConnection: vi.fn(), resolveEvidence: vi.fn(),
    newConversation: vi.fn(() => new Promise<{ conversationId: string; evictedConversationIds: string[] }>(resolve => { finish = resolve; })) };
  const store = new AssistantStore(api); await store.restore('epoch-1'); await store.openPanel();
  const pending = store.send('Explain this project', { kind: 'question' });
  await store.closePanel(); finish({ conversationId: 'late-chat', evictedConversationIds: [] });
  await pending.catch(() => undefined);
  expect(api.start).not.toHaveBeenCalled(); store.dispose();
});
