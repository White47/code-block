import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AssistantSettings from './AssistantSettings';
import { AssistantStore } from '../assistant/store';
import { translator } from '../i18n';
import type { ConnectionPoll } from '../assistant/types';
import { ok } from '../testFixtures';

afterEach(() => { cleanup(); delete window.pywebview; });

it.each([
  ['en', 'Test connection (no source)',
    'JSON output + local schema validation; no server-side schema guarantee.'],
  ['zh-CN', '测试连接（不发送源码）', 'JSON 输出＋本地结构校验；不代表服务端保证结构。'],
] as const)('labels local validation honestly in %s only after an explicit probe', async (language, button, label) => {
  const settings = { available: true, enabled: true, baseUrl: 'https://api.deepseek.com',
    model: 'deepseek-flash', keySet: true, configRevision: 1, issue: null };
  window.pywebview = { api: { assistant_get_settings: vi.fn(async () => ok(settings)) } };
  const result: ConnectionPoll = {
    kind: 'connection', jobId: 'probe', projectEpoch: null, analysisGeneration: null,
    conversationId: null, conversationRevision: null, configRevision: 1, consentRevision: null,
    status: 'Completed', cancelRequested: false, cancelAccepted: false, cancellation: 'none',
    dispatchClosed: true, resources: 'released', resourcesReleased: true, issue: null,
    usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0,
      toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
    observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } },
    connectionResult: { toolSupported: true, structuredOutputSupported: false,
      jsonObjectSupported: true, outputValidation: 'local-json-schema' },
    reset: true, seq: 1, events: [], evictedConversationIds: [],
  };
  const testConnection = vi.fn(async () => ({ kind: 'connection' as const, jobId: 'probe',
    status: 'Queued' as const, scope: null, conversationId: null }));
  const store = new AssistantStore({ getState: vi.fn(), start: vi.fn(), poll: vi.fn(async () => result),
    cancel: vi.fn(), clearConversation: vi.fn(), newConversation: vi.fn(),
    resolveEvidence: vi.fn(), testConnection });
  try {
    render(<AssistantSettings store={store} onChanged={vi.fn()} t={translator(language)}/>);
    const trigger = await screen.findByRole('button', { name: button });
    await screen.findByText(language === 'en'
      ? 'Key set; leave blank to keep it. Re-enter after exit.'
      : '已设置密钥；留空保持不变，退出后需重新输入。');
    expect(testConnection).not.toHaveBeenCalled();
    expect(screen.queryByText(label)).toBeNull();
    fireEvent.click(trigger);
    expect(await screen.findByText(label)).toBeTruthy();
    expect(testConnection).toHaveBeenCalledExactlyOnceWith({ requestId: expect.any(String), configRevision: 1 });
    expect(screen.queryByText(language === 'en' ? 'Remote strict schema validation.' : '服务端严格结构校验。')).toBeNull();
  } finally { store.dispose(); }
});
