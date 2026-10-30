import type { AssistantAnswer, AssistantContext, AssistantState, ProjectUnderstanding } from './types';

/** The server-owned reference selects the original validated answer; chat selection is irrelevant. */
export function effectiveProjectUnderstanding(state: AssistantState | null):
  { reference: ProjectUnderstanding; answer: AssistantAnswer } | null {
  const reference = state?.projectUnderstanding;
  if (!state || !reference || reference.projectEpoch !== state.projectEpoch
    || reference.analysisGeneration !== state.analysisGeneration || reference.configRevision !== state.configRevision
    || !state.grant?.enabled || reference.consentRevision !== state.grant.consentRevision) return null;
  const owner = state.conversations.find(chat => chat.conversationId === reference.conversationId
    && chat.conversationRevision === reference.conversationRevision);
  const messages = owner?.messages.filter(message => message.jobId === reference.jobId && message.type === 'validated_answer');
  if (messages?.length !== 1) return null;
  const message = messages[0];
  if (message.type !== 'validated_answer' || message.stale || message.projectEpoch !== reference.projectEpoch
    || message.analysisGeneration !== reference.analysisGeneration || message.configRevision !== reference.configRevision
    || message.consentRevision !== reference.consentRevision || message.conversationRevision !== reference.conversationRevision) return null;
  return { reference, answer: message.answer };
}

export type EffectiveUnderstanding = NonNullable<ReturnType<typeof effectiveProjectUnderstanding>>;

export function understandingItems(model: EffectiveUnderstanding) {
  return model.answer.sections.flatMap(section => section.claims.map((claim, index) => ({
    id: `${section.kind}:${index}`, section: section.kind, claim,
  })));
}

export function understandingEvidence(model: EffectiveUnderstanding, itemId: string) {
  if (itemId === 'project') return model.answer.evidence;
  const item = understandingItems(model).find(value => value.id === itemId);
  const ids = new Set(item ? [...item.claim.evidenceIds, ...(item.claim.relationEvidenceId ? [item.claim.relationEvidenceId] : [])] : []);
  return model.answer.evidence.filter(card => ids.has(card.evidenceId));
}

export function understandingContextAvailable(context: AssistantContext, state: AssistantState | null) {
  if (context.kind !== 'understanding') return true;
  const model = effectiveProjectUnderstanding(state);
  return model !== null && model.reference.artifactId === context.artifactId && model.reference.revision === context.revision
    && (context.itemId === 'project' || understandingItems(model).some(item => item.id === context.itemId));
}

