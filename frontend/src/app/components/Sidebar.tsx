import type { Session, ViewKind } from '../../types';
import { viewKinds } from '../../types';
import type { Translate } from '../i18n';
import { Icon } from './ActivityBar';
import { viewLabel } from './EditorTabs';
export default function Sidebar({ session, active, openView, openPath, choose, analyze, busy, t }: { session: Session | null; active: string | null; openView: (v: ViewKind) => void; openPath: (path: string) => void; choose: () => void; analyze: () => void; busy: boolean; t: Translate }) {
  return <><div className="panel-heading">CODEVIZ <span>EXPLORER</span></div><div className="sidebar-project"><small>{t('local')}</small><strong>{session?.projectPath?.split(/[\\/]/).filter(Boolean).pop() ?? 'Code workspace'}</strong><p title={session?.projectPath ?? ''}>{session?.projectPath ?? t('empty')}</p><button className="primary" disabled={busy} onClick={choose}>＋ {t('open')}</button>{session?.projectPath && <button disabled={busy} onClick={analyze}>{t('reanalyze')}</button>}</div><div className="section-label">{t('overview')}</div>{viewKinds.map(v => <button className={`view-link ${active === v ? 'active' : ''}`} key={v} onClick={() => openView(v)}><Icon kind={v === 'Overview' ? 'overview' : 'graph'} />{viewLabel(v, t)}<span>›</span></button>)}<div className="section-label">{t('recent')}</div>{session?.recentProjects.map(path => <button className="recent-project" key={path} title={path} disabled={busy} onClick={() => openPath(path)}>{path.split(/[\\/]/).pop()}<small>{path}</small></button>)}</>;
}
