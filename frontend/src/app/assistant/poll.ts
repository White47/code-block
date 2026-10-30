import { BridgeError } from '../../api/bridge';
import { validAnswer, wellFormed } from './answer';
import { validNavigationTarget } from './navigation';
import { copyProtocolDiagnostic, validProtocolDiagnostic } from './diagnostic';
import type { AssistantIssue, AssistantPoll, AssistantPollRequest, AssistantUsage, ConnectionCancelReceipt,
  ConnectionCancelRequest, ConnectionPoll, ExplanationCancelReceipt,
  ExplanationCancelRequest, ExplanationPoll, NavigationCancelReceipt,
  NavigationPoll } from './types';

const identity = ['kind', 'jobId', 'projectEpoch', 'analysisGeneration', 'conversationId',
  'conversationRevision'] as const;
const base = [...identity, 'status', 'configRevision', 'consentRevision', 'cancelRequested',
  'cancelAccepted', 'cancellation', 'dispatchClosed', 'resources', 'resourcesReleased',
  'issue', 'usage', 'connectionResult', 'reset', 'seq', 'events', 'evictedConversationIds'] as const;
const cancelKeys = [...identity, 'status', 'configRevision', 'consentRevision',
  'cancelRequested', 'cancelAccepted', 'cancellation', 'dispatchClosed',
  'resources', 'resourcesReleased', 'issue', 'seq'] as const;
const explanationKeys = [...identity, 'status', 'configRevision', 'consentRevision',
  'cancelRequested', 'cancelAccepted', 'cancellation', 'dispatchClosed',
  'resources', 'resourcesReleased', 'issue', 'usage', 'displayText', 'answer',
  'reset', 'seq', 'events', 'evictedConversationIds'] as const;
const navigationKeys = [...identity, 'status', 'configRevision', 'consentRevision',
  'cancelRequested', 'cancelAccepted', 'cancellation', 'dispatchClosed',
  'resources', 'resourcesReleased', 'issue', 'action', 'evidenceId', 'target',
  'reset', 'seq', 'events', 'evictedConversationIds'] as const;
const statuses = new Set(['Queued', 'Running', 'Completed', 'Cancelled', 'Failed', 'Stale']);
export const assistantErrorCodes = new Set([
  'AI_NOT_CONFIGURED', 'AI_DISABLED', 'AI_DEPENDENCY_MISSING', 'AI_ANALYSIS_REQUIRED',
  'AI_ANALYSIS_BUSY', 'AI_ANALYSIS_STALE', 'AI_CONSENT_REQUIRED', 'AI_SOURCE_BLOCKED',
  'AI_AUTH_FAILED', 'AI_RATE_LIMITED', 'AI_PROTOCOL_INCOMPATIBLE', 'AI_NETWORK_TIMEOUT',
  'AI_EVIDENCE_INVALID', 'AI_CANCELLED', 'AI_BUDGET_EXCEEDED', 'AI_CAPACITY',
  'AI_REQUEST_CONFLICT', 'AI_REQUEST_LIMIT', 'AI_RESULT_UNAVAILABLE', 'AI_INVALID_INPUT',
]);

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function exact(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return record(value) && Object.keys(value).length === keys.length
    && Object.keys(value).every(key => keys.includes(key));
}

