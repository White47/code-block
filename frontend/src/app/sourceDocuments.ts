import type { Anchor, EditableSource, SaveSourceRequest } from '../types';

export interface SourceDocument extends EditableSource {
  key: string;
  baselineText: string;
  dirty: boolean;
  saving: boolean;
  error: Error | null;
}
export function sourceDocumentKey(projectPath: string, filePath: string): string {
  return JSON.stringify([projectPath, filePath]);
}
export class SourceDocumentStore {
  private documents = new Map<string, SourceDocument>();
  private opening = new Map<string, Promise<SourceDocument>>();
  private openingTokens = new Map<string, symbol>();
  private saving = new Map<string, Promise<SourceDocument>>();
  private listeners = new Set<() => void>();
  private queue: Promise<unknown> = Promise.resolve();
  constructor(
    private openDocument: (projectPath: string, anchor: Anchor) => Promise<EditableSource>,
    private saveDocument: (request: SaveSourceRequest) => Promise<EditableSource>,
  ) {}
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private emit() { this.listeners.forEach(listener => listener()); }
  get(key: string) { return this.documents.get(key); }
  keys() { return [...new Set([...this.documents.keys(), ...this.opening.keys()])]; }
  open(projectPath: string, anchor: Anchor): Promise<SourceDocument> {
    const key = sourceDocumentKey(projectPath, anchor.filePath);
    const existing = this.get(key);
    if (existing) return Promise.resolve(existing);
    const pending = this.opening.get(key);
    if (pending) return pending;
    const token = Symbol(key); this.openingTokens.set(key, token);
    const request = this.openDocument(projectPath, anchor).then(value => {
      if (this.openingTokens.get(key) !== token) throw new Error('SOURCE_PROJECT_CHANGED: Source view was released.');
      if (value.projectPath !== projectPath || value.filePath !== anchor.filePath) throw new Error('SOURCE_PROJECT_CHANGED: Source identity changed.');
      const document = { ...value, key, baselineText: value.text, dirty: false, saving: false, error: null };
      this.documents.set(key, document); this.emit(); return document;
    }).finally(() => { if (this.openingTokens.get(key) === token) { this.opening.delete(key); this.openingTokens.delete(key); } });
    this.opening.set(key, request); return request;
  }
  update(key: string, text: string) {
    const current = this.get(key);
    if (!current || current.readOnlyReason || text === current.text) return;
    this.documents.set(key, { ...current, text, dirty: text !== current.baselineText, error: null }); this.emit();
  }
  save(key: string): Promise<SourceDocument> {
    const pending = this.saving.get(key); if (pending) return pending;
    const current = this.get(key);
    if (!current) return Promise.reject(new Error('Source document is closed.'));
    if (current.readOnlyReason) return Promise.reject(new Error(current.readOnlyReason));
    if (!current.dirty) return Promise.resolve(current);
    const request: SaveSourceRequest = { projectPath: current.projectPath, filePath: current.filePath, text: current.text, revision: current.revision };
    this.documents.set(key, { ...current, saving: true, error: null }); this.emit();
    const result = this.queue.then(async () => {
      try {
        const saved = await this.saveDocument(request);
        if (saved.projectPath !== request.projectPath || saved.filePath !== request.filePath) throw new Error('SOURCE_PROJECT_CHANGED: Saved source identity changed.');
        const latest = this.get(key)!;
        const next = { ...latest, revision: saved.revision, encoding: saved.encoding, newline: saved.newline,
          readOnlyReason: saved.readOnlyReason, baselineText: saved.text,
          text: latest.text === request.text ? saved.text : latest.text,
          dirty: latest.text === request.text ? false : latest.text !== saved.text, saving: false, error: null };
        this.documents.set(key, next); this.emit(); return next;
      } catch (error) {
        const latest = this.get(key)!;
        this.documents.set(key, { ...latest, saving: false, error: error instanceof Error ? error : new Error(String(error)) }); this.emit(); throw error;
      }
    }).finally(() => { this.saving.delete(key); });
    this.saving.set(key, result); this.queue = result.catch(() => {}); return result;
  }
  discard(key: string) {
    const current = this.get(key); if (!current) return;
    if (current.saving) throw new Error('Wait for source saving to finish.');
    this.documents.set(key, { ...current, text: current.baselineText, dirty: false, error: null }); this.emit();
  }
  release(key: string) {
    const current = this.get(key);
    if (current?.dirty || current?.saving) throw new Error('Resolve unsaved source before closing.');
    this.documents.delete(key); this.opening.delete(key); this.openingTokens.delete(key); this.emit();
  }
}
