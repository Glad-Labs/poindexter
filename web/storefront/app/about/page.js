import Link from 'next/link';
import { Eyebrow, Display, Button } from '@glad-labs/brand';

export const metadata = {
  title: 'About',
  description:
    'Glad Labs is a one-person AI/ML shop building local-first publishing pipelines and the hardware + playbooks to run them.',
};

export default function AboutPage() {
  return (
    <section className="sf-page">
      <div className="sf-container">
        <div className="sf-rail" style={{ maxWidth: '760px' }}>
          <div className="sf-reveal sf-reveal--1 sf-hero__meta">
            <span>
              <span className="dot" aria-hidden="true" /> EST. 2025-09-25
            </span>
            <span>OPERATOR: MATTHEW M. GLADDING</span>
            <span>HQ: ON-PREM</span>
          </div>

          <div
            className="sf-reveal sf-reveal--2"
            style={{ marginTop: '2.5rem' }}
          >
            <Eyebrow>GLAD LABS · LLC</Eyebrow>
            <Display>
              Own the pipeline.{' '}
              <Display.Accent>Outsource nothing that matters.</Display.Accent>
            </Display>
          </div>

          <div
            className="sf-reveal sf-reveal--3 gl-body gl-body--lg"
            style={{
              maxWidth: '640px',
              marginTop: '1.75rem',
              display: 'grid',
              gap: '1.2rem',
            }}
          >
            <p>
              Glad Labs builds the tooling, the hardware, and the playbooks for
              people who want to run their own AI publishing pipeline — without
              handing the content, the relationships, or the vendor-lock-in to a
              SaaS company.
            </p>
            <p>
              The product is Poindexter: an open-source autonomous publishing
              agent that runs one post a day at a quality score above 80, in our
              house style, on commodity enthusiast-grade hardware. No paid APIs.
              No external inference. No one else deciding what you can write
              about.
            </p>
            <p>
              Poindexter Pro is how you get there fastest — tuned prompts,
              premium dashboards, and the full operator book. The Writing &amp;
              Research site (
              <Link
                href="https://www.gladlabs.io"
                style={{
                  color: 'var(--gl-cyan)',
                  textDecoration: 'underline',
                  textDecorationThickness: '1px',
                  textUnderlineOffset: '3px',
                }}
              >
                gladlabs.io
              </Link>
              ) is where we show our work.
            </p>
          </div>
        </div>

        <section className="sf-reveal sf-reveal--4 sf-facts">
          <div className="sf-fact">
            <div className="sf-fact__label">// MODE</div>
            <div className="sf-fact__value">
              <em>Local-first</em>
            </div>
            <p className="sf-fact__body">
              Every model runs on hardware you own. Ollama, SDXL, Piper. No paid
              inference, no egress to a vendor dashboard.
            </p>
          </div>

          <div className="sf-fact">
            <div className="sf-fact__label">// LICENSE</div>
            <div className="sf-fact__value">
              <em>AGPL-3.0</em>
            </div>
            <p className="sf-fact__body">
              Fork the entire pipeline. Run it. Modify it. Sell services around
              it. The only ask is you keep the next person&apos;s freedoms
              intact.
            </p>
          </div>

          <div className="sf-fact">
            <div className="sf-fact__label">// REVENUE</div>
            <div className="sf-fact__value">
              <em>Direct</em>
            </div>
            <p className="sf-fact__body">
              No subscription. Buy the guide, download it, keep it. Lifetime
              updates. Stripe-less — checkout runs on Lemon Squeezy.
            </p>
          </div>

          <div className="sf-fact">
            <div className="sf-fact__label">// STACK</div>
            <div className="sf-fact__value">
              <em>Boring</em>
            </div>
            <p className="sf-fact__body">
              PostgreSQL. Next.js. FastAPI. Ollama. Prometheus + Grafana.
              Everything else is a liability.
            </p>
          </div>
        </section>

        <section
          className="sf-reveal sf-reveal--4"
          style={{ marginTop: '3.5rem' }}
        >
          <Button as={Link} href="/" variant="ghost">
            ← Back to landing
          </Button>
        </section>
      </div>
    </section>
  );
}
