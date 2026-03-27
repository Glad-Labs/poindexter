import Link from 'next/link';
import { OrganizationSchema } from '../../components/StructuredData';

export const metadata = {
  title: 'About Glad Labs',
  description:
    "Glad Labs is Matthew Gladding's one-person AI lab. Building autonomous agent systems for content operations and writing about what actually works.",
  keywords: [
    'AI content operations',
    'autonomous agents',
    'AI publishing',
    'content automation',
    'AI lab',
    'Matthew Gladding',
  ],
  openGraph: {
    title: 'About Glad Labs',
    description:
      'A one-person AI lab building autonomous agent systems for content operations.',
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
              A one-person AI lab,
              <br />
              <span className="bg-gradient-to-r from-cyan-400 to-blue-400 bg-clip-text text-transparent">
                building in public.
              </span>
            </h1>

            {/* Main prose */}
            <div className="space-y-6 text-lg text-slate-300 leading-relaxed">
              <p>
                Hi — I&apos;m{' '}
                <span className="text-white font-medium">Matthew Gladding</span>
                . Glad Labs is where I build AI systems and write about what I
                learn along the way.
              </p>

              <p>
                The core project is a content operations platform I built for
                myself: an autonomous agent pipeline that handles research,
                drafting, critique, refinement, and publishing — with me in the
                loop at every meaningful decision point. The articles on this
                site are produced by that system. When the system improves, so
                does the writing.
              </p>

              <p>
                The pipeline runs on a FastAPI backend with a multi-provider LLM
                router (Anthropic, OpenAI, Gemini, and local Ollama) that
                selects models by cost tier and task complexity. A React admin
                dashboard gives me real-time visibility into every agent run.
                The whole thing is deployed on Railway and Vercel, versioned in
                GitHub, and tested enough that I trust it not to embarrass me at
                2am.
              </p>

              <p>
                Right now this is a solo operation — I&apos;m learning what
                works, generating some revenue, and iterating fast. The longer
                arc is to turn the platform into something other content teams
                can use. But I&apos;m not pretending that&apos;s happened yet.
              </p>

              <p>
                I write about AI, developer tooling, and the practical side of
                building systems like this. No hype, no enterprise positioning —
                just what I actually built and what I actually think.
              </p>
            </div>

            {/* Divider */}
            <div className="my-12 border-t border-slate-700/50" />

            {/* Quick facts */}
            <div className="grid sm:grid-cols-2 gap-6 mb-12">
              <div className="bg-slate-800/40 rounded-xl p-6 border border-slate-700/50">
                <h2 className="text-sm font-mono text-cyan-500 uppercase tracking-widest mb-3">
                  The Platform
                </h2>
                <ul className="space-y-2 text-slate-300 text-sm">
                  <li>FastAPI backend · async throughout</li>
                  <li>6-stage self-critiquing agent pipeline</li>
                  <li>Multi-provider LLM routing</li>
                  <li>PostgreSQL · Railway · Vercel</li>
                  <li>Next.js 15 content distribution</li>
                </ul>
              </div>

              <div className="bg-slate-800/40 rounded-xl p-6 border border-slate-700/50">
                <h2 className="text-sm font-mono text-cyan-500 uppercase tracking-widest mb-3">
                  The Blog
                </h2>
                <ul className="space-y-2 text-slate-300 text-sm">
                  <li>AI · developer tooling · building in public</li>
                  <li>Written with the system, edited by me</li>
                  <li>No recycled takes · no sponsored posts</li>
                  <li>Published when it&apos;s ready, not on a schedule</li>
                </ul>
              </div>
            </div>

            {/* CTA Section */}
            <div className="text-center">
              <h2 className="text-3xl font-bold mb-6 text-white">
                Ready to Scale Your AI Operations?
              </h2>
              <p className="text-xl text-slate-300 mb-8 max-w-2xl mx-auto">
                Explore how Glad Labs orchestrates autonomous agents, optimizes
                LLM costs, and delivers enterprise-ready AI at scale.
              </p>
              <div className="flex flex-col sm:flex-row gap-4 justify-center">
                <Link
                  href="/posts"
                  className="px-8 py-4 bg-gradient-to-r from-cyan-500 to-blue-600 text-white font-semibold rounded-xl hover:shadow-lg hover:shadow-cyan-500/50 transition-all duration-300"
                >
                  Read Our Blog
                </Link>
                <Link
                  href="/"
                  className="px-8 py-4 border border-cyan-500/50 text-cyan-400 font-semibold rounded-xl hover:bg-cyan-500/10 transition-all duration-300"
                >
                  Back to Home
                </Link>
              </div>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
