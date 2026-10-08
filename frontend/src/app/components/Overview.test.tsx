import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import Overview from './Overview';
import { translator } from '../i18n';
import { anchor, overview } from '../testFixtures';

describe('generic Python overview', () => {
  it('routes generic entries and library functions to FunctionFlow', () => {
    const openView = vi.fn();
    const data = { ...overview,
      entries: [{ id: 'main', name: 'main', sourceAnchor: anchor, viewKind: 'FunctionFlow' as const }],
      functions: [{ id: 'utility', name: 'utility', sourceAnchor: anchor }],
    };
    render(<Overview data={data} openView={openView} t={translator('en')}/>);
    fireEvent.click(screen.getByRole('button', { name: /main/ }));
    expect(openView).toHaveBeenCalledWith('FunctionFlow', 'main');
    fireEvent.click(screen.getByRole('button', { name: /utility/ }));
    expect(openView).toHaveBeenCalledWith('FunctionFlow', 'utility');
  });
});
