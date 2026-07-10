The **Glad Labs brand (E3)** — a dark-only, colorblind-safe, industrial system. Import components from `@glad-labs/brand`; at runtime they render from `window.GladBrand.*`.

## Dark canvas is mandatory

E3 is **dark-only**. Every component uses light text (`--gl-text`) and accent colors tuned for a deep-navy canvas. **Set the page/root background first** — on a light background every headline and body line renders invisible:

```css
:root,
body {
  background: var(--gl-base);
  color: var(--gl-text);
  font-family: var(--gl-font-body);
}
```

Tokens and fonts come from a single import at the CSS root — `@import '@glad-labs/brand/tokens';` — which the bound bundle wires through `styles.css` (→ `tokens/colors.css`, `tokens/typography.css`, `tokens/effects.css`, and the component CSS `_ds_bundle.css`). There is **no React provider**: styling is pure CSS custom properties, so beyond the dark canvas no wrapper is required. Fonts (Space Grotesk, Geist, JetBrains Mono) load via the tokens CSS.

## The idiom: semantic components + design tokens (no utility classes)

Compose the exported components and pass **semantic props**. Do **not** hand-write utility classes and **never invent colors** — for your own layout glue, reach for the CSS variables:

- **Color** — `--gl-base` (canvas), `--gl-surface` / `--gl-surface-2` (raised cards), `--gl-text` / `--gl-text-muted` / `--gl-text-dim`, and accents `--gl-cyan` (primary / interactive / links), `--gl-amber` (categorical + emphasis + warnings), `--gl-mint` (success), `--gl-red` (error), `--gl-blue` (long-form links).
- **Type** — three voices: `--gl-font-display` (Space Grotesk, UPPERCASE — brand moments only), `--gl-font-body` (Geist — prose and UI), `--gl-font-mono` (JetBrains Mono — eyebrows, metadata, code, logs).
- **Feel** — square corners (zero border-radius), hairline borders (`--gl-hairline`), tight tracking on display, wide tracking (`--gl-tracking-wide`) on mono labels.

## Components (prefer these over the raw HTML they wrap)

- **`Button`** — `variant` primary | secondary | ghost; polymorphic `as` / `href` for links. Cyan-fill CTA, cyan-outline secondary, neutral ghost.
- **`Card`** (+ `Card.Meta`, `Card.Tag`, `Card.Title`, `Card.Body`) — surface card with a left accent tick; `accent` cyan | amber | mint.
- **`Display`** (+ `Display.Accent`) — uppercase hero headline; `xl` for the largest size; wrap a word in `Display.Accent` for amber emphasis.
- **`Eyebrow`** — the signature `// GLAD LABS · CONTEXT` mono-cyan kicker (it prepends the `//` itself). Sits directly above a `Display`.
- **`Logo`** — the cyan `GL` word-mark (a bare `<span>`; wrap it at the call site to make it a link).
- **`Status`** — `kind` ok | warn | err. **Colorblind-safe**: the ✓ / ⚠ / ✕ glyph carries the signal and color only reinforces it — never encode state by color alone.

## Where the truth lives

Read the bound stylesheets before styling: `styles.css` and its imports (`tokens/colors.css`, `tokens/typography.css`, `tokens/effects.css`, `_ds_bundle.css`). Per component, read `<Name>.d.ts` (the prop contract) and `<Name>.prompt.md` (usage).

## Idiomatic example

```jsx
import { Eyebrow, Display, Button } from '@glad-labs/brand';

// The page root sets the dark canvas (see above). This is a hero block.
<section style={{ padding: '64px 32px', maxWidth: 720 }}>
  <Eyebrow>Glad Labs · Poindexter</Eyebrow>
  <Display>
    Ship an <Display.Accent>AI writer.</Display.Accent>
  </Display>
  <p className="gl-body" style={{ marginTop: 16 }}>
    An autonomous content pipeline on a single local GPU.
  </p>
  <div style={{ display: 'flex', gap: 12, marginTop: 24 }}>
    <Button variant="primary">Get the guide</Button>
    <Button variant="secondary">Read the docs</Button>
  </div>
</section>;
```
