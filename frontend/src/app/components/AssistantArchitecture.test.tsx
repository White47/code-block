import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import AssistantPanel from './AssistantPanel.test-harness';
import AssistantArchitecture from './AssistantArchitecture';
import { AssistantStore } from '../assistant/store';
import { effectiveProjectUnderstanding } from '../assistant/project-understanding';
import { translator } from '../i18n';
import type { AssistantAnswer, AssistantSettings, AssistantStartRequest, AssistantState } from '../assistant/types';

vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ nodes, onNodeClick, children }: { nodes: { id: string; data: { label: string } }[];
    onNodeClick: (event: unknown, node: { id: string }) => void; children: ReactNode }) => <div>{nodes.map(node =>
      <button key={node.id} onClick={() => onNodeClick({}, node)}>{node.data.label}</button>)}{children}</div>,
  Background: () => null, Controls: () => null, Handle: () => null,
  MarkerType: { ArrowClosed: 'arrowclosed' }, Position: { Top: 'top', Bottom: 'bottom' },
}));
afterEach(cleanup);
function setup(project = true) {
  const answer: AssistantAnswer = { sections: [{ kind: 'summary', claims: [{ text: 'Input processing module',
    basis: 'inference', evidenceIds: [], relationEvidenceId: null }] }], evidence: [], relationFacts: [],
    incomplete: false, coverageGaps: [], checkedAt: '2026-10-24T00:00:00Z' };
  const state: AssistantState = { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    grant: { enabled: true, origin: 'https://provider.invalid:443', allowedFiles: ['app.py'], consentRevision: 1 },
    conversations: [{ conversationId: 'chat-1', conversationRevision: 0, messages: [{ type: 'validated_answer', answer,
      jobId: 'old-job', projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1, consentRevision: 1,
      conversationRevision: 0, stale: false, seq: 1 }] }], activeJobs: [] };
  if (project) state.conversations[0].messages.unshift({ type: 'question', text: translator('en')('aiProjectAnalysisPrompt'),
    jobId: 'old-job', projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1, consentRevision: 1,
    conversationRevision: 0, stale: false, seq: 0 });
  const api = { getState: vi.fn(async () => state), newConversation: vi.fn(), poll: vi.fn(), cancel: vi.fn(),
    clearConversation: vi.fn(), testConnection: vi.fn(), resolveEvidence: vi.fn(),
    start: vi.fn(async (request: AssistantStartRequest) => ({ kind: 'explanation' as const, jobId: 'job-1', status: 'Queued' as const,
      scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'request-1' }, conversationId: request.conversationId })) };
  return { api, answer, state, store: new AssistantStore(api, () => 'request-1') };
}
const settings: AssistantSettings = { available: true, enabled: true, keySet: true, baseUrl: 'https://provider.invalid/v1',
  model: 'test', configRevision: 1, issue: null };
