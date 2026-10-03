import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
export default defineConfig({
  base: './',
  plugins: [react()],
  test: { environment: 'jsdom', restoreMocks: true },
  build: {
    sourcemap: false,
    minify: 'esbuild',
    rollupOptions: {
      // Profiling isolated pathological Rollup tree-shaking: the same 205 modules
      // exceeded 95s / 3GB with analysis enabled, but built in 658ms without it.
      // Keep production minification; only disable the problematic analysis pass.
      treeshake: false,
    },
  },
});
