import Link from 'next/link';
import { Eyebrow, Display, Button } from '@glad-labs/brand';
import { ProCTA } from '@/components/ProCTA';
import {
  PRO_MONTHLY_USD,
  PRO_ANNUAL_USD,
  POINDEXTER_VERSION,
} from '@/lib/site.config';

export default function Landing() {
  return (
    <>
      <section className="sf-page">
        <div className="sf-container">
          <div className="sf-hero sf-rail">
            <div className="sf-reveal sf-reveal--1 sf-hero__meta">
              <span>
                <span className="dot" aria-hidden="true" />
                POINDEXTER · V{POINDEXTER_VERSION}
              </span>
              <span>LICENSED APACHE-2.0</span>
              <span>SHIPS WITH HARDWARE SPEC</span>
            </div>

            <div className="sf-reveal sf-reveal--2">
              <Eyebrow>GLAD LABS · POINDEXTER</Eyebrow>
              <Display xl>
                Ship an <Display.Accent>AI writer.</Display.Accent>
                <br />
                Own the stack.
              </Display>
            </div>

            <p
              className="sf-reveal sf-reveal--3 gl-body gl-body--lg"
              style={{ maxWidth: '640px' }}
            >
              One post a day at 80+ quality. Every channel. Fully autonomous.
              Runs on your hardware with Ollama — no paid APIs, no subscription,
              no vendor deciding what you&apos;re allowed to write about.
            </p>

            <div className="sf-reveal sf-reveal--4 sf-hero__ctas">
              <ProCTA variant="primary" />
              <Button as={Link} href="/guide" variant="secondary">
                What&apos;s in Pro
              </Button>
            </div>
            <p
              className="sf-reveal sf-reveal--4"
              style={{
                marginTop: '1rem',
                fontSize: '0.85rem',
                opacity: 0.7,
              }}
            >
              Poindexter Pro — ${PRO_MONTHLY_USD}/mo or ${PRO_ANNUAL_USD}/yr ·
              Founding Member rate, locked for life. The free OSS engine never
              needs a subscription.
            </p>
          </div>

          <div className="sf-cards">
            <article className="sf-card">
              <div className="sf-card__num">01 · TUNING</div>
              <h3 className="sf-card__title">Production Tuning</h3>
              <p className="sf-card__body">
                The prompt packs, QA configuration, and tuned settings that run
                Matt&apos;s live content business — exported from the system as
                it&apos;s tuned, not a frozen snapshot. Import it and skip the
                months of trial and error. This is the product.
              </p>
            </article>

            <article className="sf-card">
              <div className="sf-card__num">02 · HARDWARE</div>
              <h3 className="sf-card__title">The Hardware Spec</h3>
              <p className="sf-card__body">
                Parts list, rack layout, thermals, electrical, and the precise
                model-per-GPU allocation that makes a single-node agent viable
                in 2026. Tested on a 9950X3D + 5090 workstation and reproducible
                on smaller rigs.
              </p>
            </article>

            <article className="sf-card">
              <div className="sf-card__num">03 · COMPANION</div>
              <h3 className="sf-card__title">Book &amp; Community</h3>
              <p className="sf-card__body">
                A long-form operator book on the architecture and the decisions
                behind it, plus the VIP Discord where live tuning knowledge
                accrues. Both ride alongside the engine — which stays free and
                Apache-2.0.
              </p>
            </article>
          </div>
        </div>
      </section>
    </>
  );
}
