import type { AssistantAnswer } from '../assistant/types';
import type { Translate } from '../i18n';

export default function AssistantEvidence({ answer, disabled, navigate, t }: {
  answer: AssistantAnswer; disabled: boolean; navigate: (id: string, action: 'source' | 'graph') => void; t: Translate;
}) {
  return <div className="assistant-evidence"><p className="muted">{t('aiWholeLine')}</p>
    {answer.evidence.some(card => card.graphAvailable) && <p className="muted">{t('aiGraphCandidate')}</p>}
    {answer.evidence.map(card => <div className="anchor-card" key={card.evidenceId}>
    <strong>{card.label}</strong><span className={`confidence ${card.confidence.toLowerCase()}`}>{card.confidence}</span>
    {card.truncated && <small>{t('aiExcerptTruncated')}</small>}{!card.graphAvailable && <p className="muted">{t('aiSourceOnly')}</p>}
    <div><button disabled={disabled || !card.sourceAvailable} onClick={() => navigate(card.evidenceId, 'source')}>{t('aiOpenSource')}</button>
      <button disabled={disabled || !card.graphAvailable} onClick={() => navigate(card.evidenceId, 'graph')}>{t('aiReveal')}</button></div>
  </div>)}</div>;
}
