import { useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Background, BaseEdge, Controls, getSmoothStepPath, Handle, MarkerType, Position, ReactFlow,
  type Edge, type EdgeProps, type Node, type NodeProps } from '@xyflow/react';
import type { AssistantAnswer } from '../assistant/types';
import { buildAIArchitecture, type AIArchitectureNode } from '../assistant/architecture';
import type { Translate } from '../i18n';
import AssistantEvidence from './AssistantEvidence';
import AssistantCoverageStatus from './AssistantCoverageStatus';
import '@xyflow/react/dist/style.css';
import './AssistantArchitecture.css';

type FlowNode = Node<{ item: AIArchitectureNode; label: string; badge: string }>;
function InterpretationNode({ data, selected }: NodeProps<FlowNode>) {
  return <div className={`ai-interpretation-node${selected ? ' selected' : ''}`}>
    <Handle type="target" position={Position.Top}/><small>{data.badge}</small><strong>{data.label}</strong>
    <Handle type="source" position={Position.Bottom}/>
  </div>;
}
const nodeTypes = { interpretation: InterpretationNode };
type FlowEdge = Edge<{ gutter: number; viaGutter: boolean }>;
function InterpretationEdge(props: EdgeProps<FlowEdge>) {
  const { sourceX, sourceY, targetX, targetY, data, markerEnd, style } = props;
  const path = data?.viaGutter
    ? `M ${sourceX},${sourceY} L ${sourceX},${sourceY + 20} L ${data.gutter},${sourceY + 20} L ${data.gutter},${targetY - 20} L ${targetX},${targetY - 20} L ${targetX},${targetY}`
    : getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition: Position.Bottom, targetPosition: Position.Top })[0];
  return <BaseEdge id={props.id} path={path} markerEnd={markerEnd} style={style}/>;
}
const edgeTypes = { interpretation: InterpretationEdge };
const sectionKeys = { root: 'aiArchitecture', summary: 'aiSummary', entry: 'aiEntry', steps: 'aiSteps',
  branches: 'aiBranches', relations: 'aiRelations', evidence: 'aiEvidence', unknowns: 'aiUnknowns' } as const;

/** An independent interpretation canvas; never passes these node IDs to native graph APIs. */
export default function AssistantArchitecture({ answer, disabled, onClose, onAsk, navigate, t }: {
  answer: AssistantAnswer; disabled: boolean; onClose: () => void; onAsk: (item: AIArchitectureNode) => void;
  navigate: (id: string, action: 'source' | 'graph') => void; t: Translate;
}) {
  const graph = useMemo(() => buildAIArchitecture(answer), [answer]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = graph.nodes.find(item => item.id === selectedId);
  const nodes: FlowNode[] = graph.nodes.map(item => ({ id: item.id, type: 'interpretation', position: item.position,
    selected: item.id === selectedId, draggable: false, connectable: false,
    data: { item, label: item.section === 'root' ? t('aiArchitecture') : item.title,
      badge: `${t(sectionKeys[item.section])} · ${t(item.basis === 'unknown' ? 'aiUnknown' : 'aiInferred')}` } }));
  const gutter = Math.min(...graph.nodes.map(item => item.position.x)) - 30;
  const edges: FlowEdge[] = graph.edges.map(edge => ({ ...edge, type: 'interpretation', animated: false,
    data: { gutter, viaGutter: edge.viaGutter },
    style: { stroke: '#809cb6', strokeDasharray: '5 5' }, markerEnd: { type: MarkerType.ArrowClosed, color: '#809cb6' } }));
  const selectedAnswer: AssistantAnswer = { ...answer,
    evidence: answer.evidence.filter(card => selected?.evidenceIds.includes(card.evidenceId)) };
  return createPortal(<section className="ai-architecture-workspace" role="dialog" aria-modal="false"
    aria-label={t('aiArchitecture')} onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onClose(); } }}>
    <header><strong>{t('aiArchitecture')}</strong><button type="button" onClick={onClose}>{t('close')}</button></header>
    <p className="ai-architecture-note">{t('aiArchitectureCaveat')}{disabled && <> · {t('aiStale')}</>}
      {graph.omitted > 0 && <> · {t('aiGraphOmitted')}: {graph.omitted}</>}</p>
    <div className="ai-architecture-body"><div className="ai-architecture-canvas">
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} fitView fitViewOptions={{ padding: .15 }}
        minZoom={.08} maxZoom={1.8} nodesDraggable={false} nodesConnectable={false}
        onNodeClick={(_event, item) => setSelectedId(item.id)}><Background/><Controls showInteractive={false}/></ReactFlow>
    </div><aside className="ai-architecture-details">
      {selected && selected.section !== 'root' ? <>
        <h3>{selected.title}</h3><p className="assistant-plain">{selected.description}</p>
        <p className="muted">{t(selected.basis === 'unknown' ? 'aiUnknown' : 'aiInferred')}</p>
        <button type="button" disabled={disabled} onClick={() => onAsk(selected)}>{t('aiAskNode')}</button>
        {selected.evidenceIds.length === 0 && <p>{t('aiNoNodeEvidence')}</p>}
        <AssistantEvidence answer={selectedAnswer} disabled={disabled} navigate={(id, action) => { navigate(id, action); onClose(); }} t={t}/>
      </> : <p>{t('aiSelectInterpretation')}</p>}
      {answer.relationFacts.length > 0 && <details><summary>{t('aiRelations')}</summary>
        {answer.relationFacts.map(fact => <p className="assistant-plain" key={fact.evidenceId}>
          {fact.sourceId} → {fact.targetId}<br/>{fact.type} · {fact.confidence}</p>)}</details>}
      <AssistantCoverageStatus answer={answer} t={t}/>
    </aside></div>
  </section>, document.body);
}
