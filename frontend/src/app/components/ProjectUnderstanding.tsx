import { useState, useSyncExternalStore } from 'react';
import { useAui } from '@assistant-ui/react';
import type { AssistantStore } from '../assistant/store';
import type { AssistantContext, AssistantSettings } from '../assistant/types';
import { understandingEvidence, type EffectiveUnderstanding } from '../assistant/project-understanding';
import AssistantEvidence from './AssistantEvidence';
import AssistantCoverageStatus from './AssistantCoverageStatus';
import { sendBlockReason, assistantJobReleased } from '../assistant/readiness';
import { assistantIssue } from '../assistant/ui';
import type { Translate } from '../i18n';

const sectionKeys = { summary: 'aiSummary', entry: 'aiEntry', steps: 'aiSteps', branches: 'aiBranches',
  relations: 'aiRelations', evidence: 'aiEvidence', unknowns: 'aiUnknowns' } as const;

/** One explicit send through the shared controlled runtime; mounting never sends. */
export function ProjectAnalysisControls({ store, settings, blocked, dirty, prepare, configure, authorize, t }:
  { store: AssistantStore; settings: AssistantSettings | null; blocked: boolean; dirty?: boolean;
    prepare: (context: AssistantContext) => void; configure: () => void; authorize: () => void; t: Translate }) {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const aui = useAui();
  const [issue, setIssue] = useState<unknown>(null);
  const reason = sendBlockReason(snapshot, settings, blocked);
  const jobs = snapshot.jobs.filter(job => job.purpose === 'project_understanding');
  const active = snapshot.projectPending || jobs.some(job => !assistantJobReleased(job));
  const jobIssue = [...jobs].reverse().find(job => job.issue)?.issue;
  return <section className="project-analysis-controls" aria-label={t('aiAnalyzeProject')}>
    <div className="assistant-actions"><button disabled={reason !== null} onClick={() => {
      setIssue(null); prepare({ kind: 'project' }); aui.thread.append(t('aiProjectAnalysisPrompt'));
    }}>{t(snapshot.state?.projectUnderstanding ? 'aiUpdateArchitecture' : 'aiAnalyzeProject')}</button>
      {active && <button onClick={() => { void store.stop().catch(setIssue); }}>{t('aiStop')}</button>}
      {(reason === 'aiSendKey' || reason === 'aiSendEnable' || reason === 'aiUnavailable') && <button onClick={configure}>{t('aiSettings')}</button>}
      {reason === 'aiSendConsent' && <button onClick={authorize}>{t('aiAuthorize')}</button>}
      {snapshot.projectPending && snapshot.startRecoveryRequired && <button onClick={() => { void store.recoverStart().catch(setIssue); }}>{t('aiRecover')}</button>}
    </div>
    {active && <p role="status">{t('aiProjectRunning')}</p>}
    {dirty && <p className="inline-warning">{t('aiDirty')}</p>}
    {reason && !active && <p className="muted">{t(reason)}</p>}
    {(issue ?? jobIssue ?? snapshot.issue) && <p role="alert">{assistantIssue(issue ?? jobIssue ?? snapshot.issue, t)}</p>}
  </section>;
}

export function ProjectUnderstandingSummary({ model, t, navigate, disabled, onAsk }: { model: EffectiveUnderstanding; t: Translate;
  navigate?: (itemId: string, evidenceId: string, action: 'source' | 'graph') => void; disabled?: boolean; onAsk?: (itemId: string) => void }) {
  const coverage = model.reference.coverage;
  return <section className="project-understanding" aria-label={t('aiProjectUnderstanding')}>
    <div className="card-heading">{t('aiProjectUnderstanding')} · v{model.reference.revision}</div>
    {onAsk && <button disabled={disabled} onClick={() => onAsk('project')}>{t('aiAskProject')}</button>}
    <p className="muted">{t('aiProjectCoverage')}: {coverage.indexedFileCount} / {coverage.authorizedFileCount} / {coverage.metadataFileCount} / {coverage.sourceFileCount}</p>
    <p className="muted">{t('aiProjectCoverageNote')}</p>
    {model.answer.sections.filter(section => section.claims.length > 0).map(section => <section key={section.kind}>
      <h3>{t(sectionKeys[section.kind])}</h3>{section.claims.map((claim, index) => {
        const itemId = `${section.kind}:${index}`;
        const evidence = understandingEvidence(model, itemId);
        return <div key={index}><p>{claim.text}<small className="muted"> · {t(claim.basis === 'unknown' ? 'aiUnknown' : 'aiInferred')}</small></p>
          {onAsk && <button disabled={disabled} onClick={() => onAsk(itemId)}>{t('aiAskNode')}</button>}
          {navigate && evidence.length > 0 && <details><summary>{t('aiRelatedEvidence')} · {evidence.length}</summary>
            <AssistantEvidence answer={{ ...model.answer, evidence }} disabled={disabled ?? false}
              navigate={(id, action) => navigate(itemId, id, action)} t={t}/></details>}
        </div>;
      })}
    </section>)}
    {model.answer.relationFacts.length > 0 && <details className="project-understanding-relations">
      <summary>{t('aiVerifiedRelations')} · {model.answer.relationFacts.length}</summary>
      {model.answer.relationFacts.slice(0, 100).map(fact => <div key={fact.evidenceId}>
        <p>{fact.type} · {fact.confidence}<br/><small>{fact.sourceId} → {fact.targetId}</small></p>
        {navigate && <AssistantEvidence answer={{ ...model.answer, evidence: model.answer.evidence.filter(card => card.evidenceId === fact.evidenceId) }}
          disabled={disabled ?? false} navigate={(id, action) => navigate('project', id, action)} t={t}/>}
      </div>)}
      {model.answer.relationFacts.length > 100 && <p>{t('aiGraphOmitted')}: {model.answer.relationFacts.length - 100}</p>}
    </details>}
    <AssistantCoverageStatus answer={model.answer} gapCodes={coverage.gapCodes} t={t}/>
  </section>;
}

/** The explanation supplements the original static graph; it cannot create nodes or edges. */
export function ProjectUnderstandingPanel({ view, ...props }: Parameters<typeof ProjectUnderstandingSummary>[0]
  & { view: 'ProjectStructure' | 'Architecture' }) {
  return <details open className="project-understanding-panel">
    <summary>{props.t(view === 'Architecture' ? 'architecture' : 'ProjectStructure')} · {props.t('aiExplanationPanel')} · v{props.model.reference.revision}</summary>
    <p className="project-understanding-base-note">{props.t(view === 'Architecture' ? 'aiStaticArchitectureBase' : 'aiStaticStructureBase')}</p>
    <ProjectUnderstandingSummary {...props}/>
  </details>;
}
