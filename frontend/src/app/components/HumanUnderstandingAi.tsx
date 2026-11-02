import type { AiAnnotation, HumanUnderstandingEnhancement } from '../assistant/human-understanding-overlay';
import type { EffectiveUnderstanding } from '../assistant/project-understanding';
import type { Translate } from '../i18n';
import AssistantEvidence from './AssistantEvidence';

export interface UnderstandingAiActions {
  model?: EffectiveUnderstanding | null;
  enhancement?: HumanUnderstandingEnhancement | null;
  navigate?: (itemId: string, evidenceId: string, action: 'source' | 'graph') => void;
  onAsk?: (itemId: string) => void;
  disabled?: boolean;
}

/** Uses original claim/evidence IDs; display text is never a navigation target. */
export function AiAnnotations({ notes, model, navigate, onAsk, disabled, t }:
  UnderstandingAiActions & { notes: AiAnnotation[]; model: EffectiveUnderstanding; t: Translate }) {
  return <div className="summary-ai-notes">{notes.map(note => {
    const evidence = model.answer.evidence.filter(card => note.evidenceIds.includes(card.evidenceId));
    return <section key={note.itemId}>
      <p>{note.text}</p><small className="muted">{t(note.basis === 'unknown' ? 'aiUnknown' : 'aiInferred')} · v{model.reference.revision} · {t('aiRelatedEvidence')}: {evidence.length}</small>
      {onAsk && <button disabled={disabled} onClick={() => onAsk(note.itemId)}>{t('aiAskNode')}</button>}
      {navigate && evidence.length > 0 && <details><summary>{t('aiRelatedEvidence')} · {evidence.length}</summary>
        <AssistantEvidence answer={{ ...model.answer, evidence }} disabled={disabled ?? false}
          navigate={(id, action) => navigate(note.itemId, id, action)} t={t}/></details>}
    </section>;
  })}</div>;
}
