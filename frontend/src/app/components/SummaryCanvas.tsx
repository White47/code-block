import { useEffect, useMemo, useRef, useState } from 'react';
import { call, hasMethod } from '../../api/bridge';
import type { Anchor, SummaryContext, SummaryLayout, SummaryRelation, SummaryStep, ViewKind, ViewState } from '../../types';
import type { Translate } from '../i18n';
import GraphEditor from './GraphEditor';
import { buildSummaryGraph, summaryRelationLabel } from './summaryGraphAdapter';
import { summaryStepText, summaryTitle } from './summaryText';
import { summaryContextKey, summaryRelationKey } from '../assistant/human-understanding-overlay';
import { AiAnnotations, type UnderstandingAiActions } from './HumanUnderstandingAi';

export interface SummaryActions extends UnderstandingAiActions {
  names: ReadonlyMap<string, string>; projectPath: string;
  openSource: (anchor: Anchor) => void; openView: (view: ViewKind, root?: string) => void; t: Translate;
}
interface Props extends SummaryActions {
  context: SummaryContext; title: string; steps: SummaryStep[]; relations: SummaryRelation[];
  incomplete: boolean; omittedCount: number;
}

/** Local summary selection never enters the native CodeGraph selection API. */
export default function SummaryCanvas(props: Props) {
  const identity = JSON.stringify([props.projectPath, props.context, props.steps, props.relations]);
  return <CanvasSession key={identity} {...props}/>;
}

