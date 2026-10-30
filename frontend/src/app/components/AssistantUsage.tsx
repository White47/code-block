import type { AssistantUsage as Usage } from '../assistant/types';
import type { Translate } from '../i18n';

export function AssistantInputCaveat({ t }: { t: Translate }) {
  return <p className="muted">{t('aiInputCaveat')}</p>;
}

export default function AssistantUsage({ usage, t }: { usage: Usage; t: Translate }) {
  const accounting = usage.inputAccounting;
  const basis = usage.observed.basis;
  const observed = <><dt>{t('aiObserved')}</dt><dd>{usage.observed.inputTokens ?? '—'} / {usage.observed.outputTokens ?? '—'} tokens · {t(basis === 'actual' ? 'aiActual' : basis === 'estimated' ? 'aiEstimated' : 'aiUnknown')}</dd></>;
  const resources = <dl className="assistant-usage">
    <dt>{t('aiReservedSourceCharacters')}</dt><dd>{usage.reserved.sourceCharacters}</dd>
    <dt>{t('aiReservedFiles')}</dt><dd>{usage.reserved.fileCount}</dd>
    <dt>{t('aiToolAttempts')}</dt><dd>{usage.reserved.toolCalls}</dd>
  </dl>;
  if (!accounting) return <><dl className="assistant-usage"><dt>{t('aiReserved')}</dt>
    <dd>{usage.reserved.inputTokens} / {usage.reserved.outputTokens} tokens · {usage.reserved.modelRequests} requests</dd>{observed}</dl>{resources}</>;
  const reason = accounting.blockedReason;
  return <>
    <dl className="assistant-usage">
      <dt>{t('aiInputEstimate')}</dt><dd>{usage.reserved.inputTokens} / {accounting.limitTokens} tokens</dd>
      <dt>{t('aiOutputReserved')}</dt><dd>{usage.reserved.outputTokens}{accounting.limitTokens === 100000 ? ' / 8192' : ''} tokens · {usage.reserved.modelRequests} requests</dd>
      {observed}
      {basis === 'unknown' && <><dt>{t('aiPartialActual')}</dt><dd>{accounting.knownActualInputTokens} tokens</dd></>}
    </dl>
    {resources}
    {reason !== null && <p className="inline-warning" role="status">{t(reason === 'estimated_limit'
      ? 'aiEstimateLimit' : reason === 'observed_overrun' ? 'aiObservedOverrun' : 'aiUsageUnknown')}</p>}
  </>;
}
