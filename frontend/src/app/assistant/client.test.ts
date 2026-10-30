import { afterEach, expect, it, vi } from 'vitest';
import { call } from '../../api/bridge';
import type { Envelope, Session } from '../../types';
import { assistantClient } from './client';
import type { AssistantStartRequest, AssistantStartResult } from './types';

afterEach(() => { delete window.pywebview; });

it('starts only on explicit call without waiting for a pending project mutation', async () => {
  const request: AssistantStartRequest = {
    projectEpoch: 'epoch-1', analysisGeneration: 2, conversationId: 'conversation-1',
    requestId: 'request-1', configRevision: 3, consentRevision: 4,
    question: 'What does this function do?', context: { kind: 'question' },
  };
  const result: AssistantStartResult = {
    jobId: 'job-1', status: 'Queued' as const,
    scope: { projectEpoch: 'epoch-1', analysisGeneration: 2, requestId: 'request-1' },
    conversationId: 'conversation-1', kind: 'explanation',
  };
  let finishProject!: (value: Envelope<Session>) => void;
  const project = vi.fn(() => new Promise<Envelope<Session>>(resolve => { finishProject = resolve; }));
  const start = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: result }));
  window.pywebview = { api: { open_project: project, assistant_start: start } };
  const pendingProject = call('open_project', 'E:\\pending');
  const actual = await assistantClient.start(request);
  expect(actual).toEqual(result);
  expect(start).toHaveBeenCalledExactlyOnceWith(request);
  finishProject({ ok: true, schemaVersion: 1, data: {
    projectPath: 'E:\\pending', status: 'NotAnalyzed', hasAnalysis: false,
    outdatedReasons: [], cacheIssue: null, recentProjects: [],
  } });
  await pendingProject;
});

it('accepts a closed connection snapshot without contacting a provider', async () => {
  const snapshot = {
    kind: 'connection', jobId: 'job-1', projectEpoch: null,
    analysisGeneration: null, conversationId: null, conversationRevision: null,
    status: 'Completed', configRevision: 2, consentRevision: null,
    cancelRequested: false, cancelAccepted: false, cancellation: 'none',
    dispatchClosed: true, resources: 'released', resourcesReleased: true, issue: null,
    usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0,
      toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
    observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } },
    connectionResult: { toolSupported: true, structuredOutputSupported: true },
    reset: false, seq: 1, events: [], evictedConversationIds: [],
  };
  const poll = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: snapshot }));
  window.pywebview = { api: { assistant_poll: poll } };
  await expect(assistantClient.poll({ jobId: 'job-1', projectEpoch: null, afterSeq: 0 }))
    .resolves.toEqual(snapshot);
  expect(poll).toHaveBeenCalledExactlyOnceWith({ jobId: 'job-1', projectEpoch: null, afterSeq: 0 });
});

it('rejects source-bearing fields on a connection poll', async () => {
  const poll = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: {
    kind: 'connection', jobId: 'job-1', projectEpoch: null,
    analysisGeneration: null, conversationId: null, conversationRevision: null,
    status: 'Completed', configRevision: 2, consentRevision: null,
    cancelRequested: false, cancelAccepted: false, cancellation: 'none',
    dispatchClosed: true, resources: 'released', resourcesReleased: true, issue: null,
    usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0,
      toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
    observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } },
    connectionResult: { toolSupported: true, structuredOutputSupported: true },
    reset: false, seq: 1, events: [], evictedConversationIds: [],
    displayText: 'private source must not be exposed',
  } }));
  window.pywebview = { api: { assistant_poll: poll } };
  await expect(assistantClient.poll({ jobId: 'job-1', projectEpoch: null, afterSeq: 0 }))
    .rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
});

it('rejects source text in a question before invoking the desktop bridge', async () => {
  const start = vi.fn();
  window.pywebview = { api: { assistant_start: start } };
  const request = {
    projectEpoch: 'epoch-1', analysisGeneration: 2, conversationId: 'conversation-1',
    requestId: 'request-1', configRevision: 3, consentRevision: 4,
    question: 'Explain this', context: { kind: 'question', sourceText: 'private code' },
  } as unknown as AssistantStartRequest;
  await expect(assistantClient.start(request)).rejects.toMatchObject({ code: 'AI_INVALID_INPUT' });
  expect(start).not.toHaveBeenCalled();
});

it('rejects a negative poll cursor before invoking the desktop bridge', async () => {
  const poll = vi.fn();
  window.pywebview = { api: { assistant_poll: poll } };
  await expect(assistantClient.poll({ jobId: 'job-1', projectEpoch: null, afterSeq: -1 }))
    .rejects.toMatchObject({ code: 'AI_INVALID_INPUT' });
  expect(poll).not.toHaveBeenCalled();
});

