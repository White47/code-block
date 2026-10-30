import { BridgeError } from '../../api/bridge';
import { assistantClient } from './client';
import { captureAssistantContext } from './context';
import type { AssistantCancelReceipt, AssistantContext, AssistantIssue, AssistantPoll, AssistantStartRequest,
  AssistantState, AssistantUsage, ConnectionPoll, NavigationPoll, StateMessage } from './types';

type Client = Pick<typeof assistantClient, 'getState' | 'start' | 'poll' | 'cancel' | 'clearConversation'
  | 'newConversation' | 'testConnection' | 'resolveEvidence'>;
export interface AssistantStoreSnapshot {
  state: AssistantState | null;
  selectedConversationId: string | null;
  visible: boolean;
  sending: boolean;
  startRecoveryRequired: boolean;
  issue: AssistantIssue | null;
  jobs: (AssistantCancelReceipt & { purpose?: 'project_understanding' })[];
  projectPending?: boolean;
  usage: AssistantUsage | null;
  connection: ConnectionPoll | null;
  navigation: NavigationPoll | null;
}
interface TrackedJob {
  identity: AssistantCancelReceipt;
  timer: ReturnType<typeof setTimeout> | null;
  inFlight: boolean;
  cancelling: boolean;
  failures: number;
  cleanupAttempts: number;
  stalled: boolean;
  purpose?: 'project_understanding';
  navigation?: { evidenceId: string; action: 'source' | 'graph' };
}
interface PreparingSend {
  context: AssistantContext;
  projectEpoch: string;
  analysisGeneration: number;
  configRevision: number;
  consentRevision: number;
  cancelRequested: boolean;
}
const terminal = (status: string) => ['Completed', 'Failed', 'Cancelled', 'Stale'].includes(status);
const bindingKeys = ['kind', 'jobId', 'projectEpoch', 'analysisGeneration', 'conversationId',
  'conversationRevision', 'configRevision', 'consentRevision'] as const;
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const failure = (code: string) => new BridgeError(code, 'Assistant request could not be completed.');

