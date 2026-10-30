import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { call, hasMethod } from '../../api/bridge';
import type { Anchor, Source } from '../../types';
import type { Translate } from '../i18n';
import { SourceDocumentStore, sourceDocumentKey } from '../sourceDocuments';
import SourceEditor from './SourceEditor';
export default function SourceViewer({ anchor, t, projectPath, store: sharedStore, onSave, onExplain, outdated = false, locked = false }: {
  anchor: Anchor; t: Translate; projectPath?: string; store?: SourceDocumentStore;
  onExplain?: (mode: 'function' | 'file') => void; onSave?: (key: string) => Promise<unknown>; outdated?: boolean; locked?: boolean;
}) {
  const [ownStore] = useState(() => new SourceDocumentStore((path, value) => call('open_source_document', path, value), request => call('save_source_document', request)));
  const store = sharedStore ?? ownStore;
  const key = sourceDocumentKey(projectPath ?? '', anchor.filePath);
  const document = useSyncExternalStore(store.subscribe, () => store.get(key));
  const [legacy, setLegacy] = useState(!projectPath);
  const [openError, setOpenError] = useState('');
  useEffect(() => {
    let live = true; setOpenError('');
    if (!projectPath) { setLegacy(true); return; }
    void hasMethod('open_source_document').then(async available => {
      if (!live) return;
      setLegacy(!available);
      if (available) await store.open(projectPath, anchor);
    }).catch(error => { if (live) setOpenError(error.message); });
    return () => { live = false; };
  }, [projectPath, anchor, store]);
  const save = () => { if (!locked) void (onSave ? onSave(key) : store.save(key)).catch(() => {}); };
  if (legacy) return <LegacySourceViewer anchor={anchor} t={t}/>;
  return <section className="source-view"><div className="source-heading"><IconDot/><span title={anchor.filePath}>{anchor.filePath}{document?.dirty ? ' ●' : ''}</span><small>L{anchor.startLine}–{anchor.endLine}</small><button title="Ctrl+S" disabled={locked || !document?.dirty || document.saving || Boolean(document.readOnlyReason)} onClick={save}>{document?.saving ? t('savingSource') : t('saveSource')}</button>{onExplain && <><button disabled={locked || !document} onClick={() => onExplain('function')}>{t('aiExplainFunction')}</button><button disabled={locked || !document} onClick={() => onExplain('file')}>{t('aiExplainFile')}</button></>}</div>
    {openError && <div className="inline-error" role="alert">{openError}</div>}
    {document?.error && <div className="inline-error" role="alert">{document.error.message}</div>}
    {document?.readOnlyReason && <div className="source-notice" role="status">{document.readOnlyReason}</div>}
    {(document?.dirty || outdated) && <div className="source-notice" role="status">{t('sourceStale')}</div>}
    {document ? <SourceEditor document={document} anchor={anchor} locked={locked} onChange={text => { if (!locked) store.update(key, text); }} onSave={save}/> : !openError && <p className="muted padded">{t('waiting')}</p>}
  </section>;
}
function LegacySourceViewer({ anchor, t }: { anchor: Anchor; t: Translate }) {
  const [source, setSource] = useState<Source | null>(null); const [error, setError] = useState(''); const selected = useRef<HTMLTableRowElement>(null);
  useEffect(() => { let live = true; setSource(null); setError(''); call('read_source', anchor).then(value => { if (live) setSource(value); }).catch(e => { if (live) setError(e.message); }); return () => { live = false; }; }, [anchor]);
  useEffect(() => { selected.current?.scrollIntoView?.({ block: 'center' }); }, [source]);
  return <section className="source-view"><div className="source-heading"><IconDot/> <span title={anchor.filePath}>{anchor.filePath}</span><small>L{anchor.startLine}–{anchor.endLine}</small></div>{error ? <div className="inline-error" role="alert">{error}</div> : source ? <div className="source-scroll"><table className="code-table"><tbody>{source.text.split(/\r?\n/).map((line, index) => {
    const number = index + 1; const highlighted = number >= anchor.startLine && number <= anchor.endLine;
    return <tr key={number} ref={number === anchor.startLine ? selected : undefined} className={highlighted ? 'highlighted' : ''}><td className="line-number">{number}</td><td><pre>{line || ' '}</pre></td></tr>;
  })}</tbody></table></div> : <p className="muted padded">{t('waiting')}</p>}</section>;
}
function IconDot() { return <span className="source-dot"/>; }
