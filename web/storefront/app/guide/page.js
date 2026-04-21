import Link from 'next/link';
import { Eyebrow, Display, Button } from '@glad-labs/brand';
import { LemonSqueezyOverlay } from '@/components/LemonSqueezyOverlay';
import { LS_GUIDE_URL, GUIDE_PRICE_USD } from '@/lib/site.config';

export const metadata = {
  title: 'The Quick Start Guide',
  description:
    'The $29 Quick Start Guide — stand up an autonomous AI publishing pipeline on your own hardware in a weekend.',
};

export default function GuidePage() {
  return (
    <section className="sf-page">
      <div className="sf-container">
        <div className="sf-rail" style={{ maxWidth: '760px' }}>
          <div className="sf-reveal sf-reveal--1 sf-hero__meta">
            <span>
              <span className="dot" aria-hidden="true" /> PRODUCT · 01
            </span>
            <span>PDF · EPUB · REPO ACCESS</span>
            <span>INSTANT DELIVERY</span>
          </div>

          <div
            className="sf-reveal sf-reveal--2"
            style={{ marginTop: '2.5rem' }}
          >
            <Eyebrow>GLAD LABS · QUICK START GUIDE</Eyebrow>
            <Display>
              A weekend <Display.Accent>is enough.</Display.Accent>
            </Display>
          </div>

          <p
            className="sf-reveal sf-reveal--3 gl-body gl-body--lg"
            style={{ maxWidth: '640px', marginTop: '1.5rem' }}
          >
            The exact path from an empty workstation to a publishing pipeline
            that ships one post a day — with a human review gate, quality
            scoring, SEO, a newsletter feed, and a podcast. Written by someone
            who actually runs it.
          </p>
        </div>

        <section
          className="sf-reveal sf-reveal--4"
          style={{ marginTop: '4rem', maxWidth: '760px' }}
        >
          <div
            className="gl-eyebrow"
            style={{ marginBottom: '1.2rem', color: 'var(--gl-cyan)' }}
          >
            // WHAT&apos;S INSIDE
          </div>

          <ul className="sf-checklist">
            <li>
              <span>
                <strong>CHAPTER 01</strong> — Architecture. The five surfaces
                (tap, stage, quality, publish, observability) and why every
                other decision falls out of that shape.
              </span>
            </li>
            <li>
              <span>
                <strong>CHAPTER 02</strong> — Hardware. The exact parts list +
                thermal + electrical considerations for a 24/7 single-node rig.
                Budget tier, enthusiast tier, and &ldquo;what Matt actually
                runs&rdquo;.
              </span>
            </li>
            <li>
              <span>
                <strong>CHAPTER 03</strong> — Models. Model-per-GPU allocation,
                quant trade-offs, when to prefer a bigger model at lower
                throughput vs. a smaller one with more parallelism.
              </span>
            </li>
            <li>
              <span>
                <strong>CHAPTER 04</strong> — Prompts. The actual prompt
                templates — research, drafting, scoring, editing — with the
                failure modes and the DB-tunable knobs.
              </span>
            </li>
            <li>
              <span>
                <strong>CHAPTER 05</strong> — Quality. How to score output
                reliably, how to gate publishing on the score, and how to keep
                the bar high without the agent drifting.
              </span>
            </li>
            <li>
              <span>
                <strong>CHAPTER 06</strong> — Ops. Observability (LGTM+P), error
                reporting (GlitchTip), uptime (Kuma), and how to let the system
                self-heal instead of paging you.
              </span>
            </li>
            <li>
              <span>
                <strong>CHAPTER 07</strong> — Distribution. Cross-posting,
                newsletter, podcast, RSS, and the analytics you actually need
                (not the ones every platform wants you to stare at).
              </span>
            </li>
            <li>
              <span>
                <strong>APPENDIX</strong> — The full Poindexter source,
                AGPL-3.0. You fork it. You own what you build with it.
              </span>
            </li>
          </ul>
        </section>

        <section
          className="sf-reveal sf-reveal--4"
          style={{ marginTop: '4rem', maxWidth: '760px' }}
        >
          <div className="sf-pricing">
            <div>
              <div className="sf-pricing__label">
                // One-time · Lifetime updates
              </div>
              <div className="sf-pricing__amount">
                ${GUIDE_PRICE_USD}
                <span className="sf-pricing__cents">.00</span>
              </div>
              <div className="sf-pricing__tagline">
                Instant PDF + EPUB + repository access. Cancel-proof (you own
                it).
              </div>
            </div>
            <LemonSqueezyOverlay productUrl={LS_GUIDE_URL} variant="primary">
              ▶ Get the guide
            </LemonSqueezyOverlay>
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
