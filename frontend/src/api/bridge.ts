import type { AllBridgeMethods as BridgeMethods, Envelope } from '../types';
type Api = Partial<{ [K in keyof BridgeMethods]: (...args: BridgeMethods[K]['args']) => Promise<Envelope<BridgeMethods[K]['data']>> }>;
// A frontend deadline cannot cancel native mutations. Keep their callers and
// the project write queue pending until Python actually returns.
const projectMutations: ReadonlySet<keyof BridgeMethods> = new Set<keyof BridgeMethods>([
  'choose_project', 'open_project', 'analyze', 'save_layout', 'move_node',
  'save_viewport', 'expand', 'collapse', 'open_detail_island',
  'move_detail_island', 'close_detail_island', 'set_analysis_settings',
  'save_source_document', 'approve_close', 'cancel_close',
  'create_project_entry', 'prepare_project_entry_operation', 'apply_project_entry_operation',
  'move_summary_node', 'save_summary_viewport',
]);
declare global { interface Window { pywebview?: { api?: Api }; __codevizCloseReady?: boolean } }
export class BridgeError extends Error {
  constructor(public code: string, message: string) { super(message); this.name = 'BridgeError'; }
}
function populatedApi(): Api | undefined {
  const api = window.pywebview?.api;
  return api && Object.values(api).some(value => typeof value === 'function') ? api : undefined;
}
export function waitForBridge(timeout = 5000): Promise<Api> {
  const api = populatedApi();
  if (api) return Promise.resolve(api);
  return new Promise((resolve, reject) => {
    // pywebview creates api:{} before installing exposed methods. That object
    // alone is not readiness; the official event still supports an empty API.
    const ready = () => { if (window.pywebview?.api) { cleanup(); resolve(window.pywebview.api); } };
    const check = () => { const api = populatedApi(); if (api) { cleanup(); resolve(api); } };
    const cleanup = () => { clearTimeout(timer); clearInterval(poll); window.removeEventListener('pywebviewready', ready); };
    const timer = setTimeout(() => { cleanup(); reject(new BridgeError('DESKTOP_UNAVAILABLE', 'Desktop bridge did not become ready. Open CodeViz in its native desktop window.')); }, timeout);
    const poll = setInterval(check, 50);
    window.addEventListener('pywebviewready', ready);
    check();
  });
}
export async function call<K extends keyof BridgeMethods>(method: K, ...args: BridgeMethods[K]['args']): Promise<BridgeMethods[K]['data']> {
  const api = await waitForBridge();
  const fn = api[method];
  if (!fn) throw new BridgeError('METHOD_UNAVAILABLE', `Desktop bridge is missing ${method}.`);
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const nativeResponse = (fn as (...values: BridgeMethods[K]['args']) => Promise<Envelope<BridgeMethods[K]['data']>>).apply(api, args);
    const response = await (projectMutations.has(method) ? nativeResponse : Promise.race([
      nativeResponse,
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new BridgeError('BRIDGE_TIMEOUT', `${method} timed out.`)), 15000); }),
    ]));
    if (!response || response.schemaVersion !== 1 || typeof response.ok !== 'boolean') throw new BridgeError('INVALID_RESPONSE', 'Unsupported desktop response.');
    if (!response.ok) throw new BridgeError(response.error?.code ?? 'INVALID_RESPONSE', response.error?.message ?? 'Invalid desktop error.');
    if (!('data' in response)) throw new BridgeError('INVALID_RESPONSE', 'Missing desktop response data.');
    return response.data;
  } catch (error) {
    if (error instanceof BridgeError) throw error;
    throw new BridgeError('BRIDGE_ERROR', error instanceof Error ? error.message : String(error));
  } finally { clearTimeout(timer); }
}
export async function hasMethod(method: keyof BridgeMethods) { return Boolean((await waitForBridge())[method]); }
export const bridge = { call, openProject: (path: string) => call('open_project', path) };
