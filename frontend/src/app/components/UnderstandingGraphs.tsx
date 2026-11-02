import { useMemo, useState } from 'react';
import type { Anchor, Overview, ViewKind } from '../../types';
import type { Translate } from '../i18n';
import FunctionalArchitecture from './FunctionalArchitecture';
import TaskFlow from './TaskFlow';
import { summaryTitle } from './summaryText';
import { projectHumanUnderstandingEnhancement } from '../assistant/human-understanding-overlay';
import type { UnderstandingAiActions } from './HumanUnderstandingAi';
import { ProjectUnderstandingSummary } from './ProjectUnderstanding';

export default function UnderstandingGraphs({ data, openSource, openView, t, model = null, navigate, onAsk, disabled }: {
  data: Overview; openSource: (anchor: Anchor) => void; openView: (view: ViewKind, root?: string) => void; t: Translate;
} & UnderstandingAiActions) {
  const understanding = data.understanding;
  const [mode, setMode] = useState<'architecture' | 'flow'>('flow');
  const [flowId, setFlowId] = useState<string | null>(null);
  const flows = understanding?.flows;
  const flow = flows?.find(item => item.id === flowId)
    ?? flows?.find(item => item.id === understanding?.defaultFlowId) ?? flows?.[0];
  const names = useMemo(() => new Map([
    ...(data.understanding?.responsibilities.map(card => [card.ownerSymbolId, card.codeName] as const) ?? []),
    ...[data.models, data.entries, data.functions ?? [], data.modules ?? [], data.classes ?? []].flat().map(item => [item.id, item.name] as const),
  ]), [data]);
  const architecture = understanding?.architecture;
  const enhancement = useMemo(() => projectHumanUnderstandingEnhancement(understanding, model), [understanding, model]);
  if (!architecture && !flow && !model) return null;
  const actions = { names, projectPath: data.projectPath, openSource, openView, t, model, enhancement, navigate, onAsk, disabled };
  const showArchitecture = !!architecture && (mode === 'architecture' || !flow);
  return <section className="understanding-graphs">
    <div className="summary-switch" role="group" aria-label={t('summaryArchitecture')}>
      {architecture && <button aria-pressed={showArchitecture} onClick={() => setMode('architecture')}>{t('summaryArchitecture')}</button>}
      {flow && <button aria-pressed={!showArchitecture} onClick={() => setMode('flow')}>{t('summaryFlow')}</button>}
      {!showArchitecture && flows && <label>{t('summaryEntry')} <select value={flow?.id} onChange={event => setFlowId(event.target.value)}>
        {flows.map(item => <option key={item.id} value={item.id}>{summaryTitle(item.title, t, item.steps.some(step => step.title === item.title && step.basis === 'source_author_statement'))} · {names.get(item.entrySymbolId) ?? item.steps[0]?.sourceAnchors[0]?.filePath}</option>)}
      </select></label>}
    </div>
    {(understanding?.omittedFlowCount ?? 0) > 0 && <p role="status">{t('summaryPartial')} {t('hidden')}: {understanding?.omittedFlowCount}</p>}
    {showArchitecture ? <FunctionalArchitecture architecture={architecture} {...actions}/> : flow && <TaskFlow flow={flow} {...actions}/>}
    {model && enhancement && <details className="summary-ai-explanation">
      <summary>{t('summaryAiExplanation')} · v{enhancement.revision}</summary>
      <p className="muted">{t('summaryAiProjectNotes')}: {enhancement.projectAnnotations.length} · {t('summaryAiOmitted')}: {enhancement.omittedProjectCount}</p>
      <ProjectUnderstandingSummary model={model} navigate={navigate} onAsk={onAsk} disabled={disabled} t={t}/>
    </details>}
  </section>;
}
