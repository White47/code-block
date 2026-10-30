import { expect, it } from 'vitest';
import { assistantPoll } from './poll';

function completedNavigation(action: 'source' | 'graph' = 'source') {
  return {
    kind: 'navigation', jobId: 'navigation-job', projectEpoch: 'epoch-1',
    analysisGeneration: 2, conversationId: 'conversation-1', conversationRevision: 3,
    status: 'Completed', configRevision: 4, consentRevision: 5,
    cancelRequested: false, cancelAccepted: false, cancellation: 'none',
    dispatchClosed: true, resources: 'released', resourcesReleased: true, issue: null,
    action, evidenceId: 'evidence-1', target: {
      evidenceId: 'evidence-1',
      scope: { projectEpoch: 'epoch-1', analysisGeneration: 2, requestId: 'original-request' },
      binding: { projectEpoch: 'epoch-1', analysisGeneration: 2 },
      action, astAnchor: { filePath: 'app.py', startLine: 10, startColumn: 0,
        endLine: 12, endColumn: 8 },
      monacoRange: action === 'source'
        ? { startLineNumber: 10, startColumn: 1, endLineNumber: 12, endColumn: 9 } : null,
      reveal: action === 'graph'
        ? { viewKind: 'Symbol', rootId: null as string | null, selectedId: 'symbol-1', reason: null } : null,
      checkedAt: '2026-10-18T00:00:00Z', conversationId: 'conversation-1',
      conversationRevision: 3, configRevision: 4, consentRevision: 5,
    },
    reset: false, seq: 1, events: [], evictedConversationIds: [],
  };
}

const request = { jobId: 'navigation-job', projectEpoch: 'epoch-1', afterSeq: 0 };

it.each(['source', 'graph'] as const)('accepts an identity-bound completed %s navigation target', action => {
  const snapshot = completedNavigation(action);
  expect(assistantPoll(snapshot, request)).toEqual(snapshot);
});

it('counts a Unicode evidence identifier in characters rather than UTF-16 units', () => {
  const snapshot = completedNavigation();
  snapshot.evidenceId = snapshot.target.evidenceId = '😀'.repeat(512);
  expect(assistantPoll(snapshot, request)).toEqual(snapshot);
});

it.each(['scope', 'binding'] as const)('rejects a foreign generation in target %s', key => {
  const snapshot = completedNavigation();
  snapshot.target[key].analysisGeneration += 1;
  expect(() => assistantPoll(snapshot, request)).toThrow('Invalid assistant poll.');
});

it.each(['conversationRevision', 'configRevision', 'consentRevision'] as const)(
  'rejects a foreign target %s', key => {
    const snapshot = completedNavigation();
    snapshot.target[key] += 1;
    expect(() => assistantPoll(snapshot, request)).toThrow('Invalid assistant poll.');
  },
);

it('rejects internal filesystem fields on the public binding', () => {
  const snapshot = completedNavigation();
  Object.assign(snapshot.target.binding, { projectRoot: 'E:/private', ignorePaths: [] });
  expect(() => assistantPoll(snapshot, request)).toThrow('Invalid assistant poll.');
});

it('rejects a source selection outside its AST line range', () => {
  const snapshot = completedNavigation();
  snapshot.target.monacoRange!.endLineNumber = 13;
  expect(() => assistantPoll(snapshot, request)).toThrow('Invalid assistant poll.');
});

it('rejects a graph target carrying an editor selection', () => {
  const snapshot = completedNavigation('graph');
  snapshot.target.monacoRange = { startLineNumber: 10, startColumn: 1,
    endLineNumber: 12, endColumn: 9 };
  expect(() => assistantPoll(snapshot, request)).toThrow('Invalid assistant poll.');
});

it('accepts exactly 8 KiB of target UTF-8 and rejects one extra byte', () => {
  const snapshot = completedNavigation('graph');
  snapshot.evidenceId = snapshot.target.evidenceId = '😀'.repeat(512);
  snapshot.target.astAnchor.filePath = `${'😀'.repeat(508)}.py`;
  snapshot.target.reveal!.selectedId = '😀'.repeat(512);
  snapshot.target.reveal!.rootId = '';
  const remaining = 8192 - new TextEncoder().encode(JSON.stringify(snapshot.target)).length;
  expect(remaining).toBeGreaterThan(0);
  snapshot.target.reveal!.rootId = '😀'.repeat(Math.floor(remaining / 4)) + 'x'.repeat(remaining % 4);
  expect(Array.from(snapshot.target.reveal!.rootId).length).toBeLessThanOrEqual(512);
  expect(assistantPoll(snapshot, request)).toEqual(snapshot);
  snapshot.target.reveal!.rootId += 'x';
  expect(() => assistantPoll(snapshot, request)).toThrow('Invalid assistant poll.');
});

it.each(['\u202E', '\u2028'])('rejects a non-printable evidence identifier %j', character => {
  const snapshot = completedNavigation();
  snapshot.evidenceId = snapshot.target.evidenceId = `evidence${character}1`;
  expect(() => assistantPoll(snapshot, request)).toThrow('Invalid assistant poll.');
});

it.each([-1, 0.5, Number.NaN])('rejects an invalid cursor %s at the exported poll validator', afterSeq => {
  expect(() => assistantPoll(completedNavigation(), { ...request, afterSeq }))
    .toThrow('Invalid assistant poll.');
});
