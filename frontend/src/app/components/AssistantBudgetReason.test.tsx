import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import AssistantUsage from './AssistantUsage';
import { translator } from '../i18n';
import type { AssistantUsage as Usage } from '../assistant/types';

afterEach(cleanup);
it('explains that the rejected next request is not included in earlier reserved input', () => {
  const usage: Usage = { reserved: { inputTokens: 3475, outputTokens: 512, modelRequests: 1,
    toolCalls: 1, sourceCharacters: 0, fileCount: 1 }, observed: { inputTokens: 1026, outputTokens: 157, basis: 'actual' },
    inputAccounting: { basis: 'estimated', method: 'wire-bytes-v1', limitTokens: 20000,
      upperBound: false, knownActualInputTokens: 1026, blockedReason: 'estimated_limit' } };
  render(<AssistantUsage usage={usage} t={translator('zh-CN')}/>);
  expect(screen.getByText('3475 / 20000 tokens')).toBeTruthy();
  expect(screen.getByRole('status').textContent).toContain('下一次请求');
  expect(screen.getByRole('status').textContent).toContain('此前已预留量');
});
