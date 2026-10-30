import { expect, it } from 'vitest';
import { captureAssistantContext, dirtyContextWarning } from './context';

it('captures only saved identifiers and a detached AST anchor, never source drafts', () => {
  const anchor = { filePath: 'src/app.py', startLine: 2, startColumn: 0, endLine: 4, endColumn: 8 };
  const captured = captureAssistantContext({ kind: 'source', filePath: anchor.filePath, anchor, mode: 'function' });
  anchor.startLine = 99;
  expect(captured).toEqual({ kind: 'source', filePath: 'src/app.py', mode: 'function',
    anchor: { filePath: 'src/app.py', startLine: 2, startColumn: 0, endLine: 4, endColumn: 8 } });
  expect(() => captureAssistantContext({ ...captured, sourceText: 'UNSAVED EDITOR DRAFT' }))
    .toThrowError(expect.objectContaining({ code: 'AI_INVALID_INPUT' }));
  expect(() => captureAssistantContext({ ...captured, anchor: { ...anchor, text: 'DRAFT' } }))
    .toThrowError(expect.objectContaining({ code: 'AI_INVALID_INPUT' }));
});

it('keeps visual group identities for backend mapping rather than inventing code symbols', () => {
  expect(captureAssistantContext({ kind: 'node', viewKind: 'Architecture', visibleNodeId: 'group:models',
    rootId: null, detailSourceId: null })).toEqual({ kind: 'node', viewKind: 'Architecture',
    visibleNodeId: 'group:models', rootId: null, detailSourceId: null });
  expect(captureAssistantContext({ kind: 'question' })).toEqual({ kind: 'question' });
  expect(() => captureAssistantContext({ kind: 'question', draft: 'not a context' })).toThrow();
});

it('warns for any project dirty document when dependencies are unknown, without adding it to context', () => {
  expect(dirtyContextWarning(['other.py'])).toBe(true);
  expect(dirtyContextWarning(['other.py'], ['app.py'])).toBe(false);
  expect(dirtyContextWarning(['app.py'], ['app.py'])).toBe(true);
  expect(dirtyContextWarning([])).toBe(false);
});

it('captures only a typed project item reference without copying old AI text or source into a question', () => {
  const context = { kind: 'understanding', artifactId: 'artifact-1', revision: 1, itemId: 'summary:0' };
  expect(captureAssistantContext(context)).toEqual(context);
  for (const invalid of [{ ...context, text: 'previous generated prose' }, { ...context, revision: true },
    { ...context, itemId: 'summary:01' }, { ...context, source: 'private source' }]) {
    expect(() => captureAssistantContext(invalid)).toThrowError(expect.objectContaining({ code: 'AI_INVALID_INPUT' }));
  }
});
