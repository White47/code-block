import { expect, it } from 'vitest';
import { validAnswer } from './answer';

it('accepts 2000 Unicode characters in a validated answer claim', () => {
  const sections = ['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns']
    .map(kind => ({ kind, claims: [] as unknown[] }));
  sections[0].claims.push({ text: '😀'.repeat(2000), basis: 'inference',
    evidenceIds: [], relationEvidenceId: null });
  expect(validAnswer({ sections, evidence: [], relationFacts: [], incomplete: false,
    coverageGaps: [], checkedAt: '2026-10-18T00:00:00Z' })).toBe(true);
});

it('rejects an answer claim that names evidence missing from the published cards', () => {
  const sections = ['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns']
    .map(kind => ({ kind, claims: [] as unknown[] }));
  sections[0].claims.push({ text: 'The function returns a value.', basis: 'inference',
    evidenceIds: ['missing-evidence'], relationEvidenceId: null });
  expect(validAnswer({ sections, evidence: [], relationFacts: [], incomplete: false,
    coverageGaps: [], checkedAt: '2026-10-16T00:00:00Z' })).toBe(false);
});

it('retains a source-backed uncertain call fact with repeated possible targets', () => {
  const sections = ['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns']
    .map(kind => ({ kind, claims: [] as unknown[] }));
  sections[4].claims.push({ text: 'This may call the helper.', basis: 'inference',
    evidenceIds: ['edge-1'], relationEvidenceId: 'edge-1' });
  expect(validAnswer({ sections, evidence: [{
    evidenceId: 'edge-1', label: 'app.py:10-12', confidence: 'Probable',
    truncated: false, sourceAvailable: true, graphAvailable: true,
    unavailableReason: 'navigation_check_required',
  }], relationFacts: [{
    evidenceId: 'edge-1', sourceId: 'caller', targetId: 'callee', type: 'CALLS',
    confidence: 'Probable', candidateTargets: ['callee', 'callee'], category: 'call',
  }], incomplete: false, coverageGaps: [], checkedAt: '2026-10-16T00:00:00Z' })).toBe(true);
});

it.each(['2026-02-30T00:00:00Z', '0000-01-01T00:00:00Z'])(
  'rejects an impossible answer check date %s', checkedAt => {
  const sections = ['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns']
    .map(kind => ({ kind, claims: [] }));
  expect(validAnswer({ sections, evidence: [], relationFacts: [], incomplete: false,
    coverageGaps: [], checkedAt })).toBe(false);
  },
);

it('rejects control characters inside answer prose', () => {
  const sections = ['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns']
    .map(kind => ({ kind, claims: [] as unknown[] }));
  sections[0].claims.push({ text: 'line\u0000hidden', basis: 'inference',
    evidenceIds: [], relationEvidenceId: null });
  expect(validAnswer({ sections, evidence: [], relationFacts: [], incomplete: false,
    coverageGaps: [], checkedAt: '2026-10-16T00:00:00Z' })).toBe(false);
});

it('rejects unpaired surrogate code units in answer prose', () => {
  const sections = ['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns']
    .map(kind => ({ kind, claims: [] as unknown[] }));
  sections[0].claims.push({ text: 'broken\uD800', basis: 'inference',
    evidenceIds: [], relationEvidenceId: null });
  expect(validAnswer({ sections, evidence: [], relationFacts: [], incomplete: false,
    coverageGaps: [], checkedAt: '2026-10-16T00:00:00Z' })).toBe(false);
});
