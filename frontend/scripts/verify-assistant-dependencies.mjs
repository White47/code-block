// C1 isolated compatibility gate only. Builds a virtual entry in memory;
// no production source, manifests, generated assets or package installs.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire, syncBuiltinESMExports } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { preflightFixture } from "./assistant-ref-consumer-guards.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const args = process.argv.slice(2);
assert.ok(args.includes("--isolated") && args.includes("--fake-only"), "--isolated --fake-only required");
assert.ok(args.every((arg, index) => ["--isolated", "--fake-only", "--node-env", "--native-python-env"].includes(arg) || ["--node-env", "--native-python-env"].includes(args[index - 1])), "unknown argument");
const envIndex = args.indexOf("--node-env");
const env = path.resolve(envIndex < 0 ? path.join(root, "build/assistant-ref-workspace-20261013/ci-a-cache-access") : args[envIndex + 1]);
const preflight = preflightFixture(env);
assert.equal(process.version, "v22.23.3", "task Node22 binary required");
assert.notEqual(env, path.join(root, "frontend"), "production frontend dependency tree forbidden");
const require = createRequire(path.join(env, "package.json"));
const results = [];
const record = (probe, status, detail) => results.push({ probe, status, detail });
const sha = (data) => createHash("sha256").update(data).digest("hex");
const metadata = (name) => JSON.parse(fs.readFileSync(path.join(env, "node_modules", name, "package.json"), "utf8"));
const lockBytes = fs.readFileSync(path.join(env, "package-lock.json"));
const lock = JSON.parse(lockBytes);
const graph = Object.entries(lock.packages).filter(([key]) => key).map(([location, value]) => ({
  location, version: value.version, integrity: value.integrity ?? null,
  license: value.license ?? null, engines: value.engines ?? null,
  dependencies: value.dependencies ?? {}, peerDependencies: value.peerDependencies ?? {},
}));
const notices = ["@assistant-ui/react", "@assistant-ui/core", "@assistant-ui/store", "@assistant-ui/tap", "react", "react-dom", "nanoid"].map((name) => {
  const directory = path.join(env, "node_modules", name);
  const license = fs.readdirSync(directory).find((filename) => /^license(?:\.|$)/i.test(filename));
  return { name, version: metadata(name).version, license: metadata(name).license,
    path: license ? path.join(directory, license) : null,
    sha256: license ? sha(fs.readFileSync(path.join(directory, license))) : null };
});

// Guard all common Node/browser outbound paths before assistant/Vite imports.
// The script never prints or enumerates ambient environment values.
const blocked = [];
const restorers = [];
const deny = () => { blocked.push("blocked"); throw new Error("network/code-generation forbidden"); };
const codegenBlocked = [];
let phase = "setup";
const denyCodegen = () => { codegenBlocked.push(phase); throw new Error("code-generation forbidden"); };
function replace(object, key, value) {
  const descriptor = Object.getOwnPropertyDescriptor(object, key);
  Object.defineProperty(object, key, { configurable: true, writable: true, value });
  restorers.push(() => descriptor ? Object.defineProperty(object, key, descriptor) : delete object[key]);
}
for (const [module, keys] of [
  ["node:http", ["request", "get", "createServer"]], ["node:https", ["request", "get", "createServer"]],
  ["node:net", ["connect", "createConnection", "createServer"]], ["node:tls", ["connect", "createServer"]],
  ["node:dns", ["lookup", "resolve", "resolve4", "resolve6"]],
  ["node:dgram", ["createSocket"]],
]) {
  const builtin = require(module);
  for (const key of keys) replace(builtin, key, deny);
}
replace(require("node:net").Socket.prototype, "connect", deny);
replace(require("node:net").Server.prototype, "listen", deny);
const dnsPromises = require("node:dns").promises;
for (const key of ["lookup", "resolve", "resolve4", "resolve6"]) replace(dnsPromises, key, deny);
syncBuiltinESMExports();
for (const key of ["fetch", "WebSocket", "XMLHttpRequest", "EventSource"]) replace(globalThis, key, deny);

