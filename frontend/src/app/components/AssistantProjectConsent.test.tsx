import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AssistantConsent from './AssistantConsent';
import { AssistantStore } from '../assistant/store';
import { translator } from '../i18n';
import { ok } from '../testFixtures';
import type { AssistantState } from '../assistant/types';

afterEach(() => { cleanup(); delete window.pywebview; });
it('prefills the entire backend candidate list but grants only after one explicit confirmation', async () => {
  const state: AssistantState = { projectEpoch: 'epoch-1', analysisGeneration: 1, configRevision: 1,
    grant: null, conversations: [], activeJobs: [] };
  const grant = vi.fn(async () => {
    state.grant = { consentRevision: 1, enabled: true, origin: 'https://provider.invalid:443', allowedFiles: ['a.py', 'b.py'] };
    return ok(state.grant);
  });
  const start = vi.fn();
  window.pywebview = { api: { assistant_get_state: vi.fn(async () => ok(state)),
    assistant_get_consent_candidates: vi.fn(async () => ok({ projectEpoch: 'epoch-1', analysisGeneration: 1, files: ['a.py', 'b.py'] })),
    assistant_grant_consent: grant, assistant_start: start } };
  const store = new AssistantStore(); await store.restore('epoch-1');
  render(<AssistantConsent store={store} origin="https://provider.invalid:443" close={vi.fn()} t={translator('en')}/>);
  await waitFor(() => expect((screen.getByLabelText('Allowed Python files · one per line') as HTMLTextAreaElement).value).toBe('a.py\nb.py'));
  expect(grant).not.toHaveBeenCalled(); expect(start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByLabelText('I approve sending the listed saved files and permitted context to this provider.'));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm authorization' }));
  await waitFor(() => expect(grant).toHaveBeenCalledExactlyOnceWith({ projectEpoch: 'epoch-1', analysisGeneration: 1,
    configRevision: 1, allowedFiles: ['a.py', 'b.py'], confirmed: true }));
  expect(start).not.toHaveBeenCalled(); store.dispose();
});
