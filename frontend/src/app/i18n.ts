import type { Language } from '../types';
const text = {
  upstream: ['上游', 'Upstream'], downstream: ['下游', 'Downstream'], both: ['双向', 'Both'], moreHops: ['更多层', 'More hops'],
  overview: ['项目概览', 'Overview'], architecture: ['架构', 'Architecture'], execution: ['执行流程', 'Execution'], Calls: ['调用关系', 'Calls'], DataFlow: ['数据流', 'Data flow'], Gradient: ['梯度', 'Gradient'], Symbol: ['符号', 'Symbols'],
  search: ['搜索', 'Search'], settings: ['设置', 'Settings'], problems: ['问题', 'Problems'], inspector: ['检查器', 'Inspector'], open: ['打开项目', 'Open project'], path: ['本地项目绝对路径', 'Absolute local project path'], analyze: ['分析项目', 'Analyze project'], reanalyze: ['重新分析', 'Re-analyze'],
  waiting: ['正在连接桌面服务…', 'Connecting to desktop…'], retry: ['重试连接', 'Retry connection'], desktop: ['桌面服务不可用，请从 Python 桌面入口打开应用。', 'Desktop unavailable. Open the app from the Python desktop entry.'], empty: ['打开本地项目，开始探索代码。', 'Open a local project to explore its code.'],
  source: ['源码', 'Source'], openSource: ['打开源码', 'Open source'], side: ['在侧边打开', 'Open to side'], reveal: ['在图中定位', 'Reveal in graph'], detail: ['展开细节岛', 'Open detail island'], expand: ['展开', 'Expand'], collapse: ['折叠', 'Collapse'], close: ['关闭', 'Close'],
  models: ['模型', 'Models'], entries: ['入口', 'Entry points'], files: ['已解析文件', 'Parsed files'], symbols: ['已解析符号', 'Resolved symbols'], exact: ['精确', 'Exact'], probable: ['推测', 'Probable'], unresolved: ['未解析', 'Unresolved'],
  recent: ['最近项目', 'Recent projects'], language: ['界面语言', 'Interface language'], cache: ['缓存状态', 'Cache status'], healthy: ['无缓存错误', 'No cache errors'], outdated: ['分析已过期，请重新分析。', 'Analysis is outdated. Re-analyze the project.'],
  missingInspector: ['bridge 暂缺 get_inspector；以下为图节点已有信息。', 'Bridge has no get_inspector; showing available graph node data.'], select: ['选择图节点查看详情。', 'Select a node to inspect it.'], evidence: ['证据', 'Evidence'], relations: ['关系', 'Relations'], data: ['数据', 'Data'], children: ['子节点', 'Children'], confidence: ['置信度', 'Confidence'],
  noProblems: ['没有报告的问题', 'No reported problems'], noResults: ['没有搜索结果', 'No results'], query: ['搜索名称、符号、作用域…', 'Search names, symbols, scopes…'], noSource: ['选择源码锚点以查看代码。', 'Select a source anchor to view code.'],
  nodes: ['节点', 'nodes'], edges: ['关系', 'edges'], hidden: ['已隐藏', 'hidden'], local: ['本地工作空间', 'LOCAL WORKSPACE'], analysisSettings: ['分析设置', 'Analysis settings'], ignore: ['忽略路径由 Python 分析配置管理；当前 bridge 未提供修改接口。', 'Ignore paths are managed by Python analysis configuration; this bridge exposes no editor.'],
  NotAnalyzed: ['未分析', 'Not analyzed'], Analyzing: ['分析中', 'Analyzing'], Ready: ['就绪', 'Ready'], Partial: ['部分完成', 'Partial'], Outdated: ['已过期', 'Outdated'], Failed: ['失败', 'Failed'], fit: ['适应画布', 'Fit canvas'], split: ['图 / 源码', 'Graph / Source'], searchCount: ['结果', 'results'], noTabs: ['工作台已清空，从侧栏打开视图。', 'Workspace cleared. Open a view from the sidebar.'],
} satisfies Record<string, [string, string]>;
export type TextKey = keyof typeof text;
export function translator(language: Language) { return (key: TextKey) => text[key][language === 'en' ? 1 : 0]; }
export type Translate = ReturnType<typeof translator>;
