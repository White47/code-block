import { expect, it } from 'vitest';
import { connectionPoll } from './poll';

const request = { jobId: 'probe', projectEpoch: null, afterSeq: 0 };
const localResult = { toolSupported: true, structuredOutputSupported: false,
  jsonObjectSupported: true, outputValidation: 'local-json-schema' };
const completed = {
  kind: 'connection', jobId: 'probe', projectEpoch: null, analysisGeneration: null,
  conversationId: null, conversationRevision: null, configRevision: 1, consentRevision: null,
  status: 'Completed', cancelRequested: false, cancelAccepted: false, cancellation: 'none',
  dispatchClosed: true, resources: 'released', resourcesReleased: true, issue: null,
  usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0,
    toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
  observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } },
  connectionResult: localResult, reset: true, seq: 1, events: [], evictedConversationIds: [],
};

it('accepts the explicit local-validation branch without upgrading it to remote strictness', () => {
  expect(connectionPoll(completed, request).connectionResult).toEqual(localResult);
});

it('rejects ambiguous or open capability variants', () => {
  for (const result of [
    { toolSupported: true, structuredOutputSupported: false },
    { ...localResult, structuredOutputSupported: true },
    { ...localResult, jsonObjectSupported: 'true' },
    { ...localResult, outputValidation: 'remote-json-schema' },
    { ...localResult, sourceText: 'untrusted extra field' },
  ]) {
    expect(() => connectionPoll({ ...completed, connectionResult: result }, request))
      .toThrow('Invalid assistant poll.');
  }
});

it('does not expose a local capability before successful completion', () => {
  expect(() => connectionPoll({ ...completed, status: 'Failed' }, request))
    .toThrow('Invalid assistant poll.');
});
