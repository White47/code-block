import { viewKinds } from '../../types';
import type { Anchor } from '../../types';
import { utcTimestamp, wellFormed } from './answer';
import type { SourceDocument } from '../sourceDocuments';
import type { AssistantNavigationTarget, NavigationPoll } from './types';

function exact(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    && Object.keys(value).length === keys.length
    && Object.keys(value).every(key => keys.includes(key));
}

function integer(value: unknown, minimum = 0): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum;
}

function text(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.length > 0 && Array.from(value).length <= maximum
    && wellFormed(value) && !/[\p{C}\p{Z}]/u.test(value.replace(/ /g, ''));
}

function identifier(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value);
}

function anchor(value: unknown): value is AssistantNavigationTarget['astAnchor'] {
  if (!exact(value, ['filePath', 'startLine', 'startColumn', 'endLine', 'endColumn'])
      || !text(value.filePath, 512) || /[\\:]/.test(value.filePath)
      || value.filePath.split('/').some(part => part === '' || part === '.' || part === '..')
      || !integer(value.startLine, 1) || !integer(value.endLine, 1)
      || value.endLine < value.startLine) return false;
  return (value.startColumn === null && value.endColumn === null)
    || (integer(value.startColumn) && integer(value.endColumn)
      && (value.startLine !== value.endLine || value.startColumn <= value.endColumn));
}

function range(value: unknown, source: AssistantNavigationTarget['astAnchor']): boolean {
  return exact(value, ['startLineNumber', 'startColumn', 'endLineNumber', 'endColumn'])
    && integer(value.startLineNumber, 1) && integer(value.startColumn, 1)
    && integer(value.endLineNumber, 1) && integer(value.endColumn, 1)
    && value.startLineNumber >= source.startLine && value.endLineNumber <= source.endLine
    && (value.startLineNumber < value.endLineNumber
      || (value.startLineNumber === value.endLineNumber && value.startColumn <= value.endColumn));
}

export function validNavigationTarget(value: unknown, snapshot: Record<string, unknown>):
  value is AssistantNavigationTarget {
  if (!exact(value, ['evidenceId', 'scope', 'binding', 'action', 'astAnchor',
    'monacoRange', 'reveal', 'checkedAt', 'conversationId', 'conversationRevision',
    'configRevision', 'consentRevision'])
      || !text(value.evidenceId, 512) || !text(value.conversationId, 128)
      || !['action', 'evidenceId', 'conversationId', 'conversationRevision',
        'configRevision', 'consentRevision'].every(key => value[key] === snapshot[key])
      || !integer(value.conversationRevision) || !integer(value.configRevision)
      || !integer(value.consentRevision) || !utcTimestamp(value.checkedAt)
      || !exact(value.scope, ['projectEpoch', 'analysisGeneration', 'requestId'])
      || !identifier(value.scope.projectEpoch) || !identifier(value.scope.requestId)
      || !integer(value.scope.analysisGeneration)
      || !exact(value.binding, ['projectEpoch', 'analysisGeneration'])
      || !['projectEpoch', 'analysisGeneration'].every(
        key => (value.scope as Record<string, unknown>)[key] === snapshot[key]
          && (value.binding as Record<string, unknown>)[key] === snapshot[key])
      || !anchor(value.astAnchor)) return false;
  if (value.action === 'source') {
    if (value.reveal !== null || (value.monacoRange !== null
        && (value.astAnchor.startColumn === null || !range(value.monacoRange, value.astAnchor)))) return false;
  } else if (value.action === 'graph') {
    if (value.monacoRange !== null || !exact(value.reveal, ['viewKind', 'rootId', 'selectedId', 'reason'])
        || !viewKinds.includes(value.reveal.viewKind as typeof viewKinds[number])
        || !text(value.reveal.selectedId, 512) || value.reveal.reason !== null
        || (value.reveal.rootId !== null && !text(value.reveal.rootId, 512))) return false;
  } else return false;
  return new TextEncoder().encode(JSON.stringify(value)).length <= 8 * 1024;
}

/** Coordinate math only: the caller must independently establish saved-file identity.
 * A range returned here is never evidence that editor text matches a navigation target.
 */
