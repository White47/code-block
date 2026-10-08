import type { editor } from 'monaco-editor';
const models = new Map<string, editor.ITextModel>();
export function getSourceModel(key: string) { return models.get(key); }
export function retainSourceModel(key: string, model: editor.ITextModel) { models.set(key, model); }
export function releaseSourceModel(key: string) { models.get(key)?.dispose(); models.delete(key); }
