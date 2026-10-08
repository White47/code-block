import type { Tab } from '../state';
import type { Translate } from '../i18n';
import { Icon } from './ActivityBar';
export function viewLabel(view: string, t: Translate) { return view === 'Overview' ? t('overview') : view === 'Architecture' ? t('architecture') : view === 'Execution' ? t('execution') : view === 'ProjectStructure' || view === 'FunctionFlow' || view === 'Calls' || view === 'DataFlow' || view === 'Gradient' || view === 'Symbol' ? t(view) : view; }
export default function EditorTabs({ tabs, active, onOpen, onClose, t, isDirty }: { tabs: Tab[]; active: string | null; onOpen: (tab: Tab) => void; onClose: (id: string) => void; t: Translate; isDirty?: (tab: Tab) => boolean }) {
  return <div className="tabs" role="tablist">{tabs.map(tab => <div className={`tab ${active === tab.id ? 'active' : ''}`} key={tab.id}><button role="tab" aria-selected={active === tab.id} onClick={() => onOpen(tab)}><Icon kind={tab.kind === 'source' ? 'source' : tab.kind === 'settings' ? 'settings' : tab.kind === 'Overview' ? 'overview' : 'graph'} /><span>{tab.kind === 'source' ? tab.anchor?.filePath.split(/[\\/]/).pop() : tab.kind === 'settings' ? t('settings') : viewLabel(tab.view ?? tab.id, t)}{isDirty?.(tab) ? ' ●' : ''}</span></button><button className="close-tab" onClick={() => onClose(tab.id)} aria-label={`${t('close')} ${tab.id}`} title="Ctrl+W">×</button></div>)}</div>;
}
