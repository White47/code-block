import { configDefaults, defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
export default defineConfig({
  base: './',
  plugins: [react()],
  test: {
    environment: 'jsdom',
    // Bound jsdom workers so desktop bridge tests keep their existing time limits under load.
    maxWorkers: 2,
    restoreMocks: true,
    exclude: [...configDefaults.exclude, 'scripts/**/*.test.mjs'],
  },
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
