import { useEffect, useId, useState, useSyncExternalStore } from 'react';
import type { Translate } from '../i18n';
import type { AssistantStore } from '../assistant/store';
import type { AssistantSettings as Settings } from '../assistant/types';
import { assistantClient } from '../assistant/client';
import { assistantIssue } from '../assistant/ui';

export default function AssistantSettings({ store, onChanged, t }: { store: AssistantStore; onChanged: (settings: Settings) => void; t: Translate }) {
  const id = useId();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [baseUrl, setBaseUrl] = useState(''); const [model, setModel] = useState('');
  const [key, setKey] = useState(''); const [enabled, setEnabled] = useState(false);
  const [saving, setSaving] = useState(false); const [issue, setIssue] = useState<unknown>(null);
  const [saveUnconfirmed, setSaveUnconfirmed] = useState(false);
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot);
  useEffect(() => {
    let live = true;
    void assistantClient.getSettings().then(value => {
      if (!live) return;
      setSettings(value); setBaseUrl(value.baseUrl); setModel(value.model); setEnabled(value.enabled);
    }).catch(error => { if (live) setIssue(error); });
    return () => { live = false; };
  }, []);
  async function save(clear = false) {
    if (saving || !settings) return;
    const credential = clear ? '' : key || null;
    setKey(''); setSaving(true); setIssue(null); setSaveUnconfirmed(true);
    try {
      const value = await assistantClient.configure({ baseUrl: clear ? settings.baseUrl : baseUrl,
        model: clear ? settings.model : model, key: credential, enabled: clear ? false : enabled });
      setSettings(value); setBaseUrl(value.baseUrl); setModel(value.model); setEnabled(value.enabled);
      setSaveUnconfirmed(false); onChanged(value);
    } catch (error) { setIssue(error); }
    finally { setSaving(false); }
  }
  const pending = snapshot.jobs.some(job => !job.resourcesReleased);
  const dirty = !!settings && (saveUnconfirmed || baseUrl !== settings.baseUrl
    || model !== settings.model || enabled !== settings.enabled || key !== '');
  const canTest = !!settings?.available && settings.keySet && settings.enabled && !saving && !pending && !dirty;
  const connection = !dirty && snapshot.connection?.configRevision === settings?.configRevision
    ? snapshot.connection : null;
  const capabilities = connection?.status === 'Completed' ? connection.connectionResult : null;
  return <section className="settings-card assistant-settings" aria-label={t('aiSettings')}>
    <h2>{t('aiSettings')}</h2><p className="muted">{t('aiStatic')}</p>
    <label htmlFor={`${id}-enabled`}><input id={`${id}-enabled`} type="checkbox" checked={enabled} disabled={saving || !settings} onChange={e => setEnabled(e.target.checked)}/>{t('aiEnabled')}</label>
    <label htmlFor={`${id}-url`}>{t('aiEndpoint')}</label><input id={`${id}-url`} type="url" autoComplete="off" value={baseUrl} onChange={e => setBaseUrl(e.target.value)} disabled={saving || !settings}/>
    <label htmlFor={`${id}-model`}>{t('aiModel')}</label><input id={`${id}-model`} autoComplete="off" value={model} onChange={e => setModel(e.target.value)} disabled={saving || !settings}/>
    <label htmlFor={`${id}-key`}>{t('aiKey')}</label><input id={`${id}-key`} type="password" autoComplete="new-password" spellCheck={false} value={key} onChange={e => setKey(e.target.value)} disabled={saving || !settings}/>
    <p>{t(settings?.keySet ? 'aiKeySet' : 'aiKeyUnset')}</p>
    {dirty && <p role="status" className="inline-warning">{t('aiSettingsPending')}</p>}
    {settings && !settings.available && <p className="inline-warning">{t('aiUnavailable')}</p>}
    {(issue || settings?.issue) && <p role="alert">{assistantIssue(issue ?? settings?.issue, t)}</p>}
    <div className="assistant-actions"><button disabled={!settings || saving} onClick={() => { void save(); }}>{t('aiSaveSettings')}</button>
      <button disabled={!settings || saving} onClick={() => { void save(true); }}>{t('aiClearKey')}</button>
      <button disabled={!canTest} onClick={() => { if (canTest && settings) void store.testConnection(settings.configRevision).catch(setIssue); }}>{t('aiTest')}</button></div>
    {connection && <p role="status">{connection.status}{connection.issue && ` · ${assistantIssue(connection.issue, t)}`}</p>}
    {capabilities && <div className="muted">
      <p>{t('aiConnectionTools')}</p>
      <p>{t(capabilities.structuredOutputSupported ? 'aiConnectionRemoteSchema' : 'aiConnectionLocalSchema')}</p>
    </div>}
    {snapshot.jobs.some(job => job.cancelRequested && !job.resourcesReleased) && <p role="status">{t('aiCleanup')}</p>}
  </section>;
}
