import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { graph, ok, overview, session, viewState } from './testFixtures';
import type { AssistantAnswer, AssistantStartRequest, AssistantState } from './assistant/types';
import type { Graph, ViewKind } from '../types';

vi.mock('./components/GraphEditor', () => ({ default: ({ graph: data, onSelect }: { graph: Graph;
  onSelect: (node: typeof graph.nodes[number], source: null) => void }) =>
  <section aria-label={`${data.viewKind} static graph`}>Static graph canvas
    {data.nodes.map(node => <p key={node.id}>{node.displayName}</p>)}
    {data.edges.map((edge, index) => <span key={index}>{edge.type}</span>)}
    <button onClick={() => onSelect(data.nodes[0], null)}>Select static node</button></section> }));
vi.mock('./components/SourceEditor', () => ({ default: ({ document }: { document: { filePath: string } }) => <div>Opened saved source: {document.filePath}</div> }));
afterEach(() => { cleanup(); delete window.pywebview; });

it('updates the original Overview from the server project artifact through the single runtime without opening the sidebar', async () => {
  const state: AssistantState = { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    grant: { origin: 'https://provider.invalid:443', allowedFiles: ['model.py', 'train.py'], enabled: true, consentRevision: 1 },
    conversations: [{ conversationId: 'chat-1', conversationRevision: 0, messages: [] }], activeJobs: [] };
  const answer: AssistantAnswer = { sections: (['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns'] as const)
    .map(kind => ({ kind, claims: kind === 'summary' ? [{ text: 'Coordinates training and persistence across modules',
      basis: 'inference', evidenceIds: ['ev-1'], relationEvidenceId: null }] : [] })),
    evidence: [{ evidenceId: 'ev-1', label: 'Saved model source', confidence: 'Exact', truncated: false,
      sourceAvailable: true, graphAvailable: false, unavailableReason: 'source_only' }],
    relationFacts: [], incomplete: true, coverageGaps: ['Representative source sample'], checkedAt: '2026-10-28T00:00:00Z' };
  const binding = { jobId: 'project-job', projectEpoch: 'epoch-1', analysisGeneration: 1,
    conversationRevision: 0, configRevision: 1, consentRevision: 1, seq: 1, stale: false };
  let followupOwner = 'chat-1';
  const followupAnswer: AssistantAnswer = { ...answer, evidence: [{ ...answer.evidence[0], evidenceId: 'fresh-ev' }],
    sections: answer.sections.map(section => ({ ...section, claims: section.kind === 'summary' ? [{ text: 'Fresh explanation of training dependencies',
      basis: 'inference', evidenceIds: ['fresh-ev'], relationEvidenceId: null }] : [] })) };
  const start = vi.fn(async (request: AssistantStartRequest) => {
    if (request.context.kind === 'understanding') {
      followupOwner = request.conversationId;
      state.conversations.find(chat => chat.conversationId === followupOwner)!.messages.push({ ...binding,
        jobId: 'followup-job', type: 'question', text: request.question });
      return ok({ kind: 'explanation' as const, jobId: 'followup-job', status: 'Queued' as const,
        conversationId: request.conversationId, scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: request.requestId } });
    }
    state.conversations[0].messages = [{ ...binding, type: 'question', text: request.question }];
    return ok({ kind: 'explanation' as const, jobId: 'project-job', status: 'Queued' as const, conversationId: request.conversationId,
      scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: request.requestId } });
  });
  const layout = { ...viewState, activeView: null, openTabs: ['Overview'] };
  const resolveEvidence = vi.fn(async () => ok({ kind: 'navigation', jobId: 'nav-1', status: 'Queued',
    conversationId: 'chat-1', scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'navigation-1' } }));
  const openDetail = vi.fn(async () => ok(viewState));
  const structure: Graph = { ...graph, viewKind: 'ProjectStructure', rootIds: ['project-root'],
    nodes: [{ ...graph.nodes[0], id: 'project-root', displayName: 'Source hierarchy', role: 'Project' },
      { ...graph.nodes[0], id: 'source-file', displayName: 'model.py', role: 'File' }],
    edges: [{ source: 'project-root', target: 'source-file', type: 'CONTAINS', confidence: 'Exact',
      sourceAnchors: [], codeEdgeIds: [], semanticComponentIds: [] }] };
  const getStaticGraph = vi.fn(async (kind: ViewKind) => ok(kind === 'ProjectStructure' ? structure : graph));
  const inspectStatic = vi.fn(async () => ok({ id: graph.nodes[0].id, displayName: 'Static node', codeName: 'static_node',
    role: 'Layer', confidence: 'Exact' as const, sourceAnchors: [], memberIds: [], data: [], children: [], evidence: [], relations: [], canOpenSource: false }));
  window.pywebview = { api: {
    get_session: vi.fn(async () => ok(session)), get_preferences: vi.fn(async () => ok({ language: 'en' as const, issue: null })),
    get_view_state: vi.fn(async () => ok(layout)), save_layout: vi.fn(async () => ok(layout)),
    get_overview: vi.fn(async () => ok(overview)), get_problems: vi.fn(async () => ok([])), get_view: getStaticGraph,
    get_inspector: inspectStatic, open_detail_island: openDetail,
    list_project_directory: vi.fn(async () => ok({ projectPath: session.projectPath!, projectEpoch: 'epoch-1', directory: '', directoryRevision: 'r1', entries: [], nextCursor: null })),
    assistant_get_settings: vi.fn(async () => ok({ available: true, enabled: true, keySet: true, baseUrl: 'https://provider.invalid/v1', model: 'test', configRevision: 1, issue: null })),
    assistant_get_state: vi.fn(async () => ok(structuredClone(state))), assistant_start: start,
    assistant_resolve_evidence: resolveEvidence,
    open_source_document: vi.fn(async () => ok({ projectPath: session.projectPath!, filePath: 'model.py', text: 'saved = 1',
      revision: 'saved-1', encoding: 'utf-8', newline: 'LF' as const, readOnlyReason: null })),
    assistant_poll: vi.fn(async (request: { jobId: string }) => {
      if (request.jobId === 'followup-job') {
        const followupBinding = { ...binding, jobId: 'followup-job' };
        state.conversations.find(chat => chat.conversationId === followupOwner)!.messages.push({ ...followupBinding,
          type: 'validated_answer', answer: followupAnswer });
        const followupPollBinding = { ...followupBinding }; delete (followupPollBinding as Partial<typeof followupBinding>).stale;
        return ok({ ...followupPollBinding, kind: 'explanation', conversationId: followupOwner, status: 'Completed',
          cancelRequested: false, cancelAccepted: false, cancellation: 'none', dispatchClosed: true, resources: 'released',
          resourcesReleased: true, issue: null, displayText: '', answer: followupAnswer, reset: true, events: [], evictedConversationIds: [],
          usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0, toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
            observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } } });
      }
      if (request.jobId === 'nav-1') return ok({ kind: 'navigation', jobId: 'nav-1', projectEpoch: 'epoch-1',
        analysisGeneration: 1, conversationId: 'chat-1', conversationRevision: 0, configRevision: 1, consentRevision: 1,
        seq: 1, status: 'Completed', cancelRequested: false, cancelAccepted: false, cancellation: 'none',
        dispatchClosed: true, resources: 'released', resourcesReleased: true, issue: null, action: 'source', evidenceId: 'ev-1',
        reset: true, events: [], evictedConversationIds: [], target: { evidenceId: 'ev-1', action: 'source',
          scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'project-question' },
          binding: { projectEpoch: 'epoch-1', analysisGeneration: 1 }, astAnchor: { filePath: 'model.py', startLine: 1, endLine: 1, startColumn: null, endColumn: null },
          monacoRange: null, reveal: null, checkedAt: '2026-10-28T00:00:00Z', conversationId: 'chat-1',
          conversationRevision: 0, configRevision: 1, consentRevision: 1 } });
      state.conversations[0].messages.push({ ...binding, type: 'validated_answer', answer });
      state.projectUnderstanding = { schemaVersion: 1, artifactId: 'artifact-1', revision: 1,
        jobId: 'project-job', projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1, consentRevision: 1,
        conversationRevision: 0, conversationId: 'chat-1', coverage: { indexedFileCount: 6, authorizedFileCount: 2,
          metadataFileCount: 2, sourceFileCount: 2, gapCodes: ['representative_sample'] } };
      // Only the closed artifact reference belongs to get_state, never poll.
      const pollBinding = { ...binding };
      delete (pollBinding as Partial<typeof binding>).stale;
      return ok({ ...pollBinding, kind: 'explanation', conversationId: 'chat-1', status: 'Completed', cancelRequested: false,
        cancelAccepted: false, cancellation: 'none', dispatchClosed: true, resources: 'released', resourcesReleased: true,
        issue: null, displayText: '', answer, reset: true, events: [], evictedConversationIds: [],
        usage: { reserved: { inputTokens: 0, outputTokens: 0, modelRequests: 0, toolCalls: 0, sourceCharacters: 0, fileCount: 0 },
          observed: { inputTokens: null, outputTokens: null, basis: 'unknown' } } });
    }),
  } };
  render(<App/>);
  await waitFor(() => expect((screen.getByRole('button', { name: 'Analyze project with AI' }) as HTMLButtonElement).disabled).toBe(false));
  expect(screen.queryByRole('region', { name: 'Project understanding' })).toBeNull();
  expect(start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Analyze project with AI' }));
  const model = await screen.findByRole('region', { name: 'Project understanding' });
  expect(within(model).getByText('Coordinates training and persistence across modules')).toBeTruthy();
  expect(within(model).getByText(/6 \/ 2 \/ 2 \/ 2/)).toBeTruthy();
  expect(start).toHaveBeenCalledTimes(1);
  expect(start.mock.calls[0][0].context).toEqual({ kind: 'project' });
  expect(screen.queryByRole('region', { name: 'AI Assistant' })).toBeNull();
  expect(document.querySelector('.overview-page')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Static analysis' }));
  expect(screen.queryByRole('region', { name: 'Project understanding' })).toBeNull();
  expect(document.querySelector('.overview-page')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'AI enhanced' }));
  expect(within(screen.getByRole('region', { name: 'Project understanding' })).getByText('Coordinates training and persistence across modules')).toBeTruthy();
  const currentModel = screen.getByRole('region', { name: 'Project understanding' });
  fireEvent.click(within(currentModel).getByText(/Related source and evidence/));
  fireEvent.click(within(currentModel).getByRole('button', { name: 'Open source' }));
  expect(await screen.findByText('Opened saved source: model.py')).toBeTruthy();
  expect(resolveEvidence).toHaveBeenCalledExactlyOnceWith({ projectEpoch: 'epoch-1', analysisGeneration: 1,
    conversationId: 'chat-1', evidenceId: 'ev-1', action: 'source',
    understanding: { artifactId: 'artifact-1', revision: 1, itemId: 'summary:0' } });
  const sidebar = document.querySelector('.sidebar')!;
  fireEvent.click(within(sidebar as HTMLElement).getByRole('button', { name: /Project structure/ }));
  const structureCanvas = await screen.findByRole('region', { name: 'ProjectStructure static graph' });
  expect(within(structureCanvas).getByText('Source hierarchy')).toBeTruthy();
  expect(within(structureCanvas).getByText('CONTAINS')).toBeTruthy();
  expect(screen.getByRole('region', { name: 'Project understanding' })).toBeTruthy();
  const structureRequests = getStaticGraph.mock.calls.length;
  fireEvent.click(screen.getByRole('button', { name: 'Static analysis' }));
  expect(screen.queryByRole('region', { name: 'Project understanding' })).toBeNull();
  expect(screen.getByRole('region', { name: 'ProjectStructure static graph' })).toBe(structureCanvas);
  fireEvent.click(screen.getByRole('button', { name: 'AI enhanced' }));
  expect(screen.getByRole('region', { name: 'Project understanding' })).toBeTruthy();
  expect(getStaticGraph).toHaveBeenCalledTimes(structureRequests);
  fireEvent.click(within(sidebar as HTMLElement).getByRole('button', { name: /Architecture/ }));
  const architectureCanvas = await screen.findByRole('region', { name: 'Architecture static graph' });
  expect(within(architectureCanvas).getByText('Encoder')).toBeTruthy();
  expect(within(architectureCanvas).queryByText('Source hierarchy')).toBeNull();
  const architectureRequests = getStaticGraph.mock.calls.length;
  fireEvent.click(screen.getByRole('button', { name: 'Static analysis' }));
  expect(await screen.findByText('Static graph canvas')).toBeTruthy();
  expect(getStaticGraph).toHaveBeenCalledTimes(architectureRequests);
  fireEvent.click(screen.getByRole('button', { name: 'Select static node' }));
  await waitFor(() => expect(inspectStatic).toHaveBeenCalled());
  expect(screen.queryByRole('region', { name: 'AI Assistant' })).toBeNull();
  state.conversations.push({ conversationId: 'chat-2', conversationRevision: 0, messages: [] });
  fireEvent.click(screen.getByRole('button', { name: 'AI enhanced' }));
  const disclosure = document.querySelector('.project-understanding-panel > summary') as HTMLElement;
  await act(async () => { fireEvent.keyDown(disclosure, { key: 'Enter' }); await Promise.resolve(); });
  expect(openDetail).not.toHaveBeenCalled();
  await act(async () => { fireEvent.keyDown(document, { key: 'Enter' }); await Promise.resolve(); });
  await waitFor(() => expect(openDetail).toHaveBeenCalled());
  const interpretation = screen.getByRole('region', { name: 'Project understanding' });
  fireEvent.click(within(interpretation).getByRole('button', { name: 'Ask about this node' }));
  let panel = await screen.findByRole('region', { name: 'AI Assistant' });
  expect(within(panel).getByText('Scope: selected module / step in project understanding')).toBeTruthy();
  expect(start).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(within(panel).getAllByRole('option')).toHaveLength(2));
  fireEvent.change(within(panel).getByLabelText('Conversation'), { target: { value: 'chat-2' } });
  panel = await screen.findByRole('region', { name: 'AI Assistant' });
  fireEvent.change(within(panel).getByLabelText('Ask about the project'), { target: { value: 'How do persistence and training cooperate?' } });
  fireEvent.click(within(panel).getByRole('button', { name: 'Send' }));
  expect(await screen.findByText('Fresh explanation of training dependencies')).toBeTruthy();
  expect(start).toHaveBeenCalledTimes(2);
  expect(start.mock.calls[1][0]).toMatchObject({ conversationId: 'chat-2',
    context: { kind: 'understanding', artifactId: 'artifact-1', revision: 1, itemId: 'summary:0' } });
  expect(state.projectUnderstanding).toMatchObject({ artifactId: 'artifact-1', revision: 1, jobId: 'project-job' });
  fireEvent.click(screen.getByRole('button', { name: 'Close AI Assistant' }));
  expect(screen.queryByRole('region', { name: 'AI Assistant' })).toBeNull();
  expect(screen.getByRole('region', { name: 'Architecture static graph' })).toBeTruthy();
  expect(screen.getByRole('region', { name: 'Project understanding' }).textContent).toContain('v1');
});
