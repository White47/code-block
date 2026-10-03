import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import SourceViewer from './SourceViewer';
import { translator } from '../i18n';
afterEach(() => { cleanup(); delete window.pywebview; });
it('numbers full text from line 1 and highlights the anchor at line 20', async () => {
  const text = Array.from({ length: 25 }, (_, i) => `python_line_${i + 1}`).join('\n');
  window.pywebview = { api: { read_source: vi.fn().mockResolvedValue({ ok: true, schemaVersion: 1, data: { filePath: 'model.py', text, startLine: 20, endLine: 22 } }) } };
  render(<SourceViewer anchor={{ filePath: 'model.py', startLine: 20, endLine: 22, startColumn: null, endColumn: null }} t={translator('en')}/>);
  await waitFor(() => expect(screen.getByText('python_line_1')).toBeDefined());
  const first = screen.getByText('python_line_1').closest('tr')!; const twentieth = screen.getByText('python_line_20').closest('tr')!;
  expect(first.querySelector('.line-number')?.textContent).toBe('1'); expect(first.classList.contains('highlighted')).toBe(false);
  expect(twentieth.querySelector('.line-number')?.textContent).toBe('20'); expect(twentieth.classList.contains('highlighted')).toBe(true);
  expect(document.querySelectorAll('tr.highlighted')).toHaveLength(3);
});
