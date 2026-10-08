import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { sourceTab } from './state';
import { anchor, graph, ok, overview, selectedNode, session, viewState } from './testFixtures';
import type { EditableSource, SaveSourceRequest } from '../types';
import type { SourceDocument } from './sourceDocuments';

// Only the Monaco presentation boundary is replaced. App, SourceViewer, store,
// keyboard listeners and unsaved-close dialog stay real. No colour/native proof.
vi.mock('./components/SourceEditor', () => ({ default: ({ document, onChange, locked }: {
  document: SourceDocument; onChange: (text: string) => void; locked?: boolean;
}) => <div className="monaco-editor" role="textbox" aria-label={document.filePath}
  contentEditable={!locked} suppressContentEditableWarning
  onInput={event => onChange(event.currentTarget.textContent ?? '')}>
  <span className="view-line">{document.text}</span>
</div> }));
vi.mock('./components/GraphEditor', () => ({ default: () => <div tabIndex={0}>Keyboard graph canvas</div> }));
const initialText = 'value = 1'; const draft = 'keyboard draft';
function install(sourceActive = false) {
  const layout = { ...viewState, ...(sourceActive ? {
    activeView: null, openTabs: ['Overview', 'Architecture', sourceTab(anchor).id],
  } : {}) };
  const document: EditableSource = { projectPath: session.projectPath!, filePath: anchor.filePath,
    text: initialText, revision: 'keyboard-opening:bytes-1', encoding: 'utf-8', newline: 'LF', readOnlyReason: null };
  const api = {
    get_session: vi.fn().mockResolvedValue(ok(session)),
    get_preferences: vi.fn().mockResolvedValue(ok({ language: 'en', issue: null })),
    get_view_state: vi.fn().mockResolvedValue(ok(layout)), get_overview: vi.fn().mockResolvedValue(ok(overview)),
    get_problems: vi.fn().mockResolvedValue(ok([])), get_view: vi.fn().mockResolvedValue(ok(graph)),
    get_inspector: vi.fn().mockResolvedValue(ok({ ...selectedNode, evidence: [], children: [],
      relations: [], data: [], canOpenSource: true })),
    save_layout: vi.fn().mockResolvedValue(ok(layout)),
    open_source_document: vi.fn().mockResolvedValue(ok(document)),
    save_source_document: vi.fn(async (request: SaveSourceRequest) => ok({ ...document, ...request, revision: 'keyboard-opening:bytes-2' })),
    open_detail_island: vi.fn().mockResolvedValue(ok(layout)),
  };
  window.pywebview = { api }; return api;
}
async function openEditor(where: 'source tab' | 'split') {
  const api = install(where === 'source tab'); render(<App/>);
  if (where === 'split') fireEvent.click(await screen.findByRole('button', { name: 'Open to side' }));
  const editor = await screen.findByRole('textbox', { name: anchor.filePath });
  await waitFor(() => expect(editor.textContent).toContain(initialText));
  return { api, editor };
}
function key(target: HTMLElement, key: string, modifiers: KeyboardEventInit = {}) {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...modifiers });
  target.dispatchEvent(event); return event;
}
function stopBubblingAtEditor(editor: HTMLElement) {
  const stopped = vi.fn((event: Event) => event.stopPropagation());
  editor.addEventListener('keydown', stopped); return stopped;
}
afterEach(() => { cleanup(); delete window.pywebview; });

describe('Monaco DOM keyboard boundary', () => {
  it.each(['role textbox DIV', 'contenteditable DIV', 'nested view-line SPAN'] as const)(
    'Enter inside %s does not open a detail island while the graph remains selected', async surface => {
      const { api, editor } = await openEditor('split');
      if (surface === 'role textbox DIV') editor.removeAttribute('contenteditable');
      if (surface === 'contenteditable DIV') editor.removeAttribute('role');
      const target = surface === 'nested view-line SPAN' ? editor.querySelector<HTMLElement>('.view-line')! : editor;
      const enter = key(target, 'Enter');
      await act(async () => { await Promise.resolve(); });
      expect(api.open_detail_island).not.toHaveBeenCalled();
      expect(enter.defaultPrevented, 'graph Enter handling must not consume an editor newline').toBe(false);
    },
  );
  it('Enter on the selected graph still opens its detail island', async () => {
    const api = install(); render(<App/>);
    await screen.findByRole('button', { name: 'Open to side' });
    const enter = key(screen.getByText('Keyboard graph canvas'), 'Enter');
    await waitFor(() => expect(api.open_detail_island).toHaveBeenCalledWith('Architecture', selectedNode.id, null));
    expect(enter.defaultPrevented).toBe(true);
  });
  it.each(['source tab', 'split'] as const)(
    'Ctrl+S saves the focused %s even when Monaco stops keydown bubbling', async where => {
      const { api, editor } = await openEditor(where);
      fireEvent.input(editor, { target: { textContent: draft } });
      const stopped = stopBubblingAtEditor(editor);
      const save = key(editor, 's', { ctrlKey: true });
      expect(stopped).toHaveBeenCalledOnce();
      expect(save.defaultPrevented, 'save must be handled before Monaco stops bubbling').toBe(true);
      await waitFor(() => expect(api.save_source_document.mock.calls).toEqual([[{
        projectPath: session.projectPath, filePath: anchor.filePath, text: draft, revision: 'keyboard-opening:bytes-1',
      }]]));
      expect(api.open_detail_island).not.toHaveBeenCalled();
    },
  );
  it('Ctrl+W opens the dirty-source guard even when Monaco stops keydown bubbling', async () => {
    const { api, editor } = await openEditor('source tab');
    fireEvent.input(editor, { target: { textContent: draft } });
    const stopped = stopBubblingAtEditor(editor);
    const close = key(editor, 'w', { ctrlKey: true });
    expect(stopped).toHaveBeenCalledOnce();
    expect(close.defaultPrevented, 'close must be handled before Monaco stops bubbling').toBe(true);
    const dialog = await screen.findByRole('dialog');
    expect(api.save_source_document).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByRole('textbox', { name: anchor.filePath }).textContent).toContain(draft);
    expect(screen.getByRole('tab', { name: /model.py/ }).getAttribute('aria-selected')).toBe('true');
  });
});