function CanvasSession({ context, title: rawTitle, steps: rawSteps, relations, incomplete, omittedCount, names, projectPath, openSource, openView, t, model, enhancement, navigate, onAsk, disabled }: Props) {
  const { kind, id } = context;
  const steps = useMemo(() => rawSteps.map(step => summaryStepText(step, t)), [rawSteps, t]);
  const title = summaryTitle(rawTitle, t, rawSteps.some(step => step.title === rawTitle && step.basis === 'source_author_statement'));
  const [selected, setSelected] = useState<string | null>(null);
  const [layout, setLayout] = useState<SummaryLayout | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(false);
  const host = useRef<HTMLElement>(null);
  const [columns, setColumns] = useState<1 | 2 | 3>(2);
  const writes = useRef<Promise<unknown>>(Promise.resolve());
  const active = useRef(true);
  const generation = useRef(0);
  const rendering = useMemo(() => buildSummaryGraph(steps, relations, names, t, columns), [steps, relations, names, t, columns]);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const update = (width: number) => { if (width > 0) setColumns(width < 600 ? 1 : width < 1000 ? 2 : 3); };
    update(element.getBoundingClientRect().width);
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(entries => { if (entries[0]) update(entries[0].contentRect.width); });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let cancelled = false;
    generation.current++;
    active.current = true;
    setReady(false);
    void (async () => {
      try {
        if (await hasMethod('get_summary_layout')) {
          const result = await call('get_summary_layout', { kind, id });
          if (!cancelled) { setLayout(result); setError(false); }
        }
      } catch { if (!cancelled) setError(true); }
      finally { if (!cancelled) setReady(true); }
    })();
    return () => { cancelled = true; active.current = false; };
  }, [kind, id, rawSteps, relations]);
  const current = steps.find(step => step.id === selected);
  const annotations = model && enhancement?.artifactId === model.reference.artifactId && enhancement.revision === model.reference.revision
    ? enhancement.byContext[summaryContextKey(context)] : null;
  const viewState: ViewState | null = layout ? {
    activeView: 'Overview', selectedId: null, openTabs: [], graphRoots: {}, expandedIds: [],
    positions: {}, positionsByView: { Overview: layout.positions }, zoom: layout.viewport.zoom,
    pan: layout.viewport.pan, viewportByView: { Overview: layout.viewport }, detailIslands: [], filters: [],
  } : null;
  function save(action: () => Promise<SummaryLayout>) {
    const version = generation.current;
    writes.current = writes.current.catch(() => undefined).then(async () => {
      if (!active.current || version !== generation.current) return;
      try {
        const saved = await action();
        if (active.current && version === generation.current) setLayout(saved);
      } catch { if (active.current && version === generation.current) setError(true); }
    });
  }
  function implementation(id: string) {
    const owner = steps.find(step => step.id === id)?.ownerSymbolIds[0];
    if (owner) openView('FunctionFlow', owner);
  }
  return <section ref={host} className="summary-workspace" aria-label={title}>
    <p className="overview-subtitle">{t('summaryNote')}</p>
    {(incomplete || omittedCount > 0 || rendering.rejectedCount > 0) && <p role="status" className="summary-notice">{t('summaryPartial')} · {t('hidden')}: {omittedCount + rendering.rejectedCount}</p>}
    {error && <p role="status">{t('summaryLayoutError')}</p>}
    {ready && rendering.graph.nodes.length > 0 ? <GraphEditor graph={rendering.graph} viewState={viewState} details={{}} selectedId={selected}
      title={title} initialFit="width" panOnScroll edgePresentation={rendering.edgePresentation}
      cameraContext={JSON.stringify([projectPath, context, columns])} restoreViewport={layout?.viewportSaved ?? false}
      onSelect={node => setSelected(node.id)} onDetail={node => implementation(node.id)}
      onMove={(id, position) => { if (layout) save(() => call('move_summary_node', context, layout.layoutToken, id, position)); }}
      onViewport={(_, viewport) => { if (layout) save(() => call('save_summary_viewport', context, layout.layoutToken, { zoom: viewport.zoom, pan: [viewport.x, viewport.y] })); }}
      onCloseIsland={() => undefined} t={t}/> : <p>{t(ready ? 'summaryEmpty' : 'waiting')}</p>}
    {current && <article className="summary-selection" aria-label={t('summaryDetails')}>
      <h3>{current.title}</h3><p>{t(current.confidence === 'Exact' ? 'exact' : current.confidence === 'Probable' ? 'probable' : 'unresolved')} · {t('humanSourceLocations')}: {current.sourceLocationCount}</p>
      <p>{t(current.basis === 'source_author_statement' ? 'humanAuthorStatement' : 'summaryStaticEvidence')}</p>
      <div className="responsibility-actions">{current.sourceAnchors.map((anchor, index) => <button key={index}
        aria-label={`${t('openSource')} ${anchor.filePath}:${anchor.startLine}`} onClick={() => openSource(anchor)}>{t('openSource')} {anchor.filePath}:{anchor.startLine}</button>)}
      {current.ownerSymbolIds.map(id => <button key={id} onClick={() => openView('FunctionFlow', id)}>{t('summaryImplementation')} {names.get(id) ?? current.sourceAnchors[0]?.filePath}</button>)}</div>
      {model && annotations?.nodeAnnotations[current.id] && <AiAnnotations notes={annotations.nodeAnnotations[current.id]} model={model} navigate={navigate} onAsk={onAsk} disabled={disabled} t={t}/>}
      <details><summary>{t('summaryRelations')}</summary><ul>{relations.filter(edge => edge.source === current.id || edge.target === current.id).map((edge, index) => <li key={index}>
        {steps.find(step => step.id === edge.source)?.title} → {steps.find(step => step.id === edge.target)?.title} · {summaryRelationLabel(edge, t)} · {edge.confidence}
        {model && annotations?.relationAnnotations[summaryRelationKey(edge)] && <AiAnnotations notes={annotations.relationAnnotations[summaryRelationKey(edge)]} model={model} navigate={navigate} onAsk={onAsk} disabled={disabled} t={t}/>}
      </li>)}</ul></details>
    </article>}
    {(annotations?.omittedCount ?? 0) > 0 && <p className="summary-notice">{t('summaryAiOmitted')}: {annotations?.omittedCount}</p>}
  </section>;
}
