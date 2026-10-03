import type { Translate } from '../i18n';
export type Activity = 'overview' | 'search' | 'problems' | 'settings';
export function Icon({ kind }: { kind: string }) {
  const paths: Record<string, string> = { overview: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z', search: 'M20 20l-5-5 M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0', problems: 'M12 3L2 21h20L12 3z M12 9v5 M12 17v1', settings: 'M9 3h6l1 4 4 1v8l-4 1-1 4H9l-1-4-4-1V8l4-1 1-4z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0', graph: 'M9 3h6v5H9z M2 16h6v5H2z M16 16h6v5h-6z M12 8v4H5v4 M12 12h7v4', source: 'M8 5l-6 7 6 7 M16 5l6 7-6 7 M14 3l-4 18' };
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[kind] ?? paths.graph} /></svg>;
}
export default function ActivityBar({ activity, onChange, t }: { activity: Activity | null; onChange: (v: Activity) => void; t: Translate }) {
  return <nav className="activity" aria-label="Activity bar"><div className="brand-symbol">C<span>V</span></div>{(['overview', 'search', 'problems'] as const).map(v => <button key={v} className={activity === v ? 'active' : ''} onClick={() => onChange(v)} aria-label={t(v)} title={t(v)}><Icon kind={v} /></button>)}<div className="activity-spacer"/><button className={activity === 'settings' ? 'active' : ''} onClick={() => onChange('settings')} aria-label={t('settings')} title={t('settings')}><Icon kind="settings" /></button></nav>;
}
