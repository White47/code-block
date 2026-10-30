import type { AssistantAnswer } from './types';

// The desktop bridge is a transport, not authority for model output. Keep the
// published C6 answer schema closed before any text reaches a React renderer.
const sectionKinds = ['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns'] as const;
const confidences = new Set(['Exact', 'Probable', 'Unresolved']);
const categories: Record<string, string> = {
  CALLS: 'call',
  CONTROL_FLOW: 'control', BRANCH_TRUE: 'control', BRANCH_FALSE: 'control', LOOP_BACK: 'control',
  READS: 'data', WRITES: 'data', PASSES_TO: 'data', RETURNS_TO: 'data',
  DATA_FLOW: 'data', GRADIENT_FLOW: 'data',
  CONTAINS: 'structure', IMPORTS: 'structure', EXPORTS: 'structure',
  INHERITS: 'structure', IMPLEMENTS: 'structure',
};

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function exact(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return record(value) && Object.keys(value).length === keys.length
    && Object.keys(value).every(key => keys.includes(key));
}

export function wellFormed(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xD800 && unit <= 0xDBFF) {
      if (index + 1 >= value.length) return false;
      const next = value.charCodeAt(++index);
      if (next < 0xDC00 || next > 0xDFFF) return false;
    } else if (unit >= 0xDC00 && unit <= 0xDFFF) return false;
  }
  return true;
}

function text(value: unknown, max: number): value is string {
  return typeof value === 'string' && value.length > 0 && Array.from(value).length <= max
    && value.trim().length > 0 && wellFormed(value) && !/\p{Cc}/u.test(value);
}

function prose(value: unknown, max: number): value is string {
  return typeof value === 'string' && value.length > 0 && Array.from(value).length <= max
    && value.trim().length > 0 && wellFormed(value)
    && !/\p{Cc}/u.test(value.replace(/[\n\t]/g, ''));
}

function ids(value: unknown, max: number): value is string[] {
  return Array.isArray(value) && value.length <= max
    && value.every(id => text(id, 512)) && new Set(value).size === value.length;
}

function section(value: unknown, expected: string): value is Record<string, unknown> {
  if (!exact(value, ['kind', 'claims']) || value.kind !== expected
      || !Array.isArray(value.claims) || value.claims.length > 20) return false;
  return value.claims.every(claim => exact(claim, ['text', 'basis', 'evidenceIds', 'relationEvidenceId'])
    && prose(claim.text, 2000)
    && (claim.basis === 'inference' || claim.basis === 'unknown')
    && ids(claim.evidenceIds, 8)
    && (claim.relationEvidenceId === null
      || (text(claim.relationEvidenceId, 512)
        && claim.evidenceIds.includes(claim.relationEvidenceId))));
}

function card(value: unknown): boolean {
  return exact(value, ['evidenceId', 'label', 'confidence', 'truncated',
    'sourceAvailable', 'graphAvailable', 'unavailableReason'])
    && text(value.evidenceId, 512) && text(value.label, 64 * 1024)
    && typeof value.confidence === 'string' && confidences.has(value.confidence)
    && typeof value.truncated === 'boolean' && value.sourceAvailable === true
    && typeof value.graphAvailable === 'boolean'
    && (value.unavailableReason === 'source_only'
      || value.unavailableReason === 'navigation_check_required')
    && value.graphAvailable === (value.unavailableReason === 'navigation_check_required');
}

function fact(value: unknown): boolean {
  return exact(value, ['evidenceId', 'sourceId', 'targetId', 'type',
    'confidence', 'candidateTargets', 'category'])
    && text(value.evidenceId, 512) && text(value.sourceId, 512)
    && text(value.targetId, 512) && typeof value.type === 'string'
    && Object.hasOwn(categories, value.type)
    && typeof value.confidence === 'string' && confidences.has(value.confidence)
    && value.category === categories[value.type]
    && Array.isArray(value.candidateTargets) && value.candidateTargets.length <= 1000
    && value.candidateTargets.every(candidate => text(candidate, 512))
    && (value.candidateTargets.length === 0
      || (value.type === 'CALLS' && value.confidence !== 'Exact'));
}

export function utcTimestamp(value: unknown): value is string {
  if (typeof value !== 'string' || value.length > 64) return false;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|\+00:00)$/.exec(value);
  if (parts === null) return false;
  const [year, month, day, hour, minute, second] = parts.slice(1, 7).map(Number);
  if (year === 0) return false;
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  date.setUTCHours(hour, minute, second, 0);
  return date.getUTCFullYear() === year && date.getUTCMonth() + 1 === month
    && date.getUTCDate() === day && date.getUTCHours() === hour
    && date.getUTCMinutes() === minute && date.getUTCSeconds() === second;
}

export function validAnswer(value: unknown): value is AssistantAnswer {
  if (!exact(value, ['sections', 'evidence', 'relationFacts', 'incomplete',
    'coverageGaps', 'checkedAt'])
      || !Array.isArray(value.sections) || value.sections.length !== sectionKinds.length
      || !value.sections.every((entry, index) => section(entry, sectionKinds[index]))
      || value.sections.reduce((count, entry) => count + (entry as { claims: unknown[] }).claims.length, 0) > 60
      || !Array.isArray(value.evidence) || value.evidence.length > 480
      || !value.evidence.every(card)
      || !Array.isArray(value.relationFacts) || value.relationFacts.length > 480
      || !value.relationFacts.every(fact)
      || typeof value.incomplete !== 'boolean'
      || !Array.isArray(value.coverageGaps) || value.coverageGaps.length > 20
      || !value.coverageGaps.every(gap => text(gap, 200))
      || !utcTimestamp(value.checkedAt)) return false;
  const publishedIds = new Set(value.evidence.map(card => card.evidenceId));
  if (publishedIds.size !== value.evidence.length
      || !value.sections.every(entry => (entry as { claims: { evidenceIds: string[] }[] }).claims.every(
        claim => claim.evidenceIds.every(id => publishedIds.has(id))))
      || !value.relationFacts.every(fact => publishedIds.has(fact.evidenceId))) return false;
  return new TextEncoder().encode(JSON.stringify(value)).length <= 64 * 1024;
}
