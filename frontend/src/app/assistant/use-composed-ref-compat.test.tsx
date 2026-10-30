import { act, startTransition, StrictMode, Suspense, version, type ReactNode, type RefCallback, type RefObject } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { version as domVersion } from 'react-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Independently specified from the approved lifecycle table and licensed
// react-textarea-autosize's useComposedRef(libRef, userRef) call sites.
// This harness signature is NOT declaration acceptance: index.d.ts and actual
// consumer compilation (including rejected assignment-expression returns) remain pending.
type Node = HTMLTextAreaElement;
type ObjectRef = RefObject<Node | null>;
type Composed = (node: Node | null) => void | (() => void);
type Hook = (internal: ObjectRef | RefCallback<Node>, forwarded?: ObjectRef | RefCallback<Node> | null) => Composed;
let useCandidate: Hook;
let root: Root | undefined;
let host: HTMLDivElement;
let composed: Composed;
const internalRef = (): ObjectRef => ({ current: null });
const environment = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean };
let previousActEnvironment: boolean | undefined;

function Harness({ internal, forwarded, nodeKey = 'a', suspend = false }: {
  internal: ObjectRef | RefCallback<Node>;
  forwarded?: ObjectRef | RefCallback<Node> | null;
  nodeKey?: string;
  suspend?: boolean;
}) {
  const ref = useCandidate(internal, forwarded);
  composed = ref;
  if (suspend) throw neverResolves;
  return <textarea key={nodeKey} ref={ref} defaultValue="abcdef" />;
}
const neverResolves = new Promise<void>(() => {});
function render(element: ReactNode) { act(() => { root!.render(element); }); }
function unmount() { act(() => { root!.unmount(); }); root = undefined; }
function release(value: ReturnType<Composed>): () => void {
  expect(value, 'each attachment supplies React 19 cleanup').toBeTypeOf('function');
  return value as () => void;
}

beforeEach(async () => {
  previousActEnvironment = environment.IS_REACT_ACT_ENVIRONMENT;
  // Deferred import: a missing candidate is an ordinary failing assertion,
  // never a collection-time import error or an upstream-hook fallback.
  const candidatePath = '../../../packages/use-composed-ref-compat/index.mjs';
  let loaded: { default?: Hook } | undefined;
  try { loaded = await import(/* @vite-ignore */ candidatePath); }
  catch (error) { expect.fail(`Local compatibility candidate unavailable: ${String(error)}`); }
  expect(loaded?.default, 'local candidate default hook').toBeTypeOf('function');
  useCandidate = loaded!.default!;
  expect(version).toBe('19.1.1');
  expect(domVersion).toBe('19.1.1');
  environment.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  if (root) unmount();
  host?.remove();
  environment.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
});

