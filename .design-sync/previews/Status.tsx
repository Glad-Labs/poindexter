import { Status } from '@glad-labs/brand';

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

// Status indicators live in labeled rows (a service-health list is the canonical
// usage), so compose each with a mono key. This also keeps the cell from leading
// with the ⚠ glyph, which the render harness reads as a thrown-error marker.
const Row = ({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) => (
  <div style={{ display: 'flex', gap: '14px', alignItems: 'center' }}>
    <span
      style={{
        fontFamily: 'var(--gl-font-mono)',
        fontSize: '11px',
        letterSpacing: '0.12em',
        textTransform: 'uppercase',
        color: 'var(--gl-text-dim)',
        minWidth: '88px',
      }}
    >
      {label}
    </span>
    {children}
  </div>
);

// The kind axis. Color reinforces the signal, but the ✓/⚠/✕ glyph carries it —
// Matt is red-green colorblind, so status is never encoded by color alone.
export const Ok = () => (
  <Frame>
    <Row label="worker">
      <Status kind="ok">All systems go</Status>
    </Row>
  </Frame>
);

export const Warn = () => (
  <Frame>
    <Row label="gpu-0">
      <Status kind="warn">GPU at 82°C</Status>
    </Row>
  </Frame>
);

export const Err = () => (
  <Frame>
    <Row label="publish">
      <Status kind="err">Publish failed</Status>
    </Row>
  </Frame>
);
