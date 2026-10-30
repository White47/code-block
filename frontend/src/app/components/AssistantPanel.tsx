import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { useAui } from '@assistant-ui/react';
import type { Translate } from '../i18n';
import type { AssistantStore } from '../assistant/store';
import type { AssistantContext, AssistantQuestionDraft, AssistantSettings, NavigationPoll } from '../assistant/types';
import { captureAssistantContext } from '../assistant/context';
import { assistantClient } from '../assistant/client';
import { assistantIssue } from '../assistant/ui';
import { assistantJobReleased, sendBlockReason } from '../assistant/readiness';
import AssistantConsent from './AssistantConsent';
import AssistantEvidence from './AssistantEvidence';
import AssistantCoverageStatus from './AssistantCoverageStatus';
import AssistantUsage, { AssistantInputCaveat } from './AssistantUsage';
import { effectiveProjectUnderstanding, understandingContextAvailable } from '../assistant/project-understanding';

type Props = { store: AssistantStore; settings: AssistantSettings | null;
  context: AssistantContext; blocked: boolean; dirty: boolean; onSettings: () => void;
  onNavigate: (navigation: NavigationPoll) => void; t: Translate;
  prepareContext: (context: AssistantContext) => void; questionDraft?: AssistantQuestionDraft | null };
const sectionKeys = { summary: 'aiSummary', entry: 'aiEntry', steps: 'aiSteps', branches: 'aiBranches',
  relations: 'aiRelations', evidence: 'aiEvidence', unknowns: 'aiUnknowns' } as const;

