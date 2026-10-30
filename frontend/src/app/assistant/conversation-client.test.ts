import { afterEach, expect, it, vi } from 'vitest';
import { assistantClient } from './client';

afterEach(() => { delete window.pywebview; });

it('uses explicit conversation and revoke actions with closed receipts', async () => {
  const created = { conversationId: 'chat-2', evictedConversationIds: ['chat-1'] };
  const cleared = { cleared: true as const, conversationRevision: 2 };
  const revoked = { revoked: true as const, consentRevision: 3 };
  const create = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: created }));
  const clear = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: cleared }));
  const revoke = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: revoked }));
  window.pywebview = { api: { assistant_new_conversation: create,
    assistant_clear_conversation: clear, assistant_revoke_consent: revoke } };
  await expect(assistantClient.newConversation({ projectEpoch: 'epoch-1' })).resolves.toEqual(created);
  await expect(assistantClient.clearConversation({ projectEpoch: 'epoch-1', conversationId: 'chat-2' }))
    .resolves.toEqual(cleared);
  await expect(assistantClient.revokeConsent({ projectEpoch: 'epoch-1' })).resolves.toEqual(revoked);
  expect(create).toHaveBeenCalledExactlyOnceWith({ projectEpoch: 'epoch-1' });
  expect(clear).toHaveBeenCalledExactlyOnceWith({ projectEpoch: 'epoch-1', conversationId: 'chat-2' });
  expect(revoke).toHaveBeenCalledExactlyOnceWith({ projectEpoch: 'epoch-1' });
});

it('rejects malformed action requests and untrusted receipt fields', async () => {
  const revoke = vi.fn(async () => ({ ok: true as const, schemaVersion: 1,
    data: { revoked: true as const, consentRevision: 3, sourceText: 'not allowed' } }));
  const clear = vi.fn();
  window.pywebview = { api: { assistant_revoke_consent: revoke, assistant_clear_conversation: clear } };
  await expect(assistantClient.revokeConsent({ projectEpoch: 'epoch-1' }))
    .rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
  await expect(assistantClient.clearConversation({ projectEpoch: 'epoch-1', conversationId: '../chat' }))
    .rejects.toMatchObject({ code: 'AI_INVALID_INPUT' });
  expect(clear).not.toHaveBeenCalled();
});
