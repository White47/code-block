import type { Anchor, ViewKind } from '../../types';

export type AssistantJobStatus = 'Queued' | 'Running' | 'Completed' | 'Cancelled' | 'Failed' | 'Stale';

export type ProtocolDiagnostic =
  | { boundary: 'pre_send' | 'result_validation'; httpStatus?: never; checkSite?: never; finishReason?: never }
  | { boundary: 'dispatch'; httpStatus?: number; checkSite?: never; finishReason?: never }
  | { boundary: 'dispatch'; httpStatus?: never; checkSite: Exclude<ProtocolCheckSite, 'terminal_finish'>; finishReason?: never }
  | { boundary: 'dispatch'; httpStatus?: never; checkSite: 'terminal_finish'; finishReason?: ProtocolFinishReason };

export type ProtocolCheckSite = 'response_headers' | 'sse_syntax' | 'chunk_shape' | 'usage_shape'
  | 'delta_shape' | 'terminal_finish' | 'stream_eof' | 'sdk_decode' | 'context_exit' | 'cleanup';
export type ProtocolFinishReason = 'length' | 'content_filter' | 'insufficient_system_resource' | 'aborted';

export interface AssistantIssue {
  code: string;
  message: string;
  diagnostic?: ProtocolDiagnostic;
}

export type AssistantContext =
  | { kind: 'question' }
  | { kind: 'project' }
  | ({ kind: 'understanding' } & UnderstandingItemRef)
  | { kind: 'node'; viewKind: ViewKind; visibleNodeId: string; rootId: string | null; detailSourceId: string | null }
  | { kind: 'source'; filePath: string; anchor: Anchor | null; mode: 'function' | 'file' };

export interface UnderstandingItemRef { artifactId: string; revision: number; itemId: string }
export interface AssistantQuestionDraft { key: number; text: string; context: AssistantContext }

export interface AssistantStartRequest {
  projectEpoch: string;
  analysisGeneration: number;
  conversationId: string;
  requestId: string;
  configRevision: number;
  consentRevision: number;
  question: string;
  context: AssistantContext;
}

export interface AssistantStartResult {
  jobId: string;
  status: AssistantJobStatus;
  scope: { projectEpoch: string; analysisGeneration: number; requestId: string };
  conversationId: string;
  kind: 'explanation';
}

export interface ConnectionStartResult {
  jobId: string;
  status: AssistantJobStatus;
  scope: null;
  conversationId: null;
  kind: 'connection';
}

export interface AssistantPollRequest {
  jobId: string;
  projectEpoch: string | null;
  afterSeq: number;
}

export interface ConnectionCancelRequest {
  jobId: string;
  projectEpoch: null;
}

export interface ExplanationCancelRequest {
  jobId: string;
  projectEpoch: string;
}

export type AssistantCancelRequest = ConnectionCancelRequest | ExplanationCancelRequest;

export interface ConnectionCancelReceipt {
  kind: 'connection';
  jobId: string;
  projectEpoch: null;
  analysisGeneration: null;
  conversationId: null;
  conversationRevision: null;
  status: AssistantJobStatus;
  configRevision: number;
  consentRevision: null;
  cancelRequested: boolean;
  cancelAccepted: boolean;
  cancellation: 'none' | 'requested' | 'confirmed';
  dispatchClosed: boolean;
  resources: 'held' | 'closing' | 'released';
  resourcesReleased: boolean;
  issue: AssistantIssue | null;
  seq: number;
}

export interface ExplanationCancelReceipt {
  kind: 'explanation';
  jobId: string;
  projectEpoch: string;
  analysisGeneration: number;
  conversationId: string;
  conversationRevision: number;
  status: AssistantJobStatus;
  configRevision: number;
  consentRevision: number;
  cancelRequested: boolean;
  cancelAccepted: boolean;
  cancellation: 'none' | 'requested' | 'confirmed';
  dispatchClosed: boolean;
  resources: 'held' | 'closing' | 'released';
  resourcesReleased: boolean;
  issue: AssistantIssue | null;
  seq: number;
}

export type NavigationCancelReceipt = Omit<ExplanationCancelReceipt, 'kind'> & { kind: 'navigation' };
export type AssistantCancelReceipt = ConnectionCancelReceipt | ExplanationCancelReceipt | NavigationCancelReceipt;

export type EstimatedInputAccounting = {
  basis: 'estimated';
  method: 'wire-bytes-v1';
  limitTokens: 20000 | 100000 | 1024;
  upperBound: false;
  knownActualInputTokens: number;
  blockedReason: null | 'estimated_limit' | 'observed_overrun' | 'usage_unknown';
};

