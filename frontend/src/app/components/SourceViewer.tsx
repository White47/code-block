import { useEffect, useRef, useState } from 'react';
import { call } from '../../api/bridge';
import type { Anchor, Source } from '../../types';
import type { Translate } from '../i18n';
export default function SourceViewer({ anchor, t }: { anchor: Anchor; t: Translate }) {
  const [source, setSource] = useState<Source | null>(null); const [error, setError] = useState(''); const selected = useRef<HTMLTableRowElement>(null);
  useEffect(() => { let live = true; setSource(null); setError(''); call('read_source', anchor).then(value => { if (live) setSource(value); }).catch(e => { if (live) setError(e.message); }); return () => { live = false; }; }, [anchor]);
  useEffect(() => { selected.current?.scrollIntoView?.({ block: 'center' }); }, [source]);
  return <section className="source-view"><div className="source-heading"><IconDot/> <span title={anchor.filePath}>{anchor.filePath}</span><small>L{anchor.startLine}–{anchor.endLine}</small></div>{error ? <div className="inline-error" role="alert">{error}</div> : source ? <div className="source-scroll"><table className="code-table"><tbody>{source.text.split(/\r?\n/).map((line, index) => {
    const number = index + 1; const highlighted = number >= anchor.startLine && number <= anchor.endLine;
    return <tr key={number} ref={number === anchor.startLine ? selected : undefined} className={highlighted ? 'highlighted' : ''}><td className="line-number">{number}</td><td><pre>{line || ' '}</pre></td></tr>;
  })}</tbody></table></div> : <p className="muted padded">{t('waiting')}</p>}</section>;
}
function IconDot() { return <span className="source-dot"/>; }
