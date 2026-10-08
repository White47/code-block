import { describe, expect, it, vi } from 'vitest';
import { SourceDocumentStore, sourceDocumentKey } from './sourceDocuments';
import { anchor, session } from './testFixtures';
import type { EditableSource, SaveSourceRequest } from '../types';

const project = session.projectPath!;
const baseline: EditableSource = { projectPath: project, filePath: anchor.filePath, text: 'value = 1',
  revision: 'opening-1:raw-bytes-1:identity-1', encoding: 'utf-8', newline: 'LF', readOnlyReason: null };
const key = sourceDocumentKey(project, anchor.filePath);
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(finish => { resolve = finish; }); return { promise, resolve };
}
function setup() {
  const open = vi.fn(async (projectPath: string, location: typeof anchor): Promise<EditableSource> => ({
    ...baseline, projectPath, filePath: location.filePath,
  }));
  const save = vi.fn(async (request: SaveSourceRequest): Promise<EditableSource> => ({
    ...baseline, ...request, revision: 'opening-1:raw-bytes-2:identity-2',
  }));
  return { open, save, store: new SourceDocumentStore(open, save) };
}
describe('project/file shared source drafts', () => {
  it('deduplicates pending opens across two anchors and exposes one shared draft', async () => {
    const { store, open } = setup(); const pending = deferred<EditableSource>();
    open.mockImplementationOnce(() => pending.promise);
    const first = store.open(project, anchor);
    const second = store.open(project, { ...anchor, startLine: 2, endLine: 3 });
    expect(open).toHaveBeenCalledOnce();
    pending.resolve(baseline); expect(await first).toBe(await second);
    store.update(key, 'value = 2');
    expect((await store.open(project, { ...anchor, startLine: 5, endLine: 5 })).text).toBe('value = 2');
    expect(open).toHaveBeenCalledOnce(); expect(store.keys()).toEqual([key]);
  });
  it('isolates same relative paths in different projects and different files in one project', async () => {
    const { store } = setup(); await store.open(project, anchor);
    await store.open('E:\\project-b', anchor); await store.open(project, { ...anchor, filePath: 'other.py' });
    store.update(key, 'only A/model.py');
    expect(store.get(sourceDocumentKey('E:\\project-b', anchor.filePath))?.text).toBe(baseline.text);
    expect(store.get(sourceDocumentKey(project, 'other.py'))?.text).toBe(baseline.text);
  });
  it('retains in-flight input, updates only the saved baseline and saves the next draft against its new revision', async () => {
    const { store, save } = setup(); await store.open(project, anchor); store.update(key, 'value = 2');
    const pending = deferred<EditableSource>(); save.mockImplementationOnce(() => pending.promise);
    const saving = store.save(key); await Promise.resolve();
    expect(save).toHaveBeenCalledWith({ projectPath: project, filePath: anchor.filePath, text: 'value = 2', revision: baseline.revision });
    store.update(key, 'value = 3');
    pending.resolve({ ...baseline, text: 'value = 2', revision: 'opening-1:raw-bytes-2:identity-2' });
    await saving;
    expect(store.get(key)).toMatchObject({ text: 'value = 3', baselineText: 'value = 2', dirty: true, saving: false });
    await store.save(key);
    expect(save).toHaveBeenLastCalledWith({ projectPath: project, filePath: anchor.filePath,
      text: 'value = 3', revision: 'opening-1:raw-bytes-2:identity-2' });
  });
  it('deduplicates save requests while keeping the later draft dirty', async () => {
    const { store, save } = setup(); await store.open(project, anchor); store.update(key, 'value = 2');
    const pending = deferred<EditableSource>(); save.mockImplementationOnce(() => pending.promise);
    const first = store.save(key); store.update(key, 'value = 3'); const second = store.save(key);
    expect(second).toBe(first); await Promise.resolve(); expect(save).toHaveBeenCalledOnce();
    pending.resolve({ ...baseline, text: 'value = 2', revision: 'saved-2' }); await first;
    expect(store.get(key)).toMatchObject({ text: 'value = 3', dirty: true });
  });
  it.each(['SOURCE_CONFLICT', 'SOURCE_PROJECT_CHANGED', 'SOURCE_READ_ONLY'])('%s preserves text and baseline revision', async code => {
    const { store, save } = setup(); await store.open(project, anchor); store.update(key, 'draft');
    const error = new Error(`${code}: save rejected`); save.mockRejectedValueOnce(error);
    await expect(store.save(key)).rejects.toBe(error);
    expect(store.get(key)).toMatchObject({ text: 'draft', baselineText: baseline.text, revision: baseline.revision,
      dirty: true, saving: false, error });
  });
  it('does not save on update, append a final newline, or include encoding metadata in the request', async () => {
    const { store, save } = setup(); await store.open(project, anchor); store.update(key, 'no_final_newline = True');
    expect(save).not.toHaveBeenCalled(); await store.save(key);
    expect(save.mock.calls).toEqual([[{ projectPath: project, filePath: anchor.filePath,
      text: 'no_final_newline = True', revision: baseline.revision }]]);
    expect(store.get(key)).toMatchObject({ text: 'no_final_newline = True', dirty: false });
  });
  it('refuses edits and saves for a read-only document', async () => {
    const { store, open, save } = setup(); open.mockResolvedValueOnce({ ...baseline, newline: 'Mixed', readOnlyReason: 'Mixed newlines' });
    await store.open(project, anchor); store.update(key, 'unsafe edit');
    expect(store.get(key)?.text).toBe(baseline.text); await expect(store.save(key)).rejects.toThrow('Mixed newlines');
    expect(save).not.toHaveBeenCalled();
  });
  it('refuses release/discard while saving even if input returns to the old baseline', async () => {
    const { store, save } = setup(); await store.open(project, anchor); store.update(key, 'draft');
    const pending = deferred<EditableSource>(); save.mockImplementationOnce(() => pending.promise);
    const saving = store.save(key); store.update(key, baseline.text);
    expect(store.get(key)).toMatchObject({ dirty: false, saving: true });
    expect(() => store.release(key)).toThrow(); expect(() => store.discard(key)).toThrow();
    pending.resolve({ ...baseline, text: 'draft', revision: 'saved-2' }); await saving;
    expect(store.get(key)).toMatchObject({ text: baseline.text, baselineText: 'draft', dirty: true });
  });
  it('serializes native saves across files', async () => {
    const { store, save } = setup(); const other = { ...anchor, filePath: 'other.py' };
    await store.open(project, anchor); await store.open(project, other);
    store.update(key, 'draft A'); store.update(sourceDocumentKey(project, other.filePath), 'draft B');
    const pending = deferred<EditableSource>(); save.mockImplementationOnce(() => pending.promise);
    const first = store.save(key); const second = store.save(sourceDocumentKey(project, other.filePath));
    await Promise.resolve(); expect(save).toHaveBeenCalledOnce();
    pending.resolve({ ...baseline, text: 'draft A', revision: 'saved-A' }); await first; await second;
    expect(save.mock.calls.map(([request]) => request.filePath)).toEqual([anchor.filePath, other.filePath]);
  });
  it('rejects a response for another project rather than installing it under the requested key', async () => {
    const { store, open } = setup(); open.mockResolvedValueOnce({ ...baseline, projectPath: 'E:\\project-b' });
    await expect(store.open(project, anchor)).rejects.toThrow('SOURCE_PROJECT_CHANGED');
    expect(store.get(key)).toBeUndefined();
  });
  it('cannot resurrect a released view from a late document-open response', async () => {
    const { store, open } = setup(); const pending = deferred<EditableSource>();
    open.mockImplementationOnce(() => pending.promise);
    const opening = store.open(project, anchor); void opening.catch(() => {});
    store.release(key); pending.resolve(baseline);
    await opening.catch(() => {});
    expect(store.get(key), 'late opening completion must not recreate a released document').toBeUndefined();
  });
  it('A-B-A reopening does not reuse an old in-flight opening epoch', async () => {
    const { store, open } = setup(); const pending = deferred<EditableSource>();
    open.mockImplementationOnce(() => pending.promise);
    const oldOpening = store.open(project, anchor); void oldOpening.catch(() => {});
    store.release(key); await store.open('E:\\project-b', anchor);
    const reopened = store.open(project, anchor);
    try {
      expect(open).toHaveBeenCalledTimes(3);
      expect((await reopened).revision).toBe(baseline.revision);
    } finally { pending.resolve({ ...baseline, revision: 'obsolete-opening-epoch' }); await oldOpening.catch(() => {}); }
    expect(store.get(key)?.revision).not.toBe('obsolete-opening-epoch');
  });
});