export type AssistantUsage = {
  reserved: { inputTokens: number; outputTokens: number; modelRequests: number;
    toolCalls: number; sourceCharacters: number; fileCount: number };
  observed: { inputTokens: number | null; outputTokens: number | null;
    basis: 'actual' | 'estimated' | 'unknown' };
} & ({ inputAccounting?: never } | { inputAccounting: EstimatedInputAccounting });

type AssistantIdentityKeys = 'kind' | 'jobId' | 'projectEpoch' | 'analysisGeneration'
  | 'conversationId' | 'conversationRevision';
type AssistantDisplayPayload =
  | { type: 'status'; status: AssistantJobStatus }
  | { type: 'display_text'; text: string }
  | { type: 'usage'; usage: AssistantUsage }
  | { type: 'result'; available: boolean }
  | { type: 'lifecycle'; cancelRequested: boolean; cancelAccepted: boolean;
    dispatchClosed: boolean; resourcesReleased: boolean };

export type ExplanationEvent = Pick<ExplanationPoll, AssistantIdentityKeys>
  & { seq: number } & AssistantDisplayPayload;
export type ConnectionEvent = Pick<ConnectionPoll, AssistantIdentityKeys>
  & { seq: number } & Exclude<AssistantDisplayPayload, { type: 'display_text' }>;
export type NavigationEvent = Pick<NavigationPoll, AssistantIdentityKeys>
  & { seq: number } & Exclude<AssistantDisplayPayload, { type: 'display_text' } | { type: 'usage' }>;

export interface AssistantAnswer {
  sections: {
    kind: 'summary' | 'entry' | 'steps' | 'branches' | 'relations' | 'evidence' | 'unknowns';
    claims: { text: string; basis: 'inference' | 'unknown'; evidenceIds: string[];
      relationEvidenceId: string | null }[];
  }[];
  evidence: { evidenceId: string; label: string; confidence: 'Exact' | 'Probable' | 'Unresolved';
    truncated: boolean; sourceAvailable: true; graphAvailable: boolean;
    unavailableReason: 'navigation_check_required' | 'source_only' }[];
  relationFacts: { evidenceId: string; sourceId: string; targetId: string; type: string;
    confidence: 'Exact' | 'Probable' | 'Unresolved'; candidateTargets: string[];
    category: 'call' | 'control' | 'data' | 'structure' }[];
  incomplete: boolean;
  coverageGaps: string[];
  checkedAt: string;
}

export type ConnectionResult =
  | { toolSupported: true; structuredOutputSupported: true }
  | { toolSupported: true; structuredOutputSupported: false;
      jsonObjectSupported: true; outputValidation: 'local-json-schema' };

export interface ConnectionPoll {
  kind: 'connection';
  jobId: string;
  projectEpoch: null;
  analysisGeneration: null;
  conversationId: null;
  conversationRevision: null;
  status: AssistantJobStatus;
  configRevision: number;
  consentRevision: null;
  cancelRequested: boolean;
  cancelAccepted: boolean;
  cancellation: 'none' | 'requested' | 'confirmed';
  dispatchClosed: boolean;
  resources: 'held' | 'closing' | 'released';
  resourcesReleased: boolean;
  issue: AssistantIssue | null;
  usage: AssistantUsage;
  connectionResult: ConnectionResult | null;
  reset: boolean;
  seq: number;
  events: ConnectionEvent[];
  evictedConversationIds: string[];
}

export interface ExplanationPoll {
  kind: 'explanation';
  jobId: string;
  projectEpoch: string;
  analysisGeneration: number;
  conversationId: string;
  conversationRevision: number;
  status: AssistantJobStatus;
  configRevision: number;
  consentRevision: number;
  cancelRequested: boolean;
  cancelAccepted: boolean;
  cancellation: 'none' | 'requested' | 'confirmed';
  dispatchClosed: boolean;
  resources: 'held' | 'closing' | 'released';
  resourcesReleased: boolean;
  issue: AssistantIssue | null;
  usage: AssistantUsage;
  displayText: string;
  answer: AssistantAnswer | null;
  reset: boolean;
  seq: number;
  events: ExplanationEvent[];
  evictedConversationIds: string[];
}

export interface AssistantNavigationTarget {
  evidenceId: string;
  scope: { projectEpoch: string; analysisGeneration: number; requestId: string };
  binding: { projectEpoch: string; analysisGeneration: number };
  action: 'source' | 'graph';
  astAnchor: Anchor;
  monacoRange: { startLineNumber: number; startColumn: number;
    endLineNumber: number; endColumn: number } | null;
  reveal: { viewKind: ViewKind; rootId: string | null; selectedId: string; reason: null } | null;
  checkedAt: string;
  conversationId: string;
  conversationRevision: number;
  configRevision: number;
  consentRevision: number;
}

