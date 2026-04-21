import Link from 'next/link';
import { Button, Card, Display, Eyebrow } from '@glad-labs/brand';
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
  },
  {
    step: '02',
    title: 'Research',
    description:
      'Deep web research gathers context, statistics, and sources. Everything is verified — no fabricated citations.',
  },
  {
    step: '03',
    title: 'Write',
    description:
      'Local LLM drafts long-form content with real code examples, specific tools, and actionable advice. Not generic AI slop.',
  },
  {
    step: '04',
    title: 'Review',
    description:
      'Multi-model QA scores every draft on 7 dimensions. Programmatic validator catches hallucinations, fake quotes, and impossible claims.',
  },
  {
    step: '05',
    title: 'Publish',
    description:
      'Posts that pass quality gates publish with SEO metadata, AI-generated featured images, and podcast episodes. Static JSON pushed to any CDN.',
  },
];

const FEATURES = [
  {
    title: 'Runs on Your Hardware',
    description:
      'Ollama for local inference. Your GPU, your data, your rules. No API costs for generation.',
  },
  {
    title: 'Anti-Hallucination Engine',
    description:
      'Three layers: prompt engineering, LLM cross-review, and deterministic pattern matching. Fake people, stats, and quotes get caught.',
  },
  {
    title: 'Push-Only Architecture',
    description:
      'Static JSON, RSS, and JSON Feed pushed to any S3-compatible storage. Your frontend reads files — no API server needed.',
  },
  {
    title: 'Multi-Model QA',
    description:
      "Different LLMs review each other's work. Writer and critic never share a model. Adversarial quality by design.",
  },
  {
    title: 'One Engine, Unlimited Sites',
    description:
      'One daemon manages N sites. Each site is a config row + storage bucket. Add a new site in minutes, not days.',
  },
  {
    title: 'DB-as-Config',
    description:
      'Every setting, prompt, and threshold lives in PostgreSQL. Change behavior with a SQL UPDATE — no deploys, no restarts.',
  },
];

const STATS = [
  { value: '$0', label: '/mo infra cost' },
  { value: '5,000+', label: 'tests passing' },
  { value: '6', label: 'QA reviewers' },
  { value: '50%', label: 'drafts rejected' },
];

const SOVEREIGNTY_ROWS = [
  ['Local Infrastructure', '$0 API fees and 100% data privacy'],
  ['Headless Output', 'Publish to any frontend — your site, your brand, your rules'],
  ['Automated Scale', 'Content generation that outpaces human capacity, 24/7'],
  ['Operational Dashboards', 'Monitor from your phone — Grafana, Telegram alerts, real metrics'],
  ['Integrated Stack', 'Pipeline, QA, dashboards, and monitoring wired up and ready to run'],
];

const QUALITY_POINTS = [
  {
    title: '6 Independent QA Reviewers',
    description:
      'Programmatic validator, LLM critic, topic delivery gate, internal consistency gate, vision gate, and preview screenshot check. Different models with different training data catch different blind spots.',
  },
  {
    title: 'Anti-Hallucination Layer',
    description:
      'Deterministic pattern matching catches fabricated people, statistics, quotes, and impossible claims. No LLM can override a regex match. Custom rules via the fact_overrides database.',
  },
  {
    title: 'Research-Backed Content',
    description:
      'Every article starts with web research and source verification. The writer receives real data points, real URLs, and verified facts — not just a bare topic string.',
  },
  {
    title: '50% Rejection Rate by Design',
    description:
      "Half of generated drafts don't pass QA. That's the point. Speed comes from generating more candidates and filtering aggressively — not from lowering the bar.",
  },
];

const FREE_INCLUDED = [
  'Full content pipeline engine',
  'Local AI inference (Ollama)',
  'Basic default prompts',
  'Static JSON export to any frontend',
  '7 Grafana monitoring dashboards',
];

const SEED_INCLUDED = [
  'Everything in Free',
  'Production-tuned config (200+ settings)',
  'Anti-hallucination rules database',
  'Curated writing style samples',
  '2 premium Grafana dashboards',
  'Quick Start Guide (first post in 30 min)',
];