const context = { kind: 'node' as const, viewKind: 'Architecture' as const, visibleNodeId: 'static-node', rootId: null, detailSourceId: null };
it('uses the same informational sampled-answer status in chat and legacy architecture without sending', async () => {
  const { store, api, answer } = setup(false);
  answer.incomplete = true;
  answer.coverageGaps = ['Project understanding used representative excerpts; full-project coverage is not established.'];
  await store.restore('epoch-1');
  const chat = render(<AssistantPanel store={store} settings={settings} context={context} blocked={false}
    dirty={false} onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  expect(screen.getByText('Explanation based on source excerpts')).toBeTruthy();
  expect(screen.queryByText('Incomplete explanation')).toBeNull();
  chat.unmount();
  render(<AssistantArchitecture answer={answer} disabled={false} onClose={vi.fn()}
    onAsk={vi.fn()} navigate={vi.fn()} t={translator('en')}/>);
  expect(screen.getByText('Explanation based on source excerpts')).toBeTruthy();
  expect(screen.queryByText('Incomplete explanation')).toBeNull();
  expect(api.start).not.toHaveBeenCalled();
  expect(answer.incomplete).toBe(true);
  store.dispose();
});

it('does not use a localized project prompt as authority or open a second interpretation canvas', async () => {
  const { store, api, state } = setup(); await store.restore('epoch-1');
  render(<AssistantPanel store={store} settings={settings} context={context} blocked={false} dirty={false}
    onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  expect(screen.queryByRole('button', { name: 'Open AI architecture' })).toBeNull();
  expect(effectiveProjectUnderstanding(state)).toBeNull();
  expect(api.start).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Ask about the project'), { target: { value: 'Explain the selected code' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await waitFor(() => expect(api.start).toHaveBeenCalledOnce());
  expect(api.start.mock.calls[0][0].context).toEqual(context); store.dispose();
});
it('starts explicit project analysis with project context even when a static node was selected', async () => {
  const { store, api } = setup(); await store.restore('epoch-1');
  render(<AssistantPanel store={store} settings={settings} context={context} blocked={false} dirty={false}
    onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  fireEvent.click(screen.getByRole('button', { name: 'Analyze project with AI' }));
  await waitFor(() => expect(api.start).toHaveBeenCalledOnce());
  expect(api.start.mock.calls[0][0].context).toEqual({ kind: 'project' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(api.start.mock.calls[0][0].question).toContain('summary'); store.dispose();
});
it('does not treat an ordinary answer as a project architecture', async () => {
  const { store, state } = setup(false); await store.restore('epoch-1');
  render(<AssistantPanel store={store} settings={settings} context={context} blocked={false} dirty={false}
    onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  expect(effectiveProjectUnderstanding(state)).toBeNull();
  expect(screen.queryByRole('button', { name: 'Open AI architecture' })).toBeNull();
  store.dispose();
});
it('uses the newly opened static node context after the project context was displayed', async () => {
  const { store, api } = setup(); await store.restore('epoch-1');
  const props = { store, settings, context, blocked: false, dirty: false,
    onSettings: vi.fn(), onNavigate: vi.fn(), t: translator('en') };
  const { rerender } = render(<AssistantPanel {...props} context={{ kind: 'project' }}/>);
  rerender(<AssistantPanel {...props} context={{ ...context }}/>);
  fireEvent.change(screen.getByLabelText('Ask about the project'), { target: { value: 'Explain this code node' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await waitFor(() => expect(api.start).toHaveBeenCalledOnce());
  expect(api.start.mock.calls[0][0].context).toEqual(context); store.dispose();
});
it('closes the canvas when opening source so it cannot obscure the source editor', () => {
  const { answer, store } = setup();
  answer.sections[0].claims[0].evidenceIds = ['e1'];
  answer.evidence = [{ evidenceId: 'e1', label: 'app.py', confidence: 'Exact', truncated: false,
    sourceAvailable: true, graphAvailable: false, unavailableReason: 'source_only' }];
  const close = vi.fn(), navigate = vi.fn();
  render(<AssistantArchitecture answer={answer} disabled={false} onClose={close} onAsk={vi.fn()}
    navigate={navigate} t={translator('en')}/>);
  fireEvent.click(screen.getByRole('button', { name: 'Input processing module' }));
  fireEvent.click(screen.getByRole('button', { name: 'Open source' }));
  expect(navigate).toHaveBeenCalledExactlyOnceWith('e1', 'source');
  expect(close).toHaveBeenCalledOnce(); store.dispose();
});

it('blocks an expired item question while keeping explicit project refresh available', async () => {
  const { store, state, api } = setup();
  state.projectUnderstanding = { schemaVersion: 1, artifactId: 'current-artifact', revision: 2,
    projectEpoch: 'epoch-1', analysisGeneration: 1, conversationId: 'chat-1', conversationRevision: 0,
    jobId: 'old-job', configRevision: 1, consentRevision: 1, coverage: { indexedFileCount: 1,
      authorizedFileCount: 1, metadataFileCount: 1, sourceFileCount: 1, gapCodes: [] } };
  await store.restore('epoch-1');
  render(<AssistantPanel store={store} settings={settings}
    context={{ kind: 'understanding', artifactId: 'expired-artifact', revision: 1, itemId: 'summary:0' }}
    blocked={false} dirty={false} onSettings={vi.fn()} onNavigate={vi.fn()} t={translator('en')}/>);
  fireEvent.change(screen.getByLabelText('Ask about the project'), { target: { value: 'Explain stale module' } });
  expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/This project understanding has expired/)).toBeTruthy();
  expect(api.start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Update AI architecture' }));
  await waitFor(() => expect(api.start).toHaveBeenCalledOnce());
  expect(api.start.mock.calls[0][0].context).toEqual({ kind: 'project' });
  store.dispose();
});
