import type { Anchor, HumanOperationKind, HumanUnderstanding, ViewKind } from '../../types';
import type { Translate } from '../i18n';

const operationLabels = {
  return_value: 'humanReturnValue', return_empty: 'humanReturnEmpty', standard_output: 'humanStandardOutput',
  count: 'humanCount', sum: 'humanSum', conversion: 'humanConversion',
} as const satisfies Record<HumanOperationKind, string>;

export default function ResponsibilityOverview({ data, openSource, openView, t }: {
  data: HumanUnderstanding | null | undefined;
  openSource: (anchor: Anchor) => void;
  openView: (view: ViewKind, root?: string) => void;
  t: Translate;
}) {
  const hasGaps = !!data && (data.omittedResponsibilityCount > 0 || data.omittedFactCount > 0 || data.discardedFactCount > 0);
  return <section className="responsibility-overview" aria-label={t('humanResponsibilities')}>
    <h2>{t('humanResponsibilities')}</h2>
    <p className="overview-subtitle">{t('humanResponsibilitiesNote')}</p>
    {!data?.responsibilities.length ? <p className="overview-card padded">{t('humanNoResponsibilities')}</p> :
      <div className="responsibility-grid">{data.responsibilities.map(card => <article className="overview-card responsibility-card" key={card.ownerSymbolId}>
        <p className="responsibility-text">{card.responsibility ?? t('humanResponsibilityUnknown')}</p>
        <code className="responsibility-code">{card.codeName}</code>
        {card.basis === 'source_author_statement' && <small className="responsibility-basis">{t('humanAuthorStatement')}{card.authorTruncated && <span> · {t('humanExcerpt')}</span>}</small>}
        <div className="responsibility-actions">
          <button onClick={() => openSource(card.sourceAnchor)} aria-label={`${t('openSource')} ${card.codeName}`}>{t('openSource')}</button>
          <button onClick={() => openView('FunctionFlow', card.ownerSymbolId)} aria-label={`${t('FunctionFlow')} ${card.codeName}`}>{t('FunctionFlow')}</button>
        </div>
        {card.operations.length > 0 && <details className="responsibility-operations"><summary>{t('humanStaticOperations')}</summary>
          <ul>{card.operations.map(operation => <li key={operation.kind}>{t(operationLabels[operation.kind])}<small>{t('humanSourceLocations')}: {operation.sourceLocationCount}</small></li>)}</ul>
        </details>}
      </article>)}</div>}
    {hasGaps && <details className="responsibility-coverage"><summary>{t('humanCoverage')}</summary><ul>
      {data.omittedResponsibilityCount > 0 && <li>{t('humanMoreResponsibilities')}: {data.omittedResponsibilityCount}</li>}
      {data.omittedFactCount > 0 && <li>{t('humanOmittedFacts')}: {data.omittedFactCount}</li>}
      {data.discardedFactCount > 0 && <li>{t('humanDiscardedFacts')}: {data.discardedFactCount}</li>}
    </ul></details>}
  </section>;
}
