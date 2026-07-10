# design-sync notes — @glad-labs/brand

Repo-specific gotchas for syncing `packages/brand` (`@glad-labs/brand`) to
Claude Design. Read this before a re-sync.

## Build invocation

- **Run `npm ci` at the repo root first.** This is an npm workspace and a fresh
  worktree has NO `node_modules`. The install provides `react` / `react-dom` /
  `@types/react` (hoisted to root) and the `@glad-labs/brand` workspace symlink
  that `tokensPkg` reads from.
- Entry is **source**, not a dist: `--entry ./packages/brand/src/index.js`. The
  package ships raw ESM (`files: ["src"]`, no build step / no `dist/`).
- `--node-modules ./node_modules` (repo root — npm hoists react there;
  `packages/brand/node_modules` does not exist).
- Tokens come from `tokensPkg: "@glad-labs/brand"` + `tokensGlob: "src/tokens/*.css"`
  (resolves via the workspace symlink). The Google-Fonts `@import` lives inside
  `tokens/index.css`, so all four token files must be copied (they are).

## Dark-theme DS — previews MUST be framed

E3 is **dark-only**: components use `--gl-text` (light) meant for the
`--gl-base` (#070a0f) canvas. On the default white preview card they render
near-invisible. Every `.design-sync/previews/*.tsx` wraps its cells in a local
`Frame` that sets `background: var(--gl-base)`. Keep that when editing previews.
This is also the load-bearing convention in `conventions.md` for the design agent.

## Status previews must not lead with ⚠

`package-validate` / `package-capture` flag a cell as a caught error when its
render text `.startsWith('⚠')` (the harness's error-boundary marker). A bare
`<Status kind="warn">` renders `⚠ …` and false-trips this → `bad`. `Status.tsx`
composes each status in a labeled `Row` (`WORKER ✓ …`, `GPU-0 ⚠ …`) so the mono
label leads and the glyph is not at position 0. Do not revert to bare Status cells.

## Windows EPERM on rebuild

`package-build` does `rmSync(ds-bundle, {force})` at start; on Windows this
`EPERM`s if a just-finished validate/capture chromium still holds a handle on
`ds-bundle/_screenshots`. Fix: run `rm -rf ds-bundle` as its **own** step
(not chained into the build command), then build — the inter-command gap lets
the handle release. A leaked `chrome-headless-shell.exe` (playwright's, distinct
from the `chrome.exe` browser) can be killed safely.

## Playwright / chromium

Repo pins `@playwright/test ^1.61.1` → chromium revision **1228**. The machine
cache had 1223/1187 but not 1228; `npx playwright install chromium
chromium-headless-shell` was run once to match. `package-validate` resolves
playwright from the repo-root `node_modules`.

## Known render warns

- None currently. (`[FONT_REMOTE]` is expected/non-blocking — see risks below.)

## Config notes

- `cfg.overrides.Display.cardMode = "column"` — the `xl` display headline is
  wider than a default grid cell (`[GRID_OVERFLOW]`), so Display cards render
  one story per row.

## Re-sync risks (what can silently go stale)

- **Remote fonts.** `tokens/index.css` pulls Space Grotesk / Geist / JetBrains
  Mono via a Google-Fonts `@import` (`[FONT_REMOTE]`). They loaded fine in the
  playwright render at sync time. If claude.ai/design's render environment ever
  blocks the font host, every design falls back to system fonts — the fix would
  be self-hosting the woff2s via `cfg.extraFonts` and rewriting `tokens` to
  `@font-face`. This is a deliberate match to how the package itself ships
  (the public site + console use the same remote `@import`).
- **Preview Frame tokens.** The previews reference `var(--gl-base)` /
  `var(--gl-text)` literally. If those token names are renamed in `colors.css`,
  update the Frames (and `conventions.md`).
- **React externalization.** The bundle externalizes React to `window` (served
  from `_vendor/`); react was 19.2.7 at sync time.
