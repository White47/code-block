import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// Contract for the future helper (no production helper is implemented here):
// preflightFixture(rootPath) -> { root, entries: { jsdom, react, reactDom,
// consumerCjs, consumerEsm, localCjs, localEsm, localTypes, typescript },
// lockHash, codeHashes }. All entries are canonical absolute realpaths.
// installRuntimeGuards(window) -> { restore(), selfTest(), report() }.
// selfTest() -> { passed: true }; report() -> { unexpectedAttempts: [{ entry }] }.
// Every denial is Error('consumer probe forbids network/eval').
// Bootstrap JSDOM first; install guards before consumer imports. This is a
// known-entry guard, not an arbitrary-VM/pre-captured-internals/OS sandbox.
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const helper = new URL('./assistant-ref-consumer-guards.mjs', import.meta.url).href;
const build = path.join(repo, 'build/assistant-ref-workspace-20261013');
const roots = ['ci-a-cache-access', '迁移验证/ci-b'].map(x => path.join(build, x));
const pins = {
  '@assistant-ui/react': '0.15.28', '@types/react': '19.1.16',
  '@types/react-dom': '19.1.9', jsdom: '26.1.0', react: '19.1.1',
  'react-dom': '19.1.1', vite: '7.3.6', 'use-composed-ref': '1.4.1',
};
const lockHash = '2b7c93ac1fd186cfb5a84f50384b50990b760f3feca5b3129779db4630c85979';
const codeHashes = {
  'index.cjs': '9e2f3f3746ce3dee0b09bcfe51fe5bfaa7db3fe84fde4077d9bf597ae244f228',
  'index.mjs': '175f971373d12aa0d6ddc2e96e8bdd83c7aa095d1c7e10d906617244cf5d1053',
  'index.d.ts': 'de8fd4ad18bc88ab557ffc596d4ed234053e455c8c3c94362ab7bad756e50504',
};
const prelude = `
import assert from 'node:assert/strict';
import { createRequire, syncBuiltinESMExports } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
const req = createRequire(import.meta.url);
const roots = ${JSON.stringify(roots)};
const repo = ${JSON.stringify(repo)};
const helperURL = ${JSON.stringify(helper)};
const pins = ${JSON.stringify(pins)};
const lockHash = ${JSON.stringify(lockHash)};
const codeHashes = ${JSON.stringify(codeHashes)};
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
async function load() {
  let api, failure;
  try { api = await import(helperURL); } catch (error) { failure = error; }
  assert.ok(api, 'missing/import-failed guard helper: ' + (failure?.code ?? failure?.message));
  assert.equal(typeof api.preflightFixture, 'function');
  assert.equal(typeof api.installRuntimeGuards, 'function');
  return api;
}
const denied = new RegExp('^consumer probe forbids network/eval$');
`;
function child(body) {
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', prelude + body], {
    cwd: repo, encoding: 'utf8', timeout: 15000, windowsHide: true,
  });
  assert.ifError(result.error);
  assert.equal(result.signal, null, 'child must terminate without leaked observers');
  assert.equal(result.status, 0, result.stderr || result.stdout);
}

test('preflight accepts only both canonical approved fixtures with fixed metadata and hashes', () => child(`
const { preflightFixture } = await load();
for (const root of roots) {
  const result = await preflightFixture(root);
  assert.equal(result.root, fs.realpathSync(root));
  assert.equal(result.lockHash, lockHash);
  assert.deepEqual(result.codeHashes, codeHashes);
  const json = JSON.parse(fs.readFileSync(path.join(root, 'package.json')));
  assert.deepEqual(json.dependencies, pins);
  assert.deepEqual(json.workspaces, ['packages/use-composed-ref-compat']);
  assert.equal(Object.hasOwn(json, 'overrides'), false);
  assert.equal(digest(fs.readFileSync(path.join(root, 'package-lock.json'))), lockHash);
  const local = path.join(root, 'packages/use-composed-ref-compat');
  const pkg = JSON.parse(fs.readFileSync(path.join(local, 'package.json')));
  assert.equal(pkg.name, 'use-composed-ref');
  assert.equal(pkg.version, '1.4.1'); assert.equal(pkg.private, true);
  assert.equal(pkg.license, 'UNLICENSED');
  assert.deepEqual(pkg.peerDependencies, { react: '19.1.1' });
  assert.deepEqual(pkg.exports, { '.': { types: './index.d.ts', import: './index.mjs', require: './index.cjs' } });
  for (const [name, expected] of Object.entries(codeHashes))
    assert.equal(digest(fs.readFileSync(path.join(local, name))), expected);
}
`));

test('preflight rejects reserved roots, arbitrary descendants, missing roots and prefix siblings', () => child(`
const { preflightFixture } = await load();
for (const root of [repo, path.dirname(roots[0]),
  path.join(path.dirname(roots[0]), 'fixture'), path.join(path.dirname(roots[0]), 'ci-a'),
  path.join(roots[0], 'node_modules'), roots[0] + '-other',
  path.join(path.dirname(roots[0]), 'missing-root')]) {
  await assert.rejects(async () => preflightFixture(root));
}
`));

