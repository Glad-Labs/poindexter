import Link from 'next/link';
import { notFound } from 'next/navigation';
import { Eyebrow, Display, Button } from '@glad-labs/brand';
import { LemonSqueezyOverlay } from '@/components/LemonSqueezyOverlay';
import { ProCTA } from '@/components/ProCTA';
import {
  FIELD_GUIDE_ENABLED,
  FIELD_GUIDE_SHIPPED,
  FIELD_GUIDE_SAMPLE_URL,
  LS_FIELD_GUIDE_WAITLIST_URL,
  PRO_MONTHLY_USD,
  PRO_TRIAL_DAYS,
} from '@/lib/site.config';

/*
  /field-guide — the field guide, a Poindexter Pro perk (decided 2026-09-27).
  It is never sold separately, so no price of its own appears here.

  Gated: 404s unless FIELD_GUIDE_ENABLED (see site.config). Two states:
  - Before FIELD_GUIDE_SHIPPED: "coming to Pro", plus a free waitlist for
    everyone else. No trial button: a trial would expire before the guide
    exists.
  - After: "in Pro now", with the Pro trial button. The waitlist goes away.
  Plan and the operator's turn-on checklist: marketing/field-guide/README.md.
  Chapter claims below come from marketing/field-guide/outline.md, which cites
  a repo source for every number — keep them in step when either changes.
*/

export const metadata = {
  title: 'The Field Guide',
  description:
    'Green Is Not a Result — a field guide to running a production codebase your AI agents write. The operating rules and guardrails from a ~700,000-line system built by one person directing Claude Code. Included with Poindexter Pro.',
};

const CHAPTERS = [
  {
    title: 'A check that scanned nothing has not passed',
    body: 'Ten of twelve CI lints reported "clean" when run on an empty tree. The two-function fix, and the test that keeps the next lint honest.',
  },
  {
    title: 'Liveness is not correctness',
    body: 'A retention job skipped about 20,000 rows for months while every signal stayed green. How to measure the invariant instead of the heartbeat.',
  },
  {
    title: 'A producer must have a consumer',
    body: 'A QA check ran 37 times in a month while its own counter read zero. How to catch output that nobody reads.',
  },
  {
    title: 'Ratchets, not issue-filers',
    body: 'A scanner filed 91 issues, and every one examined was a false positive, burying 18 real ones. Baselines that block regressions and file nothing.',
  },
  {
    title: "Keep the agent's manual true",
    body: 'One false line in CLAUDE.md, copied into a test, hid a missing meta description for three months. Keeping agent-facing docs mechanically true.',
  },
  {
    title: 'The guardrail kit',
    body: 'The scan floor, the ratchet baseline and the doc-anchor lint, generalised so you can drop them into any repo.',
  },
];

export default function FieldGuidePage() {
  if (!FIELD_GUIDE_ENABLED) notFound();

  return (
    <section className="sf-page">
      <div className="sf-container">
        <div className="sf-rail" style={{ maxWidth: '760px' }}>
          <div className="sf-reveal sf-reveal--1 sf-hero__meta">
            <span>
              <span className="dot" aria-hidden="true" /> FIELD GUIDE · E-BOOK
            </span>
            <span>INCLUDED WITH PRO</span>
            <span>{FIELD_GUIDE_SHIPPED ? 'OUT NOW' : 'IN PROGRESS'}</span>
          </div>

          <div
            className="sf-reveal sf-reveal--2"
            style={{ marginTop: '2.5rem' }}
          >
            <Eyebrow>GLAD LABS · FIELD GUIDE</Eyebrow>
            <Display>
              Green is not <Display.Accent>a result.</Display.Accent>
            </Display>
          </div>

          <p
            className="sf-reveal sf-reveal--3 gl-body gl-body--lg"
            style={{ maxWidth: '640px', marginTop: '1.5rem' }}
          >
            A field guide to running a production codebase your AI agents write.
            One person, evenings only, about 700,000 lines of Python, almost
            none of it typed by hand. These are the operating rules that kept it
            true, each earned by a real, measured failure and each paired with a
            guardrail you can add to your own repo the same evening.
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
            // INSIDE
          </div>

          <ul className="sf-checklist">
            {CHAPTERS.map((ch) => (
              <li key={ch.title}>
                <span>
                  <strong>{ch.title.toUpperCase()}</strong> — {ch.body}
                </span>
              </li>
            ))}
          </ul>

          <p
            className="gl-body"
            style={{ marginTop: '1.5rem', opacity: 0.75, maxWidth: '640px' }}
          >
            For developers and leads running Claude Code, Cursor, Codex or
            anything like them on a codebase that has to keep working. It
            isn&apos;t a prompting guide, and you don&apos;t need to run
            Poindexter or own a GPU.
          </p>

          {FIELD_GUIDE_SAMPLE_URL && (
            <p style={{ marginTop: '1rem' }}>
              <Button
                as="a"
                href={FIELD_GUIDE_SAMPLE_URL}
                target="_blank"
                rel="noopener noreferrer"
                variant="secondary"
              >
                Read a chapter free ↗
              </Button>
            </p>
          )}
        </section>

        <section
          className="sf-reveal sf-reveal--4"
          style={{ marginTop: '4rem', maxWidth: '760px' }}
        >
          <div className="sf-pricing">
            <div>
              <div className="sf-pricing__label">// Included with Pro</div>
              <div className="sf-pricing__amount">
                ${PRO_MONTHLY_USD}
                <span className="sf-pricing__cents">/mo</span>
              </div>
              <div className="sf-pricing__tagline">
                {FIELD_GUIDE_SHIPPED
                  ? `In the Pro repo now, with the console, the config seed and the book. ${PRO_TRIAL_DAYS}-day free trial. Not sold separately.`
                  : 'Pro subscribers get it in the Pro repo the day it ships, at no extra charge. Not sold separately.'}
              </div>
            </div>
            {FIELD_GUIDE_SHIPPED ? (
              <ProCTA variant="primary" />
            ) : (
              <Button as={Link} href="/guide" variant="secondary">
                What&apos;s in Pro
              </Button>
            )}
          </div>

          {!FIELD_GUIDE_SHIPPED && (
            <div
              className="sf-pricing"
              style={{
                marginTop: '1.5rem',
                borderTop: '1px dashed var(--gl-border)',
                paddingTop: '1.5rem',
              }}
            >
              <div>
                <div className="sf-pricing__label">// Waitlist · free</div>
                <div className="sf-pricing__amount">$0</div>
                <div className="sf-pricing__tagline">
                  One email when it ships. Nothing else.
                </div>
              </div>
              <LemonSqueezyOverlay productUrl={LS_FIELD_GUIDE_WAITLIST_URL}>
                ▶ Join the waitlist
              </LemonSqueezyOverlay>
            </div>
          )}
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
