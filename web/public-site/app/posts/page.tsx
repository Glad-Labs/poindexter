import Link from 'next/link';
import Image from 'next/image';
import type { Metadata } from 'next';
import { getPosts } from '@/lib/posts';

const POSTS_PER_PAGE = 12;

async function getAllPublishedPosts() {
  try {
    const data = await getPosts(1);
    return { posts: data.posts, total: data.total, error: null };
  } catch {
    return { posts: [], total: 0, error: 'network' };
  }
}

export const metadata: Metadata = {
  title: 'All Articles | Glad Labs',
  description:
    'Browse our complete collection of in-depth articles on AI, technology, and digital transformation.',
  alternates: { canonical: 'https://www.gladlabs.io/posts' },
  openGraph: {
    title: 'All Articles | Glad Labs',
    description:
      'Browse our complete collection of in-depth articles on AI, technology, and digital transformation.',
    type: 'website',
  },
};

export default async function PostsPage() {
  const { posts, total, error } = await getAllPublishedPosts();
  const totalPages = Math.ceil(total / POSTS_PER_PAGE);

  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-900 via-slate-800 to-slate-900">
      {/* Header Section */}
      <div className="pt-24 pb-16 px-4 sm:px-6 lg:px-8">
        <div className="max-w-4xl mx-auto text-center">
          <h1 className="text-4xl md:text-5xl font-bold mb-4">
            <span className="bg-gradient-to-r from-cyan-400 via-blue-500 to-violet-500 bg-clip-text text-transparent">
              All Articles
            </span>
          </h1>
          <p className="text-slate-400 text-lg max-w-2xl mx-auto">
            Explore our collection of in-depth articles and insights
          </p>
        </div>
      </div>

      {/* Content Section */}
      <div className="px-4 sm:px-6 lg:px-8 pb-20">
        <div className="max-w-4xl mx-auto">
          {error ? (
            <div className="text-center py-12">
              <svg
                className="w-16 h-16 text-amber-400 mx-auto mb-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"
                />
              </svg>
              <h3 className="text-xl font-semibold text-amber-300 mb-2">
                Unable to load articles right now
              </h3>
              <p className="text-slate-400">
                Our content service is temporarily unavailable. Please try again
                shortly.
              </p>
            </div>
          ) : posts.length > 0 ? (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-12">
                {posts.map((post) => (
                  <article
                    key={post.id}
                    className="group relative bg-slate-800/50 backdrop-blur border border-slate-700/50 rounded-xl overflow-hidden hover:border-cyan-400/50 transition-all duration-300 hover:shadow-lg hover:shadow-cyan-400/10 flex flex-col h-full"
                  >
                    {post.featured_image_url && (
                      <div className="relative w-full aspect-video overflow-hidden bg-slate-700">
                        <Image
                          src={post.featured_image_url}
                          alt={post.title}
                          fill
                          className="object-cover group-hover:scale-105 transition-transform duration-300"
                          sizes="(max-width: 768px) 100vw, (max-width: 1200px) 50vw, 33vw"
                        />
                      </div>
                    )}

                    <div className="flex flex-col justify-between flex-1 p-6">
                      <div>
                        <h2 className="text-xl font-bold text-white mb-2 group-hover:text-cyan-400 transition-colors line-clamp-2">
                          <Link
                            href={`/posts/${post.slug}`}
                            className="hover:text-cyan-400 transition-colors"
                          >
                            {post.title}
                          </Link>
                        </h2>
                        {post.excerpt && (
                          <p className="text-slate-400 line-clamp-3 mb-4 text-sm">
                            {post.excerpt}
                          </p>
                        )}
                      </div>

                      <div className="flex flex-col gap-3 pt-4 border-t border-slate-700/50">
                        <div className="flex items-center justify-between text-xs text-slate-500">
                          {post.published_at && (
                            <time dateTime={post.published_at}>
                              {new Date(post.published_at).toLocaleDateString(
                                'en-US',
                                {
                                  year: 'numeric',
                                  month: 'short',
                                  day: 'numeric',
                                }
                              )}
                            </time>
                          )}
                          {post.view_count > 0 && (
                            <span>{post.view_count} views</span>
                          )}
                        </div>
                        <Link
                          href={`/posts/${post.slug}`}
                          aria-hidden="true"
                          tabIndex={-1}
                          className="text-cyan-400 hover:text-cyan-300 font-semibold text-sm transition-colors flex items-center gap-2 self-start"
                        >
                          Read More
                          <svg
                            className="w-4 h-4"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                            aria-hidden="true"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              strokeWidth={2}
                              d="M9 5l7 7-7 7"
                            />
                          </svg>
                        </Link>
                      </div>
                    </div>
                  </article>
                ))}
              </div>

              {/* Link to full archive with pagination */}
              {totalPages > 1 && (
                <div className="text-center">
                  <Link
                    href="/archive/1"
                    className="inline-flex items-center gap-2 px-6 py-3 bg-gradient-to-r from-cyan-500 to-blue-500 text-white rounded-lg font-semibold hover:shadow-lg hover:shadow-cyan-500/50 transition-all"
                  >
                    Browse Full Archive ({total} articles)
                    <span className="text-lg">&rarr;</span>
                  </Link>
                </div>
              )}
            </>
          ) : (
            <div className="text-center py-12">
              <svg
                className="w-16 h-16 text-slate-600 mx-auto mb-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1}
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
              <h3 className="text-xl font-semibold text-slate-300 mb-2">
                No Articles Found
              </h3>
              <p className="text-slate-400">Check back soon for new articles</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