// Fault injection changes only in-memory filesystem responses inside a child.
// Approved fixtures and preserved failed installation samples remain read-only.
test('preflight rejects altered pins, overrides, workspace, lock and owned-code bytes', () => child(`
const original = fs.readFileSync;
let mutation;
fs.readFileSync = function(file, ...args) {
  const bytes = original.call(this, file, ...args);
  const name = String(file).replaceAll('\\\\', '/');
  if (!name.startsWith(roots[0].replaceAll('\\\\', '/') + '/')) return bytes;
  if (mutation === 'lock' && name.endsWith('/package-lock.json')) return Buffer.from('{}');
  if (mutation === 'code' && name.endsWith('/index.cjs')) return Buffer.from('tampered');
  if (!name.endsWith('/package.json')) return bytes;
  const json = JSON.parse(String(bytes));
  if (name === (roots[0] + '/package.json').replaceAll('\\\\', '/')) {
    if (mutation === 'pin') json.dependencies.react = '19.1.0';
    if (mutation === 'override') json.overrides = {};
    if (mutation === 'workspace') json.workspaces = ['packages/other'];
  } else if (name.includes('/packages/use-composed-ref-compat/')) {
    if (mutation === 'local') json.private = false;
    if (mutation === 'exports') json.exports['.'].require = '../other.cjs';
    if (mutation === 'peer') json.peerDependencies.react = '*';
    if (mutation === 'version') json.version = '1.4.0';
    if (mutation === 'license') json.license = 'MIT';
  }
  return typeof bytes === 'string' ? JSON.stringify(json) : Buffer.from(JSON.stringify(json));
};
syncBuiltinESMExports();
const { preflightFixture } = await load();
for (mutation of ['pin', 'override', 'workspace', 'lock', 'code', 'local', 'exports', 'peer', 'version', 'license'])
  await assert.rejects(async () => preflightFixture(roots[0]), mutation);
`));

test('preflight provides confined real module entries and rejects escaped resolved entries before execution', () => child(`
const original = fs.realpathSync;
let escape = false;
fs.realpathSync = function(file, ...args) {
  const value = original.call(this, file, ...args);
  return escape && String(file).replaceAll('\\\\', '/').includes('/node_modules/jsdom')
    ? path.join(repo, 'frontend/node_modules/jsdom/lib/api.js') : value;
};
fs.realpathSync.native = function(file, ...args) { return fs.realpathSync(file, ...args); };
syncBuiltinESMExports();
const { preflightFixture } = await load();
for (const root of roots) {
  const { entries } = await preflightFixture(root);
  assert.deepEqual(Object.keys(entries).sort(), ['jsdom','react','reactDom','consumerCjs','consumerEsm','localCjs','localEsm','localTypes','typescript'].sort());
  for (const key of ['jsdom','react','reactDom','consumerCjs','consumerEsm']) {
    assert.equal(entries[key], original(entries[key]));
    const relative = path.relative(original(path.join(root, 'node_modules')), entries[key]);
    assert.ok(relative && !relative.startsWith('..') && !path.isAbsolute(relative), key);
  }
  for (const [key, name] of [['localCjs','index.cjs'],['localEsm','index.mjs'],['localTypes','index.d.ts']])
    assert.equal(entries[key], original(path.join(root, 'packages/use-composed-ref-compat', name)));
  assert.equal(entries.typescript, original(path.join(repo, 'frontend/node_modules/typescript/lib/typescript.js')));
}
escape = true;
await assert.rejects(async () => preflightFixture(roots[0]));
`));

const runtimeSetup = `
const api = await load();
const { entries } = await api.preflightFixture(roots[0]);
const { JSDOM } = req(entries.jsdom);
const dom = new JSDOM('<!doctype html><textarea></textarea>');
dom.window.getComputedStyle(dom.window.document.querySelector('textarea'));
const window = dom.window;
let originalsCalled = 0;
const tripwire = function() { originalsCalled++; throw new Error('ORIGINAL IO CALLED'); };
const slots = [];
function fake(object, key) {
  const descriptor = Object.getOwnPropertyDescriptor(object, key);
  Object.defineProperty(object, key, { configurable: true, writable: true, value: tripwire });
  slots.push([object, key, descriptor]);
}
const moduleSlots = [
 ['node:net',['connect','createConnection','createServer']],
 ['node:tls',['connect','createServer']], ['node:http',['request','get','createServer']],
 ['node:https',['request','get','createServer']], ['node:dgram',['createSocket']],
];
for (const [name, keys] of moduleSlots) for (const key of keys) fake(req(name), key);
const socketPrototype = req('node:net').Socket.prototype;
fake(socketPrototype, 'connect');
for (const object of [globalThis, window]) for (const key of ['fetch','WebSocket']) fake(object, key);
fake(window, 'XMLHttpRequest');
const OriginalFunction = globalThis.Function;
const descriptors = [...slots.map(([object,key]) => [object,key,Object.getOwnPropertyDescriptor(object,key)]),
 ...[globalThis,window].flatMap(object => ['eval','Function'].map(key => [object,key,Object.getOwnPropertyDescriptor(object,key)])),
 [OriginalFunction.prototype,'constructor',Object.getOwnPropertyDescriptor(OriginalFunction.prototype,'constructor')]];
const listeners = process.eventNames().map(name => [name, process.rawListeners(name)]);
const guard = api.installRuntimeGuards(window);
function deny(action) { assert.throws(action, error => error instanceof Error && denied.test(error.message)); }
function cleanup() {
  guard.restore();
  for (const [object,key,descriptor] of descriptors)
    assert.deepEqual(Object.getOwnPropertyDescriptor(object,key), descriptor, 'restore ' + key);
  for (const [name,before] of listeners) assert.deepEqual(process.rawListeners(name), before);
  for (const name of process.eventNames()) assert.ok(listeners.some(([old]) => old === name));
  for (const [object,key,descriptor] of slots.reverse()) {
    if (descriptor) Object.defineProperty(object,key,descriptor); else delete object[key];
  }
  dom.window.close();
}
`;

