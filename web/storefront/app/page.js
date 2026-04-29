import Link from 'next/link';
import { Eyebrow, Display, Button } from '@glad-labs/brand';
import { LemonSqueezyOverlay } from '@/components/LemonSqueezyOverlay';
import { LS_PRO_URL, PRO_MONTHLY_USD, PRO_TRIAL_DAYS } from '@/lib/site.config';

export default function Landing() {
  return (
    <>
      <section className="sf-page">
        <div className="sf-container">
          <div className="sf-hero sf-rail">
            <div className="sf-reveal sf-reveal--1 sf-hero__meta">
              <span>
                <span className="dot" aria-hidden="true" />
                POINDEXTER · V3.1
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
              <LemonSqueezyOverlay productUrl={LS_PRO_URL} variant="primary">
                ▶ Start {PRO_TRIAL_DAYS}-day free trial
              </LemonSqueezyOverlay>
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
              Pro — ${PRO_MONTHLY_USD}/month or $89/year · cancel anytime · free
              OSS engine doesn&apos;t require a subscription.
            </p>
          </div>

          <div className="sf-cards">
            <article className="sf-card">
              <div className="sf-card__num">01 · PLAYBOOK</div>
              <h3 className="sf-card__title">The Playbook</h3>
              <p className="sf-card__body">
                A 120-page opinionated walkthrough: how to stand up an
                autonomous publishing agent end-to-end on your own hardware.
                Architecture, prompt engineering, quality gates, failure modes —
                the actual decisions, not the marketing ones.
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
              <div className="sf-card__num">03 · SOURCE</div>
              <h3 className="sf-card__title">The Source</h3>
              <p className="sf-card__body">
                Full access to the Poindexter repository — pipelines, plugins,
                MCP servers, observability, CI. Apache 2.0. Fork it, run it,
                modify it, ship a competing SaaS if you want — the moat is
                ongoing tuning, not the code.
              </p>
            </article>
          </div>
        </div>
      </section>
    </>
  );
}
