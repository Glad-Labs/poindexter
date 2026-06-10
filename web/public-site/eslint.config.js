import js from '@eslint/js';
import globals from 'globals';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import nextPlugin from '@next/eslint-plugin-next';

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
    },
    settings: {
      react: {
        version: 'detect',
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
