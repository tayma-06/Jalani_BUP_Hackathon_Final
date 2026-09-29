import { defineConfig, type Plugin } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

/**
 * Fails the build when the entry chunk grows past a budget.
 *
 * The chart code is loaded on demand, which is what keeps this number small. Without a hard
 * limit, an innocent-looking import can pull a heavy dependency back into the entry chunk and
 * every operator pays for it on first paint, so the regression has to break the build.
 */
const ENTRY_BUDGET_KB = 400;

function entryBudget(): Plugin {
  return {
    name: 'jalani-entry-budget',
    apply: 'build',
    enforce: 'post',
    generateBundle(_options, bundle) {
      const offenders = Object.values(bundle)
        .filter(chunk => chunk.type === 'chunk' && chunk.isEntry && chunk.fileName.endsWith('.js'))
        .filter(chunk => chunk.code.length / 1024 > ENTRY_BUDGET_KB)
        .map(chunk => `${chunk.fileName} is ${(chunk.code.length / 1024).toFixed(0)} kB`);
      if (offenders.length) {
        this.error(`Entry bundle exceeds the ${ENTRY_BUDGET_KB} kB budget: ${offenders.join(', ')}. ` +
          'Keep heavy dependencies behind a dynamic import.');
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), entryBudget()],
  server: { proxy: { '/api': 'http://127.0.0.1:8080' } },
  test: { environment: 'happy-dom', globals: true, setupFiles: './src/test-setup.ts' },
});
