import Link from 'next/link';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';

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

const BONUS = [
  {
    name: 'Persistent Memory System',
    desc: 'Full guide to Claude Code\'s memory architecture — user profiles, feedback loops, project state, session handoffs. The secret weapon.',
  },
  {
    name: 'How I Use Claude Code',
    desc: 'Matt\'s actual workflow across 3,160 commits — autonomous overnight sessions, phone-based approval, cron jobs, Telegram integration.',
  },
];

const CHECKOUT_URL =
  'https://gladlabs.lemonsqueezy.com/checkout/buy/ece7930f-f35e-44dc-93d2-6f56709b5f52';

export default function ClaudeTemplatesPage() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950">
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-40 -right-40 w-96 h-96 bg-violet-500/10 rounded-full blur-3xl animate-pulse" />
        <div className="absolute top-1/2 -left-40 w-80 h-80 bg-cyan-500/10 rounded-full blur-3xl animate-pulse delay-1000" />
      </div>

      {/* Hero */}
      <section className="relative pt-24 pb-16 px-4 sm:px-6 lg:px-8">
        <div className="max-w-4xl mx-auto text-center">
          <p className="text-sm font-mono text-violet-400 mb-4 tracking-widest uppercase">
            Claude Code Template Pack
          </p>
          <h1 className="text-4xl md:text-6xl font-bold text-white mb-6 leading-tight">
            3,160 commits with Claude Code.
            <br />
            <span className="text-transparent bg-clip-text bg-gradient-to-r from-violet-400 to-cyan-400">
              This is what I learned.
            </span>
          </h1>
          <p className="text-lg text-slate-300 max-w-2xl mx-auto mb-8">
            Production-tested CLAUDE.md templates that turn Claude Code from a
            chatbot into a disciplined engineering partner. Five roles, one
            memory system, zero guesswork.
          </p>
          <a
            href={CHECKOUT_URL}
            className="inline-block px-8 py-4 bg-gradient-to-r from-violet-600 to-cyan-600 text-white font-bold rounded-xl text-lg hover:from-violet-500 hover:to-cyan-500 transition-all shadow-lg shadow-violet-500/25"
          >
            Get the Template Pack — $29
          </a>
          <p className="text-sm text-slate-500 mt-3">
            One-time purchase. Instant download. No subscription.
          </p>
        </div>
      </section>

      {/* The Problem */}
      <section className="relative py-16 px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl mx-auto">
          <h2 className="text-2xl font-bold text-white mb-6 text-center">
            Without a CLAUDE.md, every session starts from zero
          </h2>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-5">
              <h3 className="text-red-400 font-semibold mb-2">Without templates</h3>
              <ul className="text-sm text-slate-400 space-y-2">
                <li>Claude forgets your conventions between sessions</li>
                <li>Scope creep — it builds things you didn&apos;t ask for</li>
                <li>Wrong abstractions, wrong patterns, wrong tone</li>
                <li>You re-explain the same rules every conversation</li>
                <li>No memory of past decisions or mistakes</li>
              </ul>
            </div>
            <div className="bg-green-500/10 border border-green-500/30 rounded-xl p-5">
              <h3 className="text-green-400 font-semibold mb-2">With templates</h3>
              <ul className="text-sm text-slate-400 space-y-2">
                <li>Session #100 is as productive as session #1</li>
                <li>Milestones enforced — stays in lane automatically</li>
                <li>Your code style, your rules, every time</li>
                <li>Persistent memory across sessions</li>
                <li>Runs autonomously overnight with cron jobs</li>
              </ul>
            </div>
          </div>
        </div>
      </section>

      {/* Templates */}
      <section className="relative py-16 px-4 sm:px-6 lg:px-8">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-2xl font-bold text-white mb-8 text-center">
            5 Role-Specific Templates
          </h2>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {TEMPLATES.map((t) => (
              <div
                key={t.name}
                className="bg-slate-800/50 border border-slate-700/50 rounded-xl p-5 hover:border-violet-500/50 transition-colors"
              >
                <div className="text-3xl mb-3">{t.icon}</div>
                <h3 className="text-white font-semibold mb-2">{t.name}</h3>
                <p className="text-sm text-slate-400">{t.desc}</p>
              </div>
            ))}
            <div className="bg-gradient-to-br from-violet-500/10 to-cyan-500/10 border border-violet-500/30 rounded-xl p-5">
              <div className="text-3xl mb-3">🎁</div>
              <h3 className="text-white font-semibold mb-2">Bonus: Memory System</h3>
              <p className="text-sm text-slate-400">
                The full persistent memory architecture — user profiles, feedback
                loops, project state, session handoffs. This alone is worth the price.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Credibility */}
      <section className="relative py-16 px-4 sm:px-6 lg:px-8 bg-slate-800/30">
        <div className="max-w-3xl mx-auto text-center">
          <h2 className="text-2xl font-bold text-white mb-6">
            Built from a real production system
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-8">
            {[
              { value: '3,160', label: 'commits' },
              { value: '5,097', label: 'tests passing' },
              { value: '256', label: 'DB-backed settings' },
              { value: '40+', label: 'published posts' },
            ].map((s) => (
              <div key={s.label}>
                <div className="text-2xl font-bold text-cyan-400">{s.value}</div>
                <div className="text-xs text-slate-500 uppercase tracking-wider">{s.label}</div>
              </div>
            ))}
          </div>
          <p className="text-slate-300 max-w-xl mx-auto">
            These templates are extracted from{' '}
            <a
              href="https://github.com/Glad-Labs/poindexter"
              className="text-cyan-400 hover:text-cyan-300 underline"
            >
              Poindexter
            </a>
            , an open-source AI content pipeline that runs 24/7 on a single
            machine. The CLAUDE.md that powers it has been refined across 6
            months of daily use with Claude Code as the sole engineering partner.
          </p>
        </div>
      </section>

      {/* CTA */}
      <section className="relative py-20 px-4 sm:px-6 lg:px-8">
        <div className="max-w-2xl mx-auto text-center">
          <h2 className="text-3xl font-bold text-white mb-4">
            Stop re-explaining yourself to Claude
          </h2>
          <p className="text-slate-300 mb-8">
            One file. Every session. Your rules, your style, your workflow —
            permanently encoded.
          </p>
          <a
            href={CHECKOUT_URL}
            className="inline-block px-8 py-4 bg-gradient-to-r from-violet-600 to-cyan-600 text-white font-bold rounded-xl text-lg hover:from-violet-500 hover:to-cyan-500 transition-all shadow-lg shadow-violet-500/25"
          >
            Get the Template Pack — $29
          </a>
          <p className="text-sm text-slate-500 mt-4">
            Built by{' '}
            <Link href="/about" className="text-slate-400 hover:text-cyan-400 underline">
              Matt Gladding
            </Link>{' '}
            at Glad Labs. Questions? matt@gladlabs.io
          </p>
        </div>
      </section>
    </div>
  );
}
