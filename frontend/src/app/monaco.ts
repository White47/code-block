import * as monaco from 'monaco-editor/editor/editor.api.js';
// Monaco 0.57's exports map covers JS only; import the pinned local font CSS directly.
import '../../node_modules/monaco-editor/esm/vs/base/browser/ui/codicons/codicon/codicon.css';
import 'monaco-editor/editor/standalone/browser/standaloneEditor.js';
import 'monaco-editor/editor/contrib/find/browser/findController.js';
import 'monaco-editor/editor/contrib/folding/browser/folding.js';
import 'monaco-editor/editor/contrib/wordOperations/browser/wordOperations.js';
import 'monaco-editor/editor/contrib/clipboard/browser/clipboard.js';
import 'monaco-editor/editor/contrib/unicodeHighlighter/browser/unicodeHighlighter.js';
import 'monaco-editor/languages/definitions/python/register.js';
import workerUrl from 'monaco-editor/editor/editor.worker.js?worker&url';

globalThis.MonacoEnvironment = { getWorker: () => new Worker(workerUrl, { type: 'module', name: 'codeviz-editor' }) };
export { monaco };
