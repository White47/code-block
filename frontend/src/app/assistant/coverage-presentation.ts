const representativeGap = 'Project understanding used representative excerpts; full-project coverage is not established.';
const knownCodes = ['representative_sample', 'retrieval_gap', 'unverified'] as const;
type KnownCode = typeof knownCodes[number];
const isKnown = (code: string): code is KnownCode => knownCodes.some(known => known === code);

/** Presentation only: never changes the retained answer, evidence, scope or job state. */
export function coveragePresentation(answer: { readonly incomplete: boolean; readonly coverageGaps: readonly string[] },
  gapCodes?: readonly string[]) {
  const codes = gapCodes ?? [];
  const noSignals = !answer.incomplete && answer.coverageGaps.length === 0 && codes.length === 0;
  const sampled = answer.coverageGaps.length === 1 && answer.coverageGaps[0] === representativeGap
    && (codes.length === 0 || codes.length === 1 && codes[0] === 'representative_sample');
  const severity: 'none' | 'info' | 'warning' = noSignals ? 'none' : sampled ? 'info' : 'warning';
  return { severity, knownCodes: [...new Set(codes.filter(isKnown))],
    unknownCodes: [...new Set(codes.filter(code => !isKnown(code)))], details: answer.coverageGaps,
    unknownReason: severity === 'warning' && answer.coverageGaps.length === 0 };
}
