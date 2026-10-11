import type { Session, ViewKind } from '../../types';
import { viewKinds } from '../../types';
import type { Translate } from '../i18n';
import { Icon } from './ActivityBar';
import { viewLabel } from './EditorTabs';
import type { ReactNode } from 'react';
export default function Sidebar({ session, active, openView, openPath, choose, analyze, busy, explorer, t }: { session: Session | null; active: string | null; openView: (v: ViewKind) => void; openPath: (path: string) => void; choose: () => void; analyze: () => void; busy: boolean; explorer?: ReactNode; t: Translate }) {
  return <><div className="panel-heading">CODEVIZ <span>EXPLORER</span></div><div className="sidebar-project"><small>{t('local')}</small><p title={session?.projectPath ?? ''}>{session?.projectPath ?? t('empty')}</p><button className="primary" disabled={busy} onClick={choose}>＋ {t('open')}</button>{session?.projectPath && <button disabled={busy} onClick={analyze}>{t('reanalyze')}</button>}</div>{explorer}<details className="sidebar-section" open><summary>{t('overview')}</summary>{viewKinds.map(v => <button className={`view-link ${active === v ? 'active' : ''}`} disabled={busy} key={v} onClick={() => openView(v)}><Icon kind={v === 'Overview' ? 'overview' : 'graph'} />{viewLabel(v, t)}<span>›</span></button>)}</details><details className="sidebar-section" open><summary>{t('recent')}</summary>{session?.recentProjects.map(path => <button className="recent-project" key={path} title={path} disabled={busy} onClick={() => openPath(path)}>{path.split(/[\\/]/).pop()}<small>{path}</small></button>)}</details></>;
}
