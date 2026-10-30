import { useRef, type ComponentProps } from 'react';
import AssistantPanel from './AssistantPanel';
import { AssistantRuntimeBoundary } from '../assistant/runtime';
import type { AssistantContext } from '../assistant/types';

/** Component tests reproduce the App-owned runtime; the production panel owns no runtime. */
export default function AssistantPanelHarness(props: Omit<ComponentProps<typeof AssistantPanel>, 'prepareContext'>) {
  const pending = useRef<AssistantContext | null>(null);
  return <AssistantRuntimeBoundary store={props.store} getContext={() => {
    const context = pending.current ?? props.context; pending.current = null; return context;
  }}><AssistantPanel {...props} prepareContext={context => { pending.current = context; }}/></AssistantRuntimeBoundary>;
}
