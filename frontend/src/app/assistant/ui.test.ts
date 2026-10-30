import { expect, it } from 'vitest';
import { translator } from '../i18n';
import { assistantIssue } from './ui';

it('does not infer unsupported provider capabilities from a generic protocol failure', () => {
  const issue = { code: 'AI_PROTOCOL_INCOMPATIBLE', message: 'untrusted provider text' };
  expect(assistantIssue(issue, translator('zh-CN'))).toContain('协议校验未通过');
  expect(assistantIssue(issue, translator('en'))).toContain('Protocol verification failed');
});

it('localizes closed rejection sites and finish reasons without displaying provider text', () => {
  const issue = { code: 'AI_PROTOCOL_INCOMPATIBLE', message: 'private response',
    diagnostic: { boundary: 'dispatch', checkSite: 'terminal_finish', finishReason: 'length' } };
  expect(assistantIssue(issue, translator('zh-CN'))).toContain('达到长度上限');
  expect(assistantIssue(issue, translator('en'))).toContain('Length limit reached');
  expect(assistantIssue(issue, translator('zh-CN'))).not.toContain('private response');
});

it('localizes only closed boundary names and numeric HTTP status', () => {
  const issue = { code: 'AI_PROTOCOL_INCOMPATIBLE', message: 'private provider text',
    diagnostic: { boundary: 'dispatch', httpStatus: 402 } };
  const chinese = assistantIssue(issue, translator('zh-CN'));
  expect(chinese).toContain('发送或响应流处理');
  expect(chinese).toContain('HTTP 402');
  expect(chinese).not.toContain('private provider text');
  expect(assistantIssue(issue, translator('en'))).toContain('Dispatch or response stream');
});

it('ignores diagnostics that are not in the closed display contract', () => {
  expect(assistantIssue({ code: 'AI_PROTOCOL_INCOMPATIBLE', message: 'ignored',
    diagnostic: { boundary: 'dispatch', httpStatus: 'private status' } }, translator('zh-CN')))
    .not.toContain('private status');
});

it('does not display inherited diagnostic fields', () => {
  const diagnostic = Object.assign(Object.create({ httpStatus: 'private status' }), { boundary: 'dispatch' });
  expect(assistantIssue({ code: 'AI_PROTOCOL_INCOMPATIBLE', diagnostic }, translator('zh-CN')))
    .not.toContain('private status');
});
