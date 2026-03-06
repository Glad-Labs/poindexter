'use client';

import Link from 'next/link';

interface AuthorCardProps {
  authorId?: string;
  authorName?: string;
}

// Minimal author profiles - can be expanded with API calls later
const authorProfiles: Record<string, { name: string; bio: string }> = {
  'poindexter-ai': {
    name: 'Poindexter AI',
    bio: 'AI Content Generation Engine',
  },
  default: {
    name: 'Glad Labs',
    bio: 'Where AI meets thoughtful content creation',
  },
};

export function AuthorCard({ authorId, authorName }: AuthorCardProps) {
  // Use provided author name, fallback to ID, then default
  const displayName = authorName || 'Glad Labs';

  // Find matching profile or use default
  const profileKey =
    authorId && authorId.toLowerCase().includes('poindexter')
      ? 'poindexter-ai'
      : 'default';
  const profile = authorProfiles[profileKey] || authorProfiles.default;

  return (
    <div className="my-12 p-6 rounded-lg bg-gradient-to-r from-slate-800/50 to-slate-700/30 border border-slate-700">
      <div className="flex items-start gap-4">
        <div className="flex-1">
          <h3 className="text-lg font-semibold text-white mb-1">
            About the Author
          </h3>
          <p className="block text-base font-medium text-cyan-400 hover:text-cyan-300 transition-colors mb-2">
            <Link href={`/author/${authorId || 'default'}`}>{displayName}</Link>
          </p>
          <p className="text-sm text-slate-400 leading-relaxed">
            {profile.bio}
          </p>
          <Link
            href={`/author/${authorId || 'default'}`}
            className="inline-block mt-3 text-sm font-medium text-cyan-400 hover:text-cyan-300 transition-colors"
          >
            View more articles →
          </Link>
        </div>
      </div>
    </div>
  );
}
