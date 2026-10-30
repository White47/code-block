import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import AssistantCoverageStatus from './AssistantCoverageStatus';
import { translator } from '../i18n';

afterEach(cleanup);
it('keeps unknown causes and original repair details visible rather than softening them into sample information', () => {
  const raw = 'Repair condensed the answer; coverage is incomplete.';
  render(<AssistantCoverageStatus answer={{ incomplete: true, coverageGaps: [raw] }}
    gapCodes={['unverified', 'future_code']} t={translator('en')}/>);
  expect(screen.getByText('Incomplete explanation')).toBeTruthy();
  expect(screen.queryByText('Explanation based on source excerpts')).toBeNull();
  expect(screen.getByText('future_code')).toBeTruthy();
  fireEvent.click(screen.getByText(/Inspect coverage details/));
  expect(screen.getByText(raw)).toBeTruthy();
});
