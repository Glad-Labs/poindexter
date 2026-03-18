import { defineConfig } from 'vitest/config';
import path from 'path';

export default defineConfig({
  esbuild: {
    jsx: 'automatic',
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.js'],
    env: {
      REACT_APP_API_URL: 'http://localhost:8000',
      REACT_APP_OLLAMA_URL: 'http://localhost:11434',
    },
    exclude: [
      '**/node_modules/**',
      '**/dist/**',
      '**/e2e/**',
      '**/*.integration.test.*',
    ],
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
