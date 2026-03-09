import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.js'],
    pool: 'forks',
    poolOptions: {
      forks: {
        minForks: 1,
        maxForks: 4,
      },
    },
    server: {
      deps: {
        inline: [/@testing-library/, /@mui\/icons-material/],
      },
    },
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      // Enforce minimum coverage thresholds. Fail CI if any threshold is missed.
      thresholds: {
        lines: 50,
        functions: 50,
        branches: 40,
        statements: 50,
        // Critical service/hook paths — raise thresholds here as coverage grows
        'src/services/**': {
          lines: 60,
          functions: 60,
          branches: 50,
          statements: 60,
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
