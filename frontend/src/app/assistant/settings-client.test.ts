import { afterEach, expect, it, vi } from 'vitest';
import { assistantClient } from './client';
import type { AssistantSettings } from './types';

afterEach(() => { delete window.pywebview; });

const settings: AssistantSettings = { available: false, enabled: false, baseUrl: '',
  model: '', keySet: false, configRevision: 0,
  issue: { code: 'AI_DEPENDENCY_MISSING', message: 'untrusted debug details' } };

it('loads only the closed settings shape with a fixed safe issue', async () => {
  const get = vi.fn(async () => ({ ok: true as const, schemaVersion: 1, data: settings }));
  window.pywebview = { api: { assistant_get_settings: get } };
  const result = await assistantClient.getSettings();
  expect(result).toEqual({ ...settings,
    issue: { code: 'AI_DEPENDENCY_MISSING', message: 'Assistant request could not be completed.' } });
  expect(get).toHaveBeenCalledExactlyOnceWith();
});

it('refuses secret-bearing settings and preserves only fixed configuration errors', async () => {
  window.pywebview = { api: { assistant_get_settings: vi.fn(async () => ({
    ok: true as const, schemaVersion: 1, data: { ...settings, key: 'not-a-real-key' },
  })) } };
  await expect(assistantClient.getSettings()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
  const configure = vi.fn(async () => ({ ok: false as const, schemaVersion: 1,
    error: { code: 'AI_INVALID_INPUT', message: 'private request data' } }));
  window.pywebview = { api: { assistant_configure: configure } };
  await expect(assistantClient.configure({ baseUrl: 'https://example.test/v1',
    model: 'test', key: null, enabled: false })).rejects.toMatchObject({
    code: 'AI_INVALID_INPUT', message: 'Assistant request could not be completed.',
  });
  expect(configure).toHaveBeenCalledTimes(1);
});
