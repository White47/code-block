import type { FunctionalArchitectureData } from '../../types';
import SummaryCanvas, { type SummaryActions } from './SummaryCanvas';

export default function FunctionalArchitecture({ architecture, ...actions }: SummaryActions & { architecture: FunctionalArchitectureData }) {
  return <SummaryCanvas {...actions} context={{ kind: 'architecture', id: architecture.id }} title={actions.t('summaryArchitecture')}
    steps={architecture.areas} relations={architecture.relations} incomplete={architecture.incomplete} omittedCount={architecture.omittedCount}/>;
}
