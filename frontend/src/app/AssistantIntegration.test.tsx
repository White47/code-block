import { StrictMode } from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { anchor, graph, ok, overview, session, viewState } from './testFixtures';
import { sourceTab } from './state';
import type { AssistantAnswer, AssistantBridgeMethods, AssistantState, NavigationPoll } from './assistant/types';

vi.mock('./components/GraphEditor', () => ({ default: () => <div>Graph canvas</div> }));
vi.mock('./components/SourceEditor', () => ({ default: ({ document, onChange }: {
  document: { text: string }; onChange: (text: string) => void;
}) => <textarea aria-label="Saved source editor" value={document.text} onChange={e => onChange(e.target.value)}/> }));
function install(withEvidence = false) {
  const layout = { ...viewState, activeView: null, openTabs: ['Overview', sourceTab(anchor).id] };
  const state: AssistantState = { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    grant: { origin: 'https://provider.invalid:443', allowedFiles: ['model.py'], enabled: true, consentRevision: 1 },
    conversations: [{ conversationId: 'chat-1', conversationRevision: 0, messages: [] }], activeJobs: [] };
  if (withEvidence) {
    const answer: AssistantAnswer = { sections: (['summary', 'entry', 'steps', 'branches', 'relations', 'evidence', 'unknowns'] as const).map(kind => ({ kind, claims: [] })),
      evidence: [{ evidenceId: 'ev-1', label: 'Saved function', confidence: 'Exact', truncated: false, sourceAvailable: true, graphAvailable: true, unavailableReason: 'navigation_check_required' }],
      relationFacts: [], incomplete: false, coverageGaps: [], checkedAt: '2026-10-20T00:00:00Z' };
    state.conversations[0].messages = [{ type: 'validated_answer', answer, stale: false, jobId: 'explain-1',
      projectEpoch: 'epoch-1', analysisGeneration: 1, conversationRevision: 0, configRevision: 1, consentRevision: 1, seq: 1 }];
  }
  let action: 'source' | 'graph' = 'source';
  let navigationId = 0;
  const result = (): NavigationPoll => ({ kind: 'navigation', jobId: `nav-${navigationId}`, projectEpoch: 'epoch-1', analysisGeneration: 1,
    conversationId: 'chat-1', conversationRevision: 0, configRevision: 1, consentRevision: 1, seq: 1,
    status: 'Completed', cancelRequested: false, cancelAccepted: false, cancellation: 'none', dispatchClosed: true,
    resources: 'released', resourcesReleased: true, issue: null, action, evidenceId: 'ev-1', reset: true, events: [], evictedConversationIds: [],
    target: { evidenceId: 'ev-1', action, scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'question-1' },
      binding: { projectEpoch: 'epoch-1', analysisGeneration: 1 }, astAnchor: { ...anchor, startLine: 1, endLine: 1, startColumn: 4, endColumn: 9 },
      monacoRange: null, reveal: action === 'graph' ? { viewKind: 'Architecture', rootId: 'model', selectedId: 'layer', reason: null } : null,
      checkedAt: '2026-10-20T00:00:00Z', conversationId: 'chat-1', conversationRevision: 0, configRevision: 1, consentRevision: 1 } });
  const api = {
    get_session: vi.fn(async () => ok(session)), get_preferences: vi.fn(async () => ok({ language: 'en' as const, issue: null })),
    get_view_state: vi.fn(async () => ok(layout)), get_overview: vi.fn(async () => ok(overview)),
    get_view: vi.fn(async () => ok(graph)), get_problems: vi.fn(async () => ok([])),
    save_layout: vi.fn(async () => ok(layout)),
    list_project_directory: vi.fn(async () => ok({ projectPath: session.projectPath!, projectEpoch: 'epoch-1', directory: '', directoryRevision: 'r1', entries: [], nextCursor: null })),
    open_source_document: vi.fn(async () => ok({ projectPath: session.projectPath!, filePath: anchor.filePath, text: 'saved = 1', revision: 'saved-1', encoding: 'utf-8', newline: 'LF' as const, readOnlyReason: null })),
    assistant_get_settings: vi.fn(async () => ok({ available: true, enabled: true, keySet: true, baseUrl: 'https://provider.invalid/v1', model: 'test', configRevision: 1, issue: null })),
    assistant_get_state: vi.fn(async () => ok(state)), assistant_start: vi.fn(),
    open_project: vi.fn(async () => ok({ ...session, projectPath: 'E:/other' })),
    choose_project: vi.fn(async () => ok({ ...session, projectPath: 'E:/other' })),
    assistant_resolve_evidence: vi.fn(async (request: AssistantBridgeMethods['assistant_resolve_evidence']['args'][0]) => {
      action = request.action; navigationId++; return ok({ kind: 'navigation', jobId: `nav-${navigationId}`, status: 'Queued',
        scope: { projectEpoch: 'epoch-1', analysisGeneration: 1, requestId: 'navigation-request' }, conversationId: 'chat-1' });
    }),
    assistant_poll: vi.fn(async () => ok(result())),
    assistant_cancel: vi.fn(async () => ok(Object.fromEntries(Object.entries(result()).filter(([name]) =>
      !['action', 'evidenceId', 'target', 'reset', 'events', 'evictedConversationIds'].includes(name))))),
  };
  window.pywebview = { api };
  return api;
}
afterEach(() => { cleanup(); delete window.pywebview; });

