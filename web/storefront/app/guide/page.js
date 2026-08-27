import Link from 'next/link';
import { Eyebrow, Display, Button } from '@glad-labs/brand';
import { ProCTA } from '@/components/ProCTA';
import { PRO_MONTHLY_USD, PRO_ANNUAL_USD } from '@/lib/site.config';

export const metadata = {
  title: 'Poindexter Pro',
  description:
    'Poindexter Pro — the operator console, the live-tuned production config seed, the operator book, and weekly rebuilds from the running system. $19/month or $180/year, Founding Member rate.',
};

export default function GuidePage() {
  return (
    <section className="sf-page">
      <div className="sf-container">
        <div className="sf-rail" style={{ maxWidth: '760px' }}>
          <div className="sf-reveal sf-reveal--1 sf-hero__meta">
            <span>
              <span className="dot" aria-hidden="true" /> PRODUCT · PRO
            </span>
            <span>CONSOLE · SEED · BOOK · DISCORD</span>
            <span>FOUNDING MEMBER RATE</span>
          </div>

          <div
            className="sf-reveal sf-reveal--2"
            style={{ marginTop: '2.5rem' }}
          >
            <Eyebrow>GLAD LABS · POINDEXTER PRO</Eyebrow>
            <Display>
              Skip the tuning. <Display.Accent>Ship faster.</Display.Accent>
            </Display>
          </div>

          <p
            className="sf-reveal sf-reveal--3 gl-body gl-body--lg"
            style={{ maxWidth: '640px', marginTop: '1.5rem' }}
          >
            The free Poindexter engine runs the full pipeline end-to-end. Pro is
            the operator layer on top: the cockpit console, the exact
            configuration running Matt&apos;s production content business, and
            the operator book — rebuilt from the live system every week, so your
            copy never goes stale.
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
            // WHAT&apos;S IN PRO
          </div>

          <ul className="sf-checklist">
            <li>
              <span>
                <strong>THE OPERATOR CONSOLE</strong> — the cockpit UI. System
                pulse, the approval queue, pipeline stages, QA rails, GPU and
                cost telemetry — one screen for the whole machine. Pro-only
                surface; installs with a copy and a restart.
              </span>
            </li>
            <li>
              <span>
                <strong>THE LIVE-TUNED CONFIG SEED</strong> — 950+ production
                values from the running business: quality thresholds, QA-rail
                strictness, cadence, routing, cost controls. One command —
                <code> poindexter pro apply</code> — adopts them safely, never
                overwriting your own tuning.
              </span>
            </li>
            <li>
              <span>
                <strong>THE POINDEXTER BOOK</strong> — the full operator book
                covering architecture, hardware, models, prompts, quality gates,
                operations, distribution, and the DB-driven config plane — kept
                reconciled against the live code.
              </span>
            </li>
            <li>
              <span>
                <strong>REBUILT WEEKLY, AUTOMATICALLY</strong> — an automated
                session exports the live system&apos;s current tuning to your
                repo every week (the full prompt pack and Grafana boards ride
                along, mirrored from the live tree). The CHANGELOG is the
                receipt; <code>git pull</code> is the whole upgrade. Cancel
                anytime, keep everything you&apos;ve downloaded.
              </span>
            </li>
            <li>
              <span>
                <strong>FOUNDING DISCORD</strong> — the operators&apos; room.
                Share tunings, niche-specific tweaks, and what&apos;s working —
                the community is where the real tuning knowledge lives.
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
                // Monthly · cancel anytime
              </div>
              <div className="sf-pricing__amount">
                ${PRO_MONTHLY_USD}
                <span className="sf-pricing__cents">/mo</span>
              </div>
              <div className="sf-pricing__tagline">
                Founding Member rate — locked for life as the standard rate
                rises.
              </div>
            </div>
            <ProCTA variant="primary" />
          </div>

          <div
            className="sf-pricing"
            style={{
              marginTop: '1.5rem',
              borderTop: '1px dashed var(--gl-border)',
              paddingTop: '1.5rem',
            }}
          >
            <div>
              <div className="sf-pricing__label">// Annual · save ~21%</div>
              <div className="sf-pricing__amount">
                ${PRO_ANNUAL_USD}
                <span className="sf-pricing__cents">/yr</span>
              </div>
              <div className="sf-pricing__tagline">
                Best value. Equivalent to ${(PRO_ANNUAL_USD / 12).toFixed(2)}
                /month.
              </div>
            </div>
            <ProCTA variant="secondary" />
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
