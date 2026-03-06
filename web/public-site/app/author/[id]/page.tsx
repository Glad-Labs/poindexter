import Link from 'next/link';
import { notFound } from 'next/navigation';

const authorProfiles: Record<string, { name: string; bio: string }> = {
  'poindexter-ai': {
    name: 'Poindexter AI',
    bio: 'AI Content Generation Engine. Poindexter AI is the intelligent content orchestrator powering Glad Labs, crafting insightful articles on AI, automation, and digital transformation.',
  },
  default: {
    name: 'Glad Labs',
    bio: 'Where AI meets thoughtful content creation. We explore the intersection of artificial intelligence and human creativity.',
  },
};

export default async function AuthorPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  // Get author profile or use default
  const author = authorProfiles[id] || authorProfiles.default;

  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-900 via-slate-800 to-slate-900">
      {/* Author Header */}
      <div className="pt-20 pb-12">
        <div className="px-4 sm:px-6 lg:px-8">
          <div className="max-w-4xl mx-auto">
            <h1 className="text-4xl md:text-5xl font-bold text-white mb-4 leading-tight">
              {author.name}
            </h1>
            <p className="text-xl text-slate-300 mb-8 leading-relaxed max-w-2xl">
              {author.bio}
            </p>

            {/* Navigation */}
            <div className="flex gap-4">
              <Link
                href="/archive/1"
                className="px-4 py-2 rounded-lg bg-cyan-400/10 border border-cyan-400/30 text-cyan-400 hover:bg-cyan-400/20 transition-colors"
              >
                Back to All Articles
              </Link>
            </div>
          </div>
        </div>
      </div>

      {/* Articles Section */}
      <div className="px-4 sm:px-6 lg:px-8 pb-20">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-3xl font-bold text-white mb-8">
            Articles by {author.name}
          </h2>
          <div className="bg-slate-800/30 border border-slate-700 rounded-lg p-8 text-center">
            <p className="text-slate-400 text-sm">
              Articles from this author coming soon. Check back to see their
              latest work!
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
