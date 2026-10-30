import { useMemo, useRef, useSyncExternalStore, type ReactNode } from 'react';
import { AssistantRuntimeProvider, fromThreadMessageLike, useExternalStoreRuntime,
  type AppendMessage, type MessageStatus, type ThreadMessage } from '@assistant-ui/react';
import { BridgeError } from '../../api/bridge';
import type { AssistantContext, StateMessage } from './types';
import type { AssistantStore, AssistantStoreSnapshot } from './store';
import { assistantJobReleased as released } from './readiness';

type Props = { store: AssistantStore; getContext: () => AssistantContext; children: ReactNode };

function message(value: StateMessage, snapshot: AssistantStoreSnapshot): ThreadMessage {
  const user = value.type === 'question';
  const job = snapshot.jobs.find(item => item.jobId === value.jobId);
  const text = value.type === 'validated_answer'
    ? value.answer.sections.map(section => `${section.kind}\n${section.claims.map(claim => claim.text).join('\n')}`).join('\n\n')
    : value.text;
  const status: MessageStatus = value.type === 'validated_answer' ? { type: 'complete', reason: 'stop' }
    : job?.status === 'Failed' ? { type: 'incomplete', reason: 'error' }
      : job?.cancelRequested ? { type: 'incomplete', reason: 'cancelled' }
        : job && !released(job) ? { type: 'running' } : { type: 'incomplete', reason: 'other' };
  return fromThreadMessageLike({ id: `${value.jobId}:${user ? 'question' : 'assistant'}`,
    role: user ? 'user' : 'assistant', createdAt: new Date(0), content: [{ type: 'text', text }],
    attachments: [], ...(user ? {} : { status }), metadata: { custom: { display: value } } },
    `${value.jobId}:${user ? 'question' : 'assistant'}`, status);
}

/** Re-key the library repository on identity/revision changes; no cloud/history adapter. */
export function AssistantRuntimeBoundary(props: Props) {
  const snapshot = useSyncExternalStore(props.store.subscribe, props.store.getSnapshot, props.store.getSnapshot);
  const conversation = snapshot.state?.conversations.find(chat => chat.conversationId === snapshot.selectedConversationId);
  const scope = JSON.stringify([snapshot.state?.projectEpoch, snapshot.selectedConversationId,
    conversation?.conversationRevision]);
  return <ControlledRuntime key={scope} {...props}/>;
}

function ControlledRuntime({ store, getContext, children }: Props) {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const latestContext = useRef(getContext); latestContext.current = getContext;
  const chat = snapshot.state?.conversations.find(item => item.conversationId === snapshot.selectedConversationId);
  const messages = useMemo(() => (chat?.messages ?? []).map(value => message(value, snapshot)), [chat, snapshot]);
  const isRunning = snapshot.sending || snapshot.jobs.some(job => job.kind === 'explanation'
    && job.projectEpoch === snapshot.state?.projectEpoch && job.conversationId === chat?.conversationId
    && job.conversationRevision === chat?.conversationRevision && !released(job));
  const onNew = async (append: AppendMessage) => {
    if (append.role !== 'user' || append.attachments?.length
        || append.content.some(part => part.type !== 'text')) {
      throw new BridgeError('AI_INVALID_INPUT', 'Only explicit text questions are supported.');
    }
    const text = append.content.map(part => part.type === 'text' ? part.text : '').join('');
    await store.send(text, latestContext.current());
  };
  const runtime = useExternalStoreRuntime({ messages, isRunning, onNew,
    onCancel: async () => { await store.stop(); }, unstable_enableToolInvocations: false,
    isSendDisabled: !snapshot.state?.grant?.enabled || snapshot.state.analysisGeneration === null
      || snapshot.startRecoveryRequired || snapshot.jobs.some(job => !released(job)) });
  // The application owns close/project lifecycle. Library mount/unmount is side-effect free.
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}

/** An abort only detaches local work; backend acknowledgement remains in the store poller. */
export async function cancelBeforeLocalAbort(store: AssistantStore, local: AbortController): Promise<boolean> {
  if (!await store.stop()) return false;
  local.abort();
  return true;
}