const PREMIUM_INCLUDED = [
  'Everything in Seed Package',
  'Monthly updated seeds from live production',
  'Private repo with prompt iterations',
  'New fact-check rules and topic sources',
  'Premium Discord — direct access to Matt',
  'AI Content Pipeline book (chapters as they ship)',
];

function PricingList({ items }) {
  return (
    <ul className="gl-body gl-body--sm mt-5 mb-6 space-y-2 list-none">
      {items.map((item) => (
        <li key={item} className="flex gap-2">
          <span className="gl-mono gl-mono--accent flex-shrink-0">+</span>
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

export default function ProductPage() {
  return (
    <div className="gl-atmosphere min-h-screen">
      {/* HERO */}
      <section className="relative pt-24 pb-16 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-4xl text-center">
          <Eyebrow>GLAD LABS · SOVEREIGN MEDIA STACK</Eyebrow>
          <Display xl>
            AI operations,{' '}
            <Display.Accent>not AI chat.</Display.Accent>
          </Display>
          <p className="gl-body gl-body--lg mt-6 max-w-2xl mx-auto">
            A plug-and-play content engine. Researches, writes, reviews, and
            publishes — autonomously on your hardware. $0 API fees. 100% data
            privacy.
          </p>
          <div className="flex flex-col sm:flex-row gap-3 justify-center mt-8">
            <Button
              as="a"
              href="https://github.com/Glad-Labs/poindexter"
              target="_blank"
              rel="noopener noreferrer"
              variant="primary"
            >
              ▶ Get started free
            </Button>
            <Button as="a" href="#how-it-works" variant="secondary">
              See how it works
            </Button>
          </div>
          <p className="gl-mono gl-mono--upper opacity-50 mt-6" style={{ fontSize: '0.6875rem' }}>
            Free and open-source · AGPL · Premium prompts + guides available
          </p>
        </div>
      </section>

      {/* STATS */}
      <section className="py-12 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-4xl">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {STATS.map((stat) => (
              <Card key={stat.label} className="text-center">
                <p className="gl-display text-3xl md:text-4xl">
                  <span className="gl-accent">{stat.value}</span>
                </p>
                <Card.Meta className="mt-2">{stat.label}</Card.Meta>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section
        id="how-it-works"
        className="py-16 px-4 sm:px-6 lg:px-8"
      >
        <div className="container mx-auto max-w-4xl">
          <div className="text-center mb-12">
            <Eyebrow>GLAD LABS · PIPELINE</Eyebrow>
            <h2 className="gl-h2 mt-1" style={{ fontSize: 'clamp(1.75rem, 3vw, 2.25rem)' }}>
              From idea to published post. <span className="gl-accent">Automatically.</span>
            </h2>
            <p className="gl-body gl-body--lg mt-4 max-w-2xl mx-auto">
              Five stages, three quality gates, zero manual steps. Every post
              goes through the same rigorous pipeline.
            </p>
          </div>

          <div className="space-y-4">
            {PIPELINE_STEPS.map((step) => (
              <Card key={step.step}>
                <div className="flex gap-6 items-start">
                  <div
                    className="flex-shrink-0 w-12 h-12 flex items-center justify-center"
                    style={{
                      background: 'var(--gl-cyan-bg)',
                      border: '1px solid var(--gl-cyan-border)',
                    }}
                  >
                    <span className="gl-mono gl-mono--accent font-bold text-sm">
                      {step.step}
                    </span>
                  </div>
                  <div>
                    <Card.Title>{step.title}</Card.Title>
                    <Card.Body className="mt-2">{step.description}</Card.Body>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* FEATURES */}
      <section className="py-16 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-5xl">
          <div className="text-center mb-12">
            <Eyebrow>GLAD LABS · BUILT DIFFERENT</Eyebrow>
            <h2 className="gl-h2 mt-1" style={{ fontSize: 'clamp(1.75rem, 3vw, 2.25rem)' }}>
              Not another <span className="gl-accent">GPT wrapper.</span>
            </h2>
          </div>
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {FEATURES.map((feature) => (
              <Card key={feature.title}>
                <Card.Title>{feature.title}</Card.Title>
                <Card.Body className="mt-2">{feature.description}</Card.Body>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* SOVEREIGNTY */}
      <section className="py-16 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-4xl">
          <div className="text-center mb-12">
            <Eyebrow>GLAD LABS · WHY THIS EXISTS</Eyebrow>
            <h2 className="gl-h2 mt-1" style={{ fontSize: 'clamp(1.75rem, 3vw, 2.25rem)' }}>
              You own the <span className="gl-accent">whole stack.</span>
            </h2>
          </div>
          <div
            className="grid md:grid-cols-2 gap-0 overflow-hidden"
            style={{ border: '1px solid var(--gl-hairline)' }}
          >
            <div
              className="px-6 py-4 gl-mono gl-mono--upper gl-mono--accent text-xs"
              style={{
                background: 'var(--gl-surface)',
                borderBottom: '1px solid var(--gl-hairline)',
              }}
            >
              You get…
            </div>
            <div
              className="px-6 py-4 gl-mono gl-mono--upper text-xs opacity-70"
              style={{
                background: 'var(--gl-surface)',
                borderBottom: '1px solid var(--gl-hairline)',
              }}
            >
              Which means…
            </div>

            {SOVEREIGNTY_ROWS.map(([label, meaning], idx) => {
              const isLast = idx === SOVEREIGNTY_ROWS.length - 1;
              return (
                <div
                  key={label}
                  className="contents"
                >
                  <div
                    className="px-6 py-4 gl-body gl-body--primary"
                    style={{
                      borderBottom: isLast
                        ? 'none'
                        : '1px solid var(--gl-hairline)',
                    }}
                  >
                    {label}
                  </div>
                  <div
                    className="px-6 py-4 gl-body gl-body--sm"
                    style={{
                      borderBottom: isLast
                        ? 'none'
                        : '1px solid var(--gl-hairline)',
                    }}
                  >
                    {meaning}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* QUALITY NOT SPAM */}
      <section
        className="py-16 px-4 sm:px-6 lg:px-8"
        style={{ background: 'rgba(0, 0, 0, 0.25)' }}
      >
        <div className="container mx-auto max-w-4xl">
          <div className="text-center mb-12">
            <Eyebrow>GLAD LABS · QUALITY NOT SPAM</Eyebrow>
            <h2 className="gl-h2 mt-1" style={{ fontSize: 'clamp(1.75rem, 3vw, 2.25rem)' }}>
              AI content that <span className="gl-accent">earns its ranking.</span>
            </h2>
            <p className="gl-body gl-body--lg mt-4 max-w-2xl mx-auto">
              Google&apos;s spam policy targets AI content generated &quot;without
              adding value for users.&quot; Poindexter is built around the
              opposite principle: generate more candidates, filter aggressively,
              publish only what passes.
            </p>
          </div>

          <div className="grid md:grid-cols-2 gap-6">
            {QUALITY_POINTS.map((point) => (
              <Card key={point.title}>
                <Card.Title>{point.title}</Card.Title>
                <Card.Body className="mt-2">{point.description}</Card.Body>
              </Card>
            ))}
          </div>

          <p className="gl-body gl-body--sm text-center opacity-70 mt-8 max-w-2xl mx-auto">
            The ultimate test: would you be embarrassed if someone realized the
            article was AI-generated? If no — if the content is genuinely useful
            regardless of how it was made — it&apos;s ready to publish.
          </p>
        </div>
      </section>

      {/* PRICING */}
      <section className="py-16 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-5xl">
          <div className="text-center mb-12">
            <Eyebrow>GLAD LABS · GET STARTED</Eyebrow>
            <h2 className="gl-h2 mt-1" style={{ fontSize: 'clamp(1.75rem, 3vw, 2.25rem)' }}>
              Free to run. Pay for the <span className="gl-accent">fast track.</span>
            </h2>
          </div>

          <div className="grid md:grid-cols-3 gap-6 items-stretch">
            {/* Free */}
            <Card className="flex flex-col">
              <Card.Meta>OPEN SOURCE</Card.Meta>
              <p className="gl-display mt-3 text-4xl">Free</p>
              <p className="gl-mono gl-mono--upper opacity-50 text-xs">
                forever
              </p>
              <PricingList items={FREE_INCLUDED} />
              <div className="mt-auto">
                <Button
                  as="a"
                  href="https://github.com/Glad-Labs/poindexter"
                  target="_blank"
                  rel="noopener noreferrer"
                  variant="ghost"
                  className="w-full"
                >
                  Clone the repo
                </Button>
              </div>
            </Card>

            {/* Seed — amber accent tick, "POPULAR" badge */}
            <Card accent="amber" className="flex flex-col relative">
              <span
                className="absolute -top-3 left-1/2 -translate-x-1/2 gl-mono gl-mono--upper px-3 py-1"
                style={{
                  background: 'var(--gl-amber)',
                  color: '#0a0a0a',
                  fontSize: '0.6875rem',
                  letterSpacing: 'var(--gl-tracking-wide)',
                }}
              >
                POPULAR
              </span>
              <Card.Meta>SEED PACKAGE</Card.Meta>
              <p className="gl-display mt-3 text-4xl">$29</p>
              <p className="gl-mono gl-mono--upper opacity-50 text-xs">
                one-time
              </p>
              <PricingList items={SEED_INCLUDED} />
              <div className="mt-auto">
                <Button
                  as="a"
                  href="https://gladlabs.lemonsqueezy.com/checkout/buy/ece7930f-f35e-44dc-93d2-6f56709b5f52"
                  target="_blank"
                  rel="noopener noreferrer"
                  variant="primary"
                  className="w-full"
                >
                  Buy guide — $29
                </Button>
              </div>
            </Card>

            {/* Premium */}
            <Card className="flex flex-col">
              <Card.Meta>PREMIUM</Card.Meta>
              <p className="gl-display mt-3 text-4xl">
                $9.99
                <span className="gl-body gl-body--lg opacity-70 ml-1">
                  /mo
                </span>
              </p>
              <p className="gl-mono gl-mono--upper opacity-50 text-xs">
                cancel anytime
              </p>
              <PricingList items={PREMIUM_INCLUDED} />
              <div className="mt-auto">
                <Button
                  as="a"
                  href="https://gladlabs.lemonsqueezy.com/checkout/buy/a5713f22-3c57-47ae-b1ee-5fee3a0b43b9"
                  target="_blank"
                  rel="noopener noreferrer"
                  variant="secondary"
                  className="w-full"
                >
                  Subscribe — $9.99/mo
                </Button>
              </div>
            </Card>
          </div>
        </div>
      </section>

      {/* FINAL CTA */}
      <section className="py-20 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-3xl text-center">
          <h2 className="gl-h2" style={{ fontSize: 'clamp(1.75rem, 3vw, 2.25rem)' }}>
            Every PC is a <span className="gl-accent">personal AI factory.</span>
          </h2>
          <p className="gl-body gl-body--lg mt-4">
            Stop paying per token. Stop trusting cloud APIs with your content.
            Run the whole pipeline on your own hardware and publish to anywhere.
          </p>
          <div className="flex flex-col sm:flex-row gap-3 justify-center flex-wrap mt-8">
            <Button
              as="a"
              href="https://github.com/Glad-Labs/poindexter"
              target="_blank"
              rel="noopener noreferrer"
              variant="primary"
            >
              View on GitHub
            </Button>
            <Button
              as="a"
              href="https://discord.gg/GCDBxBVv"
              target="_blank"
              rel="noopener noreferrer"
              variant="secondary"
            >
              Join Discord
            </Button>
            <Button as={Link} href="/posts" variant="ghost">
              Read the blog
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}
