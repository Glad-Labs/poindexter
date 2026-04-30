# @glad-labs/brand

Glad Labs brand system. Shared across `gladlabs.io` (publishing platform) and `gladlabs.ai` (storefront).

**Canonical style: E3** — uppercase Space Grotesk display + mono eyebrow + cyan/amber/mint palette, red-green-colorblind safe.

## Install

In a workspace repo, this is auto-linked via `"workspaces": ["packages/brand", ...]` in the root `package.json`.

Dependent apps declare it as:

```json
{
  "dependencies": {
    "@glad-labs/brand": "*"
  }
}
```

## Use — tokens only (CSS variables)

```css
/* app/globals.css */
@import '@glad-labs/brand/tokens';

/* now available everywhere */
.my-card {
  background: var(--gl-surface);
  color: var(--gl-text);
}
```

## Use — tokens + components

```jsx
// app/page.jsx
import { Eyebrow, Display, Button, Card, Status } from '@glad-labs/brand';

export default function Hero() {
  return (
    <section className="gl-atmosphere">
      <Eyebrow>GLAD LABS · POINDEXTER V3.1</Eyebrow>
      <Display>
        Ship an <Display.Accent>AI writer.</Display.Accent>
        <br />
        Own the stack.
      </Display>
      <p className="gl-body gl-body--lg">
        Local-first AI publishing. One post a day at 80+ quality, every channel,
        fully autonomous. Ollama only — no paid APIs.
      </p>
      <div style={{ display: 'flex', gap: '0.625rem', marginTop: '1.5rem' }}>
        <Button variant="primary">▶ Get the guide — $29</Button>
        <Button variant="secondary">Read the docs</Button>
      </div>
    </section>
  );
}
```

Make sure global CSS imports both tokens AND the component stylesheet:

```css
@import '@glad-labs/brand/tokens';
@import '@glad-labs/brand/components/components.css';
```

## Tokens

| Name                | Value                        | Role                                                  |
| ------------------- | ---------------------------- | ----------------------------------------------------- |
| `--gl-base`         | `#070a0f`                    | Page background                                       |
| `--gl-surface`      | `#101520`                    | Cards, elevated                                       |
| `--gl-text`         | `#d8e0e8`                    | Primary text                                          |
| `--gl-text-muted`   | `#7a8a92`                    | Metadata, secondary                                   |
| `--gl-cyan`         | `#00e5ff`                    | **Primary interactive** — CTAs, links, active         |
| `--gl-amber`        | `#ffb74d`                    | **Categorical** — tags, warnings, display accent word |
| `--gl-mint`         | `#7afbc4`                    | Success (always paired with ✓ glyph)                  |
| `--gl-red`          | `#ff6b6b`                    | Error (always paired with ✕ glyph)                    |
| `--gl-font-display` | Space Grotesk 700, uppercase | Brand display                                         |
| `--gl-font-body`    | Geist Sans                   | Body copy                                             |
| `--gl-font-mono`    | JetBrains Mono               | Eyebrow, metadata, terminal logs                      |

## Colorblind safety

Matt is red-green deficient. Never encode information via color alone. Status components pair color with glyph: `Status kind="err"` renders `✕ …`, `kind="warn"` renders `⚠ …`, `kind="ok"` renders `✓ …`. Mint sits close enough to cyan to register as distinct from amber — but the glyph is the signal, not the color.

## Signature patterns

- **Mono eyebrow** — `// GLAD LABS · POINDEXTER V3.1` above every hero. The `//` + `·` separator is the connective tissue.
- **Uppercase display + amber accent word** — `SHIP AN [AMBER]AI WRITER.[/AMBER]`. One word per H1 gets the amber treatment.
- **Left-cyan-tick cards** — `.gl-tick-left` + cyan border-left 3px. Replaces glassmorphism.
- **Terminal logs** — `[RUN 0047] PUBLISHED · 2026-04-20 · QUALITY=82`. Use the `.gl-log` component for the styled block.
- **Zero border-radius** — everywhere. Corners are square. The CRT scanline + radial cyan wash (`.gl-atmosphere`) does the soft-warm work that rounded corners usually do.
