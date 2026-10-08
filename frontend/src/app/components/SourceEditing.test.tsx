import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SourceViewer from './SourceViewer';
import { translator } from '../i18n';
import { anchor, ok, session } from '../testFixtures';
import { SourceDocumentStore } from '../sourceDocuments';
import { call } from '../../api/bridge';

const projectPath = session.projectPath!;
const document = { projectPath, filePath: anchor.filePath, text: 'value = 1',
  revision: 'epoch-a:bytes-1:identity-1', encoding: 'utf-8', newline: 'LF' as const, readOnlyReason: null as string | null };
function install() {
  const api = {
    open_source_document: vi.fn().mockResolvedValue(ok(document)),
    save_source_document: vi.fn().mockResolvedValue(ok({ ...document, revision: 'epoch-a:bytes-2:identity-2' })),
    // Compatibility text must never become an editable baseline.
    read_source: vi.fn().mockResolvedValue(ok({ filePath: anchor.filePath, text: 'replacement-only legacy text', startLine: 20, endLine: 22 })),
  };
  window.pywebview = { api } as unknown as NonNullable<Window['pywebview']>;
  return api;
}
afterEach(() => { cleanup(); delete window.pywebview; });
describe('standalone source editing boundary', () => {
  it('requests an editable baseline with project identity and the full anchor', async () => {
    const api = install();
    const props = { projectPath, anchor, t: translator('en') };
    render(<SourceViewer {...props}/>);
    await waitFor(() => expect(api.open_source_document).toHaveBeenCalledWith(projectPath, anchor));
    expect(api.read_source).not.toHaveBeenCalled();
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('does not save on mount or when an anchor changes', async () => {
    const api = install();
    const props = { projectPath, anchor, t: translator('en') };
    const { rerender } = render(<SourceViewer {...props}/>);
    await waitFor(() => expect(api.open_source_document).toHaveBeenCalled());
    rerender(<SourceViewer {...props} anchor={{ ...anchor, startLine: 2, endLine: 3 }}/>);
    await act(async () => { await Promise.resolve(); });
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('shows the read-only reason returned for mixed newlines', async () => {
    const api = install();
    api.open_source_document.mockResolvedValue(ok({ ...document, newline: 'Mixed', readOnlyReason: 'Mixed newlines cannot be edited safely' }));
    const props = { projectPath, anchor, t: translator('en') };
    render(<SourceViewer {...props}/>);
    expect(await screen.findByText('Mixed newlines cannot be edited safely')).toBeDefined();
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('reports an editable-open error instead of silently editing legacy replacement text', async () => {
    const api = install();
    api.open_source_document.mockRejectedValue(new Error('SOURCE_PROJECT_CHANGED: Project changed'));
    const props = { projectPath, anchor, t: translator('en') };
    render(<SourceViewer {...props}/>);
    expect((await screen.findByRole('alert')).textContent).toContain('Project changed');
    expect(api.read_source).not.toHaveBeenCalled();
    expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('shares edits between two standalone viewers with different anchors through the actual store', async () => {
    const api = install();
    const store = new SourceDocumentStore((path, value) => call('open_source_document', path, value), request => call('save_source_document', request));
    render(<><SourceViewer projectPath={projectPath} anchor={anchor} store={store} t={translator('en')}/>
      <SourceViewer projectPath={projectPath} anchor={{ ...anchor, startLine: 2, endLine: 3 }} store={store} t={translator('en')}/></>);
    await waitFor(() => expect(screen.getAllByRole('textbox', { name: anchor.filePath })).toHaveLength(2));
    const editors = screen.getAllByRole('textbox', { name: anchor.filePath }) as HTMLTextAreaElement[];
    fireEvent.change(editors[0], { target: { value: 'shared draft' } });
    await waitFor(() => expect(editors.map(editor => editor.value)).toEqual(['shared draft', 'shared draft']));
    expect(api.open_source_document).toHaveBeenCalledOnce(); expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('preserves drafts when a source viewer unmounts for an ordinary tab switch and returns', async () => {
    const api = install();
    const store = new SourceDocumentStore((path, value) => call('open_source_document', path, value), request => call('save_source_document', request));
    const props = { projectPath, anchor, store, t: translator('en') };
    const { rerender } = render(<SourceViewer {...props}/>);
    const editor = await screen.findByRole('textbox', { name: anchor.filePath });
    fireEvent.change(editor, { target: { value: 'retained draft' } });
    rerender(<p>Graph tab</p>); rerender(<SourceViewer {...props}/>);
    expect((await screen.findByRole('textbox', { name: anchor.filePath }) as HTMLTextAreaElement).value).toBe('retained draft');
    expect(api.open_source_document).toHaveBeenCalledOnce(); expect(api.save_source_document).not.toHaveBeenCalled();
  });
  it('uses the explicit Save button and retains later input on successful completion', async () => {
    const api = install(); let finish!: (value: ReturnType<typeof ok<typeof document>>) => void;
    api.save_source_document.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    render(<SourceViewer projectPath={projectPath} anchor={anchor} t={translator('en')}/>);
    const editor = await screen.findByRole('textbox', { name: anchor.filePath });
    fireEvent.change(editor, { target: { value: 'saved snapshot' } });
    fireEvent.click(screen.getByRole('button', { name: /^save/i }));
    await waitFor(() => expect(api.save_source_document).toHaveBeenCalledWith({ projectPath,
      filePath: anchor.filePath, text: 'saved snapshot', revision: document.revision }));
    fireEvent.change(editor, { target: { value: 'later input' } });
    await act(async () => { finish(ok({ ...document, text: 'saved snapshot', revision: 'new-baseline' })); });
    expect((editor as HTMLTextAreaElement).value).toBe('later input');
    expect(globalThis.document.querySelector('.source-heading')?.textContent).toContain('●');
  });
});
