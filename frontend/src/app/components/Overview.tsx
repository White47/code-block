import type { Overview as OverviewData, OverviewChoice, ViewKind } from '../../types';
import type { ReactNode } from 'react';
import type { Translate } from '../i18n';

export default function Overview({ data, openView, t, children }: { data: OverviewData | null; openView: (v: ViewKind, root?: string) => void; t: Translate; children?: ReactNode }) {
  const choices = (items: OverviewChoice[], kind: ViewKind) => items.map(item =>
    <button className="entity-row" key={`${item.viewKind ?? kind}:${item.id}`} onClick={() => openView(item.viewKind ?? kind, item.id)} title={item.evidence?.join('\n')}>
      <span className="entity-icon">{kind === 'Architecture' ? 'M' : 'ƒ'}</span>
      <span>{item.name}<small>{item.sourceAnchor.filePath}:{item.sourceAnchor.startLine}</small>
        {item.confidence && <small>{item.confidence} · {item.viewKind === 'FunctionFlow' ? t('structuralEntry') : ''}</small>}
      </span><span>↗</span>
    </button>);
  return <section className="overview-page">
    <div className="eyebrow">CODE INTELLIGENCE / 01</div><h1>{data?.projectName ?? 'CodeViz'}</h1>
    <p className="overview-subtitle">{data ? [data.language, data.framework, data.projectPath].filter(Boolean).join(' · ') : t('empty')}</p>
    {children}
    {data && <>
      <div className="metrics">{[[t('files'), `${data.health.filesParsed}/${data.health.filesTotal}`], [t('symbols'), data.health.symbolsResolved], [t('exact'), data.health.exact], [t('problems'), data.health.issueCount]].map(([label, value]) => <article key={label}><small>{label}</small><strong>{value}</strong><div className="metric-rule"/></article>)}</div>
      <div className="overview-columns">
        <article className="overview-card"><div className="card-heading"><span className="signal"/>{t('models')}<small>{data.models.length}</small></div>{choices(data.models, 'Architecture')}{!data.models.length && <p className="padded">{t('noModels')}</p>}</article>
        <article className="overview-card"><div className="card-heading"><span className="signal amber"/>{t('entries')}<small>{data.entries.length}</small></div>{choices(data.entries, 'Execution')}{!data.entries.length && <p>{t('noEntries')}</p>}</article>
      </div>
      <p>{t('staticFlow')}</p>
      <article className="overview-card"><div className="card-heading">{t('ProjectStructure')}</div><p className="padded">{t('projectStructureNote')}</p><button className="entity-row" onClick={() => openView('ProjectStructure')}>{t('ProjectStructure')} ↗</button></article>
      <div className="overview-columns">
        <article className="overview-card"><div className="card-heading">{t('functions')}<small>{data.functions?.length ?? 0}</small></div>{choices(data.functions ?? [], 'FunctionFlow')}</article>
        <article className="overview-card"><div className="card-heading">{t('modules')}</div>{choices(data.modules ?? [], 'FunctionFlow')}<div className="card-heading">{t('classes')}</div>{choices(data.classes ?? [], 'FunctionFlow')}</article>
      </div>
      <div className="overview-card coverage"><div className="card-heading">{t('confidence')}</div><div className="coverage-bar">{[[data.health.exact, 'exact'], [data.health.probable, 'probable'], [data.health.unresolved, 'unresolved']].map(([value, name]) => <span key={name} className={String(name)} style={{ flex: Number(value) }}/>)}</div><p>{t('exact')} {data.health.exact} · {t('probable')} {data.health.probable} · {t('unresolved')} {data.health.unresolved}</p><div className="area-tags">{data.mainAreas.map(area => <span key={area}>{area}</span>)}</div></div>
    </>}
  </section>;
}
