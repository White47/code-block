import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync, realpathSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { preflightFixture, installRuntimeGuards } from './assistant-ref-consumer-guards.mjs';

// Consumer-only acceptance: no upstream ref implementation/source-map inspection.
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const report = { status: 'RED', assertions: 0, trueAssertions: [], pendingLayoutNative: true,
  fullGate: false, entries: {}, errors: [] };
let stage = 'arguments';
let dom;
let guard;
let restoreReact;
function check(name, condition) {
  assert.ok(condition, name);
  report.assertions++;
  report.trueAssertions.push(name);
}
const watchdog = setTimeout(() => {
  report.errors.push({ stage, message: '30s runtime bound exceeded' });
  process.stdout.write(`${JSON.stringify(report)}\n`);
  process.exit(1);
}, 30000);
try {
  assert.equal(process.argv.length, 4, 'usage: node verifier --fixture PATH');
  assert.equal(process.argv[2], '--fixture');
  stage = 'fixture preflight';
  const { root: fixture, entries, lockHash, codeHashes } = preflightFixture(process.argv[3]);
  report.preflight = { root: fixture, lockHash, codeHashes };
  const req = createRequire(path.join(fixture, 'package.json'));
  const manifest = JSON.parse(readFileSync(path.join(fixture, 'package.json'), 'utf8'));
  check('fixture private/local 1.4.1', manifest.private === true && manifest.dependencies['use-composed-ref'] === '1.4.1');
  check('task Node 22.23.3', process.version === 'v22.23.3');
  stage = 'DOM initialization';
  const { JSDOM } = req(entries.jsdom);
  dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://consumer.invalid/' });
  for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLTextAreaElement', 'Event', 'Node']) {
    Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
  }
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  // Initialize jsdom's own selector/style machinery before denying dynamic code.
  const warmup = document.createElement('textarea');
  document.body.append(warmup);
  dom.window.getComputedStyle(warmup);
  warmup.focus();
  warmup.remove();
  // Guards deliberately follow tool initialization. Type compilation uses only fs.
  stage = 'runtime guard self-test';
  guard = installRuntimeGuards(dom.window);
  report.guardSelfTest = guard.selfTest();
  stage = 'consumer entries';
  const React = req(entries.react);
  let activeInternals;
  const originalUseRef = React.useRef;
  restoreReact = () => { React.useRef = originalUseRef; };
  React.useRef = function (...args) {
    const ref = originalUseRef(...args);
    if (args[0] === null) activeInternals?.add(ref);
    return ref;
  };
  const { createRoot } = req(entries.reactDom);
  const consumerPath = entries.consumerCjs;
  const consumerReq = createRequire(consumerPath);
  const localEntry = realpathSync(consumerReq.resolve('use-composed-ref'));
  const localManifest = JSON.parse(readFileSync(path.join(path.dirname(localEntry), 'package.json'), 'utf8'));
  check('consumer resolves private local candidate', localManifest.private === true && localManifest.version === '1.4.1' && localEntry === entries.localCjs);
  const reactDomReq = createRequire(entries.reactDom);
  check('single fixture React instance', consumerReq('react') === React && reactDomReq('react') === React);
  const cjs = req(entries.consumerCjs).default;
  const esmPath = entries.consumerEsm;
  const esmText = readFileSync(esmPath, 'utf8');
  check('native ESM source identity', esmPath.endsWith('.esm.js') && /^import /m.test(esmText) && /export .*default/m.test(esmText) && !/module\.exports|require\(/.test(esmText));
  report.entries = { cjs: path.relative(fixture, consumerPath), nativeEsm: path.relative(fixture, esmPath) };
  const esm = await import(pathToFileURL(esmPath).href);
  check('native ESM namespace', esm[Symbol.toStringTag] === 'Module' && esm.default !== cjs);
  const esmReact = await import(pathToFileURL(entries.react).href);
  check('native ESM same React', esmReact.default === React);

  async function exercise(Component, label, strict) {
    stage = `${label}/${strict ? 'StrictMode' : 'normal'}`;
    const prefix = stage;
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    const log = [];
    const internals = new Set();
    // Observe actual consumer ref objects via React, without importing ref-hook internals.
    activeInternals = internals;
    let changes = [];
    const a = node => {
      log.push(node ? 'A+' : 'A0');
      if (node) {
        check(`${prefix}: internal before user A`, [...internals].some(ref => ref.current === node));
        return () => {
          check(`${prefix}: internal clear before cleanup`, [...internals].every(ref => ref.current !== node));
          log.push('A-');
        };
      }
    };
    const b = node => {
      log.push(node ? 'B+' : 'B0');
      check(`${prefix}: internal before user B/null`, node ? [...internals].some(ref => ref.current === node) : [...internals].every(ref => ref.current === null));
    };
    const render = async (ref, value) => {
      const element = React.createElement(Component, { ref, value, onChange: event => { changes.push(event.target.value); } });
      await React.act(async () => root.render(strict ? React.createElement(React.StrictMode, null, element) : element));
    };
    try {
      await render(a, 'hello');
      const node = host.getElementsByTagName('textarea')[0];
      check(`${prefix}: textarea/value`, node instanceof HTMLTextAreaElement && node.value === 'hello');
      node.focus();
      node.setSelectionRange(1, 3);
      const before = log.length;
      await render(a, 'hello');
      check(`${prefix}: stable rerender ref`, log.length === before);
      check(`${prefix}: focus/selection rerender`, document.activeElement === node && node.selectionStart === 1 && node.selectionEnd === 3);
      await render(b, 'hello');
      check(`${prefix}: swap cleanup before B`, log.slice(before).join(',') === 'A-,B+');
      check(`${prefix}: swap node/focus/selection`, host.getElementsByTagName('textarea')[0] === node && document.activeElement === node && node.selectionStart === 1 && node.selectionEnd === 3);
      await React.act(async () => {
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(node, 'typed');
        node.dispatchEvent(new Event('input', { bubbles: true }));
      });
      check(`${prefix}: typing onChange`, changes.length === 1 && changes[0] === 'typed');
      check(`${prefix}: controlled restoration`, node.value === 'hello');
      await render(b, 'updated');
      check(`${prefix}: controlled update`, node.value === 'updated');
      await React.act(async () => root.render(null));
      check(`${prefix}: null unmount`, log.at(-1) === 'B0' && !host.getElementsByTagName('textarea')[0]);
      check(`${prefix}: cleanup not null for A`, log.filter(x => x === 'A+').length === log.filter(x => x === 'A-').length && !log.includes('A0'));
      if (strict) check(`${prefix}: actual strict ref replay`, log.filter(x => x === 'A+').length === 2);
      await render(a, 'remount');
      check(`${prefix}: remount new node`, host.getElementsByTagName('textarea')[0] !== node && host.getElementsByTagName('textarea')[0].value === 'remount');
      await React.act(async () => root.unmount());
      check(`${prefix}: final balanced cleanup`, log.filter(x => x === 'A+').length === log.filter(x => x === 'A-').length);
    } finally {
      activeInternals = undefined;
      host.remove();
    }
  }
  for (const [label, component] of [['CJS', cjs], ['NATIVE_ESM', esm.default]]) {
    for (const strict of [false, true]) await exercise(component, label, strict);
  }
  restoreReact();
  stage = 'strict type consumer acceptance';
  const ts = req(entries.typescript);
  check('pinned TS 5.9.2', ts.version === '5.9.2');
  const typesRoot = path.join(fixture, 'node_modules/@types');
  check('target React 19 types', req('@types/react/package.json').version === '19.1.16');
  const virtual = path.join(repo, 'build/assistant-ref-consumer-20261013/acceptance.ts');
  let source = `import * as React from 'react';
import Textarea from 'react-textarea-autosize';
import compose from 'use-composed-ref';
const internal: React.RefObject<HTMLElement | null> = { current: null };
const object: React.RefObject<HTMLElement | null> = { current: null };
const voidRef: React.RefCallback<HTMLElement> = node => { object.current = node; };
const cleanup: React.RefCallback<HTMLElement> = node => { return () => { object.current = null; }; };
compose<HTMLElement>(internal, object); compose<HTMLElement>(internal, voidRef); compose<HTMLElement>(internal, cleanup);
compose<HTMLElement>(voidRef, cleanup);
const props: React.ComponentProps<typeof Textarea> = { ref: node => { return () => {}; }, value: 'x', onChange: event => { const value: string = event.currentTarget.value; } };
const textareaObject: React.RefObject<HTMLTextAreaElement | null> = { current: null };
const objectProps: React.ComponentProps<typeof Textarea> = { ref: textareaObject };
const voidProps: React.ComponentProps<typeof Textarea> = { ref: node => { textareaObject.current = node; } };
let count = 0;
// @ts-expect-error assignment expression returns number
compose<HTMLElement>(internal, node => count = 1);
// @ts-expect-error non-function ref
compose<HTMLElement>(internal, 1);
// @ts-expect-error string ref
compose<HTMLElement>(internal, 'legacy');
// @ts-expect-error actual consumer assignment-expression ref
const bad: React.ComponentProps<typeof Textarea> = { ref: node => count = 1 };
`;
  const options = { strict: true, noEmit: true, skipLibCheck: false, target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.CommonJS, moduleResolution: ts.ModuleResolutionKind.Node10,
    types: ['react'], typeRoots: [typesRoot], esModuleInterop: true,
    baseUrl: fixture, paths: { react: ['node_modules/@types/react/index.d.ts'],
      'react-textarea-autosize': ['node_modules/react-textarea-autosize'],
      'use-composed-ref': ['node_modules/use-composed-ref/index.d.ts'] } };
  const compilerHost = ts.createCompilerHost(options);
  const originalRead = compilerHost.readFile.bind(compilerHost);
  const originalExists = compilerHost.fileExists.bind(compilerHost);
  compilerHost.readFile = file => path.resolve(file) === virtual ? source : originalRead(file);
  compilerHost.fileExists = file => path.resolve(file) === virtual || originalExists(file);
  const program = ts.createProgram([virtual], options, compilerHost);
  const diagnostics = ts.getPreEmitDiagnostics(program);
  if (diagnostics.length) throw new Error(`type diagnostics ${diagnostics.map(d => `TS${d.code}:${d.start ?? 0}`).slice(0, 12).join(',')}`);
  check('strict type compile: four expected errors consumed', diagnostics.length === 0);
  const files = program.getSourceFiles().map(file => file.fileName.replaceAll('\\', '/'));
  check('actual consumer declarations compiled', files.some(file => file.includes('/react-textarea-autosize/dist/declarations/src/index.d.ts')));
  check('local index.d.ts compiled', files.some(file => file.endsWith('/use-composed-ref/index.d.ts') || file.endsWith('/use-composed-ref-compat/index.d.ts')));
  source = source.replace(/^\/\/ @ts-expect-error.*$/gm, '');
  const negativeDiagnostics = ts.getPreEmitDiagnostics(ts.createProgram([virtual], options, compilerHost));
  report.negativeTypeCodes = negativeDiagnostics.map(d => d.code);
  check('four negative controls genuinely rejected', negativeDiagnostics.length === 4 && negativeDiagnostics.every(d => d.file && path.resolve(d.file.fileName) === virtual && [2322, 2345].includes(d.code)));
  dom.window.close();
  dom = undefined;
  stage = 'runtime guard acceptance';
  report.guards = guard.report();
  assert.equal(report.guards.unexpectedAttempts.length, 0, 'unexpected consumer network/eval attempts');
  report.status = 'PASS';
} catch (error) {
  report.errors.push({ stage, message: String(error.message).slice(0, 600) });
  process.exitCode = 1;
} finally {
  try {
    restoreReact?.();
    dom?.window.close();
  } catch (error) {
    report.errors.push({ stage: 'cleanup', message: String(error.message).slice(0, 600) });
    report.status = 'RED';
    process.exitCode = 1;
  } finally {
    if (guard) {
      report.guards = guard.report();
      if (report.guards.unexpectedAttempts.length) {
        report.status = 'RED';
        process.exitCode = 1;
        report.errors.push({ stage: 'runtime guards', message: 'unexpected consumer network/eval attempts' });
      }
      guard.restore();
    }
  }
  clearTimeout(watchdog);
  process.stdout.write(`${JSON.stringify(report)}\n`);
}
