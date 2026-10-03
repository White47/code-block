import type { Anchor, Problem } from '../../types';
import type { Translate } from '../i18n';
export default function Problems({ problems, onSource, t }: { problems: Problem[]; onSource: (a: Anchor) => void; t: Translate }) {
  return <div className="problems-list">{problems.length ? problems.map((p, i) => <button className="problem-row" key={i} disabled={!p.canOpenSource || !p.filePath || !p.line} onClick={() => p.filePath && p.line && onSource({ filePath: p.filePath, startLine: p.line, endLine: p.line, startColumn: null, endColumn: null })}><span className="problem-dot">!</span><code>{p.code}</code><span>{p.message}</span><small>{p.filePath}{p.line ? `:${p.line}` : ''}</small></button>) : <p className="muted padded">{t('noProblems')}</p>}</div>;
}
