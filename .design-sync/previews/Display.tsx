import { Display, Eyebrow } from '@glad-labs/brand';

// E3 is a dark-theme system — the display headline uses --gl-text (a light
// off-white). Frame each cell on the --gl-base canvas so it's legible as designed.
const Frame = ({ children }: { children: React.ReactNode }) => (
  <div
    style={{
      background: 'var(--gl-base)',
      padding: '40px 32px',
      fontFamily: 'var(--gl-font-body)',
    }}
  >
    {children}
  </div>
);

// Hero headline — uppercase Space Grotesk with an amber accent word.
export const Hero = () => (
  <Frame>
    <Display>
      Ship an <Display.Accent>AI writer.</Display.Accent>
    </Display>
  </Frame>
);

// The extra-large hero size.
export const ExtraLarge = () => (
  <Frame>
    <Display xl>Autonomous content, on your own GPU.</Display>
  </Frame>
);

// The signature Glad Labs pattern: mono cyan eyebrow above the display headline.
export const WithEyebrow = () => (
  <Frame>
    <Eyebrow>Glad Labs · Poindexter</Eyebrow>
    <Display>Operate the whole business from your phone.</Display>
  </Frame>
);
