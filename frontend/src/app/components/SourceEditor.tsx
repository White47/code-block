import { useEffect, useRef, useState } from 'react';
import type { editor } from 'monaco-editor';
import type { Anchor } from '../../types';
import type { SourceDocument } from '../sourceDocuments';
import { getSourceModel, retainSourceModel } from '../sourceModels';

export default function SourceEditor({ document, anchor, onChange, onSave, locked = false }: {
  document: SourceDocument; anchor: Anchor; onChange: (text: string) => void; onSave: () => void; locked?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const instance = useRef<editor.IStandaloneCodeEditor | null>(null);
  const latest = useRef({ document, onChange, onSave, locked }); latest.current = { document, onChange, onSave, locked };
  const [ready, setReady] = useState(false);
  const [failure, setFailure] = useState('');
  useEffect(() => {
    // A basic accessible editor remains available if browser layout APIs are absent.
    if (!window.matchMedia || !globalThis.ResizeObserver) return;
    let live = true; let cleanup: (() => void) | undefined;
    void import('../monaco').then(({ monaco, sourceLanguage }) => {
      if (!live || !host.current) return;
      const current = latest.current.document;
      let model = getSourceModel(current.key);
      if (!model || model.isDisposed()) {
        model = monaco.editor.createModel(current.text, sourceLanguage(current.filePath), monaco.Uri.parse(`codeviz-source://document/${encodeURIComponent(current.key)}/${encodeURIComponent(current.filePath)}`));
        retainSourceModel(current.key, model);
      }
      const view = monaco.editor.create(host.current, { model, theme: 'codeviz-source', automaticLayout: true,
        readOnly: latest.current.locked || Boolean(current.readOnlyReason), fontFamily: 'Cascadia Code, Consolas, monospace', fontSize: 12,
        minimap: { enabled: false }, lineNumbers: 'on', folding: true, scrollBeyondLastLine: false,
        ariaLabel: current.filePath, padding: { top: 12 }, wordWrap: 'off' });
      instance.current = view;
      const changed = model.onDidChangeContent(() => latest.current.onChange(model!.getValue()));
      view.addAction({ id: 'codeviz.save-source', label: 'Save source', keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS], run: () => latest.current.onSave() });
      setReady(true);
      cleanup = () => { changed.dispose(); view.dispose(); instance.current = null; };
    }).catch(() => { if (live) setFailure('Editor could not load. Basic source editing is available.'); });
    return () => { live = false; cleanup?.(); };
  }, [document.key]);
  useEffect(() => {
    const model = instance.current?.getModel();
    if (model && model.getValue() !== document.text) {
      model.pushStackElement();
      model.pushEditOperations([], [{ range: model.getFullModelRange(), text: document.text }], () => null);
      model.pushStackElement();
    }
    instance.current?.updateOptions({ readOnly: locked || Boolean(document.readOnlyReason) });
  }, [document.text, document.readOnlyReason, locked, ready]);
  useEffect(() => {
    const view = instance.current; if (!view) return;
    const model = view.getModel(); if (!model) return;
    const start = Math.min(anchor.startLine, model.getLineCount()); const end = Math.min(anchor.endLine, model.getLineCount());
    const decorations = view.createDecorationsCollection([{ range: { startLineNumber: start, startColumn: 1, endLineNumber: end, endColumn: model.getLineMaxColumn(end) }, options: { isWholeLine: true, className: 'source-anchor-line', linesDecorationsClassName: 'source-anchor-gutter' } }]);
    view.revealLineInCenter(start); view.setPosition({ lineNumber: start, column: anchor.startColumn ?? 1 });
    return () => { decorations.clear(); };
  }, [anchor, ready]);
  return <><div className="monaco-source-host" ref={host} style={{ display: ready ? undefined : 'none' }}/>{!ready && <textarea className="source-basic-editor" aria-label={document.filePath} spellCheck={false} readOnly={locked || Boolean(document.readOnlyReason)} value={document.text} onChange={event => onChange(event.target.value)}/>} {failure && <small role="status">{failure}</small>}</>;
}
