import Link from 'next/link';
import { Eyebrow, Display, Button } from '@glad-labs/brand';
import { LemonSqueezyOverlay } from '@/components/LemonSqueezyOverlay';
import {
  LS_PRO_URL,
  PRO_MONTHLY_USD,
  PRO_ANNUAL_USD,
  PRO_TRIAL_DAYS,
} from '@/lib/site.config';

export const metadata = {
  title: 'Poindexter Pro',
  description:
    'Poindexter Pro — tuned prompts, the full book, premium dashboards, and the VIP Discord. $9/month or $89/year with a 7-day free trial.',
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
            <span>PROMPTS · DASHBOARDS · BOOK · DISCORD</span>
            <span>{PRO_TRIAL_DAYS}-DAY FREE TRIAL</span>
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
            The free Poindexter engine runs the full pipeline end-to-end. Pro
            gives you months of production tuning in a single install — the
            exact prompts, dashboards, and configuration Matt runs in his daily
            content business, updated continuously as the system improves.
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
                <strong>PROMPT LIBRARY</strong> — production-tuned prompts for
                drafting, research, QA, SEO, social, and image generation. The
                anti-fabrication ruleset, the multi-model QA critic, the
                pre-submission self-check. Updated as Matt tunes the live
                system.
              </span>
            </li>
            <li>
              <span>
                <strong>5 PREMIUM GRAFANA DASHBOARDS</strong> — Cost Analytics,
                Content Quality, Infrastructure, Approval Queue, Link Registry.
                Plus the QA Observability dashboard for real-time rejection
                metrics. Know exactly what&apos;s happening in your pipeline.
              </span>
            </li>
            <li>
              <span>
                <strong>THE POINDEXTER BOOK</strong> — 18 chapters plus
                appendices covering architecture, hardware, models, prompts,
                quality gates, operations, distribution, and the DB-driven
                config plane. New chapters ship as the system evolves.
              </span>
            </li>
            <li>
              <span>
                <strong>200+ TUNED APP_SETTINGS</strong> — every knob Matt
                touched to get production output. No guessing at threshold
                values or weight balances; import the known-good configuration
                and run.
              </span>
            </li>
            <li>
              <span>
                <strong>FACT OVERRIDES DATABASE</strong> — curated
                anti-hallucination rules, hardware facts, and brand corrections.
                Keeps the writer honest about things LLMs commonly get wrong.
              </span>
            </li>
            <li>
              <span>
                <strong>VIP DISCORD</strong> — private operator channel. Share
                tunings, niche-specific tweaks, and get direct access to Matt.
                The community is where the real tuning knowledge lives.
              </span>
            </li>
            <li>
              <span>
                <strong>CONTINUOUS UPDATES</strong> — every prompt improvement,
                new dashboard, and tuning win lands in your repo as the system
                evolves. Cancel anytime, keep everything you&apos;ve downloaded.
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
                {PRO_TRIAL_DAYS}-day free trial · no card on most gateways.
              </div>
            </div>
            <LemonSqueezyOverlay productUrl={LS_PRO_URL} variant="primary">
              ▶ Start free trial
            </LemonSqueezyOverlay>
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
              <div className="sf-pricing__label">// Annual · save ~17%</div>
              <div className="sf-pricing__amount">
                ${PRO_ANNUAL_USD}
                <span className="sf-pricing__cents">/yr</span>
              </div>
              <div className="sf-pricing__tagline">
                Best value. Equivalent to ${(PRO_ANNUAL_USD / 12).toFixed(2)}
                /month.
              </div>
            </div>
            <LemonSqueezyOverlay productUrl={LS_PRO_URL} variant="secondary">
              Go annual
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