/** Memory-only controlled display. Construction, subscription and reopening never send. */
export class AssistantStore {
  private current: AssistantStoreSnapshot = { state: null, selectedConversationId: null,
    visible: false, sending: false, startRecoveryRequired: false, issue: null, jobs: [],
    usage: null, connection: null, navigation: null };
  private listeners = new Set<() => void>();
  private tracked = new Map<string, TrackedJob>();
  private version = 0;
  private restoreSequence = 0;
  private disposed = false;
  private pending: { request: AssistantStartRequest; conversationRevision: number; cancelRequested: boolean } | null = null;
  private preparing: PreparingSend | null = null;
  private admissions = new Set<string>();
  constructor(private api: Client = assistantClient, private newId: () => string = () => crypto.randomUUID()) {}
  getSnapshot = () => this.current;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };

  private publish(change: Partial<AssistantStoreSnapshot>) {
    if (this.disposed) return;
    const candidate = { ...this.current, ...change,
      projectPending: this.pending?.request.context.kind === 'project' || this.preparing?.context.kind === 'project',
      jobs: [...this.tracked.values()].map(job => ({ ...clone(job.identity),
        ...(job.purpose ? { purpose: job.purpose } : {}) })) };
    if (candidate.state && (candidate.state.conversations.length > 10
        || candidate.state.conversations.some(chat => chat.messages.length > 40))) throw failure('AI_CAPACITY');
    if (new TextEncoder().encode(JSON.stringify(candidate)).length > 2 * 1024 * 1024) throw failure('AI_CAPACITY');
    this.current = candidate;
    this.listeners.forEach(listener => listener());
  }
  private issue(error: unknown) {
    this.publish({ issue: { code: error instanceof BridgeError ? error.code : 'INVALID_RESPONSE',
      message: 'Assistant request could not be completed.' } });
  }
  private complete(job: TrackedJob) {
    return job.identity.resourcesReleased && (job.cancelling
      ? job.identity.cancelAccepted : terminal(job.identity.status));
  }
  private schedule(job: TrackedJob) {
    if (this.disposed || this.complete(job) || job.timer !== null || job.inFlight || job.stalled) return;
    const attempts = Math.max(job.failures, job.cleanupAttempts);
    job.timer = setTimeout(() => { job.timer = null; void this.poll(job); },
      Math.min(4000, 250 * 2 ** Math.max(0, attempts - 1)));
  }
  private track(identity: AssistantCancelReceipt & { purpose?: 'project_understanding' }) {
    const existing = this.tracked.get(identity.jobId);
    if (existing) {
      if (!bindingKeys.every(key => identity[key] === existing.identity[key])) throw failure('INVALID_RESPONSE');
      if (identity.seq >= existing.identity.seq) existing.identity = clone(identity);
      existing.purpose ??= identity.purpose;
      existing.cancelling ||= identity.cancelRequested;
      this.schedule(existing);
      return existing;
    }
    if (this.tracked.size >= 2) {
      const finished = [...this.tracked].find(([, job]) => this.complete(job));
      if (!finished) throw failure('AI_CAPACITY');
      this.tracked.delete(finished[0]);
    }
    const job: TrackedJob = { identity: clone(identity), timer: null, inFlight: false,
      cancelling: identity.cancelRequested && !identity.cancelAccepted,
      failures: 0, cleanupAttempts: 0, stalled: false, purpose: identity.purpose };
    this.tracked.set(identity.jobId, job);
    this.schedule(job);
    return job;
  }
  async restore(projectEpoch: string) {
    const version = ++this.restoreSequence;
    if (this.current.state?.projectEpoch !== projectEpoch) {
      void this.stop();
      this.publish({ state: null, selectedConversationId: null, issue: null, navigation: null, usage: null,
        ...this.detachPending() });
    }
    const state = await this.api.getState({ projectEpoch });
    if (version !== this.restoreSequence || this.disposed) return;
    state.activeJobs.forEach(identity => this.track(identity));
    const pending = this.pending;
    const request = pending?.request;
    const preparing = this.preparing;
    const obsoletePreparation = preparing && (preparing.projectEpoch !== state.projectEpoch
      || preparing.analysisGeneration !== state.analysisGeneration || preparing.configRevision !== state.configRevision
      || !state.grant?.enabled || preparing.consentRevision !== state.grant.consentRevision);
    const obsolete = request && (request.projectEpoch !== state.projectEpoch
      || request.analysisGeneration !== state.analysisGeneration || request.configRevision !== state.configRevision
      || !state.grant?.enabled || request.consentRevision !== state.grant.consentRevision
      || !state.conversations.some(chat => chat.conversationId === request.conversationId
        && chat.conversationRevision === pending.conversationRevision));
    this.publish({ ...(obsolete || obsoletePreparation ? this.detachPending() : {}), state: clone(state), navigation: null, selectedConversationId: state.conversations.some(
      chat => chat.conversationId === this.current.selectedConversationId)
      ? this.current.selectedConversationId : state.conversations[0]?.conversationId ?? null });
    await Promise.all([...this.tracked.values()].filter(job => job.identity.kind !== 'connection'
      && !this.owns(job.identity)).map(job => this.cancel(job)));
  }
  private detachPending(): Partial<AssistantStoreSnapshot> {
    if (!this.pending && !this.preparing) return {};
    // Its in-flight submit still holds this object and must cancel a late receipt.
    if (this.pending) this.pending.cancelRequested = true;
    if (this.preparing) this.preparing.cancelRequested = true;
    this.pending = null; this.preparing = null;
    return { sending: false, startRecoveryRequired: false, issue: null };
  }
  async openPanel() {
    this.tracked.forEach(job => { job.stalled = false; job.failures = 0; job.cleanupAttempts = 0; this.schedule(job); });
    this.publish({ visible: true });
    const projectEpoch = this.current.state?.projectEpoch;
    if (projectEpoch) {
      try { await this.restore(projectEpoch); }
      catch (error) { this.issue(error); }
    }
  }
  async closePanel() { this.version++; this.publish({ visible: false }); await this.stopPanel(); }

  private async stopPanel() {
    if (this.preparing && this.preparing.context.kind !== 'project') this.preparing.cancelRequested = true;
    if (this.pending && this.pending.request.context.kind !== 'project') this.pending.cancelRequested = true;
    // Restored navigation is an observer, not proof that this panel owns the consumer.
    await Promise.all([...this.tracked.values()].filter(job => job.purpose !== 'project_understanding'
      && (job.identity.kind !== 'navigation' || job.navigation !== undefined))
      .map(job => this.cancel(job)));
  }

  private detachChatPending() {
    return this.pending?.request.context.kind === 'project' || this.preparing?.context.kind === 'project'
      ? {} : this.detachPending();
  }

  async selectConversation(conversationId: string) {
    if (!this.current.state?.conversations.some(chat => chat.conversationId === conversationId)) {
      throw failure('AI_RESULT_UNAVAILABLE');
    }
    if (conversationId === this.current.selectedConversationId) return;
    this.version++;
    this.publish({ ...this.detachChatPending(), selectedConversationId: conversationId,
      navigation: null, usage: null, issue: null });
    await this.stopPanel();
  }

  async newConversation() {
    return this.createConversation();
  }

  private async createConversation(preparing?: PreparingSend) {
    const state = this.current.state, version = this.version;
    const selected = this.current.selectedConversationId;
    if (!state) throw failure('AI_ANALYSIS_REQUIRED');
    if (this.admissions.has('new')) throw failure('AI_REQUEST_LIMIT');
    this.admissions.add('new');
    try {
      const result = await this.api.newConversation({ projectEpoch: state.projectEpoch });
      if ((!preparing || preparing.context.kind !== 'project') && version !== this.version) return;
      if (this.current.state?.projectEpoch !== state.projectEpoch || this.disposed
        || (preparing && (this.preparing !== preparing || preparing.cancelRequested))) return;
      const next = clone(this.current.state);
      next.conversations = next.conversations.filter(chat => !result.evictedConversationIds.includes(chat.conversationId));
      next.conversations.push({ conversationId: result.conversationId, conversationRevision: 0, messages: [] });
      this.version++;
      this.restoreSequence++;
      const keepSelection = preparing?.context.kind === 'project' && this.current.selectedConversationId !== selected
        && next.conversations.some(chat => chat.conversationId === this.current.selectedConversationId);
      this.publish({ ...(preparing ? {} : this.detachChatPending()), state: next,
        selectedConversationId: keepSelection ? this.current.selectedConversationId : result.conversationId,
        navigation: null, usage: null });
      if (!preparing) await this.stopPanel();
      await Promise.all([...this.tracked.values()].filter(job => !this.owns(job.identity)
        && job.identity.kind !== 'connection').map(job => this.cancel(job)));
      return result.conversationId;
    } finally { this.admissions.delete('new'); }
  }

  private begin(kind: 'connection' | 'navigation') {
    if (this.admissions.has(kind) || [...this.tracked.values()].filter(job => !this.complete(job)).length
        + this.admissions.size + (this.pending ? 1 : 0) >= 2) throw failure('AI_REQUEST_LIMIT');
    this.admissions.add(kind);
  }
  async testConnection(configRevision: number) {
    this.begin('connection');
    try {
      const result = await this.api.testConnection({ configRevision, requestId: this.newId() });
      this.track({ kind: 'connection', jobId: result.jobId, projectEpoch: null,
        analysisGeneration: null, conversationId: null, conversationRevision: null,
        configRevision, consentRevision: null, status: result.status, seq: 0,
        cancelRequested: false, cancelAccepted: false, cancellation: 'none',
        dispatchClosed: terminal(result.status), resources: 'held', resourcesReleased: false, issue: null });
      this.publish({ connection: null, issue: null });
      return result;
    } finally { this.admissions.delete('connection'); }
  }

  async resolveEvidence(evidenceId: string, action: 'source' | 'graph') {
    const state = this.current.state;
    const chat = state?.conversations.find(item => item.conversationId === this.current.selectedConversationId);
    const card = chat?.messages.flatMap(message => message.type === 'validated_answer' && !message.stale
      ? message.answer.evidence : []).find(item => item.evidenceId === evidenceId);
    if (!state || state.analysisGeneration === null || !chat || !state.grant?.enabled || !card
        || action === 'graph' && !card.graphAvailable) throw failure('AI_RESULT_UNAVAILABLE');
    this.begin('navigation');
    try {
      const result = await this.api.resolveEvidence({ projectEpoch: state.projectEpoch,
        analysisGeneration: state.analysisGeneration, conversationId: chat.conversationId, evidenceId, action });
      const job = this.track({ kind: 'navigation', jobId: result.jobId, projectEpoch: state.projectEpoch,
        analysisGeneration: state.analysisGeneration, conversationId: chat.conversationId,
        conversationRevision: chat.conversationRevision, configRevision: state.configRevision,
        consentRevision: state.grant.consentRevision, status: result.status, seq: 0,
        cancelRequested: false, cancelAccepted: false, cancellation: 'none',
        dispatchClosed: terminal(result.status), resources: 'held', resourcesReleased: false, issue: null });
      job.navigation = { evidenceId, action };
      this.publish({ navigation: null, issue: null });
      if (!this.current.visible || !this.owns(job.identity)) await this.cancel(job);
      return result;
    } finally { this.admissions.delete('navigation'); }
  }

  private owns(job: AssistantCancelReceipt) {
    const state = this.current.state;
    return job.kind !== 'connection' && state !== null && state.grant?.enabled === true
      && job.projectEpoch === state.projectEpoch && job.analysisGeneration === state.analysisGeneration
      && job.configRevision === state.configRevision && job.consentRevision === state.grant.consentRevision
      && state.conversations.some(chat => chat.conversationId === job.conversationId
        && chat.conversationRevision === job.conversationRevision);
  }

  async clearConversation() {
    const state = this.current.state;
    const conversationId = this.current.selectedConversationId;
    if (!state || !conversationId) throw failure('AI_ANALYSIS_REQUIRED');
    const result = await this.api.clearConversation({ projectEpoch: state.projectEpoch, conversationId });
    // A real owner invalidation outlives the selected chat, but never a project switch.
    if (this.disposed || this.current.state?.projectEpoch !== state.projectEpoch) return;
    const next = clone(this.current.state!);
    const chat = next.conversations.find(item => item.conversationId === conversationId);
    if (!chat || result.conversationRevision <= chat.conversationRevision) return;
    chat.messages = []; chat.conversationRevision = result.conversationRevision;
    this.version++;
    this.restoreSequence++;
    const detach = this.pending?.request.conversationId === conversationId ? this.detachPending() : {};
    if (next.projectUnderstanding?.conversationId === conversationId) delete next.projectUnderstanding;
    this.publish({ ...detach, state: next, navigation: null, usage: null });
    await Promise.all([...this.tracked.values()].filter(job => job.identity.conversationId === conversationId)
      .map(job => this.cancel(job)));
  }

  async send(question: string, context: AssistantContext) {
    const capturedContext = captureAssistantContext(context);
    let state = this.current.state;
    let chat = state?.conversations.find(item => item.conversationId === this.current.selectedConversationId);
    if (!state || state.analysisGeneration === null) throw failure('AI_ANALYSIS_REQUIRED');
    if (!state.grant?.enabled || state.configRevision === 0) throw failure('AI_CONSENT_REQUIRED');
    if (this.preparing || this.pending || this.current.sending || [...this.tracked.values()].some(job => !this.complete(job))) throw failure('AI_REQUEST_LIMIT');
    let preparing: PreparingSend | null = null;
    try {
    if (!chat) {
      const scope = [state.projectEpoch, state.analysisGeneration, state.configRevision, state.grant.consentRevision];
      preparing = { context: capturedContext, projectEpoch: state.projectEpoch, analysisGeneration: state.analysisGeneration,
        configRevision: state.configRevision, consentRevision: state.grant.consentRevision, cancelRequested: false };
      this.preparing = preparing;
      this.publish({ sending: true, issue: null });
      const created = await this.createConversation(preparing);
      if (preparing.cancelRequested || this.preparing !== preparing || this.disposed) {
        const current = this.current.state;
        if (!this.disposed && (current?.projectEpoch !== preparing.projectEpoch
          || current.analysisGeneration !== preparing.analysisGeneration || current.configRevision !== preparing.configRevision
          || !current.grant?.enabled || current.grant.consentRevision !== preparing.consentRevision)) throw failure('AI_ANALYSIS_STALE');
        return;
      }
      const next = this.current.state;
      if (!created || !next || next.analysisGeneration === null || !next.grant?.enabled
          || (capturedContext.kind !== 'project' && this.current.selectedConversationId !== created)
          || JSON.stringify(scope) !== JSON.stringify([next.projectEpoch, next.analysisGeneration,
            next.configRevision, next.grant.consentRevision])) throw failure('AI_ANALYSIS_STALE');
      state = next;
      chat = state.conversations.find(item => item.conversationId === created);
    }
    if (!chat || state.analysisGeneration === null || !state.grant?.enabled) throw failure('AI_ANALYSIS_STALE');
    if (this.pending || (!preparing && this.current.sending) || [...this.tracked.values()].some(job => !this.complete(job))) throw failure('AI_REQUEST_LIMIT');
    if (chat.messages.length > 38) throw failure('AI_CAPACITY');
    const request: AssistantStartRequest = clone({ projectEpoch: state.projectEpoch,
      analysisGeneration: state.analysisGeneration, conversationId: chat.conversationId,
      requestId: this.newId(), configRevision: state.configRevision,
      consentRevision: state.grant.consentRevision, question, context: capturedContext });
    this.pending = { request, conversationRevision: chat.conversationRevision, cancelRequested: false };
    if (this.preparing === preparing) this.preparing = null;
    return await this.submit(this.pending);
    } catch (error) {
      if (preparing && !preparing.cancelRequested) this.issue(error);
      throw error;
    } finally {
      if (preparing && this.preparing === preparing) {
        this.preparing = null; this.publish({ sending: false });
      }
    }
  }

  async recoverStart() {
    const pending = this.pending;
    const state = this.current.state;
    if (!pending || this.current.sending) throw failure('AI_REQUEST_CONFLICT');
    const request = pending.request;
    const chat = state?.conversations.find(item => item.conversationId === request.conversationId);
    if (!state || state.projectEpoch !== request.projectEpoch || state.analysisGeneration !== request.analysisGeneration
        || state.configRevision !== request.configRevision || !state.grant?.enabled
        || state.grant.consentRevision !== request.consentRevision
        || chat?.conversationRevision !== pending.conversationRevision) throw failure('AI_ANALYSIS_STALE');
    return this.submit(pending);
  }

  private async submit(pending: NonNullable<AssistantStore['pending']>) {
    const { request, conversationRevision } = pending;
    this.publish({ sending: true, issue: null });
    try {
      const result = await this.api.start(request);
      const job = this.track({ kind: 'explanation', jobId: result.jobId,
        ...(request.context.kind === 'project' ? { purpose: 'project_understanding' as const } : {}),
        projectEpoch: request.projectEpoch, analysisGeneration: request.analysisGeneration,
        conversationId: request.conversationId, conversationRevision,
        configRevision: request.configRevision, consentRevision: request.consentRevision,
        status: result.status, seq: 0, cancelRequested: false, cancelAccepted: false,
        cancellation: 'none', dispatchClosed: false, resources: 'held', resourcesReleased: false, issue: null });
      const current = this.current.state;
      const eligible = current?.projectEpoch === request.projectEpoch
        && current.analysisGeneration === request.analysisGeneration
        && current.configRevision === request.configRevision
        && current.grant?.consentRevision === request.consentRevision && current.grant.enabled
        && (request.context.kind === 'project' || this.current.selectedConversationId === request.conversationId)
        && current.conversations.some(item => item.conversationId === request.conversationId
          && item.conversationRevision === conversationRevision);
      if (eligible && current) {
        const message: StateMessage = { type: 'question', text: request.question, jobId: result.jobId,
          projectEpoch: request.projectEpoch, analysisGeneration: request.analysisGeneration,
          conversationRevision, configRevision: request.configRevision,
          consentRevision: request.consentRevision, seq: 0, stale: false };
        const next = clone(current);
        const owner = next.conversations.find(item => item.conversationId === request.conversationId);
        if (owner && !owner.messages.some(item => item.jobId === result.jobId)) owner.messages.push(message);
        this.publish({ state: next });
      }
      if (this.pending === pending) { this.pending = null; this.publish({ startRecoveryRequired: false }); }
      if (pending.cancelRequested || !eligible || (!this.current.visible && request.context.kind !== 'project')) await this.cancel(job);
      return result;
    } catch (error) {
      const ambiguous = error instanceof BridgeError && ['BRIDGE_TIMEOUT', 'BRIDGE_ERROR', 'INVALID_RESPONSE'].includes(error.code);
      if (this.pending === pending) {
        if (!ambiguous) this.pending = null;
        this.publish({ startRecoveryRequired: ambiguous }); this.issue(error);
      }
      throw error;
    }
    finally { if (this.pending === pending || this.pending === null) this.publish({ sending: false }); }
  }
  private async cancel(job: TrackedJob) {
    if (this.complete(job)) return true;
    job.cancelling = true;
    try {
      const receipt = await this.api.cancel({ jobId: job.identity.jobId, projectEpoch: job.identity.projectEpoch });
      if (!bindingKeys.every(key => receipt[key] === job.identity[key])) throw failure('INVALID_RESPONSE');
      // A completed-before-cancel receipt is not a cancellation confirmation.
      if (!receipt.cancelRequested && terminal(receipt.status) && receipt.resourcesReleased) job.cancelling = false;
      if (receipt.seq >= job.identity.seq) job.identity = clone(receipt);
      this.publish({});
      return true;
    } catch (error) { this.issue(error); return false; }
    finally { this.schedule(job); }
  }
  async stop() {
    const pending = this.pending;
    const preparing = this.preparing;
    if (preparing) preparing.cancelRequested = true;
    if (pending) pending.cancelRequested = true;
    const delivered = await Promise.all([...this.tracked.values()].map(job => this.cancel(job)));
    // Before a start receipt, Stop is retained intent, not delivered cancellation.
    return pending === null && preparing === null && delivered.every(Boolean);
  }

  private apply(poll: AssistantPoll): AssistantState | null {
    const state = this.current.state;
    if (poll.kind !== 'explanation' || !state || poll.projectEpoch !== state.projectEpoch
        || poll.analysisGeneration !== state.analysisGeneration || poll.configRevision !== state.configRevision
        || poll.consentRevision !== state.grant?.consentRevision) return state;
    const next = clone(state);
    next.conversations = next.conversations.filter(chat => !poll.evictedConversationIds.includes(chat.conversationId));
    const chat = next.conversations.find(item => item.conversationId === poll.conversationId
      && item.conversationRevision === poll.conversationRevision);
    if (chat) {
      const question = chat.messages.find(item => item.jobId === poll.jobId && item.type === 'question');
      if (question) {
        chat.messages = chat.messages.filter(item => item.jobId !== poll.jobId || item.type === 'question');
        question.seq = poll.seq;
        question.stale ||= poll.status === 'Stale';
        if (poll.answer !== null) chat.messages.push({ jobId: question.jobId, projectEpoch: question.projectEpoch,
          analysisGeneration: question.analysisGeneration, conversationRevision: question.conversationRevision,
          configRevision: question.configRevision, consentRevision: question.consentRevision,
          seq: question.seq, stale: question.stale, type: 'validated_answer', answer: clone(poll.answer) });
        else if (poll.displayText) chat.messages.push({ ...question, type: 'provisional', text: poll.displayText });
      }
    }
    return next;
  }
  private async poll(job: TrackedJob) {
    if (this.disposed || job.inFlight || this.complete(job)) return;
    job.inFlight = true;
    const afterSeq = job.identity.seq;
    try {
      const result = await this.api.poll({ jobId: job.identity.jobId,
        projectEpoch: job.identity.projectEpoch, afterSeq });
      if (this.disposed) return;
      if (!bindingKeys.every(key => result[key] === job.identity[key])) throw failure('INVALID_RESPONSE');
      if (result.kind === 'navigation' && job.navigation && (result.evidenceId !== job.navigation.evidenceId
          || result.action !== job.navigation.action)) throw failure('INVALID_RESPONSE');
      if (result.seq < job.identity.seq) return;
      const state = this.apply(result); // Full snapshots replace displays, including reset.
      const { kind, jobId, projectEpoch, analysisGeneration, conversationId, conversationRevision,
        configRevision, consentRevision, status, seq, cancelRequested, cancelAccepted, cancellation,
        dispatchClosed, resources, resourcesReleased, issue } = result;
      const previous = job.identity;
      job.identity = { kind, jobId, projectEpoch, analysisGeneration, conversationId, conversationRevision,
        configRevision, consentRevision, status, seq, cancelRequested, cancelAccepted, cancellation,
        dispatchClosed, resources, resourcesReleased, issue } as AssistantCancelReceipt;
      job.cancelling ||= result.cancelRequested;
      job.failures = 0;
      if (!this.complete(job) && (job.cancelling || terminal(result.status))) job.cleanupAttempts++;
      const extra: Partial<AssistantStoreSnapshot> = result.kind === 'connection'
        ? { connection: { ...result, events: [], reset: true } }
        : this.owns(job.identity) && result.kind === 'explanation' ? { usage: clone(result.usage) }
          : this.owns(job.identity) && result.kind === 'navigation' && job.navigation
            && this.current.selectedConversationId === result.conversationId
            ? { navigation: { ...result, events: [], reset: true } } : {};
      try { this.publish({ state, ...extra }); }
      catch (error) { job.identity = previous; throw error; }
      if (job.purpose === 'project_understanding' && result.kind === 'explanation'
        && result.status === 'Completed' && result.answer && this.owns(job.identity)) {
        // Project artifacts are published by the server transaction, never synthesized from chat text.
        await this.restore(result.projectEpoch);
      }
    } catch (error) { if (!this.disposed) { job.failures++; this.issue(error); } }
    finally {
      job.inFlight = false;
      if (Math.max(job.failures, job.cleanupAttempts) >= 8 && !this.complete(job)) {
        job.stalled = true;
        this.issue(failure('AI_NETWORK_TIMEOUT'));
      }
      this.schedule(job);
    }
  }
  dispose() {
    void this.stop(); // Deliver cancellation before detaching local timers; never call this confirmation.
    this.disposed = true;
    this.version++;
    this.restoreSequence++;
    this.tracked.forEach(job => { if (job.timer !== null) clearTimeout(job.timer); });
    this.listeners.clear();
  }
}
