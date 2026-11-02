import type { SummaryStep } from '../../types';
import type { Translate } from '../i18n';

const titles: Record<string, string> = {
  'Evaluate model behavior': '评估模型表现', 'Update learning rate': '调整学习率',
  'Accumulate gradients': '累积梯度', 'Encode input': '将输入编码为词元',
  'Sample next token': '采样下一个词元', 'Decode output': '将词元解码为输出',
  'Decode and display output': '解码并展示生成结果', 'Merge residual path': '合并残差路径',
  'Select final token state': '选取最后位置的状态', 'Enable training behavior': '切换到训练模式',
  'Enable evaluation behavior': '切换到评估模式', 'Run model forward': '计算模型预测',
  'Calculate training loss': '计算训练损失', 'Clear gradients': '清空梯度',
  'Collect evaluation loss': '汇总评估损失',
  'Backpropagate loss': '反向传播损失', 'Update parameters': '更新模型参数',
  'Disable gradient tracking': '关闭梯度记录', 'Repeat training epochs': '按轮次重复训练',
  'Iterate over batches': '逐批处理数据', 'Save training checkpoint': '保存训练检查点',
  'Load training checkpoint': '加载训练检查点', 'Represent tokens as embeddings': '将词元转换为嵌入表示',
  'Normalize activations': '归一化中间表示', 'Project model features': '映射模型特征',
  'Look up embedding vectors': '查找嵌入向量', 'Regularize activations': '正则化中间表示',
  'Transform activations': '进行非线性变换', 'Organize named model components': '组织命名模型组件',
  'Organize model components': '组织模型组件',
  'Organize repeated model blocks': '组织重复的模型块（次数由配置决定）', 'Apply attention': '计算注意力',
  'Train model': '训练模型', 'Evaluate model': '评估模型', 'Run model inference': '使用模型进行预测',
  'Export model': '导出模型', 'Model workflow': '模型任务流程', 'Generate output': '生成输出',
  'Prepare data': '准备数据', 'Define model structure': '定义模型结构', 'Model structure': '模型结构',
  'Program entry': '程序入口（职责待确认）', 'Program task': '程序任务（职责待确认）',
  'Program workflow': '程序任务流程', 'Choose a path': '按条件选择路径', 'Repeat work': '循环执行任务',
  'Iteration loop': '迭代循环', 'Conditional path': '条件分支', 'Control path': '控制路径',
  'Choose a training path': '按条件选择训练路径', 'Calculate statistics': '计算统计结果',
  'Display output': '展示输出结果', 'Read data artifact': '读取数据产物', 'Write data artifact': '写入数据产物',
};

/** Translate only generated presentation text. Author statements stay verbatim. */
export function summaryTitle(title: string, t: Translate, preserveAuthor = false): string {
  if (preserveAuthor || t('root') === 'Root') return title;
  if (titles[title]) return titles[title];
  if (title.startsWith('Call ')) return `调用 ${title.slice(5)}（职责待确认）`;
  if (title.startsWith('Model component: ')) return `模型组件：${title.slice(17)}`;
  if (title.startsWith('Condition: ')) return `条件：${title.slice(11)}`;
  if (title.startsWith('Loop while: ')) return `循环条件：${title.slice(12)}`;
  if (title.startsWith('Loop: ')) return `循环：${title.slice(6)}`;
  const repeated = /^Repeat model blocks \((\d+) configured\)$/.exec(title);
  return repeated ? `重复模型块 ×${repeated[1]}（静态配置）` : title;
}

export function summaryStepText(step: SummaryStep, t: Translate): SummaryStep {
  return { ...step, title: summaryTitle(step.title, t, step.basis === 'source_author_statement') };
}
