import { expect, it } from 'vitest';
import { graphNavigationIntent, sourceNavigationIntent, utf8AnchorToMonacoRange } from './navigation';
import type { Anchor } from '../../types';
import type { SourceDocument } from '../sourceDocuments';
import type { NavigationPoll } from './types';

function completedNavigation(): NavigationPoll {
  return {
    kind: 'navigation', jobId: 'navigation-job', projectEpoch: 'epoch-1',
    analysisGeneration: 2, conversationId: 'chat-1', conversationRevision: 3,
    status: 'Completed', configRevision: 4, consentRevision: 5,
    cancelRequested: false, cancelAccepted: false, cancellation: 'none',
    dispatchClosed: true, resources: 'released', resourcesReleased: true, issue: null,
    action: 'source', evidenceId: 'evidence-1', target: {
      evidenceId: 'evidence-1',
      scope: { projectEpoch: 'epoch-1', analysisGeneration: 2, requestId: 'original-request' },
      binding: { projectEpoch: 'epoch-1', analysisGeneration: 2 },
      action: 'source', astAnchor: { filePath: 'app.py', startLine: 1, startColumn: 5,
        endLine: 1, endColumn: 8 },
      monacoRange: { startLineNumber: 1, startColumn: 4, endLineNumber: 1, endColumn: 5 },
      reveal: null, checkedAt: '2026-10-19T00:00:00Z', conversationId: 'chat-1',
      conversationRevision: 3, configRevision: 4, consentRevision: 5,
    },
    reset: false, seq: 1, events: [], evictedConversationIds: [],
  };
}

function currentBinding() {
  return { projectEpoch: 'epoch-1', analysisGeneration: 2, conversationId: 'chat-1',
    conversationRevision: 3, configRevision: 4, consentRevision: 5,
    projectPath: 'E:/saved-project', stale: false };
}

function savedDocument(): SourceDocument {
  return { projectPath: 'E:/saved-project', filePath: 'app.py', key: 'local-document',
    text: 'a😀中z', baselineText: 'a😀中z', dirty: false, saving: false, error: null,
    revision: 'opaque-opening:raw-file:identity', encoding: 'utf-8', newline: 'LF', readOnlyReason: null };
}

function graphNavigation(): NavigationPoll {
  const snapshot = completedNavigation();
  snapshot.action = snapshot.target!.action = 'graph';
  snapshot.target!.monacoRange = null;
  snapshot.target!.reveal = { viewKind: 'Symbol', rootId: 'local-root', selectedId: 'original-symbol', reason: null };
  return snapshot;
}

it('converts known identical saved UTF-8 text to one-based UTF-16 without changing AST columns', () => {
  const savedText = 'a😀中z\r\n 𐐀end\n';
  const ast: Anchor = Object.freeze({ filePath: 'app.py', startLine: 1, startColumn: 5,
    endLine: 2, endColumn: 5 });
  expect(utf8AnchorToMonacoRange(ast, savedText)).toEqual({ startLineNumber: 1,
    startColumn: 4, endLineNumber: 2, endColumn: 4 });
  expect(ast.startColumn).toBe(5);
});

it('refuses an AST byte column inside a multibyte code point', () => {
  expect(utf8AnchorToMonacoRange({ filePath: 'app.py', startLine: 1, startColumn: 2,
    endLine: 1, endColumn: 5 }, 'a😀z')).toBeNull();
});

it('offers whole-line opening when columns are missing, preserving the original AST', () => {
  const snapshot = completedNavigation();
  snapshot.target!.astAnchor.startColumn = snapshot.target!.astAnchor.endColumn = null;
  snapshot.target!.monacoRange = null;
  expect(sourceNavigationIntent(snapshot, currentBinding(), savedDocument())).toEqual({
    kind: 'source', astAnchor: snapshot.target!.astAnchor, openAnchor: snapshot.target!.astAnchor,
    monacoRange: null, checkedAt: snapshot.target!.checkedAt, reason: 'whole_line',
  });
});

it('keeps offsets disabled when a clean saved revision cannot prove the target hash', () => {
  const snapshot = completedNavigation();
  const document = savedDocument();
  document.revision = 'a'.repeat(64); // Still opaque, not a target digest binding.
  document.text = document.baselineText = 'different saved bytes';
  const intent = sourceNavigationIntent(snapshot, currentBinding(), document);
  expect(intent).toEqual({ kind: 'source', astAnchor: snapshot.target!.astAnchor,
    openAnchor: { ...snapshot.target!.astAnchor, startColumn: null, endColumn: null },
    monacoRange: null, checkedAt: snapshot.target!.checkedAt, reason: 'unverified_source' });
  expect(snapshot.target!.astAnchor.startColumn).toBe(5);
});

it('leaves a dirty draft cursor and text alone', () => {
  const document = savedDocument();
  document.text = 'unsaved draft';
  document.dirty = true;
  expect(sourceNavigationIntent(completedNavigation(), currentBinding(), document)).toMatchObject({
    openAnchor: null, monacoRange: null, reason: 'dirty_source',
  });
  expect(document.text).toBe('unsaved draft');
});

it('rejects stale analysis before opening source', () => {
  expect(sourceNavigationIntent(completedNavigation(), { ...currentBinding(), stale: true },
    savedDocument())).toBeNull();
});

it.each(['projectEpoch', 'analysisGeneration', 'conversationId', 'conversationRevision',
  'configRevision', 'consentRevision'] as const)('rejects a late source target after %s changes', key => {
  const current = currentBinding();
  const changed = typeof current[key] === 'number' ? Number(current[key]) + 1 : 'changed';
  expect(sourceNavigationIntent(completedNavigation(), { ...current, [key]: changed },
    savedDocument())).toBeNull();
});

it('rejects provisional provider text instead of interpreting it as navigation', () => {
  const snapshot = completedNavigation();
  Object.assign(snapshot, { status: 'Running', target: '<a href="https://invalid.example">go</a>' });
  expect(sourceNavigationIntent(snapshot, currentBinding(), savedDocument())).toBeNull();
});

it('maps only the original validated graph reveal and retains its check timestamp', () => {
  const snapshot = graphNavigation();
  expect(graphNavigationIntent(snapshot, currentBinding())).toEqual({ kind: 'graph',
    reveal: snapshot.target!.reveal, checkedAt: snapshot.target!.checkedAt });
});

it('offers a source-only reason rather than guessing a graph node from the AST', () => {
  const snapshot = completedNavigation();
  expect(graphNavigationIntent(snapshot, currentBinding())).toEqual({ kind: 'source-only',
    reason: 'source_only', checkedAt: snapshot.target!.checkedAt });
});

it('rejects a graph target without a validated reveal instead of fabricating one', () => {
  const snapshot = graphNavigation();
  snapshot.target!.reveal = null;
  expect(graphNavigationIntent(snapshot, currentBinding())).toBeNull();
});

it('rejects a late graph target after reanalysis', () => {
  expect(graphNavigationIntent(graphNavigation(), { ...currentBinding(), analysisGeneration: 3 })).toBeNull();
});

it.each(['https://invalid.example/node', 'javascript:alert(1)', 'file:///private', '//invalid.example'])(
  'does not turn an external reference into a graph reveal: %s', selectedId => {
    const snapshot = graphNavigation();
    snapshot.target!.reveal!.selectedId = selectedId;
    expect(graphNavigationIntent(snapshot, currentBinding())).toBeNull();
  },
);
