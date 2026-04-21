import Link from 'next/link';
import { Button, Card, Display, Eyebrow } from '@glad-labs/brand';
import { SITE_URL } from '@/lib/site.config';

export const metadata = {
  title: 'Claude Code Template Pack — 3,160 Commits of Lessons | Glad Labs',
  description:
    'Production-tested CLAUDE.md templates for solo founders, open-source maintainers, freelancers, AI engineers, and content creators. Built from 3,160 real commits.',
  alternates: { canonical: `${SITE_URL}/claude-templates` },
  openGraph: {
    title: 'Claude Code Template Pack by Glad Labs',
    description: '3,160 commits with Claude Code. This is what I learned.',
    type: 'website',
    url: `${SITE_URL}/claude-templates`,
  },
};

const TEMPLATES = [
  {
    name: 'Solo SaaS Founder',
    desc: 'Milestone gating, revenue awareness, scope creep prevention, session handoffs. Built for shipping alone.',
    icon: '🚀',
  },
  {
    name: 'Open Source Maintainer',
    desc: 'Semantic versioning, contributor workflows, public/private separation, release checklists, changelog enforcement.',
    icon: '📦',
  },
  {
    name: 'Freelancer / Agency',
    desc: 'Scope creep detection, time tracking awareness, multi-client isolation, handoff documentation.',
    icon: '💼',
  },
  {
    name: 'AI/ML Engineer',
    desc: 'Experiment protocols, GPU resource awareness, reproducibility, model registry patterns, VRAM budgeting.',
    icon: '🧠',
  },
  {
    name: 'Content Creator',
    desc: 'Brand voice enforcement, quality checklists, SEO rules, editorial calendar, AI disclosure compliance.',
    icon: '✍️',
  },
];

const CHECKOUT_URL =
  'https://gladlabs.lemonsqueezy.com/checkout/buy/ece7930f-f35e-44dc-93d2-6f56709b5f52';

export default function ClaudeTemplatesPage() {
  return (
    <div className="gl-atmosphere min-h-screen">
      {/* Hero */}
      <section className="relative pt-20 pb-12 md:pt-32 md:pb-16 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-4xl text-center">
          <Eyebrow>GLAD LABS · TEMPLATE PACK</Eyebrow>
          <Display xl>
            3,160 commits with Claude Code.{' '}
            <Display.Accent>This is what I learned.</Display.Accent>
          </Display>
          <p className="gl-body gl-body--lg mt-6 max-w-2xl mx-auto">
            Production-tested CLAUDE.md templates that turn Claude Code from a
            chatbot into a disciplined engineering partner. Five roles, one
            memory system, zero guesswork.
          </p>
          <div className="mt-8 flex justify-center">
            <Button as="a" href={CHECKOUT_URL} variant="primary">
              Get the template pack — $29
            </Button>
          </div>
          <p className="gl-mono gl-mono--upper text-xs mt-4 opacity-60">
            One-time purchase · Instant download · No subscription
          </p>
        </div>
      </section>

      {/* The Problem */}
      <section className="px-4 sm:px-6 lg:px-8 py-12">
        <div className="container mx-auto max-w-4xl">
          <h2 className="gl-h2 text-center mb-8">
            Without a CLAUDE.md, every session starts from zero
          </h2>
          <div className="grid gap-4 md:grid-cols-2">
            <Card accent="amber">
              <Card.Meta>WITHOUT TEMPLATES</Card.Meta>
              <ul className="gl-body gl-body--sm mt-3 space-y-2 list-none">
                <li>Claude forgets your conventions between sessions</li>
                <li>Scope creep — it builds things you didn&apos;t ask for</li>
                <li>Wrong abstractions, wrong patterns, wrong tone</li>
                <li>You re-explain the same rules every conversation</li>
                <li>No memory of past decisions or mistakes</li>
              </ul>
            </Card>
            <Card accent="mint">
              <Card.Meta>WITH TEMPLATES</Card.Meta>
              <ul className="gl-body gl-body--sm mt-3 space-y-2 list-none">
                <li>Session #100 is as productive as session #1</li>
                <li>Milestones enforced — stays in lane automatically</li>
                <li>Your code style, your rules, every time</li>
                <li>Persistent memory across sessions</li>
                <li>Runs autonomously overnight with cron jobs</li>
              </ul>
            </Card>
          </div>
        </div>
      </section>

      {/* Templates */}
      <section className="px-4 sm:px-6 lg:px-8 py-12">
        <div className="container mx-auto max-w-5xl">
          <h2 className="gl-h2 text-center mb-8">
            5 role-specific templates
          </h2>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {TEMPLATES.map((t) => (
              <Card key={t.name}>
                <div className="text-3xl mb-3">{t.icon}</div>
                <Card.Title>{t.name}</Card.Title>
                <Card.Body className="mt-2">{t.desc}</Card.Body>
              </Card>
            ))}
            <Card accent="amber">
              <div className="text-3xl mb-3">🎁</div>
              <Card.Title>Bonus: memory system</Card.Title>
              <Card.Body className="mt-2">
                The full persistent memory architecture — user profiles,
                feedback loops, project state, session handoffs. This alone is
                worth the price.
              </Card.Body>
            </Card>
          </div>
        </div>
      </section>

      {/* Credibility */}
      <section className="px-4 sm:px-6 lg:px-8 py-12">
        <div className="container mx-auto max-w-3xl text-center">
          <h2 className="gl-h2 mb-6">
            Built from a real production system
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            {[
              { value: '3,160', label: 'commits' },
              { value: '5,097', label: 'tests passing' },
              { value: '256', label: 'DB-backed settings' },
              { value: '40+', label: 'published posts' },
            ].map((s) => (
              <Card key={s.label} className="text-center">
                <div className="gl-display text-3xl md:text-4xl">
                  <span className="gl-accent">{s.value}</span>
                </div>
                <Card.Meta className="mt-2">{s.label}</Card.Meta>
              </Card>
            ))}
          </div>
          <p className="gl-body gl-body--lg max-w-xl mx-auto">
            These templates are extracted from{' '}
            <a
              href="https://github.com/Glad-Labs/poindexter"
              className="text-[color:var(--gl-cyan)] hover:underline"
            >
              Poindexter
            </a>
            , an open-source AI content pipeline that runs 24/7 on a single
            machine. The CLAUDE.md that powers it has been refined across 6
            months of daily use with Claude Code as the sole engineering
            partner.
          </p>
        </div>
      </section>

      {/* CTA */}
      <section className="px-4 sm:px-6 lg:px-8 pb-20 pt-12">
        <div className="container mx-auto max-w-2xl text-center">
          <h2 className="gl-h2">Stop re-explaining yourself to Claude</h2>
          <p className="gl-body gl-body--lg mt-4 mb-8">
            One file. Every session. Your rules, your style, your workflow —
            permanently encoded.
          </p>
          <div className="flex justify-center">
            <Button as="a" href={CHECKOUT_URL} variant="primary">
              Get the template pack — $29
            </Button>
          </div>
          <p className="gl-body gl-body--sm mt-6 opacity-70">
            Built by{' '}
            <Link
              href="/about"
              className="text-[color:var(--gl-cyan)] hover:underline"
            >
              Matt Gladding
            </Link>{' '}
            at Glad Labs. Questions? hello@gladlabs.io
          </p>
        </div>
      </section>
    </div>
  );
}