it('rejects an unexpected source field in a start receipt', async () => {
  const start = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: {
    jobId: 'job-1', status: 'Queued' as const,
    scope: { projectEpoch: 'epoch-1', analysisGeneration: 2, requestId: 'request-1' },
    conversationId: 'conversation-1', kind: 'explanation' as const, sourceText: 'private code',
  } }));
  window.pywebview = { api: { assistant_start: start } };
  await expect(assistantClient.start({
    projectEpoch: 'epoch-1', analysisGeneration: 2, conversationId: 'conversation-1',
    requestId: 'request-1', configRevision: 3, consentRevision: 4,
    question: 'Explain this', context: { kind: 'question' },
  })).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
});

it('rejects a non-primitive lifecycle status from the desktop bridge', async () => {
  const poll = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: {
    kind: 'connection', jobId: 'job-1', projectEpoch: null,
    analysisGeneration: null, conversationId: null, conversationRevision: null,
    status: new String('Completed'), configRevision: 2, consentRevision: null,
    cancelRequested: false, cancelAccepted: false, cancellation: 'none',
    dispatchClosed: true, resources: 'released', resourcesReleased: true, issue: null,
    usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0,
      toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
    observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } },
    connectionResult: { toolSupported: true, structuredOutputSupported: true },
    reset: false, seq: 1, events: [], evictedConversationIds: [],
  } }));
  window.pywebview = { api: { assistant_poll: poll } };
  await expect(assistantClient.poll({ jobId: 'job-1', projectEpoch: null, afterSeq: 0 }))
    .rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
});

it('rejects a completed connection after cancellation was requested', async () => {
  const poll = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: {
    kind: 'connection', jobId: 'job-1', projectEpoch: null,
    analysisGeneration: null, conversationId: null, conversationRevision: null,
    status: 'Completed', configRevision: 2, consentRevision: null,
    cancelRequested: true, cancelAccepted: false, cancellation: 'requested',
    dispatchClosed: true, resources: 'released', resourcesReleased: true, issue: null,
    usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0,
      toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
    observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } },
    connectionResult: { toolSupported: true, structuredOutputSupported: true },
    reset: false, seq: 1, events: [], evictedConversationIds: [],
  } }));
  window.pywebview = { api: { assistant_poll: poll } };
  await expect(assistantClient.poll({ jobId: 'job-1', projectEpoch: null, afterSeq: 0 }))
    .rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
});

it('never exposes a backend issue message through the connection snapshot', async () => {
  const poll = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: {
    kind: 'connection', jobId: 'job-1', projectEpoch: null,
    analysisGeneration: null, conversationId: null, conversationRevision: null,
    status: 'Failed', configRevision: 2, consentRevision: null,
    cancelRequested: false, cancelAccepted: false, cancellation: 'none',
    dispatchClosed: true, resources: 'released', resourcesReleased: true,
    issue: { code: 'AI_AUTH_FAILED', message: 'secret-token-123' },
    usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0,
      toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
    observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } },
    connectionResult: null, reset: false, seq: 1, events: [], evictedConversationIds: [],
  } }));
  window.pywebview = { api: { assistant_poll: poll } };
  const result = await assistantClient.poll({ jobId: 'job-1', projectEpoch: null, afterSeq: 0 });
  expect(result.issue).toEqual({ code: 'AI_AUTH_FAILED', message: 'Assistant request could not be completed.' });
  expect(JSON.stringify(result)).not.toContain('secret-token-123');
});

it('rejects a boxed lifecycle status in a start receipt', async () => {
  const start = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: {
    jobId: 'job-1', status: new String('Queued'),
    scope: { projectEpoch: 'epoch-1', analysisGeneration: 2, requestId: 'request-1' },
    conversationId: 'conversation-1', kind: 'explanation' as const,
  } as unknown as AssistantStartResult }));
  window.pywebview = { api: { assistant_start: start } };
  await expect(assistantClient.start({
    projectEpoch: 'epoch-1', analysisGeneration: 2, conversationId: 'conversation-1',
    requestId: 'request-1', configRevision: 3, consentRevision: 4,
    question: 'Explain this', context: { kind: 'question' },
  })).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
});

