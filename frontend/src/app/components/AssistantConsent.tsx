import { useEffect, useId, useState } from 'react';
import { assistantClient } from '../assistant/client';
import type { AssistantStore } from '../assistant/store';
import { assistantIssue } from '../assistant/ui';
import type { Translate } from '../i18n';

export default function AssistantConsent({ store, origin, close, t }: {
  store: AssistantStore; origin: string; close: () => void; t: Translate;
}) {
  const id = useId();
  const initial = store.getSnapshot().state;
  const identityOf = (state: typeof initial) => JSON.stringify([state?.projectEpoch, state?.analysisGeneration,
    state?.configRevision, state?.grant?.consentRevision]);
  const identity = identityOf(initial);
  const [scope, setScope] = useState(initial?.grant?.allowedFiles.join('\n') ?? '');
  const [confirmed, setConfirmed] = useState(false); const [saving, setSaving] = useState(false);
  const [issue, setIssue] = useState<unknown>(null);
  const candidatesAvailable = Boolean(window.pywebview?.api?.assistant_get_consent_candidates);
  const [loading, setLoading] = useState(candidatesAvailable);
  useEffect(() => {
    if (!candidatesAvailable || !initial || initial.analysisGeneration === null) { setLoading(false); return; }
    let current = true;
    void assistantClient.getConsentCandidates({ projectEpoch: initial.projectEpoch, analysisGeneration: initial.analysisGeneration })
      .then(result => {
        if (current && identityOf(store.getSnapshot().state) === identity) {
          setScope(result.files.join('\n')); setConfirmed(false);
        }
      }).catch(error => { if (current) setIssue(error); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [identity, store, candidatesAvailable]);
  const files = scope.split(/\r?\n/).map(file => file.trim()).filter(Boolean);
  async function grant() {
    const state = store.getSnapshot().state;
    if (!state || state.analysisGeneration === null || !confirmed || saving || loading
      || identityOf(state) !== identity) return;
    setSaving(true);
    try {
      await assistantClient.grantConsent({ projectEpoch: state.projectEpoch, analysisGeneration: state.analysisGeneration,
        configRevision: state.configRevision, allowedFiles: files, confirmed: true });
      // A response from an unmounted/obsolete consent form owns no current workspace.
      if (identityOf(store.getSnapshot().state) !== identity) return;
      await store.restore(state.projectEpoch); close();
    } catch (error) { setIssue(error); }
    finally { setSaving(false); }
  }
  return <section className="assistant-consent" role="dialog" aria-modal="false" aria-labelledby={`${id}-title`} onKeyDown={e => { if (e.key === 'Escape' && !saving) { e.stopPropagation(); close(); } }}>
    <h2 id={`${id}-title`}>{t('aiAuthorize')}</h2><code>{origin}</code><p>{t('aiDisclosure')}</p>
    <p>{loading ? t('aiCandidatesLoading') : `${t('aiProjectScope')}: ${files.length} .py`}</p>
    <label htmlFor={`${id}-scope`}>{t('aiScope')}</label><textarea id={`${id}-scope`} autoFocus rows={5} disabled={saving || loading} value={scope} onChange={e => { setScope(e.target.value); setConfirmed(false); }}/>
    <label htmlFor={`${id}-confirm`}><input id={`${id}-confirm`} type="checkbox" disabled={saving || loading} checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/>{t('aiConfirmScope')}</label>
    {issue !== null && <p role="alert">{assistantIssue(issue, t)}</p>}
    <div className="assistant-actions"><button disabled={!confirmed || saving || loading || files.length === 0} onClick={() => { void grant(); }}>{t('aiGrant')}</button><button disabled={saving} onClick={close}>{t('cancelSource')}</button></div>
  </section>;
}
