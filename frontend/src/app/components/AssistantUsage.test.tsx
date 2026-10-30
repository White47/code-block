import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import AssistantUsage, { AssistantInputCaveat } from './AssistantUsage';
import { translator } from '../i18n';
import type { AssistantUsage as Usage } from '../assistant/types';

afterEach(cleanup);
const strict: Usage = { reserved: { inputTokens: 1000, outputTokens: 32, modelRequests: 1,
  toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
  observed: { inputTokens: 0, outputTokens: 0, basis: 'actual' } };

it('shows source, file and tool reservations separately when token limits have not been reached', () => {
  const usage: Usage = { ...strict,
    reserved: { inputTokens: 26871, outputTokens: 1536, modelRequests: 3,
      sourceCharacters: 12000, fileCount: 2, toolCalls: 3 },
    observed: { inputTokens: 7713, outputTokens: 636, basis: 'actual' },
    inputAccounting: { basis: 'estimated', method: 'wire-bytes-v1', limitTokens: 100000,
      upperBound: false, knownActualInputTokens: 7713, blockedReason: null } };
  render(<AssistantUsage usage={usage} t={translator('zh-CN')}/>);
  expect(screen.getByText('源码及衍生解释字符预留（含重复发送）').nextElementSibling?.textContent).toBe('12000');
  expect(screen.getByText('文件预留数').nextElementSibling?.textContent).toBe('2');
  expect(screen.getByText('工具调用尝试').nextElementSibling?.textContent).toBe('3');
  expect(screen.getByText('26871 / 100000 tokens')).toBeTruthy();
  expect(screen.queryByRole('button')).toBeNull();
});

it('preserves the strict display and renders actual zero rather than unknown', () => {
  render(<AssistantUsage usage={strict} t={translator('en')}/>);
  expect(screen.getByText('Budget reservation (not billing)')).toBeTruthy();
  expect(screen.getByText(/0 \/ 0 tokens/)).toBeTruthy();
});

it('separates estimated input from hard output reservation and known partial actual usage', () => {
  const usage: Usage = { ...strict, observed: { inputTokens: null, outputTokens: null, basis: 'unknown' },
    inputAccounting: { basis: 'estimated', method: 'wire-bytes-v1', limitTokens: 20000,
      upperBound: false, knownActualInputTokens: 7, blockedReason: 'usage_unknown' } };
  render(<AssistantUsage usage={usage} t={translator('zh-CN')}/>);
  expect(screen.getByText('输入预算预估（非账单）')).toBeTruthy();
  expect(screen.getByText('输出预算预留')).toBeTruthy();
  expect(screen.getByText(/7 tokens/)).toBeTruthy();
  expect(screen.getByText(/— \/ — tokens/)).toBeTruthy();
  expect(screen.getByRole('status').textContent).toContain('用量未知');
  expect(screen.queryByText('后端资源已释放')).toBeNull();
});

it('shows fixed Chinese and English blocked reasons without claiming cancellation', () => {
  for (const blockedReason of ['estimated_limit', 'observed_overrun', 'usage_unknown'] as const) {
    for (const language of ['en', 'zh-CN'] as const) {
      const actual = blockedReason === 'observed_overrun' ? 20001 : 0;
      const usage: Usage = { ...strict,
        observed: blockedReason === 'usage_unknown' ? { inputTokens: null, outputTokens: null, basis: 'unknown' }
          : { inputTokens: actual, outputTokens: 0, basis: 'actual' },
        inputAccounting: { basis: 'estimated', method: 'wire-bytes-v1',
          limitTokens: 20000, upperBound: false, knownActualInputTokens: actual, blockedReason } };
      const view = render(<AssistantUsage usage={usage} t={translator(language)}/>);
      expect(screen.getByRole('status').textContent).toContain(language === 'en' ? 'No further requests' : '不再发送');
      expect(screen.queryByText('Backend resources released')).toBeNull();
      view.unmount();
    }
  }
});

it('displays the first-send caveat as inert text before any usage exists', () => {
  render(<AssistantInputCaveat t={translator('en')}/>);
  expect(screen.getByText(/Actual usage may exceed the threshold/)).toBeTruthy();
  expect(screen.queryByRole('button')).toBeNull();
});

it('shows the larger input and output limits for new project analysis', () => {
  const usage: Usage = { ...strict,
    reserved: { ...strict.reserved, inputTokens: 12554, outputTokens: 1488, modelRequests: 3 },
    inputAccounting: { basis: 'estimated', method: 'wire-bytes-v1', limitTokens: 100000,
      upperBound: false, knownActualInputTokens: 0, blockedReason: null } };
  render(<AssistantUsage usage={usage} t={translator('zh-CN')}/>);
  expect(screen.getByText('12554 / 100000 tokens')).toBeTruthy();
  expect(screen.getByText('1488 / 8192 tokens · 3 requests')).toBeTruthy();
});