it('does not expose native error details from an assistant start', async () => {
  const start = vi.fn(async () => ({ ok: false as const, schemaVersion: 1,
    error: { code: 'AI_AUTH_FAILED', message: 'untrusted-provider-debug-message' } }));
  window.pywebview = { api: { assistant_start: start } };
  let failure: unknown;
  try {
    await assistantClient.start({
      projectEpoch: 'epoch-1', analysisGeneration: 2, conversationId: 'conversation-1',
      requestId: 'request-1', configRevision: 3, consentRevision: 4,
      question: 'Explain this', context: { kind: 'question' },
    });
  } catch (error) { failure = error; }
  expect(failure).toMatchObject({ code: 'AI_AUTH_FAILED' });
  expect(String(failure)).not.toContain('untrusted-provider-debug-message');
});

it('keeps a requested connection cancellation distinct from confirmation', async () => {
  const receipt = {
    kind: 'connection', jobId: 'job-1', projectEpoch: null,
    analysisGeneration: null, conversationId: null, conversationRevision: null,
    status: 'Running', configRevision: 2, consentRevision: null,
    cancelRequested: true, cancelAccepted: false, cancellation: 'requested',
    dispatchClosed: false, resources: 'held', resourcesReleased: false,
    issue: null, seq: 3,
  };
  const cancel = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: receipt }));
  window.pywebview = { api: { assistant_cancel: cancel } } as unknown as typeof window.pywebview;
  const client = assistantClient as typeof assistantClient & {
    cancel(request: { jobId: string; projectEpoch: null }): Promise<unknown>;
  };
  await expect(client.cancel({ jobId: 'job-1', projectEpoch: null })).resolves.toEqual(receipt);
  expect(cancel).toHaveBeenCalledExactlyOnceWith({ jobId: 'job-1', projectEpoch: null });
});

it('accepts a project-bound provisional explanation without a final answer', async () => {
  const identity = {
    kind: 'explanation', jobId: 'job-2', projectEpoch: 'epoch-1',
    analysisGeneration: 2, conversationId: 'conversation-1', conversationRevision: 1,
  };
  const snapshot = {
    ...identity, status: 'Running', configRevision: 3, consentRevision: 4,
    cancelRequested: false, cancelAccepted: false, cancellation: 'none',
    dispatchClosed: false, resources: 'held', resourcesReleased: false, issue: null,
    usage: { reserved: { inputTokens: 1, outputTokens: 2, modelRequests: 1,
      toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
    observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } },
    displayText: 'Working...', answer: null,
    reset: false, seq: 1, events: [{ ...identity, seq: 1,
      type: 'display_text', text: 'Working...' }], evictedConversationIds: [],
  };
  const poll = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: snapshot }));
  window.pywebview = { api: { assistant_poll: poll } };
  await expect(assistantClient.poll({ jobId: 'job-2', projectEpoch: 'epoch-1', afterSeq: 0 }))
    .resolves.toEqual(snapshot);
});

it('accepts a completed explanation only with the seven validated answer sections', async () => {
  const sectionKinds = ['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns'];
  const snapshot = {
    kind: 'explanation', jobId: 'job-2', projectEpoch: 'epoch-1',
    analysisGeneration: 2, conversationId: 'conversation-1', conversationRevision: 1,
    status: 'Completed', configRevision: 3, consentRevision: 4,
    cancelRequested: false, cancelAccepted: false, cancellation: 'none',
    dispatchClosed: true, resources: 'released', resourcesReleased: true, issue: null,
    usage: { reserved: { inputTokens: 1, outputTokens: 2, modelRequests: 1,
      toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
    observed: { inputTokens: 1, outputTokens: 2, basis: 'actual' } },
    displayText: '', answer: {
      sections: sectionKinds.map(kind => ({ kind, claims: [] })), evidence: [],
      relationFacts: [], incomplete: false, coverageGaps: [],
      checkedAt: '2026-10-16T00:00:00Z',
    },
    reset: false, seq: 1, events: [], evictedConversationIds: [],
  };
  const poll = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: snapshot }));
  window.pywebview = { api: { assistant_poll: poll } };
  await expect(assistantClient.poll({ jobId: 'job-2', projectEpoch: 'epoch-1', afterSeq: 0 }))
    .resolves.toEqual(snapshot);
});

it('keeps a project explanation cancellation request pending until confirmed', async () => {
  const receipt = {
    kind: 'explanation', jobId: 'job-2', projectEpoch: 'epoch-1',
    analysisGeneration: 2, conversationId: 'conversation-1', conversationRevision: 1,
    status: 'Running', configRevision: 3, consentRevision: 4,
    cancelRequested: true, cancelAccepted: false, cancellation: 'requested',
    dispatchClosed: false, resources: 'held', resourcesReleased: false,
    issue: null, seq: 3,
  };
  const cancel = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: receipt }));
  window.pywebview = { api: { assistant_cancel: cancel } };
  const client = assistantClient as typeof assistantClient & {
    cancel(request: { jobId: string; projectEpoch: string }): Promise<unknown>;
  };
  await expect(client.cancel({ jobId: 'job-2', projectEpoch: 'epoch-1' })).resolves.toEqual(receipt);
  expect(cancel).toHaveBeenCalledExactlyOnceWith({ jobId: 'job-2', projectEpoch: 'epoch-1' });
});