let dom;
let bundle;
let warnings = [];
let bundleArtifacts = [];
let nativeReport = null;
const moduleClosures = [];
const nativeReports = [];
const fixturePath = path.join(root, "frontend/scripts/assistant-native-ref-fixture.mjs");
async function runAssistantProbes() {
  assert.equal(metadata("@assistant-ui/react").version, "0.15.28");
  assert.equal(metadata("react").version, "19.1.1");
  assert.equal(metadata("react-dom").version, "19.1.1");
  assert.equal(metadata("vite").version, "7.3.6");
  record("installed_candidate_versions", "pass", "assistant-ui 0.15.28 / React+DOM 19.1.1 / Vite 7.3.6; lock integrity graph reported");
  record("approved_local_ref_preflight", "pass", JSON.stringify(preflight));
  const nanoid = metadata("nanoid");
  assert.equal(nanoid.engines.node, "^22 || ^24 || >=26", "re-evaluate changed nanoid engine declaration");
  const major = Number(process.versions.node.split(".")[0]);
  record("nanoid_node_engine", major === 22 || major === 24 || major >= 26 ? "pass" : "fail",
    `nanoid ${nanoid.version} requires ${nanoid.engines.node}; actual Node ${process.versions.node}; npm EBADENGINE retained`);
  record("mit_license_notices", notices.every((item) => item.path && item.license === "MIT") ? "pass" : "fail", "installed license paths/hashes recorded; production copying pending");

  // jsdom parses HTML without executing inline scripts or loading resources.
  // Its parser may use Function internally; install code-generation guards
  // after harness DOM setup but BEFORE assistant-ui import and bundle execution.
  const { JSDOM } = await import(pathToFileURL(require.resolve("jsdom")).href);
  dom = new JSDOM('<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'self\'; style-src \'self\'; connect-src \'self\'; img-src \'self\'"></head><body><div id="probe"></div></body></html>',
    { url: "https://local-probe.invalid", pretendToBeVisual: true });
  for (const key of ["window", "document", "navigator", "HTMLElement", "Element", "Node", "MutationObserver"])
    replace(globalThis, key, key === "window" ? dom.window : dom.window[key]);
  replace(globalThis, "IS_REACT_ACT_ENVIRONMENT", true);
  replace(dom.window, "fetch", deny);
  for (const key of ["fetch", "WebSocket", "XMLHttpRequest", "EventSource"])
    replace(dom.window, key, deny);
  replace(dom.window.navigator, "sendBeacon", deny);
  replace(dom.window, "eval", denyCodegen);
  replace(globalThis, "eval", denyCodegen);
  const guardedFunction = new Proxy(globalThis.Function, { apply: denyCodegen, construct: denyCodegen });
  replace(globalThis.Function.prototype, "constructor", guardedFunction);
  replace(dom.window, "Function", guardedFunction);
  replace(globalThis, "Function", guardedFunction);

  phase = "external-runtime";
  const React = await import(pathToFileURL(require.resolve("react")).href);
  const ReactDOM = await import(pathToFileURL(require.resolve("react-dom/client")).href);
  const AUI = await import(pathToFileURL(require.resolve("@assistant-ui/react")).href);
  record("guarded_external_runtime_import", "pass", "actual public useExternalStoreRuntime import; cloud package present but not configured");

  const message = (id, role, text, running = false) => ({ id, role, createdAt: new Date(0),
    content: [{ type: "text", text }], attachments: [],
    ...(role === "assistant" ? { status: running ? { type: "running" } : { type: "complete", reason: "stop" } } : {}),
    metadata: { unstable_state: null, unstable_annotations: [], unstable_data: [], steps: [], custom: {} } });
  let snapshot = { messages: [message("u0", "user", "public fake")], isRunning: false };
  const listeners = new Set();
  const calls = { onNew: 0, onCancel: 0, cleanup: 0 };
  const publish = (state) => { snapshot = state; for (const listener of listeners) listener(); };
  const subscribe = (listener) => { listeners.add(listener); return () => listeners.delete(listener); };
  let runtime;
  let acknowledgeCancel;
  const onNew = async (append) => {
    calls.onNew++;
    assert.deepEqual(append.content, [{ type: "text", text: "explicit fake send" }]);
    publish({ messages: [...snapshot.messages, message("u1", "user", "explicit fake send"), message("a1", "assistant", "", true)], isRunning: true });
  };
  const onCancel = async () => {
    calls.onCancel++;
    // Host owns backend acknowledgement. cancelRun() returns void immediately.
    await new Promise((resolve) => { acknowledgeCancel = resolve; });
    publish({ messages: snapshot.messages.map((item) => item.id === "a1" ? message("a1", "assistant", "fake delta") : item), isRunning: false });
  };
  const Host = ({ ownerCleanup = false }) => {
    const state = React.useSyncExternalStore(subscribe, () => snapshot, () => snapshot);
    runtime = AUI.useExternalStoreRuntime({ ...state, onNew, onCancel, unstable_enableToolInvocations: false });
    React.useEffect(() => () => { if (ownerCleanup && snapshot.isRunning) { calls.cleanup++; void onCancel(); } }, [ownerCleanup]);
    return React.createElement(AUI.AssistantRuntimeProvider, { runtime }, React.createElement("span", null, "public probe"));
  };
  let view = ReactDOM.createRoot(document.getElementById("probe"));
  const render = async (ownerCleanup = false) => React.act(async () => {
    view.render(React.createElement(React.StrictMode, null, React.createElement(Host, { ownerCleanup })));
  });
  await render();
  assert.equal(calls.onNew, 0);
  assert.equal(calls.onCancel, 0);
  await React.act(async () => runtime.thread.append("explicit fake send"));
  assert.equal(calls.onNew, 1);
  assert.equal(runtime.thread.getState().isRunning, true);
  await React.act(async () => publish({ ...snapshot, messages: [...snapshot.messages.slice(0, -1), message("a1", "assistant", "fake delta", true)] }));
  assert.equal(runtime.thread.getState().messages.at(-1).content[0].text, "fake delta");
  let returnValue;
  await React.act(async () => { returnValue = runtime.thread.cancelRun(); });
  assert.equal(returnValue, undefined);
  assert.equal(calls.onCancel, 1);
  assert.equal(snapshot.isRunning, true, "cancel callback must wait for host acknowledgement");
  await React.act(async () => { acknowledgeCancel(); await Promise.resolve(); });
  assert.equal(runtime.thread.getState().isRunning, false);
  record("controlled_onNew_onCancel_stream", "pass", "one explicit append; controlled delta; cancelRun returns void, host acknowledges async cancellation");

  // Bare unmount does not invoke the external backend onCancel callback.
  await React.act(async () => publish({ ...snapshot, isRunning: true }));
  const beforeUnmount = calls.onCancel;
  await React.act(async () => view.unmount());
  assert.equal(listeners.size, 0);
  assert.equal(calls.onCancel, beforeUnmount);
  record("bare_unmount_cancel_contract", "pass", "bare runtime unmount does not call onCancel; host panel/project lifecycle must cancel explicitly");
  const sendsBeforeRemount = calls.onNew;
  publish({ ...snapshot, isRunning: false });
  view = ReactDOM.createRoot(document.getElementById("probe"));
  await render(true);
  assert.equal(calls.onNew, sendsBeforeRemount);
  assert.equal(calls.onCancel, beforeUnmount);
  await React.act(async () => publish({ ...snapshot, isRunning: true }));
  await React.act(async () => view.unmount());
  assert.equal(calls.cleanup, 1);
  assert.equal(calls.onCancel, beforeUnmount + 1);
  await React.act(async () => { acknowledgeCancel(); await Promise.resolve(); });
  assert.equal(listeners.size, 0);
  record("owner_cleanup_strictmode_remount", "pass", "owner cleanup cancels once; idle StrictMode replay/remount sends zero; subscription released");

  for (const mode of ["production", "development"]) {
  phase = "vite-build-tool";
  const { build } = await import(pathToFileURL(require.resolve("vite")).href);
  const virtual = path.join(env, "__assistant_c1_probe__.js").replaceAll("\\", "/");
  const source = `import * as React from 'react';
    import {createElement, StrictMode, useSyncExternalStore} from 'react';
    import {createRoot} from 'react-dom/client';
    import TextareaAutosize from 'react-textarea-autosize';
    import {runNativeRefFixture} from ${JSON.stringify(fixturePath.replaceAll("\\", "/"))};
    import {useExternalStoreRuntime, AssistantRuntimeProvider} from '@assistant-ui/react';
    export {createRoot};
    export async function runRefProbe(host,mode) {
      if(mode!==${JSON.stringify(mode)})throw new Error('bundle mode mismatch');
      const evidence=await runNativeRefFixture({React,createRoot,TextareaAutosize,mode,host});
      if(!Array.isArray(evidence?.assertions)||!evidence.assertions.length||evidence.scenarios?.length!==2)
        throw new Error('native fixture evidence missing');
      const strict=evidence.scenarios.find(item=>item.strict);
      if(!strict||strict.extraDevelopmentCycle!==(mode==='development'))throw new Error('StrictMode ref cycle not proved');
      return {status:'pass',mode,...evidence};
    }
    export function mount(node,store) { const root=createRoot(node);
      function Host() { const state=useSyncExternalStore(store.subscribe,store.getSnapshot,store.getSnapshot);
        const runtime=useExternalStoreRuntime({...state,onNew:store.onNew,onCancel:store.onCancel,unstable_enableToolInvocations:false});
        React.useEffect(()=>()=>{if(store.ownerCleanup&&store.getSnapshot().isRunning){store.ownerCleanups++;void store.onCancel();}},[]);
        store.runtime=runtime; return createElement(AssistantRuntimeProvider,{runtime},createElement('span',null,'local bundled probe')); }
      root.render(createElement(StrictMode,null,createElement(Host))); return root; }
  `;
  bundle = await build({ root: env, configFile: false, envFile: false, logLevel: "silent", base: "./",
    // Vite library mode preserves NODE_ENV by default; this fixture runs in a browser.
    mode,
    define: { "process.env.NODE_ENV": JSON.stringify(mode) },
    resolve: { alias: {
      "react-dom/client": require.resolve("react-dom/client"),
      "react": path.dirname(require.resolve("react/package.json")),
      "@assistant-ui/react": require.resolve("@assistant-ui/react"),
      "react-textarea-autosize": preflight.entries.consumerEsm,
      "use-composed-ref": preflight.entries.localEsm,
    } },
    plugins: [{ name: "public-fake-only", resolveId(id) { if (id.replaceAll("\\", "/").endsWith("/__assistant_c1_probe__.js")) return "\0assistant-c1-probe"; },
      load(id) {
        if (id === "\0assistant-c1-probe") return source;
        if (/[/\\]use-composed-ref(?:[/\\-])/.test(id))
          assert.ok([preflight.entries.localEsm, preflight.entries.localCjs].includes(fs.realpathSync(id.replace(/^\0/, "").split("?")[0])), "original utility loading forbidden");
      },
      generateBundle() {
        const modules = [...this.getModuleIds()].map(id => {
          if (id.startsWith("\0")) return { id, virtual: true, sha256: id === "\0assistant-c1-probe" ? sha(source) : null };
          const localPath = fs.realpathSync(id.split("?")[0]);
          const relative = path.relative(env, localPath);
          assert.ok(localPath === fixturePath || (relative && !relative.startsWith("..") && !path.isAbsolute(relative)), `module outside approved closure: ${localPath}`);
          if (/[/\\]use-composed-ref(?:[/\\-])/.test(localPath))
            assert.ok([preflight.entries.localEsm, preflight.entries.localCjs].includes(localPath), "original utility must not be reachable");
          const info = this.getModuleInfo(id);
          assert.equal(info.isExternal, false, "external module in closure");
          return { id, localPath, sha256: sha(fs.readFileSync(localPath)),
            imports: info.importedIds, dynamicImports: info.dynamicallyImportedIds };
        });
        assert.ok(modules.some(item => item.localPath === preflight.entries.localEsm), "approved replacement absent from closure");
        assert.ok(modules.some(item => item.localPath === preflight.entries.consumerEsm), "real consumer absent from closure");
        moduleClosures.push({ mode, sourceMaps: false, modules, originalUtilityReachable: false });
      } }],
    build: { write: false, minify: false, sourcemap: false, target: "es2022", lib: { entry: virtual, formats: ["es"] },
      rollupOptions: { output: { inlineDynamicImports: true }, onwarn(warning) { warnings.push({ code: warning.code, message: warning.message }); } } },
  });
  const output = (Array.isArray(bundle) ? bundle : [bundle]).flatMap((item) => item.output);
  bundleArtifacts.push(...output.map((item) => ({ mode, fileName: item.fileName,
    bytes: Buffer.byteLength(item.type === "chunk" ? item.code : item.source),
    sha256: sha(item.type === "chunk" ? item.code : item.source) })));
  const chunks = output.filter((item) => item.type === "chunk");
  assert.equal(chunks.length, 1);
  assert.deepEqual(chunks[0].imports, []);
  assert.deepEqual(chunks[0].dynamicImports, []);
  assert.ok(!/\bprocess\.env\.NODE_ENV\b/.test(chunks[0].code), "browser bundle retains Node-only NODE_ENV");
  assert.ok(!/\beval\s*\(|\bnew\s+Function\s*\(/.test(chunks[0].code), "CSP unsafe code generation in bundle");
  assert.ok(!/(?:import|export)\s+(?:[^;]*?from\s*)?["']https?:/.test(chunks[0].code), "remote module in bundle");
  const css = output.filter((item) => item.type === "asset" && item.fileName.endsWith(".css"));
  assert.ok(css.every((item) => !/url\(\s*["']?https?:|@import\s+["']https?:/i.test(String(item.source))));
  record(`vite_local_bundle_csp_static_${mode}`, "pass", `in-memory ${chunks[0].code.length} chars; one local JS, zero external/dynamic imports; no eval/new Function/remote CSS; hashed module closure excludes original utility`);
  // Import in-memory bundle as a Node module under jsdom guards, not eval().
  // data: is only a harness loader; production CSP permits local script assets.
  phase = "bundled-runtime";
  const bundled = await import("data:text/javascript;base64," + Buffer.from(chunks[0].code).toString("base64"));
  const bundledStore = { subscribe, getSnapshot: () => snapshot, onNew: async () => { calls.onNew++; }, onCancel: async () => { calls.onCancel++; } };
  const beforeBundle = calls.onNew;
  // Production React act is unavailable in the bundled copy; allow its scheduler
  // two bounded event-loop turns, checking actual rendered DOM and runtime.
  replace(globalThis, "IS_REACT_ACT_ENVIRONMENT", false);
  const localRoot = bundled.mount(document.getElementById("probe"), bundledStore);
  for (let turn = 0; turn < 20 && !bundledStore.runtime; turn++) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.ok(bundledStore.runtime);
  assert.equal(document.getElementById("probe").textContent, "local bundled probe");
  assert.equal(calls.onNew, beforeBundle);
  localRoot.unmount();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(listeners.size, 0);
  record(`vite_bundle_guarded_render_unmount_${mode}`, "pass", "actual bundled hook/provider rendered and unmounted; no network/eval/cloud/history/upload configured");
  const nativeIndex = args.indexOf("--native-python-env");
  if (nativeIndex < 0) {
    record(`native_webview2_csp_${mode}`, "skipped", "use --native-python-env build/assistant-sdk-probe-20261012 for component probe; not full E");
    record(`native_real_consumer_layout_${mode}`, "skipped", "native host not requested; jsdom is not layout evidence");
  } else {
    const pythonEnv = path.resolve(args[nativeIndex + 1]);
    assert.notEqual(pythonEnv, path.join(root, ".desktop-venv"), "isolated native env required");
    const nativeEntry = `let mount,runRefProbe;
      const send = (result) => window.chrome.webview.postMessage({c1:true,...result});
      const check=(value,message)=>{if(!value)throw new Error(message)};
      const delay=()=>new Promise(resolve=>setTimeout(resolve,10));
      const listeners=new Set(); let state={messages:[],isRunning:false}; let sends=0,cancels=0;
      const publish=value=>{state=value;for(const listener of listeners)listener()};
      const browserErrors=[],cspViolations=[],timerSamples=[];let expectedEvalDenials=0,refProbe;
      const localDiagnosticURI=value=>{
        value=String(value||'');
        if(['','inline','eval','self'].includes(value))return value;
        return ['index.html','entry.mjs','bundle.mjs','favicon.ico'].some(name=>value==='https://native-probe.invalid/'+name)?value:'[unapproved-resource]';
      };
      const diagnostics={hidden:document.hidden,visibilityState:document.visibilityState,timerSamples};
      window.addEventListener('error',event=>browserErrors.push(event.message));
      window.addEventListener('unhandledrejection',event=>browserErrors.push(String(event.reason)));
      window.addEventListener('securitypolicyviolation',event=>{
        if(event.blockedURI==='eval'&&event.effectiveDirective==='script-src'&&expectedEvalDenials===0)expectedEvalDenials++;
        else cspViolations.push({directive:String(event.effectiveDirective).slice(0,80),blockedURI:localDiagnosticURI(event.blockedURI),sourceFile:localDiagnosticURI(event.sourceFile),line:event.lineNumber,column:event.columnNumber});
      });
      const originalConsoleError=console.error;
      console.error=(...values)=>{browserErrors.push(values.map(String).join(' '));originalConsoleError.apply(console,values)};
      const store={ownerCleanup:true,ownerCleanups:0,subscribe(listener){listeners.add(listener);return()=>listeners.delete(listener)},getSnapshot:()=>state,
        onNew:async()=>{sends++;publish({...state,isRunning:true})},
        onCancel:async()=>{cancels++;publish({...state,isRunning:false})}};
      try {
        let evalBlocked=false;try{new Function('return 1')()}catch(error){evalBlocked=error instanceof EvalError}
        check(evalBlocked,'CSP code generation not blocked');
        let networkAttempts=0,evalAttempts=0;
        const denied=()=>{networkAttempts++;throw new Error('network forbidden')};
        for(const name of ['fetch','WebSocket','XMLHttpRequest','EventSource'])window[name]=denied;
        navigator.sendBeacon=denied;
        const guarded=new Proxy(Function,{apply(){evalAttempts++;throw new Error('eval forbidden')},construct(){evalAttempts++;throw new Error('eval forbidden')}});
        Function.prototype.constructor=guarded;window.Function=guarded;window.eval=()=>{evalAttempts++;throw new Error('eval forbidden')};
        ({mount,runRefProbe}=await import('./bundle.mjs'));
        for(let i=0;i<2;i++){const start=performance.now();await delay();timerSamples.push(performance.now()-start)}
        refProbe=await runRefProbe(document.getElementById('ref-probe'),${JSON.stringify(mode)});
        check(refProbe&&refProbe.status==='pass','native real consumer/layout fixture did not pass');
        let view=mount(document.getElementById('probe'),store);
        for(let i=0;i<100&&!store.runtime;i++)await delay();check(store.runtime,'native mount missing');check(sends===0,'implicit mount send');
        store.runtime.thread.append('public fake');for(let i=0;i<100&&!state.isRunning;i++)await delay();check(sends===1,'explicit onNew missing');
        store.runtime.thread.cancelRun();for(let i=0;i<100&&state.isRunning;i++)await delay();check(cancels===1&&!state.isRunning,'onCancel missing');
        view.unmount();await delay();check(listeners.size===0,'unmount subscription leaked');
        view=mount(document.getElementById('probe'),store);await delay();await delay();check(sends===1&&cancels===1,'remount implicit send/cancel');
        store.runtime.thread.append('public fake owner cleanup');
        for(let i=0;i<100&&!state.isRunning;i++)await delay();check(sends===2&&state.isRunning,'owner cleanup run missing');
        view.unmount();await delay();check(store.ownerCleanups===1&&cancels===2&&!state.isRunning&&listeners.size===0,'native owner cleanup failed');
        check(!networkAttempts&&!evalAttempts,'runtime outbound/codegen attempt');
        check(expectedEvalDenials===1,'intentional CSP eval denial missing');
        check(!browserErrors.length&&!cspViolations.length,'browser error or unexpected CSP violation');
        send({status:'pass',mode:${JSON.stringify(mode)},refProbe,runtimeProbe:{explicitSends:sends,cancels,ownerCleanups:store.ownerCleanups,subscriptions:listeners.size,implicitMountRemountSends:0},detail:'actual WebView2 real consumer/layout and bundled render/onNew/explicit onCancel/owner cleanup/unmount/remount; strict self CSP blocks Function; zero runtime network/eval',evalBlocked,networkAttempts,evalAttempts,browserErrors,cspViolations,expectedEvalDenials,diagnostics});
      }catch(error){send({status:'fail',mode:${JSON.stringify(mode)},refProbe,detail:error.name+': '+error.message,browserErrors,cspViolations,expectedEvalDenials,diagnostics})}
    `;
    const nativeHtml = '<!doctype html><html><head><meta charset="utf-8"></head><body><div id="probe"></div><div id="ref-probe"></div><script type="module" src="./entry.mjs"></script></body></html>';
    const nativeArea = path.join(root, "build/assistant-native-ui-gate-20261012");
    for (const part of ["temp", "profile", "appdata", "programdata"])
      fs.mkdirSync(path.join(nativeArea, part), { recursive: true });
    const child = spawnSync(path.join(pythonEnv, "Scripts/python.exe"), ["-B", path.join(root, "scripts/verify_assistant_dependencies.py"),
      "--isolated", "--fake-only", "--native-ui-host"], { cwd: root, windowsHide: true, timeout: 50000,
      env: { SystemDrive: "C:", SystemRoot: "C:\\Windows", PROGRAMDATA: path.join(nativeArea, "programdata"),
        USERPROFILE: path.join(nativeArea, "profile"), APPDATA: path.join(nativeArea, "appdata"),
        LOCALAPPDATA: path.join(nativeArea, "appdata"), TEMP: path.join(nativeArea, "temp"), TMP: path.join(nativeArea, "temp"),
        PYTHONUTF8: "1", PYTHONDONTWRITEBYTECODE: "1" },
      input: JSON.stringify({ bundle: chunks[0].code, entry: nativeEntry, html: nativeHtml }), encoding: "utf8", maxBuffer: 4_000_000 });
    try { nativeReport = JSON.parse(child.stdout); }
    catch { nativeReport = { status: "fail", detail: child.error?.code || "native host did not return valid JSON", stderr: child.stderr?.slice(-1500) }; }
    nativeReports.push({ mode, exit: child.status, signal: child.signal, error: child.error?.code ?? null, report: nativeReport });
    const nativePassed = child.status === 0 && nativeReport.status === "pass" && nativeReport.mode === mode;
    record(`native_webview2_csp_${mode}`, nativePassed ? "pass" : "fail", nativeReport.detail);
    record(`native_real_consumer_layout_${mode}`, nativePassed && nativeReport.refProbe?.status === "pass" ? "pass" : "fail", nativeReport.refProbe ?? nativeReport.detail);
  }
  }
}

try {
  await runAssistantProbes();
} catch (error) {
  record("react_or_bundle_harness", "fail", `${error.name}: ${error.message}`);
} finally {
  const runtimeCodegen = codegenBlocked.filter((value) => value !== "vite-build-tool");
  record("no_network_or_eval", blocked.length === 0 && runtimeCodegen.length === 0 ? "pass" : "fail",
    `network attempts=${blocked.length}; runtime eval attempts=${runtimeCodegen.length}; Vite tool codegen attempts denied=${codegenBlocked.length - runtimeCodegen.length}`);
  dom?.window.close();
  for (const restore of restorers.reverse()) restore();
  syncBuiltinESMExports();
}
for (const probe of ["installed_candidate_versions", "nanoid_node_engine", "mit_license_notices",
  "guarded_external_runtime_import", "controlled_onNew_onCancel_stream", "bare_unmount_cancel_contract",
  "owner_cleanup_strictmode_remount", ...["production", "development"].flatMap(mode =>
    [`vite_local_bundle_csp_static_${mode}`, `vite_bundle_guarded_render_unmount_${mode}`, `native_webview2_csp_${mode}`, `native_real_consumer_layout_${mode}`])]) {
  if (!results.some((item) => item.probe === probe)) record(probe, "skipped", "not executed after harness failure; no pass inferred");
}
const finalReport = { scope: "C1 fake isolated only", node: process.versions.node, environment: env,
  lock_sha256: sha(lockBytes), preflight, graph, notices, bundleArtifacts, moduleClosures, nativeReports,
  scopeLimitations: "isolated C1 component only; original utility artifacts remain historical and are not shipped by this probe; no production promotion/full application/wheel/EXE PASS; no production source maps generated; owner explicit cancellation required",
  warnings: [...new Set(warnings.map((item) => item.code))].map((code) => ({ code,
    count: warnings.filter((item) => item.code === code).length,
    sample: warnings.find((item) => item.code === code).message })), probes: results };
if (args.includes("--native-python-env")) {
  const reportArea = path.join(root, "build/assistant-native-ui-gate-20261012");
  fs.mkdirSync(reportArea, { recursive: true });
  finalReport.reportPath = path.join(reportArea, `c1-native-${Date.now()}-${process.pid}.json`);
  // Every native run retains its full closure and failed samples; never replace
  // an earlier report with a later PASS or discard unexpected CSP violations.
  fs.writeFileSync(finalReport.reportPath, JSON.stringify(finalReport, null, 2), { flag: "wx" });
}
console.log(JSON.stringify(finalReport, null, 2));
process.exitCode = results.some((item) => item.status === "fail") ? 1 : 0;
