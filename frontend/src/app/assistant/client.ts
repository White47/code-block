import { BridgeError, call } from '../../api/bridge';
import { viewKinds } from '../../types';
import { validUnderstandingItem } from './context';
import { validAnswer, wellFormed } from './answer';
import { assistantErrorCodes, assistantPoll, connectionCancelReceipt, projectCancelReceipt } from './poll';
import type { AssistantPoll, AssistantPollRequest, AssistantStartRequest, AssistantStartResult,
  AssistantCancelReceipt, AssistantCancelRequest, AssistantSettings, AssistantBridgeMethods,
  ConnectionStartResult, AssistantState, StateActiveJob, StateMessage } from './types';

const idPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
type NavigationRequest = AssistantBridgeMethods['assistant_resolve_evidence']['args'][0];
type NavigationStartResult = Omit<AssistantStartResult, 'kind'> & { kind: 'navigation' };

function exact(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    && Object.keys(value).length === keys.length
    && Object.keys(value).every(key => keys.includes(key));
}

function id(value: unknown): value is string {
  return typeof value === 'string' && idPattern.test(value);
}

function natural(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function prose(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.trim().length > 0 && wellFormed(value)
    && Array.from(value).length <= maximum
    && !/\p{Cc}/u.test(value.replace(/[\n\t]/g, ''));
}

function sourcePath(value: unknown): value is string {
  if (typeof value !== 'string' || value.length === 0 || !wellFormed(value)
      || Array.from(value).length > 512 || /[\\:*?"<>|[\]\p{Cc}]/u.test(value)) return false;
  const device = /^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])$/iu;
  return value.split('/').every(part => part !== '' && part !== '.' && part !== '..'
    && !/[. ]$/.test(part) && !device.test(part.split('.')[0].replace(/ +$/, '')));
}

function selectedFiles(value: unknown): value is string[] {
  return Array.isArray(value) && value.length <= 200_000
    && value.every(path => sourcePath(path) && path.endsWith('.py'))
    && new Set(value.map(path => path.toLowerCase())).size === value.length;
}

function providerOrigin(value: unknown): value is string {
  if (typeof value !== 'string' || !prose(value, 2048) || /[\s\\%?#@]/u.test(value)) return false;
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'https:' && parsed.username === '' && parsed.password === ''
      && parsed.pathname === '/' && parsed.search === '' && parsed.hash === '';
  } catch { return false; }
}

function anchor(value: unknown): boolean {
  return exact(value, ['filePath', 'startLine', 'startColumn', 'endLine', 'endColumn'])
    && sourcePath(value.filePath)
    && natural(value.startLine) && value.startLine > 0
    && natural(value.endLine) && value.endLine >= value.startLine
    && (value.startColumn === null || natural(value.startColumn))
    && (value.endColumn === null || natural(value.endColumn))
    && (value.startLine !== value.endLine || value.startColumn === null
      || value.endColumn === null || value.startColumn <= value.endColumn);
}

function context(value: unknown): boolean {
  if (!exact(value, ['kind']) && !exact(value, ['kind', 'viewKind', 'visibleNodeId', 'rootId', 'detailSourceId'])
      && !exact(value, ['kind', 'artifactId', 'revision', 'itemId'])
      && !exact(value, ['kind', 'filePath', 'anchor', 'mode'])) return false;
  if (value.kind === 'question' || value.kind === 'project') return exact(value, ['kind']);
  if (value.kind === 'understanding') return exact(value, ['kind', 'artifactId', 'revision', 'itemId'])
    && validUnderstandingItem({ artifactId: value.artifactId, revision: value.revision, itemId: value.itemId });
  if (value.kind === 'node') {
    return exact(value, ['kind', 'viewKind', 'visibleNodeId', 'rootId', 'detailSourceId'])
      && viewKinds.includes(value.viewKind as typeof viewKinds[number])
      && prose(value.visibleNodeId, 512) && !/[\p{C}\p{Z}]/u.test(value.visibleNodeId.replace(/ /g, ''))
      && (value.rootId === null || id(value.rootId))
      && (value.detailSourceId === null || id(value.detailSourceId));
  }
  return value.kind === 'source' && exact(value, ['kind', 'filePath', 'anchor', 'mode'])
    && sourcePath(value.filePath)
    && (value.anchor === null || (anchor(value.anchor) && exact(value.anchor,
      ['filePath', 'startLine', 'startColumn', 'endLine', 'endColumn'])
      && value.anchor.filePath === value.filePath))
    && (value.mode === 'function' || value.mode === 'file');
}

function validStart(value: unknown): value is AssistantStartRequest {
  return exact(value, ['projectEpoch', 'analysisGeneration', 'conversationId', 'requestId',
    'configRevision', 'consentRevision', 'question', 'context'])
    && id(value.projectEpoch) && natural(value.analysisGeneration)
    && id(value.conversationId) && id(value.requestId)
    && natural(value.configRevision) && natural(value.consentRevision)
    && prose(value.question, 2000) && context(value.context);
}

function validPoll(value: unknown): value is AssistantPollRequest {
  return exact(value, ['jobId', 'projectEpoch', 'afterSeq'])
    && id(value.jobId) && (value.projectEpoch === null || id(value.projectEpoch))
    && natural(value.afterSeq);
}

function validStartResult(value: unknown, request: AssistantStartRequest): value is AssistantStartResult {
  return exact(value, ['jobId', 'status', 'scope', 'conversationId', 'kind'])
    && value.kind === 'explanation' && id(value.jobId)
    && typeof value.status === 'string'
    && ['Queued', 'Running', 'Completed', 'Cancelled', 'Failed', 'Stale'].includes(value.status)
    && value.conversationId === request.conversationId
    && exact(value.scope, ['projectEpoch', 'analysisGeneration', 'requestId'])
    && value.scope.projectEpoch === request.projectEpoch
    && value.scope.analysisGeneration === request.analysisGeneration
    && value.scope.requestId === request.requestId;
}

function validNavigationStart(value: unknown, request: NavigationRequest): value is NavigationStartResult {
  return exact(value, ['jobId', 'status', 'scope', 'conversationId', 'kind'])
    && value.kind === 'navigation' && id(value.jobId)
    && typeof value.status === 'string'
    && ['Queued', 'Running', 'Completed', 'Cancelled', 'Failed', 'Stale'].includes(value.status)
    && value.conversationId === request.conversationId
    && exact(value.scope, ['projectEpoch', 'analysisGeneration', 'requestId'])
    && value.scope.projectEpoch === request.projectEpoch
    && value.scope.analysisGeneration === request.analysisGeneration && id(value.scope.requestId);
}

const localErrorCodes = new Set(['DESKTOP_UNAVAILABLE', 'METHOD_UNAVAILABLE',
  'BRIDGE_TIMEOUT', 'BRIDGE_ERROR', 'INVALID_RESPONSE', 'AI_INVALID_INPUT']);

async function safeAssistantCall<T>(operation: () => Promise<T>): Promise<T> {
  try { return await operation(); }
  catch (error) {
    const code = error instanceof BridgeError && typeof error.code === 'string'
      && (assistantErrorCodes.has(error.code) || localErrorCodes.has(error.code))
      ? error.code : 'INVALID_RESPONSE';
    throw new BridgeError(code, 'Assistant request could not be completed.');
  }
}

function settings(value: unknown): AssistantSettings {
  if (!exact(value, ['available', 'enabled', 'baseUrl', 'model', 'keySet', 'configRevision', 'issue'])
      || typeof value.available !== 'boolean' || typeof value.enabled !== 'boolean'
      || typeof value.keySet !== 'boolean' || !natural(value.configRevision)
      || typeof value.baseUrl !== 'string' || !wellFormed(value.baseUrl)
      || Array.from(value.baseUrl).length > 2048 || /\p{Cc}/u.test(value.baseUrl)
      || typeof value.model !== 'string' || !wellFormed(value.model)
      || Array.from(value.model).length > 128 || /\p{Cc}/u.test(value.model)
      || (value.issue !== null && (!exact(value.issue, ['code', 'message'])
        || typeof value.issue.code !== 'string' || !assistantErrorCodes.has(value.issue.code)
        || typeof value.issue.message !== 'string'))) {
    throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant settings.');
  }
  return { available: value.available, enabled: value.enabled, baseUrl: value.baseUrl,
    model: value.model, keySet: value.keySet, configRevision: value.configRevision,
    issue: value.issue === null ? null : { code: value.issue.code as string,
      message: 'Assistant request could not be completed.' } };
}

const messageBindingKeys = ['jobId', 'projectEpoch', 'analysisGeneration', 'conversationRevision',
  'configRevision', 'consentRevision', 'seq', 'stale'];

function invalidState(): never {
  throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant recovery state.');
}

function displayText(value: unknown): value is string {
  return typeof value === 'string' && wellFormed(value)
    && new TextEncoder().encode(value).length <= 64 * 1024;
}

function pathPrecedes(left: string, right: string): boolean {
  // Python sorts Unicode code points; JS relational comparison sorts UTF-16 units.
  const a = Array.from(left, character => character.codePointAt(0) ?? -1);
  const b = Array.from(right, character => character.codePointAt(0) ?? -1);
  for (let index = 0; index < Math.min(a.length, b.length); index += 1) {
    if (a[index] !== b[index]) return a[index] < b[index];
  }
  return a.length < b.length;
}

function stateMessage(value: unknown, revision: number): StateMessage {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) invalidState();
  const data = value as Record<string, unknown>;
  const answer = data.type === 'validated_answer';
  if (!exact(data, [...messageBindingKeys, 'type', answer ? 'answer' : 'text'])
      || !id(data.jobId) || !id(data.projectEpoch)
      || !natural(data.analysisGeneration) || data.analysisGeneration < 1
      || data.conversationRevision !== revision || !natural(data.configRevision)
      || !natural(data.consentRevision) || !natural(data.seq) || typeof data.stale !== 'boolean'
      || (answer ? !validAnswer(data.answer)
        : data.type === 'question' ? !prose(data.text, 2000)
          : data.type !== 'provisional' || !displayText(data.text))) invalidState();
  return data as unknown as StateMessage;
}

function projectState(value: unknown, projectEpoch: string): AssistantState {
  const stateKeys = ['projectEpoch', 'analysisGeneration', 'configRevision', 'conversations', 'grant', 'activeJobs'];
  if (typeof value === 'object' && value !== null && 'projectUnderstanding' in value) stateKeys.push('projectUnderstanding');
  if (!exact(value, stateKeys)
      || value.projectEpoch !== projectEpoch
      || (value.analysisGeneration !== null
        && (!natural(value.analysisGeneration) || value.analysisGeneration < 1))
      || !natural(value.configRevision) || !Array.isArray(value.conversations)
      || value.conversations.length > 10 || !Array.isArray(value.activeJobs)
      || value.activeJobs.length > 2) invalidState();
  const encoded = new TextEncoder().encode(JSON.stringify({ ok: true, schemaVersion: 1, data: value }));
  if (encoded.length > 2 * 1024 * 1024) invalidState();
  const grant = value.grant;
  if (grant !== null && (!exact(grant, ['consentRevision', 'origin', 'allowedFiles', 'enabled'])
      || !natural(grant.consentRevision) || !providerOrigin(grant.origin)
      || !selectedFiles(grant.allowedFiles) || typeof grant.enabled !== 'boolean'
      || !(grant.allowedFiles as string[]).every((file, index, files) => index === 0 || pathPrecedes(files[index - 1], file)))) {
    invalidState();
  }
  const conversationIds = new Set<string>();
  const conversationRevisions = new Map<string, number>();
  const turns = new Map<string, { owner: string; binding: string; message: StateMessage;
    question: boolean; assistant: boolean }>();
  for (const conversation of value.conversations) {
    if (!exact(conversation, ['conversationId', 'conversationRevision', 'messages'])
        || !id(conversation.conversationId) || conversationIds.has(conversation.conversationId)
        || !natural(conversation.conversationRevision) || !Array.isArray(conversation.messages)
        || conversation.messages.length > 40) invalidState();
    conversationIds.add(conversation.conversationId);
    conversationRevisions.set(conversation.conversationId, conversation.conversationRevision);
    for (const raw of conversation.messages) {
      const message = stateMessage(raw, conversation.conversationRevision);
      if (message.configRevision > value.configRevision || (!message.stale
          && (message.projectEpoch !== projectEpoch || message.analysisGeneration !== value.analysisGeneration
            || message.configRevision !== value.configRevision || grant === null
            || !(grant as { enabled: boolean }).enabled
            || message.consentRevision !== (grant as { consentRevision: number }).consentRevision))) invalidState();
      const binding = JSON.stringify(messageBindingKeys.map(key => message[key as keyof StateMessage]));
      const prior = turns.get(message.jobId);
      if (prior && (prior.owner !== conversation.conversationId || prior.binding !== binding
          || (message.type === 'question' ? prior.question || prior.assistant : prior.assistant))) invalidState();
      turns.set(message.jobId, { owner: conversation.conversationId, binding, message,
        question: message.type === 'question' || (prior?.question ?? false),
        assistant: message.type !== 'question' || (prior?.assistant ?? false) });
    }
  }
  const jobIds = new Set<string>();
  const activeJobs: StateActiveJob[] = value.activeJobs.map(raw => {
    if (typeof raw !== 'object' || raw === null || !('jobId' in raw) || !id(raw.jobId)
        || jobIds.has(raw.jobId)) invalidState();
    const hasPurpose = 'purpose' in raw;
    if (hasPurpose && (raw.purpose !== 'project_understanding' || !('kind' in raw) || raw.kind !== 'explanation')) invalidState();
    const receipt = { ...raw }; delete receipt.purpose;
    const validated = projectCancelReceipt(receipt, { jobId: raw.jobId, projectEpoch });
    const job: StateActiveJob = hasPurpose && validated.kind === 'explanation'
      ? { ...validated, purpose: 'project_understanding' } : validated;
    if (!id(job.conversationId) || job.analysisGeneration < 1 || value.analysisGeneration === null
        || job.analysisGeneration > (value.analysisGeneration as number)
        || job.configRevision > (value.configRevision as number)
        || (!['Queued', 'Running'].includes(job.status) && job.resourcesReleased)) invalidState();
    const revision = conversationRevisions.get(job.conversationId);
    if (revision !== undefined && job.conversationRevision > revision) invalidState();
    const turn = turns.get(job.jobId);
    if (turn && (turn.owner !== job.conversationId || turn.message.seq !== job.seq
        || turn.message.projectEpoch !== job.projectEpoch
        || turn.message.analysisGeneration !== job.analysisGeneration
        || turn.message.conversationRevision !== job.conversationRevision
        || turn.message.configRevision !== job.configRevision
        || turn.message.consentRevision !== job.consentRevision)) invalidState();
    jobIds.add(job.jobId);
    return job;
  });
  const understanding = value.projectUnderstanding;
  if (understanding !== undefined && understanding !== null) {
    if (!exact(understanding, ['schemaVersion', 'artifactId', 'revision', 'projectEpoch', 'analysisGeneration',
      'conversationId', 'conversationRevision', 'jobId', 'configRevision', 'consentRevision', 'coverage'])
      || understanding.schemaVersion !== 1 || !id(understanding.artifactId) || !id(understanding.jobId)
      || !id(understanding.conversationId) || !natural(understanding.revision) || understanding.revision < 1
      || understanding.projectEpoch !== projectEpoch || understanding.analysisGeneration !== value.analysisGeneration
      || understanding.configRevision !== value.configRevision || !natural(understanding.conversationRevision)
      || grant === null || !(grant as { enabled: boolean }).enabled
      || understanding.consentRevision !== (grant as { consentRevision: number }).consentRevision) invalidState();
    const coverage = understanding.coverage;
    if (!exact(coverage, ['indexedFileCount', 'authorizedFileCount', 'metadataFileCount', 'sourceFileCount', 'gapCodes'])
      || !natural(coverage.indexedFileCount) || !natural(coverage.authorizedFileCount)
      || !natural(coverage.metadataFileCount) || !natural(coverage.sourceFileCount)
      || coverage.authorizedFileCount > coverage.indexedFileCount
      || coverage.metadataFileCount > coverage.authorizedFileCount || coverage.sourceFileCount > coverage.authorizedFileCount
      || !Array.isArray(coverage.gapCodes) || coverage.gapCodes.length > 3
      || !coverage.gapCodes.every((code, i, codes) => typeof code === 'string'
        && ['representative_sample', 'retrieval_gap', 'unverified'].includes(code)
        && (i === 0 || codes[i - 1] < code))) invalidState();
    const turn = turns.get(understanding.jobId);
    if (!turn || turn.owner !== understanding.conversationId || turn.message.type !== 'validated_answer'
      || turn.message.stale || turn.message.conversationRevision !== understanding.conversationRevision
      || turn.message.projectEpoch !== understanding.projectEpoch
      || turn.message.analysisGeneration !== understanding.analysisGeneration
      || turn.message.configRevision !== understanding.configRevision
      || turn.message.consentRevision !== understanding.consentRevision) invalidState();
  }
  return JSON.parse(JSON.stringify({ ...value, activeJobs })) as AssistantState;
}

export const assistantClient = {
  async getState(request: AssistantBridgeMethods['assistant_get_state']['args'][0]): Promise<AssistantState> {
    if (!exact(request, ['projectEpoch']) || !id(request.projectEpoch)) {
      throw new BridgeError('AI_INVALID_INPUT', 'Invalid assistant state request.');
    }
    return safeAssistantCall(async () => projectState(await call('assistant_get_state', request), request.projectEpoch));
  },
  async testConnection(request: AssistantBridgeMethods['assistant_test_connection']['args'][0]): Promise<ConnectionStartResult> {
    if (!exact(request, ['configRevision', 'requestId']) || !natural(request.configRevision) || !id(request.requestId)) {
      throw new BridgeError('AI_INVALID_INPUT', 'Invalid assistant connection request.');
    }
    return safeAssistantCall(async () => {
      const result = await call('assistant_test_connection', request);
      if (!exact(result, ['jobId', 'status', 'scope', 'conversationId', 'kind'])
          || result.kind !== 'connection' || !id(result.jobId)
          || typeof result.status !== 'string'
          || !['Queued', 'Running', 'Completed', 'Cancelled', 'Failed', 'Stale'].includes(result.status)
          || result.scope !== null || result.conversationId !== null) {
        throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant connection receipt.');
      }
      return result as unknown as ConnectionStartResult;
    });
  },
  async getSettings(): Promise<AssistantSettings> {
    return safeAssistantCall(async () => settings(await call('assistant_get_settings')));
  },
  async configure(request: AssistantBridgeMethods['assistant_configure']['args'][0]): Promise<AssistantSettings> {
    if (!exact(request, ['baseUrl', 'model', 'key', 'enabled'])
        || !prose(request.baseUrl, 2048) || !prose(request.model, 128)
        || typeof request.enabled !== 'boolean'
        || (request.key !== null && request.key !== '' && !prose(request.key, 8192))
        || [request.baseUrl, request.model, request.key ?? ''].some(text => /\p{Cc}/u.test(text))) {
      throw new BridgeError('AI_INVALID_INPUT', 'Invalid assistant configuration.');
    }
    return safeAssistantCall(async () => settings(await call('assistant_configure', request)));
  },
  async newConversation(request: AssistantBridgeMethods['assistant_new_conversation']['args'][0]) {
    if (!exact(request, ['projectEpoch']) || !id(request.projectEpoch)) {
      throw new BridgeError('AI_INVALID_INPUT', 'Invalid assistant conversation request.');
    }
    return safeAssistantCall(async () => {
      const result = await call('assistant_new_conversation', request);
      if (!exact(result, ['conversationId', 'evictedConversationIds']) || !id(result.conversationId)
          || !Array.isArray(result.evictedConversationIds) || result.evictedConversationIds.length > 10
          || !result.evictedConversationIds.every(id)
          || new Set(result.evictedConversationIds).size !== result.evictedConversationIds.length
          || result.evictedConversationIds.includes(result.conversationId)) {
        throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant conversation receipt.');
      }
      return { conversationId: result.conversationId, evictedConversationIds: [...result.evictedConversationIds] };
    });
  },
  async getConsentCandidates(request: AssistantBridgeMethods['assistant_get_consent_candidates']['args'][0]) {
    if (!exact(request, ['projectEpoch', 'analysisGeneration']) || !id(request.projectEpoch)
        || !natural(request.analysisGeneration) || request.analysisGeneration === 0) {
      throw new BridgeError('AI_INVALID_INPUT', 'Invalid project candidate request.');
    }
    return safeAssistantCall(async () => {
      const result = await call('assistant_get_consent_candidates', request);
      if (!exact(result, ['projectEpoch', 'analysisGeneration', 'files'])
          || result.projectEpoch !== request.projectEpoch || result.analysisGeneration !== request.analysisGeneration
          || !selectedFiles(result.files)
          || !result.files.every((file, index, files) => index === 0 || pathPrecedes(files[index - 1], file))) {
        throw new BridgeError('INVALID_RESPONSE', 'Invalid project candidate response.');
      }
      return { projectEpoch: request.projectEpoch, analysisGeneration: request.analysisGeneration, files: [...result.files] };
    });
  },
  async clearConversation(request: AssistantBridgeMethods['assistant_clear_conversation']['args'][0]) {
    if (!exact(request, ['projectEpoch', 'conversationId']) || !id(request.projectEpoch) || !id(request.conversationId)) {
      throw new BridgeError('AI_INVALID_INPUT', 'Invalid assistant clear request.');
    }
    return safeAssistantCall(async () => {
      const result = await call('assistant_clear_conversation', request);
      if (!exact(result, ['cleared', 'conversationRevision']) || result.cleared !== true
          || !natural(result.conversationRevision)) {
        throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant clear receipt.');
      }
      return { cleared: true as const, conversationRevision: result.conversationRevision };
    });
  },
  async revokeConsent(request: AssistantBridgeMethods['assistant_revoke_consent']['args'][0]) {
    if (!exact(request, ['projectEpoch']) || !id(request.projectEpoch)) {
      throw new BridgeError('AI_INVALID_INPUT', 'Invalid assistant revoke request.');
    }
    return safeAssistantCall(async () => {
      const result = await call('assistant_revoke_consent', request);
      if (!exact(result, ['revoked', 'consentRevision']) || result.revoked !== true
          || !natural(result.consentRevision)) {
        throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant revoke receipt.');
      }
      return { revoked: true as const, consentRevision: result.consentRevision };
    });
  },
  async grantConsent(request: AssistantBridgeMethods['assistant_grant_consent']['args'][0]) {
    if (!exact(request, ['projectEpoch', 'analysisGeneration', 'configRevision', 'allowedFiles', 'confirmed'])
        || !id(request.projectEpoch) || !natural(request.analysisGeneration) || !natural(request.configRevision)
        || request.confirmed !== true || !selectedFiles(request.allowedFiles)) {
      throw new BridgeError('AI_INVALID_INPUT', 'Invalid assistant consent request.');
    }
    // Capture the explicit selection, not a later mutation of the UI array.
    const selection = new Set(request.allowedFiles);
    return safeAssistantCall(async () => {
      const result = await call('assistant_grant_consent', { ...request, allowedFiles: [...selection] });
      if (!exact(result, ['consentRevision', 'origin', 'allowedFiles', 'enabled'])
          || !natural(result.consentRevision) || !providerOrigin(result.origin)
          || typeof result.enabled !== 'boolean' || !selectedFiles(result.allowedFiles)
          || result.allowedFiles.length !== selection.size || !result.allowedFiles.every(path => selection.has(path))) {
        throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant consent receipt.');
      }
      return { consentRevision: result.consentRevision, origin: result.origin,
        allowedFiles: [...result.allowedFiles], enabled: result.enabled };
    });
  },
  async resolveEvidence(request: NavigationRequest): Promise<NavigationStartResult> {
    const keys = ['projectEpoch', 'analysisGeneration', 'conversationId', 'evidenceId', 'action'];
    if (typeof request === 'object' && request !== null && 'understanding' in request) keys.push('understanding');
    if (!exact(request, keys) || ('understanding' in request && !validUnderstandingItem(request.understanding))
        || !id(request.projectEpoch) || !natural(request.analysisGeneration) || !id(request.conversationId)
        || !prose(request.evidenceId, 512) || /[\p{C}\p{Z}]/u.test(request.evidenceId.replace(/ /g, ''))
        || (request.action !== 'source' && request.action !== 'graph')) {
      throw new BridgeError('AI_INVALID_INPUT', 'Invalid assistant navigation request.');
    }
    return safeAssistantCall(async () => {
      const result = await call('assistant_resolve_evidence', request);
      if (!validNavigationStart(result, request)) throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant navigation receipt.');
      return result;
    });
  },
  async start(request: AssistantStartRequest): Promise<AssistantStartResult> {
    if (!validStart(request)) throw new BridgeError('AI_INVALID_INPUT', 'Invalid assistant request.');
    return safeAssistantCall(async () => {
      const result = await call('assistant_start', request);
      if (!validStartResult(result, request)) throw new BridgeError('INVALID_RESPONSE', 'Invalid assistant start receipt.');
      return result;
    });
  },
  async poll(request: AssistantPollRequest): Promise<AssistantPoll> {
    if (!validPoll(request)) throw new BridgeError('AI_INVALID_INPUT', 'Invalid assistant poll request.');
    return safeAssistantCall(async () => assistantPoll(await call('assistant_poll', request), request));
  },
  async cancel(request: AssistantCancelRequest): Promise<AssistantCancelReceipt> {
    if (!exact(request, ['jobId', 'projectEpoch']) || !id(request.jobId)
        || (request.projectEpoch !== null && !id(request.projectEpoch))) {
      throw new BridgeError('AI_INVALID_INPUT', 'Invalid assistant cancellation request.');
    }
    return safeAssistantCall(async () => {
      const result = await call('assistant_cancel', request);
      return request.projectEpoch === null
        ? connectionCancelReceipt(result, request)
        : projectCancelReceipt(result, request);
    });
  },
};
