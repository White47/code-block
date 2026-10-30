import { BridgeError } from '../../api/bridge';
import { assistantClient } from './client';
import { assistantJobReleased } from './readiness';
import { effectiveProjectUnderstanding, understandingEvidence, type EffectiveUnderstanding } from './project-understanding';
import type { AssistantIssue, AssistantState, NavigationCancelReceipt, NavigationPoll, ProjectUnderstanding } from './types';

export interface WorkspaceNavigationContext { origin: string; model: EffectiveUnderstanding }
/** Read the confirmed store now; a render frame cannot restore an old owner/version. */
export function currentWorkspaceNavigation(displayed: WorkspaceNavigationContext | null, state: AssistantState | null): WorkspaceNavigationContext | null {
  const model = effectiveProjectUnderstanding(state);
  const fields = ['artifactId', 'revision', 'projectEpoch', 'analysisGeneration', 'conversationId',
    'conversationRevision', 'jobId', 'configRevision', 'consentRevision'] as const;
  if (!displayed || !model || !fields.every(field => displayed.model.reference[field] === model.reference[field])) return null;
  return { origin: displayed.origin, model };
}
type Api = Pick<typeof assistantClient, 'resolveEvidence' | 'poll' | 'cancel'>;
type Pending = { key: string; reference: ProjectUnderstanding; itemId: string; evidenceId: string;
  action: 'source' | 'graph'; receipt: NavigationCancelReceipt | null; cancelled: boolean;
  timer: ReturnType<typeof setTimeout> | null; inFlight: boolean; cancelling: boolean; failures: number; cleanupAttempts: number };
const identityKeys = ['kind', 'jobId', 'projectEpoch', 'analysisGeneration', 'conversationId',
  'conversationRevision', 'configRevision', 'consentRevision'] as const;
const failure = (code = 'AI_RESULT_UNAVAILABLE') => new BridgeError(code, 'Workspace navigation unavailable.');
function key(context: WorkspaceNavigationContext | null) {
  if (!context) return null;
  const ref = context.model.reference;
  return JSON.stringify([context.origin, ref.projectEpoch, ref.analysisGeneration, ref.artifactId,
    ref.revision, ref.conversationId, ref.conversationRevision, ref.configRevision, ref.consentRevision]);
}

