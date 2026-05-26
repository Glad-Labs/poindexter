/**
 * @jest-environment node
 *
 * styles/globals.css — Tailwind v4 directive contract test.
 *
 * Pins the 2026-05-26 fix for the third layer of the Tailwind v3→v4
 * migration the #566 dependency bump kicked off. The two prior layers
 * (Next.js 16 bundler-trace + @tailwindcss/postcss plugin rename) shipped
 * in #582 and #584; production deploys still failed with
 *
 *   Error: Cannot apply unknown utility class `md:text-4xl`. Are you
 *   using CSS modules or similar and missing `@reference`?
 *
 * because globals.css still drove Tailwind via the v3 trio
 * (@tailwind base/components/utilities). v4 silently no-ops those
 * directives, so every `@apply md:text-4xl ...` later in the file fires
 * before any utility theme is loaded.
 *
 * The fix replaces the v3 trio with a single `@import "tailwindcss"`
 * plus a `@config "../tailwind.config.cjs"` pointer so the existing JS
 * config (theme tokens + typography plugin + content globs) continues
 * to drive utility generation.
 *
 * If a future migration reintroduces the v3 directives — or removes the
 * v4 import — production deploys will fail again. This test catches the
 * regression in CI instead of waiting on Vercel.
 */

const fs = require('node:fs');
const path = require('node:path');

const RAW_GLOBALS_CSS = fs.readFileSync(
  path.join(__dirname, '..', '..', 'styles', 'globals.css'),
  'utf8',
);

/*
 * Strip CSS block comments before regex-matching directives. The
 * explanatory comments in globals.css contain the literal strings
 * "@tailwind base/components/utilities" and "@apply" to document
 * what the migration replaced — without this we'd be matching
 * commentary instead of executable CSS.
 */
const GLOBALS_CSS = RAW_GLOBALS_CSS.replace(/\/\*[\s\S]*?\*\//g, '');

describe('styles/globals.css — Tailwind v4 migration', () => {
  test('uses the v4 @import "tailwindcss" directive', () => {
    expect(GLOBALS_CSS).toMatch(/@import\s+['"]tailwindcss['"]\s*;/);
  });

  test('does not use the deprecated v3 @tailwind directives', () => {
    expect(GLOBALS_CSS).not.toMatch(/@tailwind\s+base\b/);
    expect(GLOBALS_CSS).not.toMatch(/@tailwind\s+components\b/);
    expect(GLOBALS_CSS).not.toMatch(/@tailwind\s+utilities\b/);
  });

  test('keeps the legacy tailwind.config.cjs reachable via @config', () => {
    expect(GLOBALS_CSS).toMatch(
      /@config\s+['"]\.\.\/tailwind\.config\.cjs['"]\s*;/,
    );
  });

  test('@import "tailwindcss" sits before any @apply directive', () => {
    const importIdx = GLOBALS_CSS.search(/@import\s+['"]tailwindcss['"]/);
    const firstApplyIdx = GLOBALS_CSS.search(/@apply\b/);
    expect(importIdx).toBeGreaterThanOrEqual(0);
    expect(firstApplyIdx).toBeGreaterThan(importIdx);
  });
});