it('accepts a pending navigation check without a clickable target', async () => {
  const snapshot = {
    kind: 'navigation', jobId: 'job-3', projectEpoch: 'epoch-1',
    analysisGeneration: 2, conversationId: 'conversation-1', conversationRevision: 1,
    status: 'Running', configRevision: 3, consentRevision: 4,
    cancelRequested: false, cancelAccepted: false, cancellation: 'none',
    dispatchClosed: false, resources: 'held', resourcesReleased: false, issue: null,
    action: 'source', evidenceId: 'evidence-1', target: null,
    reset: false, seq: 1, events: [], evictedConversationIds: [],
  };
  const poll = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: snapshot }));
  window.pywebview = { api: { assistant_poll: poll } };
  await expect(assistantClient.poll({ jobId: 'job-3', projectEpoch: 'epoch-1', afterSeq: 0 }))
    .resolves.toEqual(snapshot);
});

it('keeps a navigation cancellation request pending until confirmation', async () => {
  const receipt = {
    kind: 'navigation', jobId: 'job-3', projectEpoch: 'epoch-1',
    analysisGeneration: 2, conversationId: 'conversation-1', conversationRevision: 1,
    status: 'Running', configRevision: 3, consentRevision: 4,
    cancelRequested: true, cancelAccepted: false, cancellation: 'requested',
    dispatchClosed: false, resources: 'held', resourcesReleased: false,
    issue: null, seq: 3,
  };
  const cancel = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: receipt }));
  window.pywebview = { api: { assistant_cancel: cancel } };
  await expect(assistantClient.cancel({ jobId: 'job-3', projectEpoch: 'epoch-1' }))
    .resolves.toEqual(receipt);
});

it('rejects invalid Unicode in provisional assistant display text', async () => {
  const snapshot = {
    kind: 'explanation', jobId: 'job-2', projectEpoch: 'epoch-1',
    analysisGeneration: 2, conversationId: 'conversation-1', conversationRevision: 1,
    status: 'Running', configRevision: 3, consentRevision: 4,
    cancelRequested: false, cancelAccepted: false, cancellation: 'none',
    dispatchClosed: false, resources: 'held', resourcesReleased: false, issue: null,
    usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0,
      toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
    observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } },
    displayText: 'bad\uD800', answer: null, reset: false, seq: 1,
    events: [], evictedConversationIds: [],
  };
  window.pywebview = { api: { assistant_poll: vi.fn(async () => ({
    ok: true as const, schemaVersion: 1, data: snapshot,
  })) } };
  await expect(assistantClient.poll({ jobId: 'job-2', projectEpoch: 'epoch-1', afterSeq: 0 }))
    .rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
});

it('rejects a reversed same-line source anchor before reaching the desktop bridge', async () => {
  const start = vi.fn();
  window.pywebview = { api: { assistant_start: start } };
  await expect(assistantClient.start({
    projectEpoch: 'epoch-1', analysisGeneration: 2, conversationId: 'conversation-1',
    requestId: 'request-1', configRevision: 3, consentRevision: 4,
    question: 'Explain this', context: { kind: 'source', filePath: 'app.py', mode: 'function',
      anchor: { filePath: 'app.py', startLine: 1, startColumn: 10, endLine: 1, endColumn: 2 } },
  })).rejects.toMatchObject({ code: 'AI_INVALID_INPUT' });
  expect(start).not.toHaveBeenCalled();
});

it('rejects a source anchor that points outside its selected file', async () => {
  const start = vi.fn();
  window.pywebview = { api: { assistant_start: start } };
  await expect(assistantClient.start({
    projectEpoch: 'epoch-1', analysisGeneration: 2, conversationId: 'conversation-1',
    requestId: 'request-1', configRevision: 3, consentRevision: 4,
    question: 'Explain this', context: { kind: 'source', filePath: 'app.py', mode: 'function',
      anchor: { filePath: 'other.py', startLine: 1, startColumn: null, endLine: 1, endColumn: null } },
  })).rejects.toMatchObject({ code: 'AI_INVALID_INPUT' });
  expect(start).not.toHaveBeenCalled();
});
