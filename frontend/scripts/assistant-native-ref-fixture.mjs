// Independently authored from the public React/component contract, not ref
// utility source, declarations, maps or tests. Browser-only; no imports.
// Integration port: pass React and createRoot from ONE pinned 19.1.1 bundle,
// and the REAL default react-textarea-autosize export resolved in the approved
// isolated workspace. Build/run development and production SEPARATELY with an
// exact NODE_ENV substitution; this fixture cannot attest package provenance.
// host is a connected, empty HTMLElement owned by the caller, not its runtime
// root. Call sequentially after installing the existing native CSP/egress guards.
// Parent owns WebView messaging, explicit onNew/onCancel, global console/error/
// CSP observation and the unchanged 15s bootstrap/20s result host deadlines.
// This fixture's total wait budget is 12s, with 1s per observation, not an
// extension of those deadlines. Parent must budget its other work accordingly.
// Strict style-src 'self' disallows style tags/attributes in HTML. React's style
// prop and direct element.style properties use CSSOM (allowed by Chromium CSP).
// No cssText, setAttribute('style'), injected style tag or external resource is
// used. Parent may instead supply an allowlisted local stylesheet, but must not
// weaken CSP. Width/font checks fail if the actual required layout is absent.
// Consumer-internal ref ordering is covered by headless tests, not invented
// component-instance observability here. No model/runtime/provider is accepted.

