import type { SummaryTaskFlow } from '../../types';
import SummaryCanvas, { type SummaryActions } from './SummaryCanvas';

export default function TaskFlow({ flow, ...actions }: SummaryActions & { flow: SummaryTaskFlow }) {
  return <SummaryCanvas {...actions} context={{ kind: 'task_flow', id: flow.id }} title={flow.title}
    steps={flow.steps} relations={flow.relations} incomplete={flow.incomplete} omittedCount={flow.omittedCount}/>;
}
