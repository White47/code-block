import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createRequire, syncBuiltinESMExports } from 'node:module';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const approvedRoots = Object.freeze(['ci-a-cache-access', '迁移验证/ci-b'].map(name =>
  path.join(repo, 'build/assistant-ref-workspace-20261013', name)));
const pins = Object.freeze({
  '@assistant-ui/react': '0.15.28', '@types/react': '19.1.16',
  '@types/react-dom': '19.1.9', jsdom: '26.1.0', react: '19.1.1',
  'react-dom': '19.1.1', vite: '7.3.6', 'use-composed-ref': '1.4.1',
});
const lockHash = '2b7c93ac1fd186cfb5a84f50384b50990b760f3feca5b3129779db4630c85979';
const codeHashes = Object.freeze({
  'index.cjs': '9e2f3f3746ce3dee0b09bcfe51fe5bfaa7db3fe84fde4077d9bf597ae244f228',
  'index.mjs': '175f971373d12aa0d6ddc2e96e8bdd83c7aa095d1c7e10d906617244cf5d1053',
  'index.d.ts': 'de8fd4ad18bc88ab557ffc596d4ed234053e455c8c3c94362ab7bad756e50504',
});
const hash = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const json = file => JSON.parse(fs.readFileSync(file, 'utf8'));
function confined(file, base) {
  const canonical = fs.realpathSync(file);
  const relative = path.relative(base, canonical);
  assert.ok(relative && relative !== '..' && !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative), `entry escapes approved directory: ${file}`);
  return canonical;
}
function fixedDirectory(directory) {
  const canonical = fs.realpathSync(directory);
  assert.equal(canonical, directory, `redirected approved directory: ${directory}`);
  return canonical;
}

// Metadata, resolution and bytes only: do not execute JSDOM or any consumer here.
export function preflightFixture(rootPath) {
  const requested = path.resolve(rootPath);
  assert.ok(approvedRoots.includes(requested), 'fixture is not one of the two approved roots');
  const root = fixedDirectory(requested);
  const manifest = json(confined(path.join(root, 'package.json'), root));
  assert.equal(manifest.private, true);
  assert.deepEqual(manifest.dependencies, pins);
  assert.deepEqual(manifest.workspaces, ['packages/use-composed-ref-compat']);
  assert.equal(Object.hasOwn(manifest, 'overrides'), false);
  assert.equal(hash(confined(path.join(root, 'package-lock.json'), root)), lockHash);
  const modules = fixedDirectory(path.join(root, 'node_modules'));
  const local = fixedDirectory(path.join(root, 'packages/use-composed-ref-compat'));
  const pkg = json(confined(path.join(local, 'package.json'), local));
  assert.equal(pkg.name, 'use-composed-ref');
  assert.equal(pkg.version, '1.4.1');
  assert.equal(pkg.private, true);
  assert.equal(pkg.license, 'UNLICENSED');
  assert.deepEqual(pkg.peerDependencies, { react: '19.1.1' });
  assert.deepEqual(pkg.exports, { '.': { types: './index.d.ts', import: './index.mjs', require: './index.cjs' } });
  assert.equal(pkg.main, './index.cjs');
  assert.equal(pkg.module, './index.mjs');
  assert.equal(pkg.types, './index.d.ts');
  const entries = {};
  for (const [key, name] of [['localCjs', 'index.cjs'], ['localEsm', 'index.mjs'], ['localTypes', 'index.d.ts']]) {
    entries[key] = confined(path.join(local, name), local);
    assert.equal(entries[key], path.join(local, name), 'local entry must be the exact approved file');
    assert.equal(hash(entries[key]), codeHashes[name]);
  }
  const req = createRequire(path.join(root, 'package.json'));
  for (const [key, specifier] of [['jsdom', 'jsdom'], ['react', 'react'],
    ['reactDom', 'react-dom/client'], ['consumerCjs', 'react-textarea-autosize']]) {
    entries[key] = confined(req.resolve(specifier), modules);
  }
  // The native ESM entry is the package's published module field, not a transform.
  const consumerManifest = confined(req.resolve('react-textarea-autosize/package.json'), modules);
  const consumer = json(consumerManifest);
  assert.equal(consumer.version, '8.5.9');
  assert.equal(consumer.module, 'dist/react-textarea-autosize.esm.js');
  entries.consumerEsm = confined(path.resolve(path.dirname(consumerManifest), consumer.module), modules);
  for (const entry of [entries.consumerCjs, entries.consumerEsm, entries.reactDom]) {
    const fromEntry = createRequire(entry);
    assert.equal(confined(fromEntry.resolve('react'), modules), entries.react, 'single confined React resolution');
  }
  for (const entry of [entries.consumerCjs, entries.consumerEsm]) {
    assert.equal(fs.realpathSync(createRequire(entry).resolve('use-composed-ref')), entries.localCjs);
  }
  assert.equal(fs.realpathSync(req.resolve('use-composed-ref')), entries.localCjs);
  // Validate installed pins as well as the root's immutable dependency declaration.
  for (const [name, version] of Object.entries(pins)) {
    if (name === 'use-composed-ref') continue;
    const metadata = json(confined(path.join(modules, name, 'package.json'), modules));
    assert.equal(metadata.version, version, `installed pin: ${name}`);
  }
  const tsDirectory = fixedDirectory(path.join(repo, 'frontend/node_modules/typescript'));
  entries.typescript = confined(path.join(tsDirectory, 'lib/typescript.js'), tsDirectory);
  assert.equal(entries.typescript, path.join(tsDirectory, 'lib/typescript.js'));
  assert.equal(json(confined(path.join(tsDirectory, 'package.json'), tsDirectory)).version, '5.9.2');
  return { root, entries, lockHash, codeHashes: { ...codeHashes } };
}

