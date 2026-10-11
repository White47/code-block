export const viewKinds = ['Overview', 'ProjectStructure', 'Architecture', 'Execution', 'FunctionFlow', 'Calls', 'DataFlow', 'Gradient', 'Symbol'] as const;
export type ViewKind = typeof viewKinds[number];
export type Language = 'zh-CN' | 'en';
export type Point = [number, number];
export interface Anchor { filePath: string; startLine: number; startColumn: number | null; endLine: number; endColumn: number | null }
export interface VisibleNode { id: string; displayName: string; codeName: string | null; role: string | null; confidence: string; sourceAnchors: Anchor[]; semanticComponentIds: string[]; codeNodeIds: string[]; memberIds: string[]; metadata: Record<string, string | number | boolean | (number | null)[]> }
export interface VisibleEdge { source: string; target: string; type: string; confidence: string; sourceAnchors: Anchor[]; codeEdgeIds: string[]; semanticComponentIds: string[] }
export interface Graph { viewKind: ViewKind; nodes: VisibleNode[]; edges: VisibleEdge[]; rootIds: string[]; layoutKind: string; layoutHints: Record<string, Point>; legend: { type: string; label: string; style: string }[]; hiddenCounts: Record<string, number>; queryContext: { controlMode?: 'structure' | 'relations' | 'none'; structureDepth?: number; canExpand?: boolean; depthLimit?: boolean; hasMore?: boolean; budgetTruncated?: boolean }; issues: { code: string; message: string }[] }
export interface Session { projectPath: string | null; fileSystemCaseSensitive?: boolean; status: 'NotAnalyzed' | 'Analyzing' | 'Ready' | 'Partial' | 'Outdated' | 'Failed'; hasAnalysis: boolean; outdatedReasons: string[]; cacheIssue: string | null; recentProjects: string[] }
export interface Preferences { language: Language; issue: string | null }
export interface AnalysisSettings { ignorePaths: string[]; issue: string | null }
export interface CacheInfo { dataDir: string; analysisPath: string | null; exists: boolean; status: Session['status']; outdatedReasons: string[]; cacheIssue: string | null }
export interface OverviewChoice { id: string; name: string; sourceAnchor: Anchor; viewKind?: ViewKind; confidence?: string; evidence?: string[]; detectionKind?: string | null; initialStructureDepth?: 1 | 2 }
export interface Overview { projectName: string; projectPath: string; language: string; framework: string | null; models: OverviewChoice[]; entries: OverviewChoice[]; functions?: OverviewChoice[]; modules?: OverviewChoice[]; classes?: OverviewChoice[]; mainAreas: string[]; health: { filesParsed: number; filesTotal: number; symbolsResolved: number; exact: number; probable: number; unresolved: number; issueCount: number } }
export interface InspectorData { id: string; displayName: string; codeName: string | null; role: string | null; confidence: string; evidence: string[]; children: string[]; relations: { type: string; otherId: string; direction: string; confidence: string }[]; data: [string, string][]; sourceAnchors: Anchor[]; memberIds: string[]; canOpenSource: boolean }
export interface Problem { code: string; message: string; filePath: string | null; line: number | null; canOpenSource: boolean }
export interface Source { filePath: string; text: string; startLine: number; endLine: number }
export interface EditableSource { projectPath: string; filePath: string; text: string; revision: string; encoding: string; newline: 'LF' | 'CRLF' | 'CR' | 'Mixed'; readOnlyReason: string | null }
export interface SaveSourceRequest { projectPath: string; filePath: string; text: string; revision: string }
export interface ProjectEntry { path: string; name: string; kind: 'file' | 'directory' | 'link' | 'other'; revision: string; readOnlyReason: string | null; canOpen: boolean; canCreate: boolean; canRename: boolean; canDelete: boolean }
export interface ProjectDirectoryRequest { projectPath: string; directory: string; projectEpoch?: string; cursor?: string | null }
export interface ProjectDirectory { projectPath: string; projectEpoch: string; directory: string; directoryRevision: string; entries: ProjectEntry[]; nextCursor: string | null }
export interface CreateProjectEntryRequest { projectPath: string; projectEpoch: string; parent: string; parentRevision: string; name: string; kind: 'file' | 'directory' }
export interface PrepareProjectEntryRequest { projectPath: string; projectEpoch: string; path: string; revision: string; operation: 'rename' | 'delete' | 'move'; newName?: string; destinationDirectory?: string; destinationRevision?: string }
export interface PreparedProjectEntry { operationToken: string; operation: 'rename' | 'delete' | 'move'; path: string; newPath: string | null; affectedPaths: string[] }
export interface ProjectEntryResult { operation: 'create' | 'rename' | 'delete' | 'move'; oldPath: string | null; newPath: string | null; affectedPaths: string[] }
export interface Reveal { viewKind: ViewKind | null; rootId: string | null; selectedId: string | null; reason: string | null }
export interface SearchResult { id: string; sourceKind: string; name: string; qualifiedName: string; kind: string; scope: string; sourceAnchor: Anchor; confidence: string; revealTarget: Reveal }
export interface SearchResponse { results: SearchResult[]; totalCount: number; truncated: boolean }
export interface Island { id: string; viewKind: ViewKind; sourceNodeId: string; position: Point | null }
export interface ViewState { activeView: ViewKind | null; selectedId: string | null; openTabs: string[]; graphRoots: Partial<Record<ViewKind, string | null>>; expandedIds: string[]; positions: Record<string, Point>; positionsByView: Partial<Record<ViewKind, Record<string, Point>>>; zoom: number; pan: Point; viewportByView: Partial<Record<ViewKind, { zoom: number; pan: Point }>>; detailIslands: Island[]; filters: string[] }
export interface Layout { activeView: ViewKind | null; selectedId: string | null; openTabs: string[]; graphRoots: Partial<Record<ViewKind, string | null>> }
export interface ProjectBridgeMethods {
  list_project_directory: { args: [ProjectDirectoryRequest]; data: ProjectDirectory };
  create_project_entry: { args: [CreateProjectEntryRequest]; data: ProjectEntryResult };
  prepare_project_entry_operation: { args: [PrepareProjectEntryRequest]; data: PreparedProjectEntry };
  apply_project_entry_operation: { args: [{ projectPath: string; projectEpoch: string; operationToken: string }]; data: ProjectEntryResult };
}
// Optional project browsing is an extension; legacy native bridge contracts
// and typed adapters remain compatible without implementing the extension.
export type AllBridgeMethods = BridgeMethods & ProjectBridgeMethods;
export interface BridgeMethods {
  open_source_document: { args: [string, Anchor]; data: EditableSource };
  save_source_document: { args: [SaveSourceRequest]; data: EditableSource };
  approve_close: { args: [string]; data: null }; cancel_close: { args: [string]; data: null };
  get_session: { args: []; data: Session }; open_project: { args: [string]; data: Session }; analyze: { args: []; data: Session };
  get_overview: { args: []; data: Overview }; get_view: { args: [ViewKind, string | null, string | null, number?, string?, number?]; data: Graph };
  get_detail_view: { args: [ViewKind, string | null, string]; data: Graph }; search: { args: [string, number?]; data: SearchResponse };
  read_source: { args: [Anchor]; data: Source }; get_preferences: { args: []; data: Preferences }; set_language: { args: [Language]; data: Preferences };
  get_problems: { args: []; data: Problem[] }; save_layout: { args: [Layout]; data: ViewState };
  move_node: { args: [string, { x: number; y: number }, ViewKind]; data: ViewState };
  save_viewport: { args: [ViewKind, number, { x: number; y: number }]; data: ViewState };
  expand: { args: [string]; data: ViewState }; collapse: { args: [string]; data: ViewState };
  open_detail_island: { args: [ViewKind, string, { x: number; y: number } | null]; data: ViewState };
  move_detail_island: { args: [string, { x: number; y: number }]; data: ViewState }; close_detail_island: { args: [string]; data: ViewState };
  get_inspector: { args: [ViewKind, string | null, string, (string | null)?, number?]; data: InspectorData }; get_view_state: { args: []; data: ViewState };
  choose_project: { args: []; data: Session }; set_preferences: { args: [{ language: Language }]; data: Preferences };
  get_analysis_settings: { args: []; data: AnalysisSettings }; set_analysis_settings: { args: [{ ignorePaths: string[] }]; data: AnalysisSettings };
  get_cache_info: { args: []; data: CacheInfo };
}
export type Envelope<T> = { ok: true; schemaVersion: number; data: T } | { ok: false; schemaVersion: number; error: { code: string; message: string } };