export function utf8AnchorToMonacoRange(ast: Anchor, savedText: string):
  AssistantNavigationTarget['monacoRange'] {
  if (!anchor(ast) || ast.startColumn === null || ast.endColumn === null
      || typeof savedText !== 'string' || !wellFormed(savedText)) return null;
  const lines = savedText.split(/\r\n|\r|\n/);
  if (ast.endLine > lines.length) return null;
  const encoder = new TextEncoder();
  const column = (line: string, byteOffset: number): number | null => {
    let bytes = 0;
    let units = 0;
    for (const character of line) {
      if (bytes === byteOffset) return units + 1;
      bytes += encoder.encode(character).length;
      units += character.length;
      if (bytes > byteOffset) return null;
    }
    return bytes === byteOffset ? units + 1 : null;
  };
  const startColumn = column(lines[ast.startLine - 1], ast.startColumn);
  const endColumn = column(lines[ast.endLine - 1], ast.endColumn);
  return startColumn === null || endColumn === null ? null : {
    startLineNumber: ast.startLine, startColumn, endLineNumber: ast.endLine, endColumn,
  };
}

/** Current local authority, recaptured at consumption time, not a file proof. */
export type NavigationContext = Readonly<Pick<NavigationPoll, 'projectEpoch' | 'analysisGeneration'
  | 'conversationId' | 'conversationRevision' | 'configRevision' | 'consentRevision'>>
  & { readonly projectPath: string; readonly stale: boolean };

function currentTarget(snapshot: NavigationPoll, current: NavigationContext): AssistantNavigationTarget | null {
  if (snapshot.kind !== 'navigation' || snapshot.status !== 'Completed'
      || snapshot.cancelRequested !== false || snapshot.cancelAccepted !== false
      || snapshot.dispatchClosed !== true || current.stale !== false
      || !validNavigationTarget(snapshot.target, { ...snapshot })
      || !(['projectEpoch', 'analysisGeneration', 'conversationId', 'conversationRevision',
        'configRevision', 'consentRevision'] as const).every(key => snapshot[key] === current[key])) return null;
  return snapshot.target;
}

export interface SourceNavigationIntent {
  kind: 'source';
  astAnchor: Anchor;
  openAnchor: Anchor | null;
  monacoRange: null;
  checkedAt: string;
  reason: 'whole_line' | 'unverified_source' | 'dirty_source';
}

/** Re-evaluate after opening a document and immediately before using the intent.
 * The closed C6 target has no source digest; neither its Monaco range nor an
 * editor revision proves byte identity. Offset selection therefore stays off.
 */
export function sourceNavigationIntent(snapshot: NavigationPoll, current: NavigationContext,
  document: SourceDocument | null = null): SourceNavigationIntent | null {
  const target = currentTarget(snapshot, current);
  if (!target || target.action !== 'source') return null;
  if (document && (document.projectPath !== current.projectPath
      || document.filePath !== target.astAnchor.filePath)) return null;
  const dirty = document !== null && (document.dirty || document.saving
    || document.text !== document.baselineText || document.error !== null);
  return {
    kind: 'source', astAnchor: { ...target.astAnchor },
    openAnchor: dirty ? null : { ...target.astAnchor, startColumn: null, endColumn: null },
    monacoRange: null, checkedAt: target.checkedAt,
    reason: dirty ? 'dirty_source' : target.astAnchor.startColumn === null ? 'whole_line' : 'unverified_source',
  };
}

export type GraphNavigationIntent = {
  kind: 'graph'; reveal: NonNullable<AssistantNavigationTarget['reveal']>; checkedAt: string;
} | { kind: 'source-only'; reason: 'source_only'; checkedAt: string };

/** Return an explicit local reveal only; never infer graph identity from source. */
export function graphNavigationIntent(snapshot: NavigationPoll,
  current: NavigationContext): GraphNavigationIntent | null {
  const target = currentTarget(snapshot, current);
  if (!target) return null;
  if (target.action === 'source') {
    return { kind: 'source-only', reason: 'source_only', checkedAt: target.checkedAt };
  }
  const reveal = target.reveal;
  const external = (id: string) => /^(?:[a-z][a-z0-9+.-]*:\/\/|(?:https?|javascript|data|file|mailto|vbscript):|\/\/)/i.test(id.trim());
  if (!reveal || external(reveal.selectedId) || (reveal.rootId !== null && external(reveal.rootId))) return null;
  return { kind: 'graph', reveal: { ...reveal }, checkedAt: target.checkedAt };
}