// Known public entry guards only; this is not an OS, arbitrary VM, or
// pre-captured-internals sandbox. Install after approved JSDOM selector bootstrap.
export function installRuntimeGuards(window) {
  assert.ok(window && window.Function && window.eval, 'initialized JSDOM window required');
  const req = createRequire(import.meta.url);
  const saved = [];
  const probes = [];
  const unexpectedAttempts = [];
  const expectedDenials = [];
  let expecting = false;
  let restored = false;
  function replace(object, key, entry, prototype) {
    if (saved.some(slot => slot.object === object && slot.key === key)) return;
    const descriptor = Object.getOwnPropertyDescriptor(object, key);
    const denied = function () {
      (expecting ? expectedDenials : unexpectedAttempts).push({ entry });
      throw new Error('consumer probe forbids network/eval');
    };
    if (prototype) denied.prototype = prototype;
    Object.defineProperty(object, key, {
      configurable: descriptor?.configurable ?? true,
      enumerable: descriptor?.enumerable ?? false,
      writable: true, value: denied,
    });
    saved.push({ object, key, descriptor });
    probes.push(() => object[key]());
  }
  function restore() {
    if (restored) return;
    for (const { object, key, descriptor } of [...saved].reverse()) {
      if (descriptor) Object.defineProperty(object, key, descriptor);
      else delete object[key];
    }
    syncBuiltinESMExports();
    restored = true;
  }
  try {
    for (const [name, methods] of [
      ['net', ['connect', 'createConnection', 'createServer']],
      ['tls', ['connect', 'createServer']], ['http', ['request', 'get', 'createServer']],
      ['https', ['request', 'get', 'createServer']], ['dgram', ['createSocket']],
    ]) {
      const api = req(`node:${name}`);
      for (const method of methods) replace(api, method, `${name}.${method}`);
    }
    replace(req('node:net').Socket.prototype, 'connect', 'net.Socket.prototype.connect');
    replace(req('node:net').Server.prototype, 'listen', 'net.Server.prototype.listen');
    for (const method of ['bind', 'connect', 'send', 'addMembership', 'addSourceSpecificMembership']) {
      replace(req('node:dgram').Socket.prototype, method, `dgram.Socket.prototype.${method}`);
    }
    // Capture both intrinsics before replacing either; default JSDOM shares them.
    const realms = [[globalThis, 'global'], [window, 'window']];
    const intrinsics = realms.map(([object]) => object.Function);
    for (let i = 0; i < realms.length; i++) {
      const [object, label] = realms[i];
      for (const key of ['fetch', 'WebSocket', 'XMLHttpRequest', 'eval']) replace(object, key, `${label}.${key}`);
      replace(object, 'Function', `${label}.Function`, intrinsics[i].prototype);
      replace(intrinsics[i].prototype, 'constructor', `${label}.Function.prototype.constructor`);
    }
    // Update native ESM builtin named exports as well as CommonJS objects.
    syncBuiltinESMExports();
  } catch (error) {
    restore();
    throw error;
  }
  return {
    restore,
    selfTest() {
      assert.equal(restored, false, 'cannot self-test restored guards');
      expecting = true;
      try {
        for (const probe of probes) assert.throws(probe, error =>
          error instanceof Error && error.message === 'consumer probe forbids network/eval');
        for (const [object] of [[globalThis], [window]]) {
          assert.throws(() => object.Object.constructor.constructor('return 1')(),
            /^Error: consumer probe forbids network\/eval$/);
        }
      } finally {
        expecting = false;
      }
      return { passed: true };
    },
    report() {
      return { unexpectedAttempts: unexpectedAttempts.map(attempt => ({ ...attempt })),
        expectedDenials: expectedDenials.map(attempt => ({ ...attempt })) };
    },
  };
}