export async function runNativeRefFixture({ React, createRoot, TextareaAutosize, mode, host }) {
  const assertions = [];
  const roots = new Set();
  const ownedContainers = new Set();
  const rootErrors = [];
  const cleanupErrors = [];
  let primaryError;
  let result;
  const demand = (condition, name) => {
    if (!condition) throw new Error(`native-ref:${name}`);
    if (assertions.length >= 512) throw new Error('native-ref:assertion-bound');
    assertions.push(name);
  };
  demand(mode === 'development' || mode === 'production', 'exact-mode');
  demand(React?.version === '19.1.1', 'pinned-react');
  demand(typeof createRoot === 'function' && TextareaAutosize != null, 'public-api-ports');
  const document = host?.ownerDocument;
  const window = document?.defaultView;
  demand(Boolean(window && host instanceof window.HTMLElement && host.isConnected), 'connected-host');
  demand(host.childNodes.length === 0, 'dedicated-empty-host');
  const clock = () => window.performance.now();
  const deadline = clock() + 12_000;
  const rootError = (error) => {
    // Preserve actual failures for rejection; never turn a React error into PASS.
    if (rootErrors.length < 8) rootErrors.push(error);
  };
  const healthy = () => {
    if (rootErrors.length) throw new AggregateError(rootErrors, 'native-ref:react-root-error');
    if (clock() >= deadline) throw new Error('native-ref:shared-deadline');
  };
  const pause = async () => {
    healthy();
    await new Promise((resolve) => window.setTimeout(resolve, 10));
    healthy();
  };
  const waitFor = async (predicate, name) => {
    const until = Math.min(deadline, clock() + 1_000);
    for (let turn = 0; turn < 100 && clock() < until; turn++) {
      healthy();
      if (predicate()) return;
      await pause();
    }
    throw new Error(`native-ref:timeout:${name}`);
  };
  const settle = async () => { await pause(); await pause(); };
  const scenarios = [];

  try {
    for (const strict of [false, true]) {
      const label = strict ? 'strict' : 'ordinary';
      const check = (value, name) => demand(value, `${label}:${name}`);
      const container = document.createElement('div');
      container.style.display = 'block';
      container.style.width = '260px';
      host.appendChild(container);
      ownedContainers.add(container);
      let root;
      let tick = 0;
      let committed = null;
      let changes = 0;
      const nativeCounts = { input: 0, change: 0 };
      const countNative = (event) => {
        if (event.target instanceof window.HTMLTextAreaElement) nativeCounts[event.type]++;
      };
      container.addEventListener('input', countNative);
      container.addEventListener('change', countNative);
      const events = [];
      const note = (event) => {
        if (events.length >= 64) throw new Error(`native-ref:${label}:event-bound`);
        events.push(event);
      };
      const observers = [];
      const observer = (name, returnsCleanup) => {
        const record = { name, returnsCleanup, setups: 0, releases: 0, nulls: 0, active: null };
        record.ref = (node) => {
          if (node === null) {
            check(!returnsCleanup, `${name}:cleanup-has-no-null`);
            check(record.active !== null, `${name}:null-owns-attachment`);
            record.nulls++;
            record.releases++;
            record.active = null;
            note(`${name}:release`);
            return;
          }
          check(node instanceof window.HTMLTextAreaElement, `${name}:actual-textarea`);
          check(record.active === null, `${name}:no-overlapping-attachment`);
          record.setups++;
          record.active = node;
          note(`${name}:setup`);
          if (returnsCleanup) {
            let released = false;
            return () => {
              check(!released, `${name}:cleanup-once`);
              check(record.active === node, `${name}:cleanup-owns-node`);
              released = true;
              record.releases++;
              record.active = null;
              note(`${name}:release`);
            };
          }
        };
        observers.push(record);
        return record;
      };
      const cleanupA = observer('cleanupA', true);
      const cleanupB = observer('cleanupB', true);
      const userA = observer('userA', false);
      const userB = observer('userB', false);
      const objectA = React.createRef();
      const objectB = React.createRef();
      const style = {
        display: 'block', width: '240px', boxSizing: 'border-box',
        fontFamily: 'monospace', fontSize: '16px', lineHeight: '20px',
        padding: '4px', border: '1px solid black', resize: 'none', overflow: 'hidden',
      };
      function Controlled({ userRef, revision }) {
        const [value, setValue] = React.useState('seed line');
        React.useLayoutEffect(() => { committed = { revision, value }; });
        return React.createElement(TextareaAutosize, {
          ref: userRef, value, minRows: 1, style,
          'aria-label': 'native ref fixture',
          onChange(event) { changes++; setValue(event.target.value); },
        });
      }
      const newRoot = () => {
        root = createRoot(container, {
          onUncaughtError: rootError, onCaughtError: rootError, onRecoverableError: rootError,
        });
        roots.add(root);
      };
      const render = async (userRef) => {
        const revision = ++tick;
        const child = React.createElement(Controlled, { userRef, revision });
        root.render(strict ? React.createElement(React.StrictMode, null, child) : child);
        await waitFor(() => committed?.revision === revision, `${label}:commit`);
        await settle();
        const node = container.querySelector('textarea');
        check(node instanceof window.HTMLTextAreaElement && node.isConnected, 'rendered-native-node');
        return node;
      };
      const balanced = (record, name) => {
        check(record.active === null && record.setups === record.releases, `${name}:balanced`);
        check(record.returnsCleanup ? record.nulls === 0 : record.nulls === record.releases,
          `${name}:release-kind`);
      };
      const snapshotCounts = () => observers.map(({ setups, releases, nulls }) => `${setups}/${releases}/${nulls}`).join(',');
      const unchanged = async (ref, node, name) => {
        node.focus();
        node.setSelectionRange(1, 4, 'backward');
        check(document.activeElement === node, `${name}:focus-before`);
        const before = snapshotCounts();
        const beforeChanges = changes;
        const value = node.value;
        const selection = [node.selectionStart, node.selectionEnd, node.selectionDirection];
        const height = node.clientHeight;
        const rerendered = await render(ref);
        check(rerendered === node && snapshotCounts() === before, `${name}:no-ref-churn`);
        check(document.activeElement === node && node.value === value && changes === beforeChanges,
          `${name}:controlled-focus-value`);
        check(node.selectionStart === selection[0] && node.selectionEnd === selection[1]
          && node.selectionDirection === selection[2], `${name}:selection-preserved`);
        check(node.clientHeight === height, `${name}:height-preserved`);
      };
      const size = (node) => ({ client: node.clientHeight, scroll: node.scrollHeight });
      const type = async (node, value, name) => {
        node.focus();
        const before = changes;
        const inputs = nativeCounts.input;
        const changeEvents = nativeCounts.change;
        // Bypass React's value tracker using the native public DOM setter.
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
        check(typeof setter === 'function', `${name}:native-value-setter`);
        setter.call(node, value);
        node.setSelectionRange(value.length, value.length);
        node.dispatchEvent(new window.Event('input', { bubbles: true }));
        node.dispatchEvent(new window.Event('change', { bubbles: true }));
        await waitFor(() => committed?.value === value && changes > before, `${label}:${name}:controlled-input`);
        await settle();
        check(changes === before + 1, `${name}:one-controlled-change`);
        check(nativeCounts.input === inputs + 1 && nativeCounts.change === changeEvents + 1,
          `${name}:real-bubbling-events`);
        check(node.value === value && document.activeElement === node, `${name}:typing-focus`);
        check(node.selectionStart === value.length && node.selectionEnd === value.length,
          `${name}:typing-selection`);
      };
      const unmount = async () => {
        root.unmount();
        roots.delete(root);
        await settle();
        check(container.childNodes.length === 0, 'unmount-empty');
        check(objectA.current === null && objectB.current === null, 'unmount-object-refs-null');
        for (const record of observers) balanced(record, `${record.name}:unmount`);
      };

      newRoot();
      const node = await render(cleanupA.ref);
      const expectedSetups = strict && mode === 'development' ? 2 : 1;
      check(cleanupA.setups === expectedSetups && cleanupA.releases === expectedSetups - 1
        && cleanupA.nulls === 0 && cleanupA.active === node, 'initial-ref-cycle');
      check(events.join(',') === (expectedSetups === 2
        ? 'cleanupA:setup,cleanupA:release,cleanupA:setup' : 'cleanupA:setup'), 'initial-cycle-order');
      const extraDevelopmentCycle = strict && mode === 'development'
        && cleanupA.setups === 2 && cleanupA.releases === 1;
      if (strict) check(mode === 'development' ? extraDevelopmentCycle : !extraDevelopmentCycle,
        mode === 'development' ? 'extra-development-cycle-proved' : 'no-development-cycle-claimed');
      const computed = window.getComputedStyle(node);
      check(computed.width === '240px' && computed.lineHeight === '20px'
        && computed.fontSize === '16px' && node.clientWidth > 0, 'real-cssom-layout');
      const small = size(node);
      check(small.client > 0 && small.scroll > 0 && Math.abs(small.client - small.scroll) <= 2,
        'initial-real-scroll-client-height');
      await unchanged(cleanupA.ref, node, 'cleanup-stable-rerender');
      const multiline = Array.from({ length: 6 }, (_, index) => `native line ${index + 1}`).join('\n');
      await type(node, multiline, 'multiline');
      const large = size(node);
      check(large.client >= small.client + 80 && large.scroll >= small.scroll + 80
        && Math.abs(large.client - large.scroll) <= 2, 'real-autosize-growth');
      await type(node, 'short line', 'single-line');
      const shrunk = size(node);
      check(shrunk.client < large.client && shrunk.scroll < large.scroll
        && Math.abs(shrunk.client - small.client) <= 2 && Math.abs(shrunk.scroll - small.scroll) <= 2,
        'real-autosize-shrink');
      await unchanged(cleanupA.ref, node, 'typed-stable-rerender');

      const swapCallback = async (previous, next) => {
        const before = events.length;
        const setups = next.setups;
        check(await render(next.ref) === node, `${next.name}:same-dom-on-swap`);
        balanced(previous, `${previous.name}:swap`);
        check(next.active === node && next.setups === setups + 1, `${next.name}:new-ref-attached`);
        check(events.slice(before).join(',') === `${previous.name}:release,${next.name}:setup`,
          `${previous.name}-to-${next.name}:release-before-setup`);
      };
      await swapCallback(cleanupA, cleanupB);
      await unchanged(cleanupB.ref, node, 'cleanupB-stable-rerender');
      await swapCallback(cleanupB, userA);
      await unchanged(userA.ref, node, 'void-stable-rerender');
      await swapCallback(userA, userB);
      check(await render(objectA) === node, 'callback-to-object-same-dom');
      balanced(userB, 'userB:object-swap');
      check(objectA.current === node && objectB.current === null, 'objectA-attached');
      await unchanged(objectA, node, 'object-stable-rerender');
      check(await render(objectB) === node, 'object-swap-same-dom');
      check(objectA.current === null && objectB.current === node, 'old-object-cleared-new-object-attached');
      await unchanged(objectB, node, 'objectB-stable-rerender');
      await unmount();
      check(!node.isConnected, 'old-node-detached');

      const setupsBeforeRemount = cleanupA.setups;
      const releasesBeforeRemount = cleanupA.releases;
      committed = null;
      newRoot();
      const reopened = await render(cleanupA.ref);
      check(reopened !== node && cleanupA.active === reopened, 'remount-new-node-owned');
      check(cleanupA.setups - setupsBeforeRemount === expectedSetups
        && cleanupA.releases - releasesBeforeRemount === expectedSetups - 1,
        'remount-independent-ref-cycle');
      check(committed.value === 'seed line' && changes === 2, 'remount-controlled-reset-no-input');
      await unmount();
      container.removeEventListener('input', countNative);
      container.removeEventListener('change', countNative);
      container.remove();
      ownedContainers.delete(container);
      scenarios.push({
        strict, extraDevelopmentCycle, balancedRefs: true, focusSelectionPreserved: true,
        autosizeGrowth: true, autosizeShrink: true, controlledChanges: changes,
        inputEvents: nativeCounts.input, changeEvents: nativeCounts.change,
        refs: observers.map(({ setups, releases, nulls }) => ({ setups, releases, nulls })),
      });
    }
    healthy();
    demand(host.childNodes.length === 0 && roots.size === 0, 'owned-roots-and-dom-released');
    // No gate status, bridge send or runtime send claim. Parent can report native
    // PASS only after executing this plus its own independent runtime/CSP gates.
    result = { assertions, scenarios };
  } catch (error) {
    primaryError = error;
  } finally {
    for (const root of roots) {
      try { root.unmount(); } catch (error) { cleanupErrors.push(error); }
    }
    roots.clear();
    for (const container of ownedContainers) container.remove();
    ownedContainers.clear();
  }
  if (cleanupErrors.length || rootErrors.length) {
    throw new AggregateError(
      [...(primaryError ? [primaryError] : []), ...rootErrors, ...cleanupErrors],
      'native-ref:failure-with-root-cleanup',
    );
  }
  if (primaryError) throw primaryError;
  return result;
}
