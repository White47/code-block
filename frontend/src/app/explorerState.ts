import { BridgeError, call } from '../api/bridge';
import type { Anchor, ProjectDirectory, ProjectEntry } from '../types';

export const parentPath = (path: string) => path.split('/').slice(0, -1).join('/');
export const containsPath = (parent: string, path: string) => parent === '' || path === parent || path.startsWith(`${parent}/`);
// Source identity comparison follows the captured host flag. Tree cache keys
// and source document keys keep their original spelling and separators.
export function containsSourcePath(parent: string, path: string, caseSensitive = true): boolean {
  const identity = (value: string) => caseSensitive ? value : value.replace(/\\/g, '/').toLowerCase();
  return containsPath(identity(parent), identity(path));
}
export function remapSourcePath(path: string, oldPath: string, newPath: string, caseSensitive = true): string {
  return containsSourcePath(oldPath, path, caseSensitive)
    ? (newPath + path.slice(oldPath.length)).replace(/\\/g, '/') : path;
}
export const fileAnchor = (filePath: string): Anchor => ({ filePath, startLine: 1, endLine: 1, startColumn: null, endColumn: null });
export const validEntryName = (name: string) => Boolean(name.trim()) && name !== '.' && name !== '..' && !/[\\/]/.test(name) && !Array.from(name).some(character => character.charCodeAt(0) < 32);
export const validDestinationDirectory = (path: string) => path === '' || (!path.includes('\\') && !path.includes(':') && path.split('/').every(validEntryName));
export const moveDestinationPath = (source: string, directory: string) => [directory, source.split('/').at(-1)].filter(Boolean).join('/');
export function safeBridgeError(error: unknown): string {
  return error instanceof BridgeError ? `${error.code}: ${error.message}` : error instanceof Error ? error.message : String(error);
}
export interface DirectoryState { listing?: ProjectDirectory; loading: boolean; error?: string }

// Each controller belongs to one project mount. Directory tickets reject older
// refreshes and pages, including results that arrive after disposal.
export class ExplorerState {
  directories = new Map<string, DirectoryState>();
  expanded = new Set(['']);
  epoch: string | undefined;
  private tickets = new Map<string, symbol>();
  private live = true;
  private version = 0;
  private listeners = new Set<() => void>();
  constructor(readonly projectPath: string) {}
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  snapshot = () => this.version;
  private emit() { this.version++; this.listeners.forEach(listener => listener()); }
  dispose() { this.live = false; this.tickets.clear(); }
  resume() { this.live = true; }
  collapse() { this.expanded = new Set(['']); this.emit(); }
  toggle(path: string) {
    if (this.expanded.has(path)) this.expanded.delete(path);
    else { this.expanded.add(path); if (!this.directories.get(path)?.listing) void this.load(path).catch(() => {}); }
    this.emit();
  }
  async load(directory: string, page = false): Promise<ProjectDirectory> {
    const previous = this.directories.get(directory)?.listing;
    const ticket = Symbol(directory); this.tickets.set(directory, ticket);
    this.directories.set(directory, { listing: previous, loading: true }); this.emit();
    const epoch = this.epoch;
    try {
      const value = await call('list_project_directory', { projectPath: this.projectPath, directory,
        ...(epoch ? { projectEpoch: epoch } : {}), ...(page && previous?.nextCursor ? { cursor: previous.nextCursor } : {}) });
      if (!this.live || this.tickets.get(directory) !== ticket) throw new BridgeError('ENTRY_STALE_RESPONSE', 'Directory request was superseded.');
      if (value.projectPath !== this.projectPath || value.directory !== directory || (epoch && value.projectEpoch !== epoch)) throw new BridgeError('PROJECT_CHANGED', 'Project directory identity changed.');
      if (page && previous && (value.projectEpoch !== previous.projectEpoch || value.directoryRevision !== previous.directoryRevision)) throw new BridgeError('ENTRY_CONFLICT', 'Directory changed while loading more entries. Refresh it.');
      this.epoch = value.projectEpoch;
      const listing = page && previous ? { ...value, entries: [...new Map([...previous.entries, ...value.entries].map(entry => [entry.path, entry])).values()] } : value;
      this.directories.set(directory, { listing, loading: false }); this.emit(); return listing;
    } catch (error) {
      if (this.live && this.tickets.get(directory) === ticket) { this.directories.set(directory, { listing: previous, loading: false, error: safeBridgeError(error) }); this.emit(); }
      throw error;
    }
  }
  async freshEntry(path: string, caseSensitive = true): Promise<ProjectEntry> {
    const parent = parentPath(path);
    let listing = await this.load(parent);
    for (;;) {
      const entry = listing.entries.find(entry => entry.path === path);
      if (entry) return entry;
      if (!listing.nextCursor) {
        // Exact paths win, including an exact match on a later page. Alias
        // fallback is safe only after every page establishes one candidate.
        if (caseSensitive === false) {
          const aliases = listing.entries.filter(entry => containsSourcePath(path, entry.path, false) && containsSourcePath(entry.path, path, false));
          if (aliases.length === 1) return aliases[0];
        }
        throw new BridgeError('ENTRY_CONFLICT', 'The project entry is missing or its identity is ambiguous. Refresh the file tree.');
      }
      listing = await this.load(parent, true);
    }
  }
  invalidate(path: string) {
    const directories = new Set([...this.directories.keys(), ...this.tickets.keys(), ...this.expanded]);
    for (const directory of directories) if (containsPath(path, directory)) {
      this.tickets.delete(directory); this.directories.delete(directory); this.expanded.delete(directory);
    }
    this.emit();
  }
  refresh(path = ''): Promise<ProjectDirectory> {
    this.invalidate(path);
    this.expanded.add(path);
    return this.load(path);
  }
  async refreshMove(source: string, destinationDirectory: string) {
    const parents = [...new Set([parentPath(source), destinationDirectory])];
    // Invalidate both parents before starting reads, so an ancestor refresh
    // cannot evict a destination response which has just completed.
    for (const parent of parents) this.invalidate(parent);
    this.expanded.add('');
    const results = await Promise.allSettled(parents.map(parent => this.load(parent)));
    const failed = results.find(result => result.status === 'rejected');
    if (failed?.status === 'rejected') throw failed.reason;
  }
}
