import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Overview from './Overview';
import { translator } from '../i18n';
import { anchor, overview } from '../testFixtures';
import type { HumanUnderstanding } from '../../types';

const understanding: HumanUnderstanding = {
  status: 'source_backed', omittedResponsibilityCount: 0, omittedFactCount: 0, discardedFactCount: 0,
  responsibilities: [{
    ownerSymbolId: 'real-report-symbol', codeName: 'report.summarize', ownerKind: 'Function',
    responsibility: '统计分数数量和平均值。', basis: 'source_author_statement', confidence: 'Probable',
    authorFactId: 'author-fact-not-a-navigation-id', authorTruncated: false, sourceAnchor: anchor,
    operations: [{ kind: 'sum', confidence: 'Probable', sourceLocationCount: 7,
      factIds: ['sum-fact'], sourceAnchors: [anchor] }],
  }],
};

describe('generic Python overview', () => {
  afterEach(cleanup);
  it('routes generic entries and library functions to FunctionFlow', () => {
    const openView = vi.fn();
    const data = { ...overview,
      entries: [{ id: 'main', name: 'main', sourceAnchor: anchor, viewKind: 'FunctionFlow' as const }],
      functions: [{ id: 'utility', name: 'utility', sourceAnchor: anchor }],
    };
    render(<Overview data={data} openView={openView} openSource={vi.fn()} t={translator('en')}/>);
    fireEvent.click(screen.getByRole('button', { name: /main/ }));
    expect(openView).toHaveBeenCalledWith('FunctionFlow', 'main');
    const summary = screen.getByText('Implementation and analysis details');
    expect(summary.closest('details')?.open).toBe(false);
    fireEvent.click(summary);
    fireEvent.click(screen.getByRole('button', { name: /utility/ }));
    expect(openView).toHaveBeenCalledWith('FunctionFlow', 'utility');
  });

  it('labels author claims and navigates with the real source and symbol', () => {
    const openView = vi.fn(); const openSource = vi.fn();
    render(<Overview data={{ ...overview, understanding }} openView={openView} openSource={openSource} t={translator('zh-CN')}/>);
    expect(screen.getByText('统计分数数量和平均值。')).toBeTruthy();
    expect(screen.getByText('源码作者说明（未验证）')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /打开源码 report.summarize/ }));
    expect(openSource).toHaveBeenCalledWith(anchor);
    fireEvent.click(screen.getByRole('button', { name: /函数流程 report.summarize/ }));
    expect(openView).toHaveBeenCalledWith('FunctionFlow', 'real-report-symbol');
    expect(openView).not.toHaveBeenCalledWith(expect.anything(), 'author-fact-not-a-navigation-id');
  });

  it('keeps unknown responsibility honest and labels aggregated source locations', () => {
    const card = { ...understanding.responsibilities[0], responsibility: null,
      basis: 'unknown' as const, confidence: 'Unresolved', authorFactId: null };
    render(<Overview data={{ ...overview, understanding: { ...understanding, status: 'partial', responsibilities: [card] } }}
      openView={vi.fn()} openSource={vi.fn()} t={translator('en')}/>);
    expect(screen.getByText('Responsibility has not been confirmed from source.')).toBeTruthy();
    expect(screen.queryByText('统计分数数量和平均值。')).toBeNull();
    fireEvent.click(screen.getByText('Static operations'));
    expect(screen.getByText(/Source locations: 7/)).toBeTruthy();
  });
});
