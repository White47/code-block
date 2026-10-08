import { afterEach, describe, expect, it, vi } from 'vitest';
import { call } from './bridge';
import { anchor, ok, session } from '../app/testFixtures';
import type { EditableSource, SaveSourceRequest } from '../types';

const editingCall = call;
const document: EditableSource = { projectPath: session.projectPath!, filePath: anchor.filePath,
  text: 'value = 1', revision: 'epoch-a:bytes-1:identity-1', encoding: 'utf-8', newline: 'LF', readOnlyReason: null };
const request: SaveSourceRequest = { projectPath: document.projectPath, filePath: document.filePath,
  text: 'value = 2', revision: document.revision };
function install(api: Record<string, (...args: never[]) => Promise<unknown>>) {
  // Exercise the real bridge; only Python's transport is simulated.
  window.pywebview = { api } as unknown as NonNullable<Window['pywebview']>;
}
afterEach(() => { delete window.pywebview; vi.useRealTimers(); });

describe('approved source editing wire contract', () => {
  it('opens using exactly projectPath and the unchanged five-field anchor', async () => {
    const open = vi.fn().mockResolvedValue(ok(document));
    install({ open_source_document: open });
    await expect(editingCall('open_source_document', document.projectPath, anchor)).resolves.toEqual(document);
    expect(open.mock.calls).toEqual([[document.projectPath, anchor]]);
  });
  it('saves one exact payload without appending a newline or sending encoding metadata', async () => {
    const save = vi.fn().mockResolvedValue(ok({ ...document, text: request.text, revision: 'epoch-a:bytes-2:identity-2' }));
    install({ save_source_document: save });
    const saved = await editingCall('save_source_document', request);
    expect(save.mock.calls).toEqual([[request]]);
    expect(saved.text).toBe('value = 2');
    expect(saved.revision).not.toBe(request.revision);
  });
  it.each(['success', 'conflict', 'transport failure'] as const)(
    'keeps a native save pending past 15 seconds until its actual %s', async outcome => {
      vi.useFakeTimers();
      let resolve!: (response: unknown) => void; let reject!: (error: Error) => void;
      const native = new Promise((res, rej) => { resolve = res; reject = rej; });
      const save = vi.fn(() => native); install({ save_source_document: save });
      let settled = false;
      const pending = editingCall('save_source_document', request);
      void pending.then(() => { settled = true; }, () => { settled = true; });
      await vi.advanceTimersByTimeAsync(17000);
      try {
        expect(save).toHaveBeenCalledOnce();
        expect(settled, 'a frontend deadline cannot cancel a native source write').toBe(false);
        if (outcome === 'success') {
          resolve(ok(document)); await expect(pending).resolves.toEqual(document);
        } else {
          const failure = expect(pending).rejects.toMatchObject({
            code: outcome === 'conflict' ? 'SOURCE_CONFLICT' : 'BRIDGE_ERROR', message: 'Save failed',
          });
          if (outcome === 'conflict') resolve({ ok: false, schemaVersion: 1,
            error: { code: 'SOURCE_CONFLICT', message: 'Save failed' } });
          else reject(new Error('Save failed'));
          await failure;
        }
      } finally { resolve(ok(document)); }
    },
  );
  it.each(['SOURCE_CONFLICT', 'SOURCE_PROJECT_CHANGED', 'SOURCE_READ_ONLY'])('preserves %s for the UI guard', async code => {
    install({ save_source_document: vi.fn().mockResolvedValue({ ok: false, schemaVersion: 1,
      error: { code, message: 'Source cannot be saved' } }) });
    await expect(editingCall('save_source_document', request)).rejects.toMatchObject({ code, message: 'Source cannot be saved' });
  });
  it.each(['approve_close', 'cancel_close'] as const)('%s forwards one opaque native request nonce', async method => {
    const close = vi.fn().mockResolvedValue(ok(null)); install({ [method]: close });
    await editingCall(method, 'window-epoch:renderer-epoch:nonce');
    expect(close.mock.calls).toEqual([['window-epoch:renderer-epoch:nonce']]);
  });
});
