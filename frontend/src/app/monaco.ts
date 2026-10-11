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
import 'monaco-editor/languages/definitions/markdown/register.js';
import 'monaco-editor/languages/definitions/yaml/register.js';
import 'monaco-editor/languages/definitions/javascript/register.js';
import 'monaco-editor/languages/definitions/typescript/register.js';
import 'monaco-editor/languages/definitions/css/register.js';
import 'monaco-editor/languages/definitions/html/register.js';
import 'monaco-editor/languages/definitions/shell/register.js';
import 'monaco-editor/languages/definitions/xml/register.js';
import workerUrl from 'monaco-editor/editor/editor.worker.js?worker&url';

globalThis.MonacoEnvironment = { getWorker: () => new Worker(workerUrl, { type: 'module', name: 'codeviz-editor' }) };
// JSON highlighting needs no rich language worker. Keep all language resources
// local and leave validation/completion services to future integration.
monaco.languages.register({ id: 'json' });
monaco.languages.setMonarchTokensProvider('json', { tokenizer: { root: [
  [/"([^"\\]|\\.)*"(?=\s*:)/, 'string.key'], [/"([^"\\]|\\.)*"/, 'string'],
  [/-?\d+(\.\d+)?([eE][+-]?\d+)?/, 'number'], [/\b(true|false|null)\b/, 'keyword'], [/[{}[\],:]/, 'delimiter'],
] } });
monaco.editor.defineTheme('codeviz-source', { base: 'vs-dark', inherit: true, rules: [], colors: { 'editor.background': '#101820', 'editor.foreground': '#b8cbd9', 'editor.selectionBackground': '#20423a', 'editorCursor.foreground': '#4dddc3', 'editorLineNumber.foreground': '#4f667d', 'editorLineNumber.activeForeground': '#4dddc3' } });
export function sourceLanguage(filePath: string): string {
  const extension = filePath.split('.').at(-1)?.toLowerCase() ?? '';
  return ({ py: 'python', pyi: 'python', md: 'markdown', markdown: 'markdown', json: 'json', yaml: 'yaml', yml: 'yaml', js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript', ts: 'typescript', tsx: 'typescript', css: 'css', html: 'html', htm: 'html', xml: 'xml', svg: 'xml', sh: 'shell', bash: 'shell' } as Record<string, string>)[extension] ?? 'plaintext';
}
export { monaco };
