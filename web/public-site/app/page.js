import Link from 'next/link';
import Image from 'next/image';

// SEO Metadata
export const metadata = {
  title: 'Glad Labs - AI & Technology Insights',
  description:
    'Deep dives into AI, technology, and digital transformation. Explore our latest insights and expert analysis.',
  openGraph: {
    title: 'Glad Labs - AI & Technology Insights',
    description: 'Deep dives into AI, technology, and digital transformation',
    type: 'website',
    locale: 'en_US',
  },
};

// Server-side data fetching with ISR (Incremental Static Regeneration)
async function getPosts() {
  try {
    const FASTAPI_URL =
      process.env.NEXT_PUBLIC_API_BASE_URL ||
      process.env.NEXT_PUBLIC_FASTAPI_URL ||
      'http://localhost:8000';

    // Validate URL is absolute
    if (
      !FASTAPI_URL.startsWith('http://') &&
      !FASTAPI_URL.startsWith('https://')
    ) {
      console.warn('Invalid NEXT_PUBLIC_API_BASE_URL, using static fallback');
      return [];
    }

    const url = `${FASTAPI_URL}/api/posts?skip=0&limit=20&published_only=true`;
    console.log('📡 Fetching posts from:', url);

    // Add timeout support using AbortController
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000); // 10 second timeout

    try {
      const response = await fetch(url, {
        // ISR: Revalidate every 1 hour (3600 seconds) - much faster than 24 hours for development
        // For production, consider webhook-triggered revalidation for instant updates when posts are published
        next: { revalidate: 3600 },
        headers: {
          'Content-Type': 'application/json',
        },
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        console.error(
          `❌ Failed to fetch posts: ${response.status} ${response.statusText}`
        );
        return [];
      }

      const data = await response.json();
      console.log(
        '✅ Posts fetched successfully, got',
        data.data?.length || 0,
        'posts'
      );
      return data.data || [];
    } catch (fetchError) {
      clearTimeout(timeoutId);
      // Specific handling for timeout vs other errors
      if (fetchError.name === 'AbortError') {
        console.error('❌ Request timeout (10s) fetching posts from', url);
      } else {
        console.error('❌ Network error fetching posts:', fetchError.message);
      }
      return [];
    }
  } catch (error) {
    console.error('❌ Error fetching posts for homepage:', error.message);
    return [];
  }
}

