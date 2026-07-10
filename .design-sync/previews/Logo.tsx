import { Logo } from '@glad-labs/brand';

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

// The Glad Labs "GL" word-mark — cyan, display font, uppercase.
export const Default = () => (
  <Frame>
    <Logo />
  </Frame>
);

// Realistic usage: the mark leading a masthead. Logo renders a bare <span>, so
// wrap it at the call site for layout.
export const InHeader = () => (
  <Frame>
    <header style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
      <Logo />
      <span
        style={{
          fontFamily: 'var(--gl-font-mono)',
          fontSize: '11px',
          letterSpacing: '0.12em',
          textTransform: 'uppercase',
          color: 'var(--gl-text-muted)',
        }}
      >
        Operator Console
      </span>
    </header>
  </Frame>
);