/** One read-only C6 consumer, independent of sidebar visibility and selected chat. */
export class WorkspaceNavigation {
  private snapshot: { busy: boolean; issue: AssistantIssue | null } = { busy: false, issue: null };
  private listeners = new Set<() => void>();
  private pending: Pending | null = null;
  private disposed = false;
  constructor(private current: () => WorkspaceNavigationContext | null,
    private consume: (poll: NavigationPoll) => void, private api: Api = assistantClient) {}
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private publish(issue: AssistantIssue | null = this.snapshot.issue) {
    if (this.disposed) return;
    this.snapshot = { busy: this.pending !== null, issue }; this.listeners.forEach(listener => listener());
  }
  private issue(error: unknown) {
    this.publish({ code: error instanceof BridgeError ? error.code : 'INVALID_RESPONSE', message: 'Workspace navigation unavailable.' });
  }
  private owns(pending: Pending) { return !pending.cancelled && key(this.current()) === pending.key; }
  private finish(pending: Pending) {
    if (pending.timer !== null) clearTimeout(pending.timer);
    if (this.pending === pending) this.pending = null;
    this.publish();
  }
  private schedule(pending: Pending) {
    if (this.disposed || this.pending !== pending || pending.inFlight || pending.timer !== null
      || Math.max(pending.failures, pending.cleanupAttempts) >= 8) return;
    pending.timer = setTimeout(() => { pending.timer = null; void this.poll(pending); },
      Math.min(4000, 250 * 2 ** Math.max(pending.failures, pending.cleanupAttempts)));
  }
  async resolve(itemId: string, evidenceId: string, action: 'source' | 'graph') {
    const context = this.current();
    if (!context || this.disposed) throw failure();
    if (this.pending) throw failure('AI_REQUEST_LIMIT');
    const card = understandingEvidence(context.model, itemId).find(card => card.evidenceId === evidenceId);
    if (!card || (action === 'source' ? !card.sourceAvailable : !card.graphAvailable)) throw failure();
    const reference = { ...context.model.reference };
    const pending: Pending = { key: key(context)!, reference, itemId, evidenceId, action,
      receipt: null, cancelled: false, timer: null, inFlight: false, cancelling: false, failures: 0, cleanupAttempts: 0 };
    this.pending = pending; this.publish(null);
    try {
      const started = await this.api.resolveEvidence({ projectEpoch: reference.projectEpoch,
        analysisGeneration: reference.analysisGeneration, conversationId: reference.conversationId, evidenceId, action,
        understanding: { artifactId: reference.artifactId, revision: reference.revision, itemId } });
      if (started.kind !== 'navigation' || started.conversationId !== reference.conversationId
        || started.scope.projectEpoch !== reference.projectEpoch || started.scope.analysisGeneration !== reference.analysisGeneration) throw failure('INVALID_RESPONSE');
      pending.receipt = { kind: 'navigation', jobId: started.jobId, projectEpoch: reference.projectEpoch,
        analysisGeneration: reference.analysisGeneration, conversationId: reference.conversationId,
        conversationRevision: reference.conversationRevision, configRevision: reference.configRevision,
        consentRevision: reference.consentRevision, status: started.status, seq: 0, cancelRequested: false,
        cancelAccepted: false, cancellation: 'none', dispatchClosed: false, resources: 'held', resourcesReleased: false, issue: null };
      if (!this.owns(pending) || this.disposed) await this.cancel(pending);
      else this.schedule(pending);
    } catch (error) {
      if (!pending.receipt) this.finish(pending);
      this.issue(error); throw error;
    }
  }
  private async cancel(pending: Pending) {
    pending.cancelled = true;
    if (!pending.receipt || pending.cancelling) return;
    pending.cancelling = true;
    try {
      const receipt = await this.api.cancel({ jobId: pending.receipt.jobId, projectEpoch: pending.reference.projectEpoch });
      if (receipt.kind !== 'navigation' || !identityKeys.every(field => receipt[field] === pending.receipt![field])) throw failure('INVALID_RESPONSE');
      if (receipt.seq >= pending.receipt.seq) pending.receipt = receipt;
      if (assistantJobReleased(pending.receipt)) this.finish(pending);
    } catch (error) { pending.failures++; this.issue(error); }
    finally { pending.cancelling = false; this.schedule(pending); }
  }
  private async poll(pending: Pending) {
    if (this.pending !== pending || !pending.receipt || pending.inFlight) return;
    pending.inFlight = true;
    try {
      if (!this.owns(pending)) await this.cancel(pending);
      if (this.pending !== pending) return;
      const result = await this.api.poll({ jobId: pending.receipt.jobId, projectEpoch: pending.reference.projectEpoch, afterSeq: pending.receipt.seq });
      if (this.pending !== pending) return;
      if (result.kind !== 'navigation' || result.action !== pending.action || result.evidenceId !== pending.evidenceId
        || !identityKeys.every(field => result[field] === pending.receipt![field])) throw failure('INVALID_RESPONSE');
      if (result.seq < pending.receipt.seq) return;
      pending.receipt = result; pending.failures = 0;
      if (assistantJobReleased(result)) {
        const canConsume = this.owns(pending) && result.status === 'Completed' && result.target !== null;
        this.finish(pending); this.publish(result.issue);
        if (canConsume) this.consume(result);
      } else {
        if (pending.cancelled || !['Queued', 'Running'].includes(result.status)) pending.cleanupAttempts++;
        if (pending.cleanupAttempts >= 8) this.issue(failure('AI_NETWORK_TIMEOUT'));
        if (!this.owns(pending)) await this.cancel(pending);
      }
    } catch (error) {
      pending.failures++; this.issue(error);
      if (pending.failures >= 8) this.issue(failure('AI_NETWORK_TIMEOUT'));
    } finally { pending.inFlight = false; this.schedule(pending); }
  }
  invalidate() {
    if (this.pending && !this.owns(this.pending)) void this.cancel(this.pending);
  }
  async stop() {
    if (this.pending) { this.pending.failures = 0; this.pending.cleanupAttempts = 0; await this.cancel(this.pending); }
  }
  dispose() {
    void this.stop(); this.disposed = true;
    if (this.pending?.timer !== null && this.pending?.timer !== undefined) clearTimeout(this.pending.timer);
    this.listeners.clear();
  }
}