export default async function HomePage() {
  const posts = await getPosts();
  const currentPost = posts[0];

  return (
    <main className="min-h-screen bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950">
      {/* Animated Background Elements */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-40 -right-40 w-80 h-80 bg-cyan-500/10 rounded-full blur-3xl animate-pulse" />
        <div className="absolute -bottom-40 -left-40 w-80 h-80 bg-blue-500/10 rounded-full blur-3xl animate-pulse delay-1000" />
      </div>

      {/* Simplified Hero Section */}
      <section className="relative pt-20 pb-12 md:pt-32 md:pb-20 px-4 md:px-0">
        <div className="container mx-auto max-w-5xl text-center">
          <h1 className="text-4xl md:text-6xl font-bold mb-4 bg-gradient-to-r from-cyan-400 via-blue-400 to-violet-400 bg-clip-text text-transparent">
            Explore Our Latest Insights
          </h1>
          <p className="text-xl text-slate-300 max-w-2xl mx-auto">
            Deep dives into AI, technology, and digital transformation
          </p>
        </div>
      </section>

      {/* Featured Post */}
      {posts.length === 0 ? (
        <section className="py-12 px-4 md:px-0">
          <div className="container mx-auto max-w-6xl">
            <div className="h-96 bg-slate-800 rounded-xl flex items-center justify-center border border-slate-700">
              <p className="text-slate-400">
                No posts available yet. Check back soon!
              </p>
            </div>
          </div>
        </section>
      ) : (
        <section className="py-12 px-4 md:px-0">
          <div className="container mx-auto max-w-6xl">
            {/* Main Featured Post Card */}
            <div className="bg-gradient-to-b from-slate-800/50 to-slate-900/50 rounded-2xl overflow-hidden border border-cyan-500/20 hover:border-cyan-400/40 transition-colors">
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 p-8">
                {/* Featured Image */}
                <div className="relative aspect-video lg:aspect-auto h-full min-h-96 bg-slate-700 rounded-xl overflow-hidden">
                  {currentPost?.featured_image_url ? (
                    <Image
                      src={currentPost.featured_image_url}
                      alt={currentPost.title || 'Featured Post'}
                      fill
                      sizes="(min-width: 1024px) 50vw, 100vw"
                      className="object-cover"
                      priority
                    />
                  ) : (
                    <div className="w-full h-full bg-gradient-to-br from-cyan-500/20 to-violet-500/20 flex items-center justify-center">
                      <span className="text-slate-400">No image available</span>
                    </div>
                  )}
                </div>

                {/* Post Content */}
                <div className="flex flex-col justify-between">
                  {/* Category & Meta */}
                  <div>
                    {currentPost?.category && (
                      <div className="inline-block mb-4">
                        <span className="px-3 py-1 bg-cyan-500/20 text-cyan-300 rounded-full text-sm font-medium border border-cyan-500/30">
                          {currentPost.category.name || 'Featured'}
                        </span>
                      </div>
                    )}

                    {/* Title */}
                    <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4 leading-tight">
                      {currentPost?.title}
                    </h2>

                    {/* Excerpt */}
                    <p className="text-lg text-slate-300 mb-6 leading-relaxed">
                      {currentPost?.excerpt ||
                        (currentPost?.content
                          ? currentPost.content.substring(0, 200) + '...'
                          : 'Read this insightful article')}
                    </p>
                  </div>

                  {/* Meta Information & CTA */}
                  <div className="flex items-center justify-between pt-6 border-t border-slate-700/50">
                    <div className="text-sm text-slate-400">
                      {currentPost?.published_at && (
                        <time dateTime={currentPost.published_at}>
                          {new Date(
                            currentPost.published_at
                          ).toLocaleDateString('en-US', {
                            year: 'numeric',
                            month: 'long',
                            day: 'numeric',
                          })}
                        </time>
                      )}
                    </div>

                    <Link
                      href={`/posts/${currentPost?.slug}`}
                      className="inline-flex items-center gap-2 px-6 py-2 bg-gradient-to-r from-cyan-500 to-blue-500 text-white rounded-lg font-semibold hover:shadow-lg hover:shadow-cyan-500/50 transition-all"
                    >
                      Read Article
                      <span className="text-xl">→</span>
                    </Link>
                  </div>
                </div>
              </div>
            </div>

            {/* Recent Posts Grid */}
            {posts.length > 1 && (
              <div className="mt-12">
                <h2 className="text-2xl font-bold text-white mb-6">
                  Recent Posts
                </h2>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                  {posts.slice(1, 7).map((post) => (
                    <Link
                      key={post.id || post.slug}
                      href={`/posts/${post.slug}`}
                      className="group bg-slate-800/50 rounded-lg overflow-hidden border border-slate-700 hover:border-cyan-500/50 transition-all hover:shadow-lg hover:shadow-cyan-500/10"
                    >
                      {/* Post Image */}
                      {post.featured_image_url && (
                        <div className="relative aspect-video overflow-hidden bg-slate-700">
                          <Image
                            src={post.featured_image_url}
                            alt={post.title}
                            fill
                            sizes="(min-width: 1024px) 33vw, (min-width: 768px) 50vw, 100vw"
                            className="object-cover group-hover:scale-105 transition-transform"
                          />
                        </div>
                      )}

                      {/* Post Info */}
                      <div className="p-6">
                        <h3 className="text-lg font-semibold text-white mb-2 group-hover:text-cyan-400 transition-colors line-clamp-2">
                          {post.title}
                        </h3>
                        <p className="text-sm text-slate-400 line-clamp-2">
                          {post.excerpt ||
                            (post.content
                              ? post.content.substring(0, 100) + '...'
                              : '')}
                        </p>
                        {post.published_at && (
                          <time className="text-xs text-slate-500 mt-3 block">
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
                      </div>
                    </Link>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Browse All Articles CTA */}
      <section className="py-16 px-4 md:px-0">
        <div className="container mx-auto max-w-6xl text-center">
          <h2 className="text-3xl font-bold text-white mb-4">
            Browse All Articles
          </h2>
          <p className="text-lg text-slate-400 mb-8">
            Explore our complete collection of insights and analyses
          </p>
          <Link
            href="/archive/1"
            className="inline-flex items-center gap-2 px-8 py-4 bg-gradient-to-r from-cyan-500 via-blue-500 to-violet-500 text-white rounded-lg font-semibold hover:shadow-lg hover:shadow-cyan-500/50 transition-all text-lg"
          >
            View All Articles
            <span className="text-2xl">→</span>
          </Link>
        </div>
      </section>
    </main>
  );
}
