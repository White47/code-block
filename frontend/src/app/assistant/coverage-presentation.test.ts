import { expect, it } from 'vitest';
import { coveragePresentation } from './coverage-presentation';

const sample = 'Project understanding used representative excerpts; full-project coverage is not established.';
const repair = 'Repair condensed the answer; coverage is incomplete.';
it.each([
  [true, [sample], undefined, 'info'],
  [true, [sample], [], 'info'],
  [true, [sample], ['representative_sample'], 'info'],
  [true, [], ['representative_sample'], 'warning'],
  [true, [sample], ['retrieval_gap'], 'warning'],
  [true, [sample], ['unverified'], 'warning'],
  [true, [sample], ['future_code'], 'warning'],
  [true, [sample, 'Unknown missing detail'], [], 'warning'],
  [true, [repair], undefined, 'warning'],
  [true, [sample, repair], ['representative_sample'], 'warning'],
  [true, [], undefined, 'warning'],
  [false, [], undefined, 'none'],
  [false, ['Unknown missing detail'], [], 'warning'],
  [true, [sample + ' '], [], 'warning'],
  [true, [sample.toLowerCase()], [], 'warning'],
  [true, [sample, sample], [], 'warning'],
] as const)('keeps original signals and classifies incomplete=%s gaps=%j codes=%j as %s', (incomplete, gaps, codes, expected) => {
  const answer = Object.freeze({ incomplete, coverageGaps: Object.freeze([...gaps]) });
  const metadata = codes === undefined ? undefined : Object.freeze([...codes]);
  expect(coveragePresentation(answer, metadata).severity).toBe(expected);
  expect(answer.coverageGaps).toEqual(gaps);
  expect(answer.incomplete).toBe(incomplete);
});
