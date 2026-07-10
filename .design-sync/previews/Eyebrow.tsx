import { Eyebrow } from '@glad-labs/brand';

// E3 is a dark-theme system — frame each cell on the --gl-base canvas.
const Frame = ({ children }: { children: React.ReactNode }) => (
  <div
    style={{
      background: 'var(--gl-base)',
      padding: '32px 28px',
      fontFamily: 'var(--gl-font-body)',
    }}
  >
    {children}
  </div>
);

// The signature `// GLAD LABS · …` mono-cyan eyebrow that sits above headlines.
// The component prepends the `//` itself.
export const Signature = () => (
  <Frame>
    <Eyebrow>Glad Labs · Poindexter v0.99</Eyebrow>
  </Frame>
);

export const SectionStamp = () => (
  <Frame>
    <Eyebrow>Architecture · Kernel / Module / Capability</Eyebrow>
  </Frame>
);
