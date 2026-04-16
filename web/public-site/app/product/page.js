import Link from 'next/link';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';

export const metadata = {
  title: 'Glad Labs — Your Personal AI Content Factory',
  description:
    'Turn your PC into an autonomous content pipeline. One engine, unlimited destinations. Open-source AI-powered headless CMS that researches, writes, reviews, and publishes — while you sleep.',
  alternates: {
    canonical: `${SITE_URL}/product`,
  },
  keywords: [
    'AI content automation',
    'headless CMS',
    'AI blog generator',
    'autonomous publishing',
    'self-hosted AI',
    'local AI inference',
    'Ollama',
    'content pipeline',
    'open source CMS',
    'AI writing tool',
  ],
  openGraph: {
    title: `${SITE_NAME} — Your Personal AI Content Factory`,
    description:
      'Turn your PC into an autonomous content pipeline. Open-source, local-first, no cloud lock-in.',
    type: 'website',
    url: `${SITE_URL}/product`,
    images: [
      {
        url: '/og-image.jpg',
        width: 1200,
        height: 630,
        alt: 'Glad Labs — AI Content Factory',
      },
    ],
  },
};

const PIPELINE_STEPS = [
  {
    step: '01',
    title: 'Discover',
    description:
      'AI scans HackerNews, Dev.to, and your niche to find trending topics worth writing about. Deduplicates against your existing content.',
    icon: '/',
  },
  {
    step: '02',
    title: 'Research',
    description:
      'Deep web research gathers context, statistics, and sources. Everything is verified — no fabricated citations.',
    icon: '/',
  },
  {
    step: '03',
    title: 'Write',
    description:
      'Local LLM drafts long-form content with real code examples, specific tools, and actionable advice. Not generic AI slop.',
    icon: '/',
  },
  {
    step: '04',
    title: 'Review',
    description:
      'Multi-model QA scores every draft on 7 dimensions. Programmatic validator catches hallucinations, fake quotes, and impossible claims.',
    icon: '/',
  },
  {
    step: '05',
    title: 'Publish',
    description:
      'Posts that pass quality gates publish with SEO metadata, AI-generated featured images, and podcast episodes. Static JSON pushed to any CDN.',
    icon: '/',
  },
];

const FEATURES = [
  {
    title: 'Runs on Your Hardware',
    description:
      'Ollama for local inference. Your GPU, your data, your rules. No API costs for generation.',
    accent: 'cyan',
  },
  {
    title: 'Anti-Hallucination Engine',
    description:
      'Three layers: prompt engineering, LLM cross-review, and deterministic pattern matching. Fake people, stats, and quotes get caught.',
    accent: 'blue',
  },
  {
    title: 'Push-Only Architecture',
    description:
      'Static JSON, RSS, and JSON Feed pushed to any S3-compatible storage. Your frontend reads files — no API server needed.',
    accent: 'violet',
  },
  {
    title: 'Multi-Model QA',
    description:
      "Different LLMs review each other's work. Writer and critic never share a model. Adversarial quality by design.",
    accent: 'cyan',
  },
  {
    title: 'One Engine, Unlimited Sites',
    description:
      'One daemon manages N sites. Each site is a config row + storage bucket. Add a new site in minutes, not days.',
    accent: 'blue',
  },
  {
    title: 'DB-as-Config',
    description:
      'Every setting, prompt, and threshold lives in PostgreSQL. Change behavior with a SQL UPDATE — no deploys, no restarts.',
    accent: 'violet',
  },
];

const STATS = [
  { value: '$16', label: '/mo operating cost' },
  { value: '5,000+', label: 'tests passing' },
  { value: '6', label: 'independent QA reviewers' },
  { value: '50%', label: 'of drafts rejected by QA' },
];