describe('local ref compatibility: commit ownership', () => {
  it.each([false, true])('internal object precedes callback; returned cleanup=%s', (hasCleanup) => {
    const internal = internalRef();
    const events: string[] = [];
    const forwarded: RefCallback<Node> = node => {
      expect(internal.current).toBe(node);
      events.push(node ? 'setup' : 'null');
      if (node && hasCleanup) return () => {
        expect(internal.current).toBeNull(); events.push('cleanup');
      };
    };
    render(<Harness internal={internal} forwarded={forwarded} />);
    expect(internal.current).toBe(host.querySelector('textarea'));
    unmount();
    expect(internal.current).toBeNull();
    expect(events).toEqual(['setup', hasCleanup ? 'cleanup' : 'null']);
  });

  it.each([undefined, null])('missing forwarded ref %s and duplicate object identity', forwarded => {
    let current: Node | null = null;
    const writes: (Node | null)[] = [];
    const internal: ObjectRef = {
      get current() { return current; },
      set current(value) { current = value; writes.push(value); },
    };
    render(<Harness internal={internal} forwarded={forwarded} />);
    expect(internal.current).toBeInstanceOf(HTMLTextAreaElement);
    const node = internal.current;
    writes.length = 0;
    render(<Harness internal={internal} forwarded={internal} />);
    expect(internal.current).toBe(host.querySelector('textarea'));
    expect(writes).toEqual([null, node]);
    unmount(); expect(internal.current).toBeNull();
    expect(writes).toEqual([null, node, null]);
  });

  it('duplicate callback identity acquires and releases once', () => {
    const cleanup = vi.fn();
    const callback = vi.fn<RefCallback<Node>>(node => { if (node) return cleanup; });
    render(<Harness internal={callback} forwarded={callback} />);
    expect(callback).toHaveBeenCalledTimes(1);
    unmount(); expect(cleanup).toHaveBeenCalledTimes(1);
    expect(callback).toHaveBeenCalledTimes(1);
  });

  it('stable inputs preserve callback, focus, selection and setup', () => {
    const internal = internalRef();
    const callback = vi.fn<RefCallback<Node>>(() => {});
    render(<Harness internal={internal} forwarded={callback} />);
    const first = composed;
    const node = internal.current!;
    node.focus(); node.setSelectionRange(2, 4);
    render(<Harness internal={internal} forwarded={callback} />);
    expect(composed).toBe(first);
    expect(internal.current).toBe(node);
    expect(document.activeElement).toBe(node);
    expect([node.selectionStart, node.selectionEnd]).toEqual([2, 4]);
    expect(callback).toHaveBeenCalledTimes(1);
  });

  it('changing forwarded and internal identities releases captured owners once', () => {
    const a = internalRef(), b = internalRef(), object = internalRef();
    const cleanupA = vi.fn(() => { expect(a.current).toBeNull(); });
    const callbackA = vi.fn<RefCallback<Node>>(node => { if (node) return cleanupA; });
    const callbackB = vi.fn<RefCallback<Node>>(() => {});
    render(<Harness internal={a} forwarded={callbackA} />);
    const node = a.current;
    render(<Harness internal={a} forwarded={callbackB} />);
    expect(cleanupA).toHaveBeenCalledTimes(1);
    expect(callbackA).toHaveBeenCalledTimes(1);
    expect(callbackB).toHaveBeenCalledWith(node);
    render(<Harness internal={b} forwarded={object} />);
    expect(a.current).toBeNull();
    expect(callbackB.mock.calls.map(([value]) => value)).toEqual([node, null]);
    expect(b.current).toBe(node); expect(object.current).toBe(node);
    unmount(); expect(b.current).toBeNull(); expect(object.current).toBeNull();
    expect(cleanupA).toHaveBeenCalledTimes(1);
  });

  it('node replacement and delayed old cleanup cannot clear the new attachment', () => {
    const internal = internalRef(), forwarded = internalRef();
    render(<Harness internal={internal} forwarded={forwarded} />);
    const ref = composed, old = internal.current!;
    render(<Harness internal={internal} forwarded={forwarded} nodeKey="b" />);
    expect(internal.current).not.toBe(old);
    expect(forwarded.current).toBe(internal.current);
    unmount();
    const cleanupA = release(ref(old));
    cleanupA();
    const next = document.createElement('textarea');
    const cleanupB = release(ref(next));
    cleanupA();
    expect(internal.current).toBe(next); expect(forwarded.current).toBe(next);
    cleanupB(); expect(internal.current).toBeNull(); expect(forwarded.current).toBeNull();
  });

  it('null before attachment and repeated null/cleanup never invent or double release', () => {
    const internal = internalRef(), cleanup = vi.fn();
    const callback = vi.fn<RefCallback<Node>>(node => { if (node) return cleanup; });
    render(<Harness internal={internal} forwarded={callback} />);
    const ref = composed;
    unmount(); callback.mockClear(); cleanup.mockClear();
    expect(ref(null)).toBeUndefined(); expect(ref(null)).toBeUndefined();
    expect(callback).not.toHaveBeenCalled();
    const done = release(ref(document.createElement('textarea')));
    expect(ref(null)).toBeUndefined(); done(); done(); ref(null);
    expect(callback).toHaveBeenCalledTimes(1); expect(cleanup).toHaveBeenCalledTimes(1);
    expect(internal.current).toBeNull();
  });

  it('StrictMode commits and remount balance each acquired resource', () => {
    const internal = internalRef();
    const cleanups: ReturnType<typeof vi.fn>[] = [];
    let active = 0;
    const callback: RefCallback<Node> = node => {
      expect(node).not.toBeNull(); active++;
      const cleanup = vi.fn(() => { active--; }); cleanups.push(cleanup); return cleanup;
    };
    render(<StrictMode><Harness internal={internal} forwarded={callback} /></StrictMode>);
    expect(cleanups.length).toBeGreaterThanOrEqual(2); expect(active).toBe(1);
    render(null); expect(active).toBe(0); expect(internal.current).toBeNull();
    render(<StrictMode><Harness internal={internal} forwarded={callback} /></StrictMode>);
    expect(active).toBe(1); unmount(); expect(active).toBe(0);
    for (const cleanup of cleanups) expect(cleanup).toHaveBeenCalledTimes(1);
    expect(internal.current).toBeNull();
  });

  it('abandoned changed-ref render has no side effects on committed ownership', async () => {
    const a = internalRef(), b = internalRef(), cleanup = vi.fn();
    const committed: RefCallback<Node> = node => { if (node) return cleanup; };
    const abandoned = vi.fn<RefCallback<Node>>(() => {});
    const tree = (suspend: boolean) => <Suspense fallback={<span>pending</span>}>
      <Harness internal={suspend ? b : a} forwarded={suspend ? abandoned : committed} suspend={suspend} />
    </Suspense>;
    render(tree(false)); const node = a.current;
    await act(async () => { startTransition(() => { root!.render(tree(true)); }); });
    expect(b.current).toBeNull(); expect(abandoned).not.toHaveBeenCalled();
    expect(a.current).toBe(node); expect(cleanup).not.toHaveBeenCalled();
    render(tree(false));
    expect(a.current).toBe(node);
    unmount(); expect(cleanup).toHaveBeenCalledTimes(1);
  });

  it('setup failure rolls back acquired refs and remains observable', () => {
    const internal = internalRef(), error = new Error('setup failed');
    let throwing = false;
    const callback: RefCallback<Node> = node => { if (node && throwing) throw error; };
    render(<Harness internal={internal} forwarded={callback} />);
    const ref = composed; unmount(); throwing = true;
    expect(() => ref(document.createElement('textarea'))).toThrow(error);
    expect(internal.current).toBeNull(); expect(ref(null)).toBeUndefined();
  });

  it('cleanup failure releases remaining owners, surfaces error and consumes record', () => {
    const error = new Error('cleanup failed'), remaining = vi.fn();
    let throwing = false;
    const first: RefCallback<Node> = node => { if (node) return () => { if (throwing) throw error; }; };
    const second: RefCallback<Node> = node => { if (node) return remaining; };
    render(<Harness internal={first} forwarded={second} />);
    const ref = composed; unmount(); remaining.mockClear();
    const done = release(ref(document.createElement('textarea'))); throwing = true;
    expect(done).toThrow(error); expect(remaining).toHaveBeenCalledTimes(1);
    expect(() => { done(); ref(null); }).not.toThrow();
    expect(remaining).toHaveBeenCalledTimes(1);
  });
});

it.todo('actual licensed textarea composer: resolver fixture pending; typing/focus/selection/autosize, ref swap and panel remount');
it.todo('index.d.ts generic and React cleanup-return rejection through actual consumer compile: local declarations/resolver pending');
