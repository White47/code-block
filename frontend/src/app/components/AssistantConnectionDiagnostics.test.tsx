import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AssistantSettings from './AssistantSettings';
import { AssistantStore } from '../assistant/store';
import { translator } from '../i18n';
import { ok } from '../testFixtures';

afterEach(() => { cleanup(); delete window.pywebview; });

it('shows a safe diagnostic through the real client and store after an explicit connection check', async () => {
  const start = vi.fn(async () => ok({ kind: 'connection', jobId: 'probe',
    status: 'Queued', scope: null, conversationId: null }));
  const poll = vi.fn(async () => ok({ kind: 'connection', jobId: 'probe', projectEpoch: null,
    analysisGeneration: null, conversationId: null, conversationRevision: null,
    status: 'Failed', configRevision: 1, consentRevision: null,
    cancelRequested: false, cancelAccepted: false, cancellation: 'none', dispatchClosed: true,
    resources: 'released', resourcesReleased: true,
    issue: { code: 'AI_PROTOCOL_INCOMPATIBLE', message: 'private response body',
      diagnostic: { boundary: 'dispatch', httpStatus: 402 } },
    usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0,
      toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
    observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } },
    connectionResult: null, reset: true, seq: 1, events: [], evictedConversationIds: [] }));
  window.pywebview = { api: { assistant_get_settings: vi.fn(async () => ok({
    available: true, enabled: true, baseUrl: 'https://provider.invalid/v1',
    model: 'test', keySet: true, configRevision: 1, issue: null })),
  assistant_test_connection: start, assistant_poll: poll } };
  const store = new AssistantStore();
  try {
    render(<AssistantSettings store={store} onChanged={vi.fn()} t={translator('zh-CN')}/>);
    const button = await screen.findByRole('button', { name: '测试连接（不发送源码）' });
    await screen.findByText('已设置密钥；留空保持不变，退出后需重新输入。');
    expect(start).not.toHaveBeenCalled();
    expect(poll).not.toHaveBeenCalled();
    fireEvent.click(button);
    expect(await screen.findByText(/发送或响应流处理 · HTTP 402/)).toBeTruthy();
    expect(start).toHaveBeenCalledOnce();
    expect(poll).toHaveBeenCalledOnce();
    expect(screen.queryByText(/private response body/)).toBeNull();
  } finally { store.dispose(); }
});
