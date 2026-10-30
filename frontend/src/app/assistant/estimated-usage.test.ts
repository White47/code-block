import { expect, it } from 'vitest';
import { assistantPoll } from './poll';

const reserved = { inputTokens: 1000, outputTokens: 32, modelRequests: 1,
  toolCalls: 0, sourceCharacters: 0, fileCount: 0 };
const strict = () => ({ reserved: { ...reserved },
  observed: { inputTokens: 0, outputTokens: 0, basis: 'actual' } });
const estimated = (limitTokens = 20000) => ({ ...strict(), inputAccounting: {
  basis: 'estimated', method: 'wire-bytes-v1', limitTokens, upperBound: false,
  knownActualInputTokens: 0, blockedReason: null,
} });
const request = (kind: string) => ({ jobId: 'job-1',
  projectEpoch: kind === 'connection' ? null : 'epoch-1', afterSeq: 0 });

function snapshot(kind: 'explanation' | 'connection', usage: unknown) {
  return { kind, jobId: 'job-1', projectEpoch: kind === 'connection' ? null : 'epoch-1',
    analysisGeneration: kind === 'connection' ? null : 1,
    conversationId: kind === 'connection' ? null : 'chat-1',
    conversationRevision: kind === 'connection' ? null : 0,
    consentRevision: kind === 'connection' ? null : 1, configRevision: 1,
    status: 'Running', cancelRequested: false, cancelAccepted: false, cancellation: 'none',
    dispatchClosed: false, resources: 'held', resourcesReleased: false, issue: null,
    usage, seq: 1, reset: false, events: [] as object[], evictedConversationIds: [],
    ...(kind === 'connection' ? { connectionResult: null } : { displayText: '', answer: null }) };
}

it('accepts the closed strict and estimated shapes for their respective job kinds, including zero', () => {
  for (const kind of ['explanation', 'connection'] as const) {
    for (const usage of [strict(), estimated(kind === 'connection' ? 1024 : 20000)]) {
      const value = snapshot(kind, usage);
      expect(assistantPoll(value, request(kind))).toEqual(value);
    }
  }
});

it('rejects extra fields, false upper-bound claims, invalid metadata and inconsistent actual totals', () => {
  const valid = estimated();
  const malformed = [
    { ...valid, sourceText: 'not allowed' },
    { ...valid, inputAccounting: { ...valid.inputAccounting, extra: true } },
    ...[{ upperBound: true }, { method: 'tokenizer-v0' }, { blockedReason: 'other' },
      { knownActualInputTokens: true }, { knownActualInputTokens: Number.MAX_SAFE_INTEGER + 1 },
      { knownActualInputTokens: 1 }, { limitTokens: 1024 }]
      .map(change => ({ ...valid, inputAccounting: { ...valid.inputAccounting, ...change } })),
    { ...valid, observed: { inputTokens: true, outputTokens: 0, basis: 'actual' } },
  ];
  for (const usage of malformed) {
    expect(() => assistantPoll(snapshot('explanation', usage), request('explanation'))).toThrow();
  }
  expect(() => assistantPoll(snapshot('connection', estimated()), request('connection'))).toThrow();
});

it('accepts the larger explanation budget without granting it to connection checks', () => {
  const usage = estimated(100000);
  const value = snapshot('explanation', usage);
  expect(assistantPoll(value, request('explanation'))).toEqual(value);
  expect(() => assistantPoll(snapshot('connection', usage), request('connection'))).toThrow();
  const aboveOldLimit = { ...usage,
    observed: { inputTokens: 25000, outputTokens: 324, basis: 'actual' },
    inputAccounting: { ...usage.inputAccounting, knownActualInputTokens: 25000,
      blockedReason: 'observed_overrun' } };
  expect(() => assistantPoll(snapshot('explanation', aboveOldLimit), request('explanation'))).toThrow();
  const overrun = { ...aboveOldLimit,
    observed: { ...aboveOldLimit.observed, inputTokens: 100001 },
    inputAccounting: { ...aboveOldLimit.inputAccounting, knownActualInputTokens: 100001 } };
  const overrunValue = snapshot('explanation', overrun);
  expect(assistantPoll(overrunValue, request('explanation'))).toEqual(overrunValue);
});

it('checks kind-specific limits in usage events and reset snapshots, not just the outer usage', () => {
  for (const kind of ['explanation', 'connection'] as const) {
    const value = snapshot(kind, estimated(kind === 'connection' ? 1024 : 20000));
    const { jobId, projectEpoch, analysisGeneration, conversationId, conversationRevision } = value;
    const event = { kind, jobId, projectEpoch, analysisGeneration, conversationId,
      conversationRevision, seq: 1, type: 'usage', usage: value.usage };
    value.events = [event];
    expect(assistantPoll(value, request(kind))).toEqual(value);
    event.usage = estimated(kind === 'connection' ? 20000 : 1024);
    expect(() => assistantPoll(value, request(kind))).toThrow();
    value.events = []; value.reset = true;
    expect(assistantPoll(value, request(kind))).toEqual(value);
    value.usage = event.usage;
    expect(() => assistantPoll(value, request(kind))).toThrow();
  }
});

it('keeps partial actual input separate from unknown aggregate and never adds usage to navigation', () => {
  const usage = { ...estimated(), observed: { inputTokens: null, outputTokens: null, basis: 'unknown' },
    inputAccounting: { ...estimated().inputAccounting, knownActualInputTokens: 7, blockedReason: 'usage_unknown' } };
  const value = snapshot('explanation', usage);
  expect(assistantPoll(value, request('explanation'))).toEqual(value);
  expect(() => assistantPoll({ ...value, usage: { ...usage,
    observed: { ...usage.observed, inputTokens: 7 } } }, request('explanation'))).toThrow();
  expect(() => assistantPoll({ ...value, kind: 'navigation' }, request('explanation'))).toThrow();
});

it('enforces zero versus unknown in strict usage as well as estimated usage', () => {
  for (const observed of [
    { inputTokens: null, outputTokens: null, basis: 'actual' },
    { inputTokens: 0, outputTokens: 0, basis: 'unknown' },
    { inputTokens: null, outputTokens: 0, basis: 'estimated' },
  ]) {
    const usage = { ...strict(), observed };
    expect(() => assistantPoll(snapshot('explanation', usage), request('explanation'))).toThrow();
  }
});

it('rejects warning metadata that contradicts known actual or unknown cumulative usage', () => {
  for (const kind of ['explanation', 'connection'] as const) {
    const limit = kind === 'connection' ? 1024 : 20000;
    const valid = estimated(limit);
    for (const blockedReason of ['usage_unknown', 'observed_overrun']) {
      const usage = { ...valid, inputAccounting: { ...valid.inputAccounting, blockedReason } };
      expect(() => assistantPoll(snapshot(kind, usage), request(kind))).toThrow();
    }
    const overrun = { ...valid, observed: { inputTokens: limit + 1, outputTokens: 0, basis: 'actual' },
      inputAccounting: { ...valid.inputAccounting, knownActualInputTokens: limit + 1, blockedReason: 'observed_overrun' } };
    expect(assistantPoll(snapshot(kind, overrun), request(kind))).toEqual(snapshot(kind, overrun));
  }
});
