import js from '@eslint/js';
import globals from 'globals';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import nextPlugin from '@next/eslint-plugin-next';
import tsParser from '@typescript-eslint/parser';

export default [
  {
    ignores: [
      // Build outputs
      'build/**',
      'dist/**',
      '.next/**',
      'out/**',
      // Dependencies
      'node_modules/**',
      // Generated files
      'coverage/**',
      '.cache/**',
      // Environment files
      '.env',
      '.env.local',
      '.env.*.local',
      // Logs
      '*.log',
      'npm-debug.log*',
      'yarn-debug.log*',
      'yarn-error.log*',
      // OS files
      '.DS_Store',
      'Thumbs.db',
      // Test files — excluded from Next.js build lint pass
      '**/__tests__/**',
      '**/*.test.{js,jsx,ts,tsx}',
      '**/*.spec.{js,jsx,ts,tsx}',
      'jest.setup.js',
      // Config files
      '*.config.js',
      'jest.config.js',
    ],
  },
  {
    files: [
      'components/**/*.{js,jsx,ts,tsx}',
      'pages/**/*.{js,jsx,ts,tsx}',
      'lib/**/*.{js,jsx,ts,tsx}',
      'app/**/*.{js,jsx,ts,tsx}',
    ],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.es2021,
        ...globals.node,
        ...globals.jest,
      },
      parserOptions: {
        ecmaFeatures: {
          jsx: true,
        },
        allowImportExportEverywhere: true,
        allowReturnOutsideFunction: true,
      },
    },
    plugins: {
      react,
      'react-hooks': reactHooks,
      '@next/next': nextPlugin,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...react.configs.recommended.rules,
      ...nextPlugin.configs.recommended.rules,
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'react/react-in-jsx-scope': 'off',
      'react/prop-types': 'off',
      'react/display-name': 'off',
      'react/no-unescaped-entities': 'off',
      'react/no-unknown-property': 'off',
      '@next/next/no-html-link-for-pages': 'off',
      '@next/next/no-img-element': 'warn',
      'no-console': 'error',
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' }],
      'no-useless-escape': 'off',
      'no-unreachable': 'warn',
      'no-undef': 'warn',
      // Brand-token guard: forbid hardcoded Tailwind palette classes that
      // bypass the --gl-* token system. Use bg-[var(--gl-surface)],
      // text-[var(--gl-text-muted)], border-[var(--gl-cyan-border)], etc.
      // See packages/brand/src/tokens/colors.css for the full token list.
      'no-restricted-syntax': [
        'error',
        {
          selector:
            'JSXAttribute[name.name="className"] Literal[value=/\\bcyan-[0-9]/]',
          message:
            'Use a --gl-* CSS variable instead of a raw Tailwind cyan-N class (e.g. text-[var(--gl-cyan)]).',
        },
        {
          selector:
            'JSXAttribute[name.name="className"] Literal[value=/\\bslate-[0-9]/]',
          message:
            'Use a --gl-* CSS variable instead of a raw Tailwind slate-N class (e.g. bg-[var(--gl-surface)]).',
        },
      ],
    },
    settings: {
      react: {
        // Explicit version avoids the auto-detect path in eslint-plugin-react
        // that calls the removed ESLint 10 `getFilename()` API.
        version: '19',
      },
    },
  },
  // TypeScript files — use the TS parser so ESLint can handle type syntax
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
  },
  // Test files - disable img element warning for mocks
  {
    files: [
      '**/__tests__/**/*.{js,jsx,ts,tsx}',
      '**/*.test.{js,jsx,ts,tsx}',
      '**/*.spec.{js,jsx,ts,tsx}',
    ],
    rules: {
      '@next/next/no-img-element': 'off',
      'no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
];
