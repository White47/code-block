import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ProjectUnderstandingSummary } from './ProjectUnderstanding';
import AssistantEvidence from './AssistantEvidence';
import { translator } from '../i18n';
import { understandingFixture } from './ProjectUnderstanding.test-fixtures';

afterEach(cleanup);
it('does not call a successfully sampled explanation or a truncated source card an incomplete answer', () => {
  const model = understandingFixture();
  model.answer.coverageGaps = ['Project understanding used representative excerpts; full-project coverage is not established.'];
  model.answer.evidence[0].truncated = true;
  render(<><ProjectUnderstandingSummary model={model} t={translator('en')}/>
    <AssistantEvidence answer={model.answer} disabled={false} navigate={vi.fn()} t={translator('en')}/></>);
  expect(screen.queryAllByText('Incomplete explanation')).toHaveLength(0);
  expect(screen.getByText('Explanation based on source excerpts')).toBeTruthy();
  expect(screen.getAllByText('Source excerpt is truncated').length).toBeGreaterThan(0);
  expect(model.answer.incomplete).toBe(true);
  expect(model.answer.coverageGaps).toHaveLength(1);
});

it('keeps incomplete and distinguishes representative, retrieval and unverified gaps from static graph coverage', () => {
  const model = understandingFixture();
  model.reference.coverage.gapCodes = ['representative_sample', 'retrieval_gap', 'unverified'];
  render(<ProjectUnderstandingSummary model={model} t={translator('en')}/>);
  expect(screen.getByText('Incomplete explanation')).toBeTruthy();
  expect(screen.getByText(/Representative source excerpts/)).toBeTruthy();
  expect(screen.getByText(/Retrieval or budget/)).toBeTruthy();
  expect(screen.getByText(/Unverified content/)).toBeTruthy();
  expect(screen.getByText(/12 \/ 8 \/ 4 \/ 2/)).toBeTruthy();
  expect(screen.getByText('Some source ranges were omitted.')).toBeTruthy();
  expect(model.answer.incomplete).toBe(true);
});

it('retains unknown cause instead of inventing a specific explanation for incomplete output', () => {
  const model = understandingFixture();
  model.reference.coverage.gapCodes = []; model.answer.coverageGaps = [];
  render(<ProjectUnderstandingSummary model={model} t={translator('en')}/>);
  expect(screen.getByText('Incomplete explanation')).toBeTruthy();
  expect(screen.getByText(/The specific cause is not available/)).toBeTruthy();
});
