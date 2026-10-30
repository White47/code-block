import type { AssistantCancelReceipt, AssistantSettings } from './types';
import type { AssistantStoreSnapshot } from './store';

export const assistantJobReleased = (job: AssistantCancelReceipt) => job.resourcesReleased
  && (job.cancelRequested ? job.cancelAccepted : ['Completed', 'Failed', 'Cancelled', 'Stale'].includes(job.status));

/** UI readiness never grants consent or submits a request. */
export function sendBlockReason(snapshot: AssistantStoreSnapshot, settings: AssistantSettings | null, blocked: boolean) {
  if (!settings) return 'aiSendLoading';
  if (!settings.available) return 'aiUnavailable';
  if (!settings.enabled) return 'aiSendEnable';
  if (!settings.keySet) return 'aiSendKey';
  if (!snapshot.state) return 'aiSendProject';
  if (blocked || snapshot.state.analysisGeneration === null) return 'aiSendAnalysis';
  if (settings.configRevision !== snapshot.state.configRevision) return 'aiSendSync';
  if (snapshot.startRecoveryRequired) return 'aiSendRecover';
  if (snapshot.sending || snapshot.jobs.some(job => !assistantJobReleased(job))) return 'aiSendWaiting';
  if (!snapshot.state.grant?.enabled) return 'aiSendConsent';
  return null;
}