test('guards deny global/window eval and shared intrinsic constructor bypasses; restore descriptors', () => child(runtimeSetup + `
try {
  for (const object of [globalThis, window]) {
    deny(() => object.eval('1'));
    deny(() => object.Function('return 1'));
    deny(() => new object.Function('return 1'));
    deny(() => object.Object.constructor.constructor('return 1')());
    deny(() => object.Function.prototype.constructor('return 1')());
  }
  deny(() => OriginalFunction.prototype.constructor.call(null, 'return 1'));
  assert.equal(originalsCalled, 0);
  assert.ok(guard.report().unexpectedAttempts.length >= 11);
} finally { cleanup(); }
`));

test('guards deny fake socket receiver and all HTTP/TLS/net/fetch/ws entries before original IO', () => child(runtimeSetup + `
try {
  deny(() => socketPrototype.connect.call(Object.create(socketPrototype), { port: 1 }));
  for (const [name, keys] of moduleSlots) for (const key of keys)
    deny(() => req(name)[key]());
  for (const object of [globalThis, window]) {
    deny(() => object.fetch('https://consumer.invalid/'));
    deny(() => new object.WebSocket('wss://consumer.invalid/'));
  }
  deny(() => new window.XMLHttpRequest());
  assert.equal(originalsCalled, 0, 'no real socket constructed or original entry invoked');
  assert.ok(guard.report().unexpectedAttempts.some(x => x.entry === 'net.Socket.prototype.connect'));
} finally { cleanup(); }
`));

test('selfTest denials are separated from consumer attempts and restoration permits termination', () => child(runtimeSetup + `
try {
  assert.deepEqual(guard.report().unexpectedAttempts, []);
  assert.equal((await guard.selfTest()).passed, true);
  assert.deepEqual(guard.report().unexpectedAttempts, []);
  deny(() => window.eval('1'));
  assert.equal(guard.report().unexpectedAttempts.length, 1);
  assert.equal(guard.report().unexpectedAttempts[0].entry, 'window.eval');
  assert.equal(originalsCalled, 0);
} finally { cleanup(); }
assert.equal(OriginalFunction('return 7')(), 7);
`));

test('historical guard fixture leaves intrinsic constructors executable and socket prototype unchanged', () => child(`
// Literal historical block from verify-assistant-ref-consumer.mjs before C1harnessGUARD.
// This inert regression fixture is independent of the repaired production harness.
const historicalGuard = \`
  const denied = () => { throw new Error('consumer probe forbids network/eval'); };
  for (const [module, methods] of [['node:net', ['connect', 'createConnection', 'createServer']],
    ['node:tls', ['connect', 'createServer']], ['node:http', ['request', 'get', 'createServer']],
    ['node:https', ['request', 'get', 'createServer']], ['node:dgram', ['createSocket']]]) {
    const api = req(module);
    for (const method of methods) api[method] = denied;
  }
  globalThis.fetch = denied;
  globalThis.WebSocket = denied;
  globalThis.eval = denied;
  const blockedFunction = function () { return denied(); };
  blockedFunction.prototype = globalThis.Function.prototype;
  globalThis.Function = blockedFunction;
  dom.window.fetch = denied;
  dom.window.WebSocket = denied;
  dom.window.XMLHttpRequest = denied;
  dom.window.eval = denied;
  dom.window.Function = blockedFunction;
\`;
const OriginalFunction = globalThis.Function;
const originalConnect = req('node:net').Socket.prototype.connect;
const window = { Function: OriginalFunction };
OriginalFunction('req', 'dom', historicalGuard)(req, { window });
assert.throws(() => globalThis.Function('return 1'), error => denied.test(error.message));
assert.equal(Object.constructor.constructor('return 41')(), 41);
assert.equal(globalThis.Function.prototype.constructor('return 42')(), 42);
assert.equal(req('node:net').Socket.prototype.connect, originalConnect);
// Identity only: never invoke a socket or any original network entry.
`));
