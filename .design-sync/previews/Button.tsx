import { Button } from '@glad-labs/brand';

// E3 is a dark-theme system — components use light text/accents meant for the
// --gl-base canvas. Frame each cell on that canvas so it renders as designed
// (the same thing gladlabs.io and the console do at the app's global root).
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

// The variant axis — the prop that most changes appearance.
export const Primary = () => (
  <Frame>
    <Button variant="primary">Get the guide</Button>
  </Frame>
);
export const Secondary = () => (
  <Frame>
    <Button variant="secondary">Read the docs</Button>
  </Frame>
);
export const Ghost = () => (
  <Frame>
    <Button variant="ghost">Cancel</Button>
  </Frame>
);

// Disabled state (statically renderable).
export const Disabled = () => (
  <Frame>
    <Button variant="primary" disabled>
      Publishing…
    </Button>
  </Frame>
);

// Polymorphic `as` — renders an <a> for router / external links.
export const AsLink = () => (
  <Frame>
    <Button as="a" href="#" variant="secondary">
      View on gladlabs.io
    </Button>
  </Frame>
);
