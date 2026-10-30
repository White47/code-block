import { coveragePresentation } from '../assistant/coverage-presentation';
import type { Translate } from '../i18n';

const causeKeys = { representative_sample: 'aiGapRepresentative', retrieval_gap: 'aiGapRetrieval',
  unverified: 'aiGapUnverified' } as const;

export default function AssistantCoverageStatus({ answer, gapCodes, t }: {
  answer: Parameters<typeof coveragePresentation>[0]; gapCodes?: readonly string[]; t: Translate;
}) {
  const status = coveragePresentation(answer, gapCodes);
  if (status.severity === 'none') return null;
  return <section className="assistant-coverage-status" data-severity={status.severity}>
    <p className={status.severity === 'warning' ? 'inline-warning' : 'muted'}>
      {t(status.severity === 'warning' ? 'aiIncomplete' : 'aiCoverageSampled')}
    </p>
    {status.severity === 'warning' && <p>{t('aiCoverageLimited')}</p>}
    {status.knownCodes.length + status.unknownCodes.length > 0 && <ul>
      {status.knownCodes.map(code => <li key={code}>{t(causeKeys[code])}</li>)}
      {status.unknownCodes.map(code => <li key={code}>{t('aiGapUnspecified')} · <code>{code}</code></li>)}
    </ul>}
    {status.unknownReason && <p>{t('aiGapUnspecified')}</p>}
    {status.details.length > 0 && <details><summary>{t('aiCoverageDetails')} · {status.details.length}</summary>
      <ul>{status.details.map((gap, index) => <li key={index}>{gap}</li>)}</ul>
    </details>}
  </section>;
}
