import type { Graph } from '../../types';
import type { Translate } from '../i18n';
export default function QueryToolbar({ mode, depth, canExpand, depthLimit, budgetTruncated, direction, hops, setDirection, more, fewer, disabled, t, inspectorVisible, toggleInspector }: {
  mode: NonNullable<Graph['queryContext']['controlMode']>; depth: number; canExpand?: boolean; depthLimit?: boolean; budgetTruncated?: boolean;
  direction: 'in' | 'out' | 'both'; hops: number; setDirection: (value: 'in' | 'out' | 'both') => void;
  more: () => void; fewer: () => void; disabled: boolean; t: Translate; inspectorVisible: boolean; toggleInspector: () => void;
}) {
  return <div className="query-toolbar">
    {mode === 'structure' && <><span>{t('structure')} · {depth}/5</span>
      <button disabled={disabled || depth <= 1} onClick={fewer}>{t('fewerLayers')} · {depth}/5</button>
      <button disabled={disabled || depth >= 5 || depthLimit === true || canExpand === false} title={depth >= 5 || depthLimit ? t('depthMaximum') : budgetTruncated ? t('graphTruncated') : canExpand === false ? t('noDeeper') : undefined} onClick={more}>{t('moreLayers')} · {depth}/5</button></>}
    {mode === 'relations' && <><span>{t('relations')}</span>{(['in', 'out', 'both'] as const).map(value => <button key={value} disabled={disabled} className={direction === value ? 'active' : ''} onClick={() => setDirection(value)}>{value === 'in' ? t('upstream') : value === 'out' ? t('downstream') : t('both')}</button>)}
      <button disabled={disabled || hops <= 1} onClick={fewer}>{t('fewerHops')} · {hops}/5</button><button disabled={disabled || hops >= 5} onClick={more}>{t('moreHops')} · {hops}/5</button></>}
    <button className="inspector-toggle" onClick={toggleInspector} aria-pressed={inspectorVisible}>{t('inspector')} {inspectorVisible ? '›' : '‹'}</button>
  </div>;
}
