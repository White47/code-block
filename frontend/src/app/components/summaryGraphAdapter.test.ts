import { describe, expect, it } from 'vitest';
import type { SummaryRelation, SummaryStep } from '../../types';
import { translator } from '../i18n';
import { buildSummaryGraph } from './summaryGraphAdapter';

const anchor = { filePath: 'main.py', startLine: 4, endLine: 4, startColumn: 0, endColumn: 10 };
const step = (id: string, owner = `owner:${id}`): SummaryStep => ({
  id, kind: 'task', title: `Work ${id}`, basis: 'source_author_statement', confidence: 'Probable',
  ownerSymbolIds: [owner], evidenceIds: [`fact:${id}`], codeNodeIds: [owner], sourceAnchors: [anchor], sourceLocationCount: 1,
});
const relation = (source: string, target: string, kind: SummaryRelation['kind']): SummaryRelation => ({
  source, target, kind, confidence: 'Probable', evidenceIds: ['fact:relation'], codeNodeIds: ['site:call'], sourceAnchors: [anchor],
});

describe('local summary presentation', () => {
  it('distinguishes model instance names that share an implementation owner', () => {
    const result = buildSummaryGraph([
      { ...step('token', 'owner:model'), codeName: 'transformer.wte' },
      { ...step('position', 'owner:model'), codeName: 'transformer.wpe' },
    ], [], new Map([['owner:model', 'GPT']]), translator('en'));
    expect(result.graph.nodes.map(node => node.codeName)).toEqual(['transformer.wte', 'transformer.wpe']);
    expect(result.graph.nodes.every(node => node.codeNodeIds[0] === 'owner:model')).toBe(true);
  });
  it('wraps broad layers without moving successors above their predecessors', () => {
    const roots = Array.from({ length: 6 }, (_, index) => step(`root:${index}`));
    const result = buildSummaryGraph([...roots, step('after')], roots.map(root => relation(root.id, 'after', 'control_next')), new Map(), translator('en'));
    expect(Math.max(...Object.values(result.graph.layoutHints).map(point => point[0]))).toBeLessThanOrEqual(420);
    expect(new Set(roots.map(root => result.graph.layoutHints[root.id][1])).size).toBe(2);
    expect(Math.max(...roots.map(root => result.graph.layoutHints[root.id][1]))).toBeLessThan(result.graph.layoutHints.after[1]);
    expect(result.graph.edges).toHaveLength(6);
    const narrow = buildSummaryGraph(roots, [], new Map(), translator('en'), 1);
    expect(new Set(Object.values(narrow.graph.layoutHints).map(point => point[1])).size).toBe(6);
    expect(Object.values(narrow.graph.layoutHints).every(point => point[0] === 0)).toBe(true);
  });
  it('lays out control flow vertically without treating summary evidence as raw edges', () => {
    const result = buildSummaryGraph([step('read'), step('count'), step('show')], [
      relation('read', 'count', 'control_next'), relation('count', 'show', 'control_next'),
    ], new Map([['owner:read', 'data.load_scores']]), translator('en'));
    expect(result.graph.layoutHints.read[1]).toBeLessThan(result.graph.layoutHints.count[1]);
    expect(result.graph.layoutHints.count[1]).toBeLessThan(result.graph.layoutHints.show[1]);
    expect(result.graph.nodes[0].codeName).toBe('data.load_scores');
    expect(result.graph.edges.every(edge => edge.type.startsWith('summary-') && edge.codeEdgeIds.length === 0)).toBe(true);
  });

  it('distinguishes artifact dependencies, shared parameters and shared module instances', () => {
    const result = buildSummaryGraph([step('left'), step('right')], [
      relation('left', 'right', 'data_artifact'),
      { ...relation('left', 'right', 'shared'), sharingKind: 'parameter', sharingAttributes: ['weight', 'weight'] },
      { ...relation('left', 'right', 'shared'), sharingKind: 'module_instance' },
    ], new Map(), translator('zh-CN'));
    expect(result.edgePresentation['edge:0'].label).toBe('产物依赖');
    expect(result.edgePresentation['edge:1'].label).toContain('共享参数');
    expect(result.edgePresentation['edge:1'].label).toContain('weight');
    expect(result.edgePresentation['edge:2'].label).toBe('共享模块实例');
  });

  it('retains repeated task occurrences and rejects dangling relations', () => {
    const result = buildSummaryGraph([step('a-first', 'owner:a'), step('b'), step('a-again', 'owner:a')], [
      relation('a-first', 'b', 'control_next'), relation('b', 'a-again', 'control_next'), relation('b', 'missing', 'control_next'),
    ], new Map(), translator('en'));
    expect(result.graph.nodes.map(node => node.id)).toEqual(['a-first', 'b', 'a-again']);
    expect(result.graph.edges).toHaveLength(2);
  });
});
