import { expect, it } from 'vitest';
import { connectionCancelReceipt, connectionPoll } from './poll';

const request = { jobId: 'diagnostic-1', projectEpoch: null, afterSeq: 0 };
function failedPoll(issue: unknown) {
  return { kind: 'connection', jobId: request.jobId, projectEpoch: null,
    analysisGeneration: null, conversationId: null, conversationRevision: null,
    status: 'Failed', configRevision: 1, consentRevision: null,
    cancelRequested: false, cancelAccepted: false, cancellation: 'none', dispatchClosed: true,
    resources: 'released', resourcesReleased: true, issue, connectionResult: null,
    usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0,
      toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
    observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } },
    reset: true, seq: 1, events: [], evictedConversationIds: [] };
}
const protocolIssue = (diagnostic?: unknown) => ({ code: 'AI_PROTOCOL_INCOMPATIBLE',
  message: 'untrusted response text', ...(diagnostic === undefined ? {} : { diagnostic }) });

it('retains legacy issues without trusting the backend message', () => {
  expect(connectionPoll(failedPoll(protocolIssue()), request).issue).toEqual({
    code: 'AI_PROTOCOL_INCOMPATIBLE', message: 'Assistant request could not be completed.',
  });
});

it('copies a closed terminal failure through poll and rejects invalid diagnostic combinations', () => {
  const diagnostic = { boundary: 'dispatch', checkSite: 'terminal_finish', finishReason: 'length' };
  const issue = connectionPoll(failedPoll(protocolIssue(diagnostic)), request).issue;
  expect(issue?.diagnostic).toEqual(diagnostic);
  expect(issue?.diagnostic).not.toBe(diagnostic);
  for (const invalid of [
    { boundary: 'dispatch', checkSite: 'private provider text' },
    { boundary: 'pre_send', checkSite: 'sdk_decode' },
    { boundary: 'dispatch', checkSite: 'usage_shape', finishReason: 'length' },
    { boundary: 'dispatch', checkSite: 'terminal_finish', finishReason: 'private reason' },
    { boundary: 'dispatch', checkSite: 'response_headers', httpStatus: 400 },
    { boundary: 'dispatch', finishReason: 'length' },
    Object.assign(Object.create({ checkSite: 'private inherited site' }), { boundary: 'dispatch' }),
    { boundary: 'dispatch', checkSite: 'terminal_finish', finishReason: 'length', raw: 'private response' },
  ]) expect(() => connectionPoll(failedPoll(protocolIssue(invalid)), request)).toThrow('Invalid assistant poll.');
});

it.each([{ boundary: 'pre_send' }, { boundary: 'result_validation' },
  { boundary: 'dispatch' }, { boundary: 'dispatch', httpStatus: 402 }])(
  'copies only closed protocol diagnostics: %j', diagnostic => {
    const input = failedPoll(protocolIssue(diagnostic));
    const result = connectionPoll(input, request);
    expect(result.issue?.diagnostic).toEqual(diagnostic);
    expect(result.issue?.diagnostic).not.toBe(diagnostic);
    expect(result.issue?.message).not.toContain('untrusted');
  });

it.each([null, { boundary: 'unknown' }, { boundary: 'pre_send', httpStatus: 400 },
  { boundary: 'dispatch', httpStatus: true }, { boundary: 'dispatch', httpStatus: 99 },
  { boundary: 'dispatch', httpStatus: 600 }, { boundary: 'dispatch', httpStatus: 400.5 },
  { boundary: 'dispatch', body: 'private response' },
  { boundary: 'dispatch', httpStatus: 400, extra: 'private response' }])(
  'rejects malformed or expanded diagnostics: %j', diagnostic => {
    expect(() => connectionPoll(failedPoll(protocolIssue(diagnostic)), request))
      .toThrow('Invalid assistant poll.');
  });

it('rejects diagnostics attached to another error code', () => {
  expect(() => connectionPoll(failedPoll({ code: 'AI_AUTH_FAILED', message: 'ignored',
    diagnostic: { boundary: 'dispatch', httpStatus: 401 } }), request))
    .toThrow('Invalid assistant poll.');
});

it('preserves the same safe diagnostic in a connection cancellation receipt', () => {
  const receipt: Record<string, unknown> = { ...failedPoll(
    protocolIssue({ boundary: 'dispatch', httpStatus: 404 })) };
  for (const field of ['usage', 'connectionResult', 'reset', 'events', 'evictedConversationIds']) delete receipt[field];
  expect(connectionCancelReceipt(receipt, request).issue?.diagnostic)
    .toEqual({ boundary: 'dispatch', httpStatus: 404 });
});
