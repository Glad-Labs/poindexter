import Link from 'next/link';
import { OrganizationSchema } from '../../components/StructuredData';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';

export const metadata = {
  title: `About ${SITE_NAME}`,
  description:
    `${SITE_NAME} is what happens when one developer, local AI, and relentless automation collide. Built by Matt Gladding — autonomous content systems, real hardware, no corporate theater.`,
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
    description:
      `One person + AI = unlimited scale. ${SITE_NAME} is building the future of autonomous content operations.`,
    type: 'website',
  },
};

export default function AboutPage() {
  return (
    <>
      <OrganizationSchema />
      <div className="min-h-screen bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950">
        {/* Ambient Background */}
        <div className="fixed inset-0 overflow-hidden pointer-events-none">
          <div className="absolute -top-40 -right-40 w-80 h-80 bg-cyan-500/10 rounded-full blur-3xl animate-pulse" />
          <div className="absolute -bottom-40 -left-40 w-80 h-80 bg-blue-500/10 rounded-full blur-3xl animate-pulse delay-1000" />
        </div>

        <section className="relative pt-24 pb-32 px-4 sm:px-6 lg:px-8">
          <div className="max-w-2xl mx-auto">
            {/* Name / label */}
            <p className="text-sm font-mono text-cyan-500 mb-6 tracking-widest uppercase">
              Glad Labs
            </p>

            <h1 className="text-4xl md:text-5xl font-bold text-white mb-8 leading-tight">
              One person. Local AI.
              <br />
              <span className="bg-gradient-to-r from-cyan-400 to-blue-400 bg-clip-text text-transparent">
                Unlimited scale.
              </span>
            </h1>

            {/* Main prose */}
            <div className="space-y-6 text-lg text-slate-300 leading-relaxed">
              <p>
                I&apos;m{' '}
                <span className="text-white font-medium">Matt Gladding</span>,
                and Glad Labs is my proof that the rules of business just
                changed. One developer with the right AI systems can do what
                used to require an entire team — and I&apos;m building that
                reality in the open.
              </p>

              <p>
                The system running this site is an autonomous content pipeline:
                AI agents that research, draft, critique, refine, and publish —
                with quality scoring at every stage. It&apos;s not a toy demo —
                and the system gets better with every run.
              </p>

              <p>
                The big idea? A{' '}
                <span className="text-white font-medium">
                  Personal Media Operating System
                </span>{' '}
                — autonomous content generation, multi-platform distribution,
                social scheduling, stream clipping, photo-to-multi-format —
                everything a creator needs to run a professional media presence
                without the operational overhead. I&apos;m building it for
                myself first, then opening it up.
              </p>

              <p>
                This isn&apos;t corporate AI theater. No pitch deck, no
                buzzwords, no pretending GPT wrappers are products. I write
                about what I actually build, what actually works, and what
                doesn&apos;t. If something breaks, you&apos;ll read about that
                too.
              </p>
            </div>

            {/* Divider */}
            <div className="my-12 border-t border-slate-700/50" />

            {/* Quick facts */}
            <div className="grid sm:grid-cols-2 gap-6 mb-12">
              <div className="bg-slate-800/40 rounded-xl p-6 border border-slate-700/50">
                <h2 className="text-sm font-mono text-cyan-500 uppercase tracking-widest mb-3">
                  The Stack
                </h2>
                <ul className="space-y-2 text-slate-300 text-sm">
                  <li>Local AI on RTX 5090 via Ollama</li>
                  <li>Self-hosted: PostgreSQL, Grafana, Gitea, Prometheus</li>
                  <li>Content pipeline with quality gate + human approval</li>
                  <li>Podcast (Edge TTS) + Video (SDXL slideshow + ffmpeg)</li>
                  <li>FastAPI backend, 5,000+ tests</li>
                  <li>Next.js 15 on Vercel + Cloudflare R2 CDN</li>
                </ul>
              </div>

              <div className="bg-slate-800/40 rounded-xl p-6 border border-slate-700/50">
                <h2 className="text-sm font-mono text-cyan-500 uppercase tracking-widest mb-3">
                  The Mission
                </h2>
                <ul className="space-y-2 text-slate-300 text-sm">
                  <li>Prove one person + AI = unlimited output</li>
                  <li>Build systems that run while you sleep</li>
                  <li>No hype — only what actually works</li>
                  <li>AI as coworker, not just a tool</li>
                  <li>Open and honest about the process</li>
                  <li>Ship it, don&apos;t deck it</li>
                </ul>
              </div>
            </div>

            {/* Stats strip */}
            <div className="grid grid-cols-2 gap-4 mb-12">
              <div className="bg-slate-800/40 rounded-xl p-4 border border-slate-700/50 text-center">
                <p className="text-2xl md:text-3xl font-bold bg-gradient-to-r from-cyan-400 to-blue-400 bg-clip-text text-transparent">
                  1
                </p>
                <p className="text-xs text-slate-400 mt-1 font-mono uppercase tracking-wide">
                  Human Operator
                </p>
              </div>
              <div className="bg-slate-800/40 rounded-xl p-4 border border-slate-700/50 text-center">
                <p className="text-2xl md:text-3xl font-bold bg-gradient-to-r from-cyan-400 to-blue-400 bg-clip-text text-transparent">
                  24/7
                </p>
                <p className="text-xs text-slate-400 mt-1 font-mono uppercase tracking-wide">
                  AI Uptime
                </p>
              </div>
            </div>

            {/* CTA Section */}
            <div className="text-center">
              <h2 className="text-3xl font-bold mb-6 text-white">
                The future is one person with the right systems.
              </h2>
              <p className="text-xl text-slate-300 mb-8 max-w-2xl mx-auto">
                Read what we&apos;re building, how we&apos;re building it, and
                why the old rules don&apos;t apply anymore.
              </p>
              <div className="flex flex-col sm:flex-row gap-4 justify-center">
                <Link
                  href="/posts"
                  className="px-8 py-4 bg-gradient-to-r from-cyan-500 to-blue-600 text-white font-semibold rounded-xl hover:shadow-lg hover:shadow-cyan-500/50 transition-all duration-300"
                >
                  Read the Blog
                </Link>
                <a
                  href="https://x.com/_gladlabs"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-8 py-4 border border-cyan-500/50 text-cyan-400 font-semibold rounded-xl hover:bg-cyan-500/10 transition-all duration-300"
                >
                  Follow on X
                </a>
              </div>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
