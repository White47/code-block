import type { Graph, VisibleNode } from '../types';
import type { Translate } from './i18n';

const labels: Record<string, [string, string]> = {
  Project: ['项目', 'Project'], Module: ['模块', 'Module'], Class: ['类', 'Class'], Function: ['函数', 'Function'], Method: ['方法', 'Method'], Symbol: ['符号', 'Symbol'],
  Model: ['模型', 'Model'], ModelDefinition: ['模型定义', 'Model definition'], ModelInstance: ['模型实例', 'Model instance'], Layer: ['层', 'Layer'], LayerGroup: ['层组', 'Layer group'], Container: ['容器', 'Container'],
  ModuleInstance: ['模块实例', 'Module instance'], ModuleInvocation: ['调用', 'Module invocation'], RepeatedMember: ['重复成员', 'Repeated member'],
  Tensor: ['张量', 'Tensor'], Data: ['数据', 'Data'], Dataset: ['数据集', 'Dataset'], DataLoader: ['数据加载器', 'Data loader'], Optimizer: ['优化器', 'Optimizer'], Loss: ['损失', 'Loss'],
  DataNode: ['数据节点', 'Data node'], Assignment: ['赋值', 'Assignment'], Expression: ['表达式', 'Expression'], ParameterBinding: ['参数绑定', 'Parameter binding'], Variable: ['变量', 'Variable'], Attribute: ['属性', 'Attribute'], Import: ['导入', 'Import'], Unknown: ['未知', 'Unknown'],
  File: ['文件', 'File'], LexicalScope: ['词法作用域', 'Lexical scope'], ExternalSymbol: ['外部符号', 'External symbol'], UnresolvedSymbol: ['未解析符号', 'Unresolved symbol'], CallSite: ['调用位置', 'Call site'], FlowStep: ['流程步骤', 'Flow step'], ControlBlock: ['控制块', 'Control block'], Definition: ['定义', 'Definition'], Instance: ['实例', 'Instance'], Invocation: ['调用', 'Invocation'],
  Input: ['输入', 'Input'], Output: ['输出', 'Output'], EntryPoint: ['入口', 'Entry point'], Statement: ['语句', 'Statement'], Call: ['调用', 'Call'], Branch: ['分支', 'Branch'], Loop: ['循环', 'Loop'], Return: ['返回', 'Return'],
  ExecutionStep: ['执行步骤', 'Execution step'], TrainingLoop: ['训练循环', 'Training loop'], EvaluationLoop: ['评估循环', 'Evaluation loop'], Parameter: ['参数', 'Parameter'], Gradient: ['梯度', 'Gradient'],
  INPUT: ['输入', 'Input'], TARGET: ['目标', 'Target'], MASK: ['掩码', 'Mask'], FEATURE: ['特征', 'Feature'], HIDDEN_STATE: ['隐藏状态', 'Hidden state'], PREDICTION: ['预测', 'Prediction'], LOSS: ['损失', 'Loss'], PARAMETER: ['参数', 'Parameter'], GRADIENT: ['梯度', 'Gradient'], OTHER: ['其他', 'Other'],
  CONTAINS: ['包含', 'Contains'], IMPORTS: ['导入', 'Imports'], EXPORTS: ['导出', 'Exports'], CALLS: ['调用', 'Calls'], INHERITS: ['继承', 'Inherits'], IMPLEMENTS: ['实现', 'Implements'],
  CONTROL_FLOW: ['控制流', 'Control flow'], BRANCH_TRUE: ['条件成立', 'True branch'], BRANCH_FALSE: ['条件不成立', 'False branch'], LOOP_BACK: ['循环回边', 'Loop back'],
  READS: ['读取', 'Reads'], WRITES: ['写入', 'Writes'], PASSES_TO: ['传递给', 'Passes to'], RETURNS_TO: ['返回至', 'Returns to'], DATA_FLOW: ['数据流', 'Data flow'], GRADIENT_FLOW: ['梯度流', 'Gradient flow'],
};
export function graphLabel(value: string | null, t: Translate): string {
  if (!value) return '';
  return labels[value]?.[t('root') === 'Root' ? 1 : 0] ?? value.replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2');
}
export function nodeName(node: VisibleNode): string {
  const qualified = node.metadata.qualifiedName;
  return typeof qualified === 'string' && qualified ? qualified : node.codeName || node.displayName;
}
// Only identities in the currently bounded graph may supply an alias. Shared
// definition aliases can have multiple legitimate occurrence names.
export function graphAliases(graphs: (Graph | null | undefined)[]): Map<string, string> {
  const names = new Map<string, Set<string>>(); const visible = new Map<string, string>();
  for (const graph of graphs) for (const node of graph?.nodes ?? []) {
    const name = nodeName(node); visible.set(node.id, name);
    for (const id of [node.id, ...node.semanticComponentIds, ...node.codeNodeIds]) {
      const values = names.get(id) ?? new Set<string>(); values.add(name); names.set(id, values);
    }
  }
  return new Map([...names].map(([id, values]) => [id, visible.get(id) ?? [...values].join(' / ')]));
}
export function definitionBreadcrumb(node: VisibleNode | null, t: Translate): string {
  if (!node) return '';
  const definition = node.metadata.definitionCodeName;
  const instance = node.metadata.instanceContext;
  return [typeof instance === 'string' ? `${t('instance')}: ${instance}` : '', typeof definition === 'string' ? `${t('definition')}: ${definition}` : ''].filter(Boolean).join(' › ');
}
