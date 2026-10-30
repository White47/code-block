// @vitest-environment node
import { createRequire } from 'node:module';
import { realpathSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';
import { useExternalStoreRuntime } from '@assistant-ui/react';

it('loads the controlled runtime while the actual textarea consumer resolves only the private ref utility', () => {
  const require = createRequire(import.meta.url);
  const assistant = createRequire(require.resolve('@assistant-ui/react'));
  const textarea = createRequire(assistant.resolve('react-textarea-autosize'));
  const expected = resolve(dirname(fileURLToPath(import.meta.url)), '../../../packages/use-composed-ref-compat/index.cjs');
  expect(useExternalStoreRuntime).toBeTypeOf('function');
  expect(realpathSync(textarea.resolve('use-composed-ref'))).toBe(realpathSync(expected));
  expect(realpathSync(assistant.resolve('react'))).toBe(realpathSync(require.resolve('react')));
});
