import Image from 'next/image';
import Link from 'next/link';
import { Button, Card, Display, Eyebrow } from '@glad-labs/brand';
import { OrganizationSchema } from '../../components/StructuredData';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';

export const metadata = {
  title: `About ${SITE_NAME}`,
  description: `${SITE_NAME} is what happens when one developer, local AI, and relentless automation collide. Built by Matt Gladding — autonomous content systems, real hardware, no corporate theater.`,
  alternates: {
    canonical: `${SITE_URL}/about`,
  },
  keywords: [
    'AI content operations',
    'autonomous agents',
    'AI publishing',
    'content automation',
    'AI lab',
    'solo AI business',
    'Matthew Gladding',
    'local AI inference',
    'Ollama',
  ],
  openGraph: {
    title: `About ${SITE_NAME}`,
    description: `One person + AI = unlimited scale. ${SITE_NAME} is building the future of autonomous content operations.`,
    type: 'website',
  },
};

export default function AboutPage() {
  return (
    <>
      <OrganizationSchema />
      <div className="gl-atmosphere min-h-screen">
        {/* Hero — E3 eyebrow + display */}
        <section className="relative pt-20 pb-12 md:pt-32 md:pb-16 px-4 sm:px-6 lg:px-8">
          <div className="container mx-auto max-w-3xl">
            <Eyebrow>GLAD LABS · ABOUT</Eyebrow>
            <Display xl>
              One person. Local AI.{' '}
              <Display.Accent>Unlimited scale.</Display.Accent>
            </Display>

            {/* Author card — photo + identity (EEAT signal for Google) */}
            <div className="mt-10 flex flex-col sm:flex-row gap-6 items-start sm:items-center">
              <div className="flex-shrink-0">
                <Image
                  src="/images/matt-gladding.png"
                  alt="Matt Gladding, founder of Glad Labs LLC"
                  width={140}
                  height={140}
                  priority
                  className="rounded-full border-2 border-cyan-500/30"
                />
              </div>
              <div className="space-y-2">
                <p className="gl-h3 mb-0">Matt Gladding</p>
                <p className="gl-body gl-body--sm gl-body--muted">
                  Founder &amp; sole operator, Glad Labs LLC. Senior data
                  engineer turned AI systems builder. Founded September 25,
                  2025.
                </p>
                <p className="gl-body gl-body--sm gl-body--muted">
                  B.A.Sc. Game Development &amp; Simulation Programming —
                  New England Institute of Technology.
                </p>
                <div className="flex flex-wrap gap-3 pt-1 text-sm">
                  <a
                    href="https://github.com/Glad-Labs/poindexter"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="gl-link"
                  >
                    GitHub · Poindexter
                  </a>
                  <span className="gl-body--muted">·</span>
                  <a
                    href="https://github.com/mattg-stack"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="gl-link"
                  >
                    GitHub · Personal
                  </a>
                  <span className="gl-body--muted">·</span>
                  <a
                    href="https://x.com/_gladlabs"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="gl-link"
                  >
                    X / Twitter
                  </a>
                  <span className="gl-body--muted">·</span>
                  <a
                    href="https://www.linkedin.com/in/matthew-gladding/"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="gl-link"
                  >
                    LinkedIn
                  </a>
                </div>
              </div>
            </div>

            {/* Main prose */}
            <div className="space-y-6 mt-10">
              <p className="gl-body gl-body--lg">
                I&apos;m{' '}
                <span className="gl-body--primary font-medium">
                  Matt Gladding
                </span>
                , and Glad Labs is my proof that the rules of business just
                changed. One developer with the right AI systems can do what
                used to require an entire team — and I&apos;m building that
                reality in the open.
              </p>

              <p className="gl-body gl-body--lg">
                I&apos;ve got a B.A.Sc. in Game Development &amp; Simulation
                Programming from New England Institute of Technology — the
                degree that made me think in terms of systems, state machines,
                and update loops long before I knew that&apos;s also how AI
                pipelines work. Before Glad Labs I spent over a decade as a
                senior data engineer — ETL pipelines, warehouse architectures,
                and the kind of slow-burn integration work where moving a
                column the wrong way breaks a hundred dashboards. Those two
                skills, combined, are exactly what AI systems need: reliable
                plumbing, idempotent migrations, observability everywhere, no
                silent failures.
              </p>

              <p className="gl-body gl-body--lg">
                The system running this site is{' '}
                <a
                  href="https://github.com/Glad-Labs/poindexter"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="gl-link"
                >
                  Poindexter
                </a>{' '}
                — an open-source autonomous content pipeline I built and run on
                my own hardware (Ryzen 9 9950X3D + RTX 5090, no cloud LLM
                dependency). AI agents research, draft, critique, refine, and
                publish — with quality scoring at every stage. Apache 2.0
                licensed, every line of code is on GitHub, every config knob
                lives in a settings table you can read.
              </p>

              <p className="gl-body gl-body--lg">
                The big idea? A{' '}
                <span className="gl-body--primary font-medium">
                  Personal Media Operating System
                </span>{' '}
                — autonomous content generation, multi-platform distribution,
                social scheduling, stream clipping, photo-to-multi-format —
                everything a creator needs to run a professional media presence
                without the operational overhead. I&apos;m building it for
                myself first, then opening it up.
              </p>

              <p className="gl-body gl-body--lg">
                This isn&apos;t corporate AI theater. No pitch deck, no
                buzzwords, no pretending GPT wrappers are products. I write
                about what I actually build, what actually works, and what
                doesn&apos;t. If something breaks, you&apos;ll read about that
                too — usually with a commit hash.
              </p>
            </div>
          </div>
        </section>

        {/* Quick facts */}
        <section className="px-4 sm:px-6 lg:px-8 pb-12">
          <div className="container mx-auto max-w-3xl">
            <div className="grid sm:grid-cols-2 gap-6">
              <Card accent="cyan">
                <Card.Meta>THE STACK</Card.Meta>
                <ul className="gl-body gl-body--sm mt-3 space-y-2 list-none">
                  <li>Local AI on RTX 5090 via Ollama</li>
                  <li>Self-hosted: PostgreSQL, Grafana, Gitea, Prometheus</li>
                  <li>Content pipeline with quality gate + human approval</li>
                  <li>Podcast (Edge TTS) + Video (SDXL slideshow + ffmpeg)</li>
                  <li>FastAPI backend, 5,000+ tests</li>
                  <li>Next.js 15 on Vercel + Cloudflare R2 CDN</li>
                </ul>
              </Card>

              <Card accent="amber">
                <Card.Meta>THE MISSION</Card.Meta>
                <ul className="gl-body gl-body--sm mt-3 space-y-2 list-none">
                  <li>Prove one person + AI = unlimited output</li>
                  <li>Build systems that run while you sleep</li>
                  <li>No hype — only what actually works</li>
                  <li>AI as coworker, not just a tool</li>
                  <li>Open and honest about the process</li>
                  <li>Ship it, don&apos;t deck it</li>
                </ul>
              </Card>
            </div>
          </div>
        </section>

        {/* Stats strip */}
        <section className="px-4 sm:px-6 lg:px-8 pb-12">
          <div className="container mx-auto max-w-3xl">
            <div className="grid grid-cols-2 gap-6">
              <Card accent="cyan" className="text-center">
                <p className="gl-display text-4xl md:text-5xl">
                  <span className="gl-accent">1</span>
                </p>
                <Card.Meta className="mt-2">HUMAN OPERATOR</Card.Meta>
              </Card>
              <Card accent="cyan" className="text-center">
                <p className="gl-display text-4xl md:text-5xl">
                  <span className="gl-accent">24/7</span>
                </p>
                <Card.Meta className="mt-2">AI UPTIME</Card.Meta>
              </Card>
            </div>
          </div>
        </section>

        {/* CTA */}
        <section className="px-4 sm:px-6 lg:px-8 pb-20">
          <div className="container mx-auto max-w-3xl text-center">
            <h2 className="gl-h2">
              The future is one person with the right systems.
            </h2>
            <p className="gl-body gl-body--lg mt-4 max-w-2xl mx-auto">
              Read what we&apos;re building, how we&apos;re building it, and why
              the old rules don&apos;t apply anymore.
            </p>
            <div className="flex flex-col sm:flex-row gap-3 justify-center mt-8">
              <Button as={Link} href="/posts" variant="primary">
                ▶ Read the blog
              </Button>
              <Button
                as="a"
                href="https://x.com/_gladlabs"
                target="_blank"
                rel="noopener noreferrer"
                variant="secondary"
              >
                Follow on X
              </Button>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
