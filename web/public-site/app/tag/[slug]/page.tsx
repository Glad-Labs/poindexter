import type { Metadata } from 'next';
import logger from '@/lib/logger';
import Link from 'next/link';

import { getAllPublishedPosts } from '@/lib/posts';

interface Post {
  id: string;
  title: string;
  slug: string;
  excerpt?: string;
  cover_image_url?: string;
  featured_image_url?: string;
  published_at?: string;
  created_at: string;
  view_count: number;
  tags?: string[];
}

const STATIC_URL =
  process.env.NEXT_PUBLIC_STATIC_URL ||
  'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';

async function getTagPosts(tag: string): Promise<Post[]> {
  try {
    // Fetch the full post index and filter by tag
    const allPosts = await getAllPublishedPosts();
    return allPosts.filter(
      (p) =>
        Array.isArray((p as Post).tags) &&
        (p as Post).tags!.some((t) => t.toLowerCase() === tag.toLowerCase())
    ) as Post[];
  } catch (error) {
    logger.error(`Error fetching posts for tag "${tag}":`, error);
    return [];
  }
}

export async function generateStaticParams() {
  try {
    const response = await fetch(`${STATIC_URL}/sitemap.json`, {
      next: { revalidate: 300 },
    });
    if (!response.ok) return [];
    const data = await response.json();
    const urls: { loc: string }[] = data.urls || data || [];
    return urls
      .filter((u) => u.loc && u.loc.includes('/tag/'))
      .map((u) => {
        const slug = u.loc.split('/tag/').pop()?.replace(/\/$/, '') || '';
        return { slug };
      })
      .filter((t) => t.slug);
  } catch {
    return [];
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const tag = decodeURIComponent(slug);
  const title = `#${tag} Articles | Glad Labs`;
  const description = `Browse all articles tagged with "${tag}" on Glad Labs.`;

  return {
    title,
    description,
    openGraph: { title, description },
  };
}

export default async function TagPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  // Decode the tag from URL-safe format
  const tag = decodeURIComponent(slug);
  const posts = await getTagPosts(tag);

  if (posts.length === 0) {
    // Could show 404, but instead show empty state for better UX
    // notFound();
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-900 via-slate-800 to-slate-900">
      {/* Tag Header */}
      <div className="pt-20 pb-12">
        <div className="px-4 sm:px-6 lg:px-8">
          <div className="max-w-4xl mx-auto">
            <div className="inline-block px-3 py-1 rounded-full bg-blue-400/10 border border-blue-400/30 text-blue-400 text-sm font-medium mb-4">
              Tag
            </div>
            <h1 className="text-4xl md:text-5xl font-bold text-white mb-4 leading-tight">
              #{tag}
            </h1>
            <div className="flex items-center gap-4">
              <span className="text-slate-400 text-sm">
                {posts.length} article{posts.length !== 1 ? 's' : ''}
              </span>
              <Link
                href="/archive/1"
                className="text-sm text-cyan-400 hover:text-cyan-300 transition-colors"
              >
                ← Back to All Articles
              </Link>
            </div>
          </div>
        </div>
      </div>

      {/* Posts Grid */}
      <div className="px-4 sm:px-6 lg:px-8 pb-20">
        <div className="max-w-4xl mx-auto">
          {posts.length === 0 ? (
            <div className="bg-slate-800/30 border border-slate-700 rounded-lg p-8 text-center">
              <p className="text-slate-400 text-sm mb-4">
                No articles found with this tag yet.
              </p>
              <Link
                href="/archive/1"
                className="inline-block text-sm text-cyan-400 hover:text-cyan-300 transition-colors"
              >
                Browse all articles
              </Link>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {posts.map((post) => {
                const imageUrl =
                  post.cover_image_url || post.featured_image_url;
                const publishDate = post.published_at || post.created_at;
                const formattedDate = new Date(publishDate).toLocaleDateString(
                  'en-US',
                  {
                    year: 'numeric',
                    month: 'short',
                    day: 'numeric',
                  }
                );

                return (
                  <Link
                    key={post.id}
                    href={`/posts/${post.slug}`}
                    className="group rounded-lg border border-slate-700 hover:border-cyan-400/50 transition-all duration-300 overflow-hidden hover:shadow-lg hover:shadow-cyan-400/10"
                  >
                    {imageUrl && (
                      <div className="relative w-full h-40 bg-slate-700">
                        <img
                          src={imageUrl}
                          alt={post.title}
                          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                        />
                      </div>
                    )}
                    <div className="p-4">
                      <h3 className="text-lg font-semibold text-cyan-400 group-hover:text-cyan-300 transition-colors line-clamp-2 mb-2">
                        {post.title}
                      </h3>
                      {post.excerpt && (
                        <p className="text-sm text-slate-400 line-clamp-2 mb-3">
                          {post.excerpt}
                        </p>
                      )}
                      <div className="flex items-center justify-between text-xs text-slate-500">
                        <span>{formattedDate}</span>
                        {post.view_count > 0 && (
                          <span>{post.view_count.toLocaleString()} views</span>
                        )}
                      </div>
                    </div>
                  </Link>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
