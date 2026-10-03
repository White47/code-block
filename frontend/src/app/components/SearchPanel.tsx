import { useEffect, useState } from 'react';
import { call } from '../../api/bridge';
import type { SearchResponse, SearchResult } from '../../types';
import type { Translate } from '../i18n';
export default function SearchPanel({ enabled, onReveal, onSource, onError, t }: { enabled: boolean; onReveal: (result: SearchResult) => void; onSource: (result: SearchResult) => void; onError: (error: unknown) => void; t: Translate }) {
  const [query, setQuery] = useState(''); const [response, setResponse] = useState<SearchResponse | null>(null); const [pending, setPending] = useState(false);
  useEffect(() => {
    let live = true;
    if (!enabled || !query.trim()) { setResponse(null); setPending(false); return; }
    setPending(true);
    const timer = setTimeout(() => { call('search', query, 100).then(value => { if (live) setResponse(value); }).catch(error => { if (live) { setResponse(null); onError(error); } }).finally(() => { if (live) setPending(false); }); }, 250);
    return () => { live = false; clearTimeout(timer); };
  }, [query, enabled, onError]);
  return <><div className="panel-heading">{t('search')}</div><div className="search-input"><input aria-label={t('search')} placeholder={t('query')} value={query} disabled={!enabled} onChange={e => setQuery(e.target.value)} /></div><div className="section-label" aria-live="polite">{pending ? '…' : response ? `${response.totalCount} ${t('searchCount')}${response.truncated ? ' · 100+' : ''}` : t('query')}</div>{response?.results.map(result => <article className="search-result" key={`${result.sourceKind}:${result.id}`}><strong>{result.name}</strong><small>{result.qualifiedName}</small><small>{result.sourceAnchor.filePath}:{result.sourceAnchor.startLine} · {result.confidence}</small><div><button onClick={() => onReveal(result)}>{t('reveal')}</button><button onClick={() => onSource(result)}>{t('source')}</button></div></article>)}{response && !response.results.length && <p className="muted padded">{t('noResults')}</p>}</>;
}