export interface NavigationPoll {
  kind: 'navigation';
  jobId: string;
  projectEpoch: string;
  analysisGeneration: number;
  conversationId: string;
  conversationRevision: number;
  status: AssistantJobStatus;
  configRevision: number;
  consentRevision: number;
  cancelRequested: boolean;
  cancelAccepted: boolean;
  cancellation: 'none' | 'requested' | 'confirmed';
  dispatchClosed: boolean;
  resources: 'held' | 'closing' | 'released';
  resourcesReleased: boolean;
  issue: AssistantIssue | null;
  action: 'source' | 'graph';
  evidenceId: string;
  target: AssistantNavigationTarget | null;
  reset: boolean;
  seq: number;
  events: NavigationEvent[];
  evictedConversationIds: string[];
}

export type AssistantPoll = ConnectionPoll | ExplanationPoll | NavigationPoll;

export interface StateMessageBinding {
  jobId: string;
  projectEpoch: string;
  analysisGeneration: number;
  conversationRevision: number;
  configRevision: number;
  consentRevision: number;
  seq: number;
  stale: boolean;
}

export type StateMessage = StateMessageBinding & (
  | { type: 'question'; text: string }
  | { type: 'provisional'; text: string }
  | { type: 'validated_answer'; answer: AssistantAnswer }
);

export interface StateConversation {
  conversationId: string;
  conversationRevision: number;
  messages: StateMessage[];
}

export interface StateGrant {
  consentRevision: number;
  origin: string;
  allowedFiles: string[];
  enabled: boolean;
}

export type StateActiveJob = (ExplanationCancelReceipt & { purpose?: 'project_understanding' }) | NavigationCancelReceipt;

export interface ProjectUnderstanding {
  schemaVersion: 1;
  artifactId: string;
  revision: number;
  projectEpoch: string;
  analysisGeneration: number;
  conversationId: string;
  conversationRevision: number;
  jobId: string;
  configRevision: number;
  consentRevision: number;
  coverage: { indexedFileCount: number; authorizedFileCount: number; metadataFileCount: number;
    sourceFileCount: number; gapCodes: ('representative_sample' | 'retrieval_gap' | 'unverified')[] };
}

export interface AssistantState {
  projectEpoch: string;
  analysisGeneration: number | null;
  configRevision: number;
  conversations: StateConversation[];
  grant: StateGrant | null;
  activeJobs: StateActiveJob[];
  projectUnderstanding?: ProjectUnderstanding | null;
}

export interface AssistantBridgeMethods {
  assistant_get_consent_candidates: { args: [{ projectEpoch: string; analysisGeneration: number }];
    data: { projectEpoch: string; analysisGeneration: number; files: string[] } };
  assistant_get_settings: { args: []; data: AssistantSettings };
  assistant_configure: { args: [{ baseUrl: string; model: string; key: string | null; enabled: boolean }]; data: AssistantSettings };
  assistant_test_connection: { args: [{ configRevision: number; requestId: string }]; data: unknown };
  assistant_get_state: { args: [{ projectEpoch: string }]; data: unknown };
  assistant_grant_consent: { args: [{ projectEpoch: string; analysisGeneration: number; configRevision: number;
    allowedFiles: string[]; confirmed: true }]; data: { consentRevision: number; origin: string; allowedFiles: string[]; enabled: boolean } };
  assistant_revoke_consent: { args: [{ projectEpoch: string }]; data: { revoked: true; consentRevision: number } };
  assistant_new_conversation: { args: [{ projectEpoch: string }]; data: { conversationId: string; evictedConversationIds: string[] } };
  assistant_clear_conversation: { args: [{ projectEpoch: string; conversationId: string }]; data: { cleared: true; conversationRevision: number } };
  assistant_resolve_evidence: { args: [{ projectEpoch: string; analysisGeneration: number; conversationId: string;
    evidenceId: string; action: 'source' | 'graph'; understanding?: UnderstandingItemRef }]; data: unknown };
  assistant_start: { args: [AssistantStartRequest]; data: AssistantStartResult };
  assistant_poll: { args: [AssistantPollRequest]; data: unknown };
  assistant_cancel: { args: [AssistantCancelRequest]; data: unknown };
}

export interface AssistantSettings {
  available: boolean;
  enabled: boolean;
  baseUrl: string;
  model: string;
  keySet: boolean;
  configRevision: number;
  issue: AssistantIssue | null;
}
