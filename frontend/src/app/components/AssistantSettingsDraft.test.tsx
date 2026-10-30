import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AssistantSettings from './AssistantSettings';
import { AssistantStore } from '../assistant/store';
import type { AssistantSettings as Settings, ConnectionPoll } from '../assistant/types';
import { translator } from '../i18n';
import { ok } from '../testFixtures';

afterEach(() => { cleanup(); delete window.pywebview; });
const saved: Settings = { available: true, enabled: true, baseUrl: 'https://provider.invalid/v1',
  model: 'saved-model', keySet: true, configRevision: 1, issue: null };

async function setup(value = saved, configure = vi.fn(async () => ok({ ...value, configRevision: 2 }))) {
  window.pywebview = { api: { assistant_get_settings: vi.fn(async () => ok(value)), assistant_configure: configure } };
  const result: ConnectionPoll = { kind: 'connection', jobId: 'probe', projectEpoch: null,
    analysisGeneration: null, conversationId: null, conversationRevision: null,
    status: 'Completed', configRevision: 1, consentRevision: null, cancelRequested: false,
    cancelAccepted: false, cancellation: 'none', dispatchClosed: true,
    resources: 'released', resourcesReleased: true, issue: null,
    usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0, toolCalls: 0,
      sourceCharacters: 0, fileCount: 0 }, observed: { inputTokens: 0, outputTokens: 0, basis: 'actual' } },
    connectionResult: { toolSupported: true, structuredOutputSupported: false,
      jsonObjectSupported: true, outputValidation: 'local-json-schema' },
    reset: true, seq: 1, events: [], evictedConversationIds: [] };
  const testConnection = vi.fn(async () => ({ kind: 'connection' as const, jobId: 'probe',
    status: 'Queued' as const, scope: null, conversationId: null }));
  const store = new AssistantStore({ getState: vi.fn(), start: vi.fn(), poll: vi.fn(async () => result),
    cancel: vi.fn(), clearConversation: vi.fn(), newConversation: vi.fn(),
    resolveEvidence: vi.fn(), testConnection });
  render(<AssistantSettings store={store} onChanged={vi.fn()} t={translator('en')}/>);
  await waitFor(() => expect((screen.getByLabelText('Model') as HTMLInputElement).value).toBe(value.model));
  return { store, testConnection, configure,
    button: screen.getByRole('button', { name: 'Test connection (no source)' }) as HTMLButtonElement };
}

it.each([
  ['API URL (HTTPS)', 'https://other.invalid/v1', saved.baseUrl],
  ['Model', 'draft-model', saved.model],
  ['API key (memory only)', 'synthetic-draft-key', ''],
  ['Enable AI (authorization and explicit send still required)', null, null],
] as const)('blocks testing an unsaved %s draft without sending', async (label, draft, original) => {
  const { store, button, testConnection } = await setup();
  try {
    const field = screen.getByLabelText(label);
    if (draft === null) fireEvent.click(field); else fireEvent.change(field, { target: { value: draft } });
    expect(button.disabled).toBe(true);
    expect(screen.getByText(/Save before testing/)).toBeTruthy();
    fireEvent.click(button);
    expect(testConnection).not.toHaveBeenCalled();
    if (original === null) fireEvent.click(field); else fireEvent.change(field, { target: { value: original } });
    expect(button.disabled).toBe(false);
  } finally { store.dispose(); }
});

it('hides old capabilities while editing and restores them only for the unchanged saved draft', async () => {
  const { store, button, testConnection } = await setup();
  try {
    fireEvent.click(button);
    const text = 'JSON output + local schema validation; no server-side schema guarantee.';
    await screen.findByText(text);
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'draft-model' } });
    expect(screen.queryByText(text)).toBeNull();
    expect(screen.queryByText('Completed')).toBeNull();
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: saved.model } });
    expect(screen.getByText(text)).toBeTruthy();
    expect(testConnection).toHaveBeenCalledOnce();
  } finally { store.dispose(); }
});

it('synchronizes normalized fields after a successful save before allowing testing', async () => {
  const normalized = { ...saved, baseUrl: 'https://provider.invalid:443/v1', model: 'new-model', configRevision: 2 };
  const { store, button } = await setup(saved, vi.fn(async () => ok(normalized)));
  try {
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'new-model' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save AI settings' }));
    await waitFor(() => expect((screen.getByLabelText('API URL (HTTPS)') as HTMLInputElement).value).toBe(normalized.baseUrl));
    expect(button.disabled).toBe(false);
  } finally { store.dispose(); }
});

it('keeps a failed key-only save blocked after the password input is cleared', async () => {
  const configure = vi.fn().mockResolvedValue({ ok: false, schemaVersion: 1,
    error: { code: 'AI_NOT_CONFIGURED', message: 'fixed failure' } });
  const { store, button, testConnection } = await setup(saved, configure);
  try {
    fireEvent.change(screen.getByLabelText('API key (memory only)'), { target: { value: 'synthetic-new-key' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save AI settings' }));
    await screen.findByRole('alert');
    expect((screen.getByLabelText('API key (memory only)') as HTMLInputElement).value).toBe('');
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(testConnection).not.toHaveBeenCalled();
    configure.mockResolvedValue(ok({ ...saved, configRevision: 2 }));
    fireEvent.click(screen.getByRole('button', { name: 'Save AI settings' }));
    await waitFor(() => expect(button.disabled).toBe(false));
  } finally { store.dispose(); }
});

it('clears the saved key even when the unsaved URL draft is invalid', async () => {
  const configure = vi.fn(async () => ok({ ...saved, enabled: false, keySet: false, configRevision: 2 }));
  const { store, button } = await setup(saved, configure);
  try {
    fireEvent.change(screen.getByLabelText('API URL (HTTPS)'), { target: { value: 'not-a-url' } });
    fireEvent.click(screen.getByRole('button', { name: 'Clear key and disable' }));
    await waitFor(() => expect(configure).toHaveBeenCalledWith({ baseUrl: saved.baseUrl,
      model: saved.model, key: '', enabled: false }));
    await screen.findByText('No key configured.');
    expect(button.disabled).toBe(true);
  } finally { store.dispose(); }
});

it('does not offer a connection check for an applied disabled configuration', async () => {
  const { store, button } = await setup({ ...saved, enabled: false });
  try { expect(button.disabled).toBe(true); } finally { store.dispose(); }
});
