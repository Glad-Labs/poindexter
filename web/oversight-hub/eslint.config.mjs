import js from '@eslint/js';
import globals from 'globals';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import jestPlugin from 'eslint-plugin-jest';

export default [
  {
    ignores: [
      'node_modules/**',
      'build/**',
      'dist/**',
      'coverage/**',
      '.vscode/**',
      '.idea/**',
      '*.swp',
      '*.swo',
      '.DS_Store',
      'Thumbs.db',
      'package-lock.json',
      'yarn.lock',
      'pnpm-lock.yaml'
    ]
  },
  {
    files: ['**/*.js', '**/*.jsx'],
    languageOptions: {
      ecmaVersion: 2021,
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.node,
        ...globals.es2021,
        ...globals.jest
      },
      parserOptions: {
        ecmaFeatures: {
          jsx: true
        }
      }
    },
    plugins: {
      react,
      'react-hooks': reactHooks,
      jest: jestPlugin
    },
    settings: {
      react: {
        version: 'detect'
      }
    },
    rules: {
      ...js.configs.recommended.rules,
      ...react.configs.recommended.rules,
      ...react.configs['jsx-runtime'].rules,
      ...reactHooks.configs.recommended.rules,
      ...jestPlugin.configs.recommended.rules,

      // Code quality
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      'no-console': ['error', { allow: [] }],
      'no-var': 'error',
      'eqeqeq': ['warn', 'always'],
      'semi': ['warn', 'always'],
      'quotes': ['warn', 'single'],

      // React-specific
      'react/react-in-jsx-scope': 'off',
      'react/prop-types': 'warn',
      'react/no-unescaped-entities': 'warn', // Relaxed for legacy code
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',

      // Jest
      'jest/expect-expect': 'warn',
      'jest/no-disabled-tests': 'warn',
      'jest/no-conditional-expect': 'warn' // Allow expects in waitFor, etc.
    }
  }
];