function integer(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function boundedText(value: unknown, limit = 512): value is string {
  return typeof value === 'string' && value.length > 0 && Array.from(value).length <= limit
    && wellFormed(value);
}

function usage(value: unknown, kind: 'explanation' | 'connection'): value is AssistantUsage {
  if (!record(value)) return false;
  const keys = Object.keys(value);
  const strict = keys.length === 2 && keys.every(key => key === 'reserved' || key === 'observed');
  if (!strict && !exact(value, ['reserved', 'observed', 'inputAccounting'])) return false;
  const reserved = value.reserved;
  const observed = value.observed;
  if (!exact(reserved, ['inputTokens', 'outputTokens', 'modelRequests', 'toolCalls',
    'sourceCharacters', 'fileCount']) || !Object.values(reserved).every(integer)) return false;
  if (!exact(observed, ['inputTokens', 'outputTokens', 'basis'])) return false;
  const validObserved = observed.basis === 'unknown'
    ? observed.inputTokens === null && observed.outputTokens === null
    : (observed.basis === 'actual' || observed.basis === 'estimated')
      && integer(observed.inputTokens) && integer(observed.outputTokens);
  if (!validObserved || strict) return validObserved;
  const accounting = value.inputAccounting;
  if (!exact(accounting, ['basis', 'method', 'limitTokens', 'upperBound',
    'knownActualInputTokens', 'blockedReason'])
      || accounting.basis !== 'estimated' || accounting.method !== 'wire-bytes-v1'
      || accounting.upperBound !== false
      || !integer(accounting.limitTokens)
      || (kind === 'connection' ? accounting.limitTokens !== 1024
        : accounting.limitTokens !== 20000 && accounting.limitTokens !== 100000)
      || !integer(accounting.knownActualInputTokens)
      || !(accounting.blockedReason === null || (typeof accounting.blockedReason === 'string'
        && ['estimated_limit', 'observed_overrun', 'usage_unknown'].includes(accounting.blockedReason)))) return false;
  const inputLimit = accounting.limitTokens;
  if (accounting.blockedReason === 'usage_unknown' && observed.basis !== 'unknown') return false;
  if (accounting.blockedReason === 'observed_overrun'
      && (observed.basis !== 'actual' || !integer(observed.inputTokens)
        || observed.inputTokens <= inputLimit)) return false;
  if (observed.basis === 'unknown') return observed.inputTokens === null && observed.outputTokens === null;
  return integer(observed.inputTokens) && integer(observed.outputTokens)
    && (observed.basis !== 'actual' || observed.inputTokens === accounting.knownActualInputTokens);
}

function issue(value: unknown): boolean {
  return value === null || ((exact(value, ['code', 'message'])
      || (exact(value, ['code', 'message', 'diagnostic'])
        && value.code === 'AI_PROTOCOL_INCOMPATIBLE' && validProtocolDiagnostic(value.diagnostic)))
    && typeof value.code === 'string' && assistantErrorCodes.has(value.code)
    && boundedText(value.message, 200));
}

function safeIssue(value: unknown): AssistantIssue | null {
  if (value === null) return null;
  if (!record(value) || !issue(value)) throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant issue.');
  return { code: value.code as string, message: 'Assistant request could not be completed.',
    ...(validProtocolDiagnostic(value.diagnostic)
      ? { diagnostic: copyProtocolDiagnostic(value.diagnostic) } : {}) };
}

function event(value: unknown, poll: Record<string, unknown>): value is { seq: number } {
  if (!record(value) || !integer(value.seq) || !identity.every(key => value[key] === poll[key])) return false;
  const common = [...identity, 'seq', 'type'];
  if (value.type === 'status') {
    return exact(value, [...common, 'status'])
      && typeof value.status === 'string' && statuses.has(value.status);
  }
  if (value.type === 'usage') {
    return (poll.kind === 'explanation' || poll.kind === 'connection')
      && exact(value, [...common, 'usage']) && usage(value.usage, poll.kind);
  }
  if (value.type === 'display_text') {
    return poll.kind === 'explanation' && exact(value, [...common, 'text'])
      && typeof value.text === 'string' && value.text.length > 0 && wellFormed(value.text);
  }
  if (value.type === 'result') return exact(value, [...common, 'available']) && typeof value.available === 'boolean';
  if (value.type === 'lifecycle') {
    return exact(value, [...common, 'cancelRequested', 'cancelAccepted', 'dispatchClosed',
      'resourcesReleased']) && ['cancelRequested', 'cancelAccepted', 'dispatchClosed',
      'resourcesReleased'].every(key => typeof value[key] === 'boolean');
  }
  return false;
}

function validConnectionResult(value: unknown): boolean {
  if (exact(value, ['toolSupported', 'structuredOutputSupported'])) {
    return value.toolSupported === true && value.structuredOutputSupported === true;
  }
  return exact(value, ['toolSupported', 'structuredOutputSupported',
    'jsonObjectSupported', 'outputValidation'])
    && value.toolSupported === true && value.structuredOutputSupported === false
    && value.jsonObjectSupported === true && value.outputValidation === 'local-json-schema';
}

export function connectionPoll(value: unknown, request: AssistantPollRequest): ConnectionPoll {
  if (!exact(value, base) || value.kind !== 'connection' || request.projectEpoch !== null
      || value.jobId !== request.jobId || !boundedText(value.jobId)
      || value.projectEpoch !== null || value.analysisGeneration !== null
      || value.conversationId !== null || value.conversationRevision !== null
      || value.consentRevision !== null || !integer(value.configRevision)
      || typeof value.status !== 'string' || !statuses.has(value.status)
      || !integer(value.seq)
      || !integer(request.afterSeq) || request.afterSeq > value.seq
      || typeof value.cancelRequested !== 'boolean' || typeof value.cancelAccepted !== 'boolean'
      || typeof value.cancellation !== 'string'
      || !['none', 'requested', 'confirmed'].includes(value.cancellation)
      || typeof value.dispatchClosed !== 'boolean'
      || typeof value.resources !== 'string'
      || !['held', 'closing', 'released'].includes(value.resources)
      || typeof value.resourcesReleased !== 'boolean' || !issue(value.issue)
      || !usage(value.usage, 'connection') || typeof value.reset !== 'boolean'
      || !Array.isArray(value.events) || value.events.length > 32
      || !Array.isArray(value.evictedConversationIds)
      || value.evictedConversationIds.length > 10
      || !value.evictedConversationIds.every(id => boundedText(id))
      || (value.cancelAccepted !== (value.cancellation === 'confirmed'))
      || ((value.cancellation === 'requested' || value.cancellation === 'confirmed')
        && !value.cancelRequested)
      || (value.cancellation === 'confirmed' && (!value.dispatchClosed || !value.resourcesReleased))
      || value.resourcesReleased !== (value.resources === 'released')
      || (['Completed', 'Cancelled', 'Failed', 'Stale'].includes(value.status)
        && !value.dispatchClosed)
      || (value.status === 'Completed' && value.cancelRequested)
      || (value.status === 'Cancelled' && !value.cancelAccepted)
      || (value.connectionResult !== null && (value.status !== 'Completed'
        || !validConnectionResult(value.connectionResult)))
      || (value.status === 'Completed' && value.connectionResult === null)
      || (value.reset && value.events.length !== 0)) {
    throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant poll.');
  }
  let previous = request.afterSeq;
  for (const entry of value.events) {
    if (!event(entry, value) || entry.seq <= previous || entry.seq > value.seq) {
      throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant poll.');
    }
    previous = entry.seq;
  }
  const encoder = new TextEncoder();
  if (encoder.encode(JSON.stringify(value.events)).length > 64 * 1024
      || encoder.encode(JSON.stringify({ ok: true, schemaVersion: 1, data: value })).length > 160 * 1024) {
    throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant poll.');
  }
  // Backend diagnostics are untrusted; only a fixed local message may reach the UI.
  return { ...value, issue: safeIssue(value.issue) } as unknown as ConnectionPoll;
}

export function connectionCancelReceipt(value: unknown, request: ConnectionCancelRequest): ConnectionCancelReceipt {
  if (!exact(value, cancelKeys) || value.kind !== 'connection'
      || value.jobId !== request.jobId || !boundedText(value.jobId)
      || value.projectEpoch !== null || value.analysisGeneration !== null
      || value.conversationId !== null || value.conversationRevision !== null
      || value.consentRevision !== null || !integer(value.configRevision)
      || typeof value.status !== 'string' || !statuses.has(value.status)
      || !integer(value.seq)
      || typeof value.cancelRequested !== 'boolean' || typeof value.cancelAccepted !== 'boolean'
      || typeof value.cancellation !== 'string'
      || !['none', 'requested', 'confirmed'].includes(value.cancellation)
      || typeof value.dispatchClosed !== 'boolean'
      || typeof value.resources !== 'string'
      || !['held', 'closing', 'released'].includes(value.resources)
      || typeof value.resourcesReleased !== 'boolean' || !issue(value.issue)
      || value.cancelAccepted !== (value.cancellation === 'confirmed')
      || ((value.cancellation === 'requested' || value.cancellation === 'confirmed')
        && !value.cancelRequested)
      || (value.cancellation === 'confirmed' && (!value.dispatchClosed || !value.resourcesReleased))
      || value.resourcesReleased !== (value.resources === 'released')
      || (['Completed', 'Cancelled', 'Failed', 'Stale'].includes(value.status)
        && !value.dispatchClosed)
      || (value.status === 'Completed' && value.cancelRequested)
      || (value.status === 'Cancelled' && !value.cancelAccepted)) {
    throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant cancellation receipt.');
  }
  return { ...value, issue: safeIssue(value.issue) } as unknown as ConnectionCancelReceipt;
}

export function projectCancelReceipt(value: unknown, request: ExplanationCancelRequest):
  ExplanationCancelReceipt | NavigationCancelReceipt {
  if (!exact(value, cancelKeys) || !['explanation', 'navigation'].includes(value.kind as string)
      || value.jobId !== request.jobId || !boundedText(value.jobId)
      || value.projectEpoch !== request.projectEpoch || !boundedText(value.projectEpoch, 128)
      || !integer(value.analysisGeneration) || !boundedText(value.conversationId, 128)
      || !integer(value.conversationRevision) || !integer(value.consentRevision)
      || !integer(value.configRevision) || !integer(value.seq)
      || typeof value.status !== 'string' || !statuses.has(value.status)
      || typeof value.cancelRequested !== 'boolean' || typeof value.cancelAccepted !== 'boolean'
      || typeof value.cancellation !== 'string'
      || !['none', 'requested', 'confirmed'].includes(value.cancellation)
      || typeof value.dispatchClosed !== 'boolean'
      || typeof value.resources !== 'string'
      || !['held', 'closing', 'released'].includes(value.resources)
      || typeof value.resourcesReleased !== 'boolean' || !issue(value.issue)
      || value.cancelAccepted !== (value.cancellation === 'confirmed')
      || ((value.cancellation === 'requested' || value.cancellation === 'confirmed')
        && !value.cancelRequested)
      || (value.cancellation === 'confirmed' && (!value.dispatchClosed || !value.resourcesReleased))
      || value.resourcesReleased !== (value.resources === 'released')
      || (['Completed', 'Cancelled', 'Failed', 'Stale'].includes(value.status)
        && !value.dispatchClosed)
      || (value.status === 'Completed' && value.cancelRequested)
      || (value.status === 'Cancelled' && !value.cancelAccepted)) {
    throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant cancellation receipt.');
  }
  return { ...value, issue: safeIssue(value.issue) } as unknown as ExplanationCancelReceipt | NavigationCancelReceipt;
}

export function assistantPoll(value: unknown, request: AssistantPollRequest): AssistantPoll {
  if (!integer(request.afterSeq)) throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant poll.');
  if (record(value) && value.kind === 'connection') return connectionPoll(value, request);
  if (record(value) && value.kind === 'navigation') return navigationPoll(value, request);
  if (!exact(value, explanationKeys) || value.kind !== 'explanation'
      || request.projectEpoch === null || value.jobId !== request.jobId
      || value.projectEpoch !== request.projectEpoch || !boundedText(value.jobId)
      || !boundedText(value.projectEpoch, 128)
      || !integer(value.analysisGeneration) || !boundedText(value.conversationId, 128)
      || !integer(value.conversationRevision) || !integer(value.consentRevision)
      || !integer(value.configRevision) || typeof value.status !== 'string'
      || !statuses.has(value.status)
      || !integer(value.seq) || request.afterSeq > value.seq
      || typeof value.cancelRequested !== 'boolean' || typeof value.cancelAccepted !== 'boolean'
      || typeof value.cancellation !== 'string'
      || !['none', 'requested', 'confirmed'].includes(value.cancellation)
      || typeof value.dispatchClosed !== 'boolean'
      || typeof value.resources !== 'string'
      || !['held', 'closing', 'released'].includes(value.resources)
      || typeof value.resourcesReleased !== 'boolean' || !issue(value.issue)
      || !usage(value.usage, 'explanation') || typeof value.displayText !== 'string'
      || !wellFormed(value.displayText)
      || (value.status === 'Completed' ? !validAnswer(value.answer) : value.answer !== null)
      || typeof value.reset !== 'boolean'
      || !Array.isArray(value.events) || value.events.length > 32
      || !Array.isArray(value.evictedConversationIds)
      || value.evictedConversationIds.length > 10
      || !value.evictedConversationIds.every(id => boundedText(id))
      || value.cancelAccepted !== (value.cancellation === 'confirmed')
      || ((value.cancellation === 'requested' || value.cancellation === 'confirmed')
        && !value.cancelRequested)
      || (value.cancellation === 'confirmed' && (!value.dispatchClosed || !value.resourcesReleased))
      || value.resourcesReleased !== (value.resources === 'released')
      || (['Completed', 'Cancelled', 'Failed', 'Stale'].includes(value.status) && !value.dispatchClosed)
      || (value.status === 'Completed' && value.cancelRequested)
      || (value.status === 'Cancelled' && !value.cancelAccepted)
      || (value.reset && value.events.length !== 0)) {
    throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant poll.');
  }
  const encoder = new TextEncoder();
  if (encoder.encode(value.displayText).length > 64 * 1024) {
    throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant poll.');
  }
  let previous = request.afterSeq;
  for (const entry of value.events) {
    if (!event(entry, value) || entry.seq <= previous || entry.seq > value.seq
        || encoder.encode(JSON.stringify(entry)).length > 8 * 1024) {
      throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant poll.');
    }
    previous = entry.seq;
  }
  if (encoder.encode(JSON.stringify(value.events)).length > 64 * 1024
      || encoder.encode(JSON.stringify({ ok: true, schemaVersion: 1, data: value })).length > 160 * 1024) {
    throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant poll.');
  }
  return { ...value, issue: safeIssue(value.issue) } as unknown as ExplanationPoll;
}

function navigationPoll(value: unknown, request: AssistantPollRequest): NavigationPoll {
  if (!exact(value, navigationKeys) || value.kind !== 'navigation'
      || request.projectEpoch === null || value.jobId !== request.jobId
      || value.projectEpoch !== request.projectEpoch || !boundedText(value.jobId)
      || !boundedText(value.projectEpoch, 128)
      || !integer(value.analysisGeneration) || !boundedText(value.conversationId, 128)
      || !integer(value.conversationRevision) || !integer(value.consentRevision)
      || !integer(value.configRevision) || typeof value.status !== 'string'
      || !statuses.has(value.status)
      || !integer(value.seq) || request.afterSeq > value.seq
      || typeof value.cancelRequested !== 'boolean' || typeof value.cancelAccepted !== 'boolean'
      || typeof value.cancellation !== 'string'
      || !['none', 'requested', 'confirmed'].includes(value.cancellation)
      || typeof value.dispatchClosed !== 'boolean'
      || typeof value.resources !== 'string'
      || !['held', 'closing', 'released'].includes(value.resources)
      || typeof value.resourcesReleased !== 'boolean' || !issue(value.issue)
      || !['source', 'graph'].includes(value.action as string)
      || !boundedText(value.evidenceId)
      || (value.status === 'Completed' ? !validNavigationTarget(value.target, value) : value.target !== null)
      || typeof value.reset !== 'boolean'
      || !Array.isArray(value.events) || value.events.length > 32
      || !Array.isArray(value.evictedConversationIds)
      || value.evictedConversationIds.length > 10
      || !value.evictedConversationIds.every(id => boundedText(id))
      || value.cancelAccepted !== (value.cancellation === 'confirmed')
      || ((value.cancellation === 'requested' || value.cancellation === 'confirmed')
        && !value.cancelRequested)
      || (value.cancellation === 'confirmed' && (!value.dispatchClosed || !value.resourcesReleased))
      || value.resourcesReleased !== (value.resources === 'released')
      || (['Completed', 'Cancelled', 'Failed', 'Stale'].includes(value.status) && !value.dispatchClosed)
      || (value.status === 'Completed' && value.cancelRequested)
      || (value.status === 'Cancelled' && !value.cancelAccepted)
      || (value.reset && value.events.length !== 0)) {
    throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant poll.');
  }
  const encoder = new TextEncoder();
  let previous = request.afterSeq;
  for (const entry of value.events) {
    if (!event(entry, value) || entry.seq <= previous || entry.seq > value.seq
        || encoder.encode(JSON.stringify(entry)).length > 8 * 1024) {
      throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant poll.');
    }
    previous = entry.seq;
  }
  if (encoder.encode(JSON.stringify(value.events)).length > 64 * 1024
      || encoder.encode(JSON.stringify({ ok: true, schemaVersion: 1, data: value })).length > 160 * 1024) {
    throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant poll.');
  }
  return { ...value, issue: safeIssue(value.issue) } as unknown as NavigationPoll;
}