export default function ProductPage() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950">
      {/* Ambient Background */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-40 -right-40 w-96 h-96 bg-cyan-500/10 rounded-full blur-3xl animate-pulse" />
        <div className="absolute top-1/2 -left-40 w-80 h-80 bg-blue-500/10 rounded-full blur-3xl animate-pulse delay-1000" />
        <div className="absolute -bottom-40 right-1/3 w-72 h-72 bg-violet-500/10 rounded-full blur-3xl animate-pulse delay-500" />
      </div>

      {/* ============================================================ */}
      {/* HERO */}
      {/* ============================================================ */}
      <section className="relative pt-24 pb-20 px-4 sm:px-6 lg:px-8">
        <div className="max-w-4xl mx-auto text-center">
          <p className="text-sm font-mono text-cyan-500 mb-6 tracking-widest uppercase">
            The Sovereign Media Stack
          </p>

          <h1 className="text-4xl md:text-6xl lg:text-7xl font-bold text-white mb-6 leading-tight font-sora">
            AI Operations,{' '}
            <span className="bg-gradient-to-r from-cyan-400 via-blue-400 to-violet-400 bg-clip-text text-transparent">
              not AI chat.
            </span>
          </h1>

          <p className="text-xl md:text-2xl text-slate-300 mb-10 max-w-2xl mx-auto leading-relaxed">
            A plug-and-play content engine. Researches, writes, reviews, and
            publishes — autonomously on your hardware. $0 API fees. 100% data
            privacy.
          </p>

          <div className="flex flex-col sm:flex-row gap-4 justify-center mb-8">
            <a
              href="https://github.com/Glad-Labs/glad-labs-codebase"
              target="_blank"
              rel="noopener noreferrer"
              className="px-8 py-4 bg-gradient-to-r from-cyan-500 to-blue-600 text-white font-semibold rounded-xl hover:shadow-lg hover:shadow-cyan-500/50 transition-all duration-300 text-lg"
            >
              Get Started Free
            </a>
            <a
              href="#how-it-works"
              className="px-8 py-4 border border-cyan-500/50 text-cyan-400 font-semibold rounded-xl hover:bg-cyan-500/10 transition-all duration-300 text-lg"
            >
              See How It Works
            </a>
          </div>

          <p className="text-sm text-slate-500">
            Free and open-source under AGPL. Premium prompts and guides
            available separately.
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/* STATS STRIP */}
      {/* ============================================================ */}
      <section className="relative py-12 px-4">
        <div className="max-w-4xl mx-auto grid grid-cols-2 md:grid-cols-4 gap-4">
          {STATS.map((stat) => (
            <div
              key={stat.label}
              className="bg-slate-800/40 rounded-xl p-5 border border-slate-700/50 text-center"
            >
              <p className="text-2xl md:text-3xl font-bold bg-gradient-to-r from-cyan-400 to-blue-400 bg-clip-text text-transparent">
                {stat.value}
              </p>
              <p className="text-xs text-slate-400 mt-1 font-mono uppercase tracking-wide">
                {stat.label}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* ============================================================ */}
      {/* HOW IT WORKS — PIPELINE */}
      {/* ============================================================ */}
      <section
        id="how-it-works"
        className="relative py-20 px-4 sm:px-6 lg:px-8"
      >
        <div className="max-w-4xl mx-auto">
          <p className="text-sm font-mono text-cyan-500 mb-4 tracking-widest uppercase text-center">
            The Pipeline
          </p>
          <h2 className="text-3xl md:text-4xl font-bold text-white mb-4 text-center font-sora">
            From idea to published post.{' '}
            <span className="bg-gradient-to-r from-cyan-400 to-blue-400 bg-clip-text text-transparent">
              Automatically.
            </span>
          </h2>
          <p className="text-lg text-slate-400 mb-16 text-center max-w-2xl mx-auto">
            Five stages, three quality gates, zero manual steps. Every post goes
            through the same rigorous pipeline.
          </p>

          <div className="space-y-6">
            {PIPELINE_STEPS.map((step) => (
              <div
                key={step.step}
                className="flex gap-6 items-start bg-slate-800/30 rounded-xl p-6 border border-slate-700/30 hover:border-cyan-500/30 transition-colors duration-300"
              >
                <div className="flex-shrink-0 w-12 h-12 rounded-lg bg-gradient-to-br from-cyan-500/20 to-blue-500/20 border border-cyan-500/30 flex items-center justify-center">
                  <span className="text-sm font-mono font-bold text-cyan-400">
                    {step.step}
                  </span>
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-white mb-1">
                    {step.title}
                  </h3>
                  <p className="text-slate-400 leading-relaxed">
                    {step.description}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* FEATURES GRID */}
      {/* ============================================================ */}
      <section className="relative py-20 px-4 sm:px-6 lg:px-8">
        <div className="max-w-5xl mx-auto">
          <p className="text-sm font-mono text-cyan-500 mb-4 tracking-widest uppercase text-center">
            Built Different
          </p>
          <h2 className="text-3xl md:text-4xl font-bold text-white mb-16 text-center font-sora">
            Not another GPT wrapper.
          </h2>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {FEATURES.map((feature) => (
              <div
                key={feature.title}
                className="bg-slate-800/40 rounded-xl p-6 border border-slate-700/50 hover:border-cyan-500/30 transition-colors duration-300"
              >
                <h3 className="text-lg font-semibold text-white mb-2">
                  {feature.title}
                </h3>
                <p className="text-slate-400 text-sm leading-relaxed">
                  {feature.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* WHY SOVEREIGNTY */}
      {/* ============================================================ */}
      <section className="relative py-20 px-4 sm:px-6 lg:px-8">
        <div className="max-w-4xl mx-auto">
          <p className="text-sm font-mono text-cyan-500 mb-4 tracking-widest uppercase text-center">
            Why This Exists
          </p>
          <h2 className="text-3xl md:text-4xl font-bold text-white mb-12 text-center font-sora">
            You own the{' '}
            <span className="bg-gradient-to-r from-cyan-400 to-blue-400 bg-clip-text text-transparent">
              whole stack.
            </span>
          </h2>
          <div className="grid md:grid-cols-2 gap-0 rounded-xl overflow-hidden border border-slate-700/50">
            <div className="bg-slate-800/60 px-6 py-4 border-b border-slate-700/50 font-mono text-sm text-cyan-400">You get...</div>
            <div className="bg-slate-800/60 px-6 py-4 border-b border-slate-700/50 font-mono text-sm text-slate-400">Which means...</div>

            <div className="px-6 py-4 border-b border-slate-700/30 text-slate-200">Local Infrastructure</div>
            <div className="px-6 py-4 border-b border-slate-700/30 text-slate-400">$0 API fees and 100% data privacy</div>

            <div className="px-6 py-4 border-b border-slate-700/30 text-slate-200">Headless Output</div>
            <div className="px-6 py-4 border-b border-slate-700/30 text-slate-400">Publish to any frontend — your site, your brand, your rules</div>

            <div className="px-6 py-4 border-b border-slate-700/30 text-slate-200">Automated Scale</div>
            <div className="px-6 py-4 border-b border-slate-700/30 text-slate-400">Content generation that outpaces human capacity, 24/7</div>

            <div className="px-6 py-4 border-b border-slate-700/30 text-slate-200">Operational Dashboards</div>
            <div className="px-6 py-4 border-b border-slate-700/30 text-slate-400">Monitor from your phone — Grafana, Telegram alerts, real metrics</div>

            <div className="px-6 py-4 text-slate-200">Integrated Stack</div>
            <div className="px-6 py-4 text-slate-400">Pipeline, QA, dashboards, and monitoring wired up and ready to run</div>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* QUALITY NOT SPAM */}
      {/* ============================================================ */}
      <section className="relative py-20 px-4 sm:px-6 lg:px-8 bg-slate-900/50">
        <div className="max-w-4xl mx-auto">
          <p className="text-sm font-mono text-cyan-500 mb-4 tracking-widest uppercase text-center">
            Quality, Not Spam
          </p>
          <h2 className="text-3xl md:text-4xl font-bold text-white mb-6 text-center font-sora">
            AI content that{' '}
            <span className="bg-gradient-to-r from-cyan-400 to-blue-400 bg-clip-text text-transparent">
              earns its ranking.
            </span>
          </h2>
          <p className="text-slate-300 text-center mb-12 max-w-2xl mx-auto leading-relaxed">
            Google&apos;s spam policy targets AI content generated &quot;without
            adding value for users.&quot; Poindexter is built around the
            opposite principle: generate more candidates, filter aggressively,
            publish only what passes.
          </p>

          <div className="grid md:grid-cols-2 gap-6">
            <div className="bg-slate-800/40 rounded-xl p-6 border border-slate-700/50">
              <h3 className="text-lg font-semibold text-white mb-3">6 Independent QA Reviewers</h3>
              <p className="text-slate-400 text-sm leading-relaxed">
                Programmatic validator, LLM critic, topic delivery gate,
                internal consistency gate, vision gate, and preview screenshot
                check. Different models with different training data catch
                different blind spots.
              </p>
            </div>
            <div className="bg-slate-800/40 rounded-xl p-6 border border-slate-700/50">
              <h3 className="text-lg font-semibold text-white mb-3">Anti-Hallucination Layer</h3>
              <p className="text-slate-400 text-sm leading-relaxed">
                Deterministic pattern matching catches fabricated people,
                statistics, quotes, and impossible claims. No LLM can override
                a regex match. Custom rules via the fact_overrides database.
              </p>
            </div>
            <div className="bg-slate-800/40 rounded-xl p-6 border border-slate-700/50">
              <h3 className="text-lg font-semibold text-white mb-3">Research-Backed Content</h3>
              <p className="text-slate-400 text-sm leading-relaxed">
                Every article starts with web research and source verification.
                The writer receives real data points, real URLs, and verified
                facts — not just a bare topic string.
              </p>
            </div>
            <div className="bg-slate-800/40 rounded-xl p-6 border border-slate-700/50">
              <h3 className="text-lg font-semibold text-white mb-3">50% Rejection Rate by Design</h3>
              <p className="text-slate-400 text-sm leading-relaxed">
                Half of generated drafts don&apos;t pass QA. That&apos;s the
                point. Speed comes from generating more candidates and filtering
                aggressively — not from lowering the bar.
              </p>
            </div>
          </div>

          <p className="text-slate-500 text-center mt-8 text-sm">
            The ultimate test: would you be embarrassed if someone realized the
            article was AI-generated? If no — if the content is genuinely useful
            regardless of how it was made — it&apos;s ready to publish.
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/* PRICING / CTA */}
      {/* ============================================================ */}
      <section className="relative py-20 px-4 sm:px-6 lg:px-8">
        <div className="max-w-4xl mx-auto">
          <p className="text-sm font-mono text-cyan-500 mb-4 tracking-widest uppercase text-center">
            Get Started
          </p>
          <h2 className="text-3xl md:text-4xl font-bold text-white mb-12 text-center font-sora">
            Free to run. Pay for the{' '}
            <span className="bg-gradient-to-r from-cyan-400 to-blue-400 bg-clip-text text-transparent">
              fast track.
            </span>
          </h2>

          <div className="grid md:grid-cols-3 gap-6">
            {/* Free Tier */}
            <div className="bg-slate-800/40 rounded-xl p-8 border border-slate-700/50">
              <h3 className="text-sm font-mono text-slate-400 uppercase tracking-widest mb-2">
                Open Source
              </h3>
              <p className="text-3xl font-bold text-white mb-1">Free</p>
              <p className="text-slate-500 text-sm mb-6">forever</p>
              <ul className="space-y-3 text-sm text-slate-300 mb-8">
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  Full content pipeline engine
                </li>
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  Local AI inference (Ollama)
                </li>
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  Basic default prompts
                </li>
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  Static JSON export to any frontend
                </li>
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  5 Grafana monitoring dashboards
                </li>
                <li className="flex gap-2">
                  <span className="text-slate-600 flex-shrink-0">-</span>
                  <span className="text-slate-500">Default config — functional but untuned</span>
                </li>
              </ul>
              <a
                href="https://github.com/Glad-Labs/poindexter"
                target="_blank"
                rel="noopener noreferrer"
                className="block w-full text-center px-6 py-3 border border-slate-600 text-slate-300 rounded-xl hover:bg-slate-700/50 transition-colors duration-300 font-medium"
              >
                Clone the Repo
              </a>
            </div>

            {/* Seed Package */}
            <div className="bg-slate-800/40 rounded-xl p-8 border border-cyan-500/50 relative">
              <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 bg-gradient-to-r from-cyan-500 to-blue-600 rounded-full text-xs font-mono text-white">
                POPULAR
              </div>
              <h3 className="text-sm font-mono text-cyan-400 uppercase tracking-widest mb-2">
                Seed Package
              </h3>
              <p className="text-3xl font-bold text-white mb-1">$29</p>
              <p className="text-slate-500 text-sm mb-6">one-time</p>
              <ul className="space-y-3 text-sm text-slate-300 mb-8">
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  Everything in Free
                </li>
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  Production-tuned config (185+ settings)
                </li>
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  Anti-hallucination rules database
                </li>
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  Curated writing style samples
                </li>
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  2 premium Grafana dashboards
                </li>
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  Quick Start Guide (first post in 30 min)
                </li>
              </ul>
              <a
                href="https://gladlabs.lemonsqueezy.com/checkout/buy/ece7930f-f35e-44dc-93d2-6f56709b5f52"
                target="_blank"
                rel="noopener noreferrer"
                className="block w-full text-center px-6 py-3 bg-gradient-to-r from-cyan-500 to-blue-600 text-white rounded-xl hover:shadow-lg hover:shadow-cyan-500/30 transition-all duration-300 font-medium"
              >
                Buy Guide — $29
              </a>
            </div>

            {/* Premium Subscription */}
            <div className="bg-slate-800/40 rounded-xl p-8 border border-slate-700/50">
              <h3 className="text-sm font-mono text-slate-400 uppercase tracking-widest mb-2">
                Premium
              </h3>
              <p className="text-3xl font-bold text-white mb-1">
                $9.99
                <span className="text-lg text-slate-400 font-normal">/mo</span>
              </p>
              <p className="text-slate-500 text-sm mb-6">cancel anytime</p>
              <ul className="space-y-3 text-sm text-slate-300 mb-8">
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  Everything in Seed Package
                </li>
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  Monthly updated seeds from live production
                </li>
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  Private repo with prompt iterations
                </li>
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  New fact-check rules and topic sources
                </li>
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  Premium Discord — direct access to Matt
                </li>
                <li className="flex gap-2">
                  <span className="text-cyan-400 flex-shrink-0">+</span>
                  AI Content Pipeline book (chapters as they ship)
                </li>
              </ul>
              <a
                href="https://gladlabs.lemonsqueezy.com/checkout/buy/a5713f22-3c57-47ae-b1ee-5fee3a0b43b9"
                target="_blank"
                rel="noopener noreferrer"
                className="block w-full text-center px-6 py-3 bg-gradient-to-r from-cyan-500 to-blue-600 text-white rounded-xl hover:shadow-lg hover:shadow-cyan-500/30 transition-all duration-300 font-medium"
              >
                Subscribe — $9.99/mo
              </a>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* FINAL CTA */}
      {/* ============================================================ */}
      <section className="relative py-20 px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl mx-auto text-center">
          <h2 className="text-3xl md:text-4xl font-bold text-white mb-6 font-sora">
            Every PC is a personal AI factory.
          </h2>
          <p className="text-xl text-slate-300 mb-10 leading-relaxed">
            Stop paying per token. Stop trusting cloud APIs with your content.
            Run the whole pipeline on your own hardware and publish to anywhere.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center flex-wrap">
            <a
              href="https://github.com/Glad-Labs/poindexter"
              target="_blank"
              rel="noopener noreferrer"
              className="px-8 py-4 bg-gradient-to-r from-cyan-500 to-blue-600 text-white font-semibold rounded-xl hover:shadow-lg hover:shadow-cyan-500/50 transition-all duration-300"
            >
              View on GitHub
            </a>
            <a
              href="https://discord.gg/GCDBxBVv"
              target="_blank"
              rel="noopener noreferrer"
              className="px-8 py-4 border border-indigo-500/50 text-indigo-300 font-semibold rounded-xl hover:bg-indigo-500/10 transition-all duration-300"
            >
              Join Discord
            </a>
            <Link
              href="/posts"
              className="px-8 py-4 border border-cyan-500/50 text-cyan-400 font-semibold rounded-xl hover:bg-cyan-500/10 transition-all duration-300"
            >
              Read the Blog
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
