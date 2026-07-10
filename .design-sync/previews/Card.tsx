import { Card } from '@glad-labs/brand';

// E3 is a dark-theme system — frame each cell on the --gl-base canvas so the
// card surface (--gl-surface) reads against the page the way it does in the app.
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

// Compound composition: Meta/Tag + Title + Body, swept across the accent axis.
export const Cyan = () => (
  <Frame>
    <Card accent="cyan">
      <Card.Meta>// POST · 2026-07-10</Card.Meta>
      <Card.Title>Ship an AI writer</Card.Title>
      <Card.Body>
        How Glad Labs runs an autonomous content pipeline — generate, review,
        publish, monetize — on a single local GPU.
      </Card.Body>
    </Card>
  </Frame>
);

export const Amber = () => (
  <Frame>
    <Card accent="amber">
      <Card.Tag>FEATURED</Card.Tag>
      <Card.Title>The QA rails that keep a bot honest</Card.Title>
      <Card.Body>
        Three anti-hallucination layers: prompts, adversarial LLM review, and a
        programmatic validator.
      </Card.Body>
    </Card>
  </Frame>
);

export const Mint = () => (
  <Frame>
    <Card accent="mint">
      <Card.Meta>// STATUS</Card.Meta>
      <Card.Title>Pipeline healthy</Card.Title>
      <Card.Body>
        110 live posts · 0 failures in the last 24h · avg quality 91.
      </Card.Body>
    </Card>
  </Frame>
);
