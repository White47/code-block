// Standalone, bounded Vite diagnostics. Never changes frontend configuration.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { fork, spawnSync } from 'node:child_process';

const script = fileURLToPath(import.meta.url);
const repo = path.resolve(path.dirname(script), '..');
const frontend = path.join(repo, 'frontend');
const output = path.join(repo, 'build', 'frontend-diagnostic');
const mode = process.argv.includes('--no-treeshake') ? 'no-treeshake' : 'normal';
const timeoutSeconds = Number(process.argv.find(a => a.startsWith('--timeout='))?.split('=')[1] ?? 45);
if (!Number.isFinite(timeoutSeconds) || timeoutSeconds < 5 || timeoutSeconds > 120) {
  throw new Error('Timeout must be between 5 and 120 seconds.');
}
fs.mkdirSync(output, { recursive: true });
const artifact = suffix => path.join(output, `${mode}.${suffix}`);

if (process.argv.includes('--child')) {
  process.chdir(frontend);
  await new Promise(resolve => process.once('message', resolve));
  const started = Date.now();
  const events = fs.openSync(artifact('modules.jsonl'), 'w');
  const record = (event, extra = {}) => fs.writeSync(events, JSON.stringify({ event, ms: Date.now() - started, ...extra }) + '\n');
  const counts = new Map();
  let parsed = 0;
  const probe = {
    name: 'frontend-diagnostic', enforce: 'pre',
    configResolved(config) {
      record('config', { root: config.root, treeshake: config.build.rollupOptions.treeshake,
        plugins: config.plugins.map(p => p.name), node: process.version });
    },
    transform(_code, id) { counts.set(id, (counts.get(id) ?? 0) + 1); record('transform', { id }); },
    moduleParsed(info) { parsed++; record('moduleParsed', { id: info.id, imports: info.importedIds }); },
    buildEnd(error) {
      record('buildEnd', { error: error?.message, parsed, uniqueTransforms: counts.size });
      fs.writeFileSync(artifact('graph.json'), JSON.stringify([...this.getModuleIds()].map(id => {
        const info = this.getModuleInfo(id);
        return { id, importers: info?.importers, imports: info?.importedIds, transformed: counts.get(id) ?? 0 };
      }), null, 2));
    },
    renderStart() { record('renderStart'); },
    generateBundle() { record('generateBundle'); },
  };
  try {
    const { build } = await import(pathToFileURL(path.join(frontend, 'node_modules/vite/dist/node/index.js')).href);
    record('buildStart');
    await build({ root: frontend, configFile: path.join(frontend, 'vite.config.ts'),
      cacheDir: path.join(output, 'cache'), plugins: [probe],
      build: { outDir: output, emptyOutDir: false,
        ...(mode === 'no-treeshake' ? { rollupOptions: { treeshake: false } } : {}) } });
    record('success', { memory: process.memoryUsage() });
    fs.closeSync(events);
    await new Promise(resolve => process.send({ event: 'buildFinished' }, resolve));
    process.exit(0);
  } catch (error) {
    record('failure', { error: error.stack });
    fs.closeSync(events);
    console.error(error);
    await new Promise(resolve => process.send({ event: 'buildFinished' }, resolve));
    process.exit(1);
  }
} else {
  const started = Date.now();
  const child = fork(script, [...process.argv.slice(2), '--child'], {
    cwd: repo, execArgv: ['--inspect=127.0.0.1:0', '--max-old-space-size=2048'],
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
  });
  const log = fs.createWriteStream(artifact('log'), { flags: 'w' });
  let socket, timer, stopping, finished = false, timedOut = false;
  let sequence = 0;
  const pending = new Map();
  const rpc = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence; pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const stopProfile = () => stopping ??= (async () => {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const result = await rpc('Profiler.stop');
    fs.writeFileSync(artifact('cpuprofile'), JSON.stringify(result.profile));
    console.log(`[diagnostic] CPU profile saved at ${(Date.now() - started) / 1000}s`);
    socket.close();
  })();
  const attach = async endpoint => {
    socket = new WebSocket(endpoint);
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data); const request = pending.get(message.id);
      if (request) { pending.delete(message.id); message.error ? request.reject(new Error(JSON.stringify(message.error))) : request.resolve(message.result); }
    });
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true });
      socket.addEventListener('error', reject, { once: true });
    });
    await rpc('Profiler.enable');
    await rpc('Profiler.setSamplingInterval', { interval: 1000 });
    await rpc('Profiler.start');
    timer = setTimeout(() => stopProfile().catch(console.error), 30000);
    child.send('start');
  };
  let stderr = '';
  child.stdout.on('data', data => { log.write(data); process.stdout.write(data); });
  child.stderr.on('data', data => {
    log.write(data); process.stderr.write(data); stderr += data;
    const endpoint = stderr.match(/ws:\/\/127\.0\.0\.1:\d+\/[\w-]+/)?.[0];
    if (endpoint && !socket) attach(endpoint).catch(error => { console.error(error); child.kill(); });
  });
  const watchdog = setTimeout(async () => {
    if (finished) return;
    timedOut = true;
    // The external inspector can sample even when the build event loop is busy.
    await Promise.race([stopProfile().catch(console.error), new Promise(resolve => setTimeout(resolve, 2000))]);
    if (!finished) {
      console.error(`[diagnostic] time limit reached; stopping owned child ${child.pid}`);
      if (process.platform === 'win32') {
        const kill = spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { timeout: 5000, encoding: 'utf8' });
        if (kill.status !== 0) {
          console.error(`[diagnostic] termination failed for owned PID ${child.pid}: ${kill.stderr || kill.error || kill.stdout}`);
          fs.writeFileSync(artifact('termination-failed.json'), JSON.stringify({ pid: child.pid, status: kill.status, error: kill.stderr || String(kill.error ?? '') }));
        }
      } else child.kill('SIGKILL');
    }
  }, timeoutSeconds * 1000);
  child.on('message', message => {
    if (message.event === 'buildFinished') {
      clearTimeout(timer);
      stopProfile().catch(console.error);
    }
  });
  child.on('exit', (code, signal) => {
    finished = true; clearTimeout(watchdog); clearTimeout(timer);
    socket?.close(); log.end();
    fs.writeFileSync(artifact('result.json'), JSON.stringify({ mode, code, signal, timedOut, elapsedSeconds: (Date.now() - started) / 1000, pid: child.pid }, null, 2));
    console.log(`[diagnostic] ${mode}: exit=${code}, timedOut=${timedOut}`);
    process.exitCode = timedOut ? 124 : code ?? 1;
  });
}