it('opens the assistant from a source tab, keeps dirty drafts local and preserves the panel on a vetoed project switch', async () => {
  const api = install(); render(<StrictMode><App/></StrictMode>);
  await screen.findByLabelText('Saved source editor');
  fireEvent.change(screen.getByLabelText('Saved source editor'), { target: { value: 'PRIVATE UNSAVED DRAFT' } });
  fireEvent.click(screen.getByRole('button', { name: 'AI Assistant' }));
  await screen.findByRole('tab', { name: 'AI Assistant' });
  await waitFor(() => expect(api.assistant_get_state).toHaveBeenCalled());
  const panel = screen.getByRole('region', { name: 'AI Assistant' });
  expect(within(panel).getByText(/Unsaved project changes/)).toBeTruthy();
  expect(api.assistant_start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Explain saved file' }));
  await waitFor(() => expect(within(panel).getByText(/Scope: saved file/)).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: 'Close AI Assistant' }));
  expect(screen.queryByRole('region', { name: 'AI Assistant' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'AI Assistant' }));
  await screen.findByRole('region', { name: 'AI Assistant' });
  fireEvent.click(screen.getByRole('button', { name: /Open project/ }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(api.open_project).not.toHaveBeenCalled();
  expect(api.choose_project).not.toHaveBeenCalled();
  expect((screen.getByLabelText('Saved source editor') as HTMLTextAreaElement).value).toBe('PRIVATE UNSAVED DRAFT');
  expect(screen.getByRole('region', { name: 'AI Assistant' })).toBeTruthy();
  expect(api.assistant_start).not.toHaveBeenCalled();
  const assistantCalls = [...api.assistant_get_state.mock.calls, ...api.assistant_get_settings.mock.calls];
  expect(JSON.stringify(assistantCalls)).not.toContain('PRIVATE UNSAVED DRAFT');
});

it('uses backend-issued source and graph destinations, falls back to whole lines, and never discards a dirty draft for a citation', async () => {
  const api = install(true); render(<App/>);
  await screen.findByLabelText('Saved source editor');
  fireEvent.click(screen.getByRole('button', { name: 'AI Assistant' }));
  const panel = await screen.findByRole('region', { name: 'AI Assistant' });
  fireEvent.click(within(panel).getByRole('button', { name: 'Open source' }));
  await waitFor(() => expect(document.querySelector('.source-heading small')?.textContent).toBe('L1–1'));
  fireEvent.change(screen.getByLabelText('Saved source editor'), { target: { value: 'KEEP THIS DRAFT' } });
  const opened = api.open_source_document.mock.calls.length;
  fireEvent.click(within(panel).getByRole('button', { name: 'Open source' }));
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Unsaved project changes'));
  expect(api.open_source_document.mock.calls.length).toBe(opened);
  expect((screen.getByLabelText('Saved source editor') as HTMLTextAreaElement).value).toBe('KEEP THIS DRAFT');
  fireEvent.click(within(panel).getByRole('button', { name: 'Reveal in graph' }));
  await waitFor(() => expect(screen.getByRole('tab', { name: 'Architecture' }).getAttribute('aria-selected')).toBe('true'));
  expect(api.assistant_start).not.toHaveBeenCalled();
});
