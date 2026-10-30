import { BridgeError } from '../../api/bridge';
import { viewKinds } from '../../types';
import type { AssistantContext, UnderstandingItemRef } from './types';

function exact(value: unknown, keys: string[]): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    && Object.keys(value).length === keys.length && Object.keys(value).every(key => keys.includes(key));
}
function text(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.length > 0 && Array.from(value).length <= maximum
    && !/[\p{C}\p{Z}]/u.test(value.replace(/ /g, ''));
}
function integer(value: unknown, minimum = 0): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum;
}
function invalid(): never { throw new BridgeError('AI_INVALID_INPUT', 'Invalid assistant context.'); }

export function validUnderstandingItem(value: unknown): value is UnderstandingItemRef {
  return exact(value, ['artifactId', 'revision', 'itemId']) && typeof value.artifactId === 'string'
    && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value.artifactId) && integer(value.revision, 1)
    && typeof value.itemId === 'string' && value.itemId.length <= 128
    && /^(?:project|(?:summary|entry|steps|branches|relations|evidence|unknowns):(?:0|[1-9][0-9]*))$/.test(value.itemId);
}

/** Closed identifier-only capture; the backend remains the authority for visual-to-code mapping. */
export function captureAssistantContext(value: unknown): AssistantContext {
  if (exact(value, ['kind']) && value.kind === 'question') return { kind: 'question' };
  if (exact(value, ['kind']) && value.kind === 'project') return { kind: 'project' };
  if (exact(value, ['kind', 'artifactId', 'revision', 'itemId']) && value.kind === 'understanding') {
    const reference = { artifactId: value.artifactId, revision: value.revision, itemId: value.itemId };
    if (validUnderstandingItem(reference)) return { kind: 'understanding', ...reference };
    invalid();
  }
  if (exact(value, ['kind', 'viewKind', 'visibleNodeId', 'rootId', 'detailSourceId'])
      && value.kind === 'node' && viewKinds.includes(value.viewKind as typeof viewKinds[number])
      && text(value.visibleNodeId, 512) && (value.rootId === null || text(value.rootId, 128))
      && (value.detailSourceId === null || text(value.detailSourceId, 128))) {
    return { kind: 'node', viewKind: value.viewKind as typeof viewKinds[number],
      visibleNodeId: value.visibleNodeId, rootId: value.rootId as string | null,
      detailSourceId: value.detailSourceId as string | null };
  }
  if (!exact(value, ['kind', 'filePath', 'anchor', 'mode']) || value.kind !== 'source'
      || !text(value.filePath, 512) || !['function', 'file'].includes(value.mode as string)) invalid();
  const anchor = value.anchor;
  if (anchor === null) return { kind: 'source', filePath: value.filePath, anchor: null,
    mode: value.mode as 'function' | 'file' };
  if (!exact(anchor, ['filePath', 'startLine', 'startColumn', 'endLine', 'endColumn'])
      || anchor.filePath !== value.filePath || !integer(anchor.startLine, 1)
      || !integer(anchor.endLine, anchor.startLine)
      || !(anchor.startColumn === null || integer(anchor.startColumn))
      || !(anchor.endColumn === null || integer(anchor.endColumn))
      || (anchor.startLine === anchor.endLine && anchor.startColumn !== null
        && anchor.endColumn !== null && anchor.startColumn > anchor.endColumn)) invalid();
  return { kind: 'source', filePath: value.filePath, mode: value.mode as 'function' | 'file',
    anchor: { filePath: value.filePath, startLine: anchor.startLine, endLine: anchor.endLine,
      startColumn: anchor.startColumn, endColumn: anchor.endColumn } };
}

/** Display-only. Unknown dependencies conservatively warn for every dirty project file. */
export function dirtyContextWarning(dirtyFiles: readonly string[], dependencies?: readonly string[]): boolean {
  return dependencies === undefined ? dirtyFiles.length > 0 : dirtyFiles.some(file => dependencies.includes(file));
}