export default function AssistantPanel(props: Props) {
  return <Panel {...props}/>;
}
function Panel({ store, settings, context, blocked, dirty, onSettings, onNavigate, t,
  prepareContext, questionDraft }: Props) {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const aui = useAui();
  const [question, setQuestion] = useState(''); const [lastIntent, setLastIntent] = useState<{ text: string; context: AssistantContext } | null>(null);
  const [issue, setIssue] = useState<unknown>(null); const [consent, setConsent] = useState(false);
  const authorizeButton = useRef<HTMLButtonElement>(null);
  const handledNavigation = useRef<string | null>(null);
  const questionInput = useRef<HTMLTextAreaElement>(null);
  const state = snapshot.state;
  const chat = state?.conversations.find(value => value.conversationId === snapshot.selectedConversationId);
  const jobIssue = [...snapshot.jobs].reverse().find(job => job.kind !== 'connection'
    && job.projectEpoch === state?.projectEpoch && job.analysisGeneration === state?.analysisGeneration
    && job.configRevision === state?.configRevision && job.consentRevision === state?.grant?.consentRevision
    && job.conversationId === chat?.conversationId
    && job.conversationRevision === chat?.conversationRevision)?.issue;
  const currentIssue = issue ?? snapshot.issue ?? jobIssue;
  const scope = JSON.stringify([state?.projectEpoch, state?.analysisGeneration, state?.configRevision]);
  const active = snapshot.sending || snapshot.jobs.some(job => !assistantJobReleased(job));
  const sendReason = sendBlockReason(snapshot, settings, blocked);
  const contextAvailable = understandingContextAvailable(context, state);
  const canSend = sendReason === null && contextAvailable;
  const architecture = effectiveProjectUnderstanding(state);
  const contextKey = JSON.stringify(context);
  const draftKey = questionDraft && JSON.stringify(questionDraft.context) === contextKey ? questionDraft.key : null;
  const draftText = draftKey === null ? '' : questionDraft?.text ?? '';
  useEffect(() => { setConsent(false); setQuestion(''); setLastIntent(null); setIssue(null); }, [scope, contextKey, chat?.conversationId, chat?.conversationRevision]);
  useEffect(() => { if (draftKey !== null) { setQuestion(draftText); questionInput.current?.focus(); } }, [draftKey, draftText]);
  useEffect(() => { setIssue(null); }, [state?.grant?.consentRevision]);
  useEffect(() => {
    const navigation = snapshot.navigation;
    if (navigation?.target && handledNavigation.current !== navigation.jobId) {
      handledNavigation.current = navigation.jobId; onNavigate(navigation);
    }
  }, [snapshot.navigation, onNavigate]);
  const perform = (operation: () => Promise<unknown>) => { setIssue(null); void operation().catch(setIssue); };
  const send = (text: string, capturedContext: AssistantContext = context) => {
    if (sendReason !== null || !understandingContextAvailable(capturedContext, state) || !text.trim()) return;
    const frozenContext = captureAssistantContext(capturedContext);
    setIssue(null); setLastIntent({ text, context: frozenContext }); setQuestion('');
    // The controlled assistant-ui callback is the sole explicit model-send entry.
    prepareContext(frozenContext);
    aui.thread.append(text);
  };
  let origin = settings?.baseUrl ?? '';
  try { origin = new URL(origin).origin; } catch { /* Inert text; native configuration validates URLs. */ }
  return <section className="assistant-panel" aria-label={t('aiAssistant')}>
    <div className="assistant-actions"><button onClick={onSettings}>{t('aiSettings')}</button>
      <button disabled={blocked || !state} onClick={() => perform(() => store.newConversation())}>{t('aiNew')}</button>
      <button disabled={blocked || !chat} onClick={() => perform(() => store.clearConversation())}>{t('aiClear')}</button></div>
    <p className="muted">{t('aiStatic')}</p>
    <AssistantInputCaveat t={t}/>
    <p className="assistant-context">{context.kind === 'understanding' ? t(context.itemId === 'project' ? 'aiUnderstandingProjectContext' : 'aiUnderstandingItemContext') : context.kind === 'project' ? t('aiProjectUnderstanding') : context.kind === 'question' ? t('aiFreeContext') : context.kind === 'node'
      ? `${t('aiNodeContext')}: ${context.visibleNodeId}` : `${t(context.mode === 'file' ? 'aiFileContext' : 'aiFunctionContext')}: ${context.filePath}`}</p>
    {dirty && <p className="inline-warning" role="status">{t('aiDirty')}</p>}
    {blocked && <p className="inline-warning" role="status">{t('aiStale')}</p>}
    {sendReason && <p className="inline-warning" role="status">{t(sendReason)}</p>}
    {!contextAvailable && <p className="inline-warning" role="status">{t('aiUnderstandingExpired')}</p>}
    <label>{t('aiConversation')}<select value={chat?.conversationId ?? ''} disabled={blocked || !state} onChange={e => perform(() => store.selectConversation(e.target.value))}>
      {!chat && <option value="">—</option>}{state?.conversations.map((value, index) => <option value={value.conversationId} key={value.conversationId}>{index + 1} · {value.conversationId}</option>)}
    </select></label>
    <div className="assistant-actions"><button ref={authorizeButton} disabled={blocked || !state?.analysisGeneration || !settings?.enabled} onClick={() => setConsent(true)}>{t('aiAuthorize')}</button>
      {state?.grant?.enabled && <button onClick={() => perform(async () => { await assistantClient.revokeConsent({ projectEpoch: state.projectEpoch }); await store.restore(state.projectEpoch); })}>{t('aiRevoke')}</button>}</div>
    {state?.grant && <details className="assistant-scope"><summary>{state.grant.origin} · {state.grant.allowedFiles.length} .py</summary>{state.grant.allowedFiles.map(file => <code key={file}>{file}</code>)}</details>}
    {consent && !blocked && <AssistantConsent key={scope} store={store} origin={origin} close={() => { setConsent(false); authorizeButton.current?.focus(); }} t={t}/>}
    <div className="assistant-actions"><button type="button" disabled={sendReason !== null} onClick={() => {
      send(t('aiProjectAnalysisPrompt'), { kind: 'project' });
    }}>{t(architecture ? 'aiUpdateArchitecture' : 'aiAnalyzeProject')}</button>
    </div>
    <div className="assistant-messages" aria-live="polite" aria-relevant="additions text">
      {chat?.messages.map((message, index) => <article key={`${message.jobId}:${index}`} className={`assistant-message ${message.type}`}>
        {message.stale && <p className="inline-warning">{t('aiStale')}</p>}
        {message.type === 'validated_answer' ? <>
          <p className="muted">{t('aiChecked')}: <time>{message.answer.checkedAt}</time></p>
          <AssistantCoverageStatus answer={message.answer} t={t}/>
          {message.answer.sections.map(section => <section key={section.kind}><h3>{t(sectionKeys[section.kind])}</h3>{section.claims.map((claim, claimIndex) => <p key={claimIndex}>{claim.text}<small className="muted"> · {t(claim.basis === 'unknown' ? 'aiUnknown' : 'probable')}</small></p>)}</section>)}
          {message.answer.relationFacts.map(fact => <p className="assistant-relation" key={fact.evidenceId}>{fact.category} · {fact.type} · {fact.sourceId} → {fact.targetId} · {fact.confidence}</p>)}
          <AssistantEvidence answer={message.answer} disabled={blocked || message.stale || !state?.grant?.enabled || active}
            navigate={(id, action) => perform(() => store.resolveEvidence(id, action))} t={t}/>
        </> : <>{message.type === 'provisional' && <small>{t('aiProvisional')}</small>}<p className="assistant-plain">{message.text}</p></>}
      </article>)}
    </div>
    {snapshot.usage && <AssistantUsage usage={snapshot.usage} t={t}/>}
    {snapshot.jobs.filter(job => job.cancelRequested).map(job => <p role="status" key={job.jobId}>{t(job.cancelAccepted && job.resourcesReleased ? 'aiReleased' : 'aiCleanup')}</p>)}
    {currentIssue && <p role="alert">{assistantIssue(currentIssue, t)}</p>}
    {snapshot.startRecoveryRequired && <div role="alert"><p>{t('aiRecovery')}</p><button disabled={blocked || active} onClick={() => perform(() => store.recoverStart())}>{t('aiRecover')}</button></div>}
    <form className="assistant-composer" onSubmit={e => { e.preventDefault(); send(question); }}>
      <label htmlFor="assistant-question">{t('aiQuestion')}</label><textarea ref={questionInput} id="assistant-question" value={question} maxLength={2000} rows={3} onChange={e => setQuestion(e.target.value)}/>
      <div className="assistant-actions"><button className="primary" disabled={!canSend || !question.trim()}>{t('aiSend')}</button>
        <button type="button" disabled={!active && !snapshot.startRecoveryRequired} onClick={() => perform(() => store.stop())}>{t('aiStop')}</button>
        {lastIntent && currentIssue && !snapshot.startRecoveryRequired && <button type="button" disabled={sendReason !== null || !understandingContextAvailable(lastIntent.context, state)} onClick={() => send(lastIntent.text, lastIntent.context)}>{t('aiRetry')}</button>}</div>
    </form>
  </section>;
}
