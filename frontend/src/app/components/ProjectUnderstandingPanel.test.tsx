import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ProjectUnderstandingSummary } from './ProjectUnderstanding';
import { translator } from '../i18n';
import { understandingFixture } from './ProjectUnderstanding.test-fixtures';

afterEach(cleanup);
it('exposes all seven sections and item-owned evidence without inventing graph edges or sending automatically', () => {
  const model = understandingFixture(), navigate = vi.fn(), ask = vi.fn();
  render(<ProjectUnderstandingSummary model={model} t={translator('en')} navigate={navigate} onAsk={ask}/>);
  for (const section of model.answer.sections) expect(screen.getByText(`Detailed ${section.kind} explanation`)).toBeTruthy();
  expect(ask).not.toHaveBeenCalled();
  expect(document.querySelector('.react-flow')).toBeNull();
  const summary = screen.getByText('Detailed summary explanation').parentElement!;
  fireEvent.click(within(summary).getByRole('button', { name: 'Ask about this node' }));
  expect(ask).toHaveBeenCalledExactlyOnceWith('summary:0');
  fireEvent.click(within(summary).getByText(/Related source and evidence/));
  expect(within(summary).queryByText('Saved source-2')).toBeNull();
  fireEvent.click(within(summary).getByRole('button', { name: 'Open source' }));
  expect(navigate).toHaveBeenCalledExactlyOnceWith('summary:0', 'source-1', 'source');
  expect((within(summary).getByRole('button', { name: 'Reveal in graph' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/caller → callee/)).toBeTruthy();
});
