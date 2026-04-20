import Link from 'next/link';
import Image from 'next/image';
import * as Sentry from '@sentry/nextjs';
import { Display, Eyebrow, Button } from '@glad-labs/brand';
import { OrganizationSchema } from '../components/StructuredData';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';

// SEO Metadata
export const metadata = {
  title: `${SITE_NAME} - AI & Technology Insights`,
  description:
    'Deep dives into AI, technology, and digital transformation. Explore our latest insights, expert analysis, and practical guides.',
  alternates: {
    canonical: `${SITE_URL}/`,
  },
  openGraph: {
    title: `${SITE_NAME} - AI & Technology Insights`,
    description:
      'Deep dives into AI, technology, and digital transformation. Explore our latest insights, expert analysis, and practical guides.',
    type: 'website',
    locale: 'en_US',
    url: `${SITE_URL}/`,
    images: [
      {
        url: '/og-image.jpg',
        width: 1200,
        height: 630,
        alt: `${SITE_NAME} - AI & Technology Insights`,
      },
    ],
  },
};

// Fetch posts from static JSON on R2/CDN — no API server needed
const STATIC_URL =
  process.env.NEXT_PUBLIC_STATIC_URL ||
  'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';

async function getPosts() {
  try {
    const response = await fetch(`${STATIC_URL}/posts/index.json`, {
      next: { revalidate: 300 },
    });

    if (!response.ok) {
      Sentry.captureMessage(
        `Failed to fetch static posts: ${response.status}`,
        'error'
      );
      return { posts: [], error: 'fetch_error' };
    }

    const data = await response.json();
    return { posts: data.posts || [], error: null };
  } catch (error) {
    Sentry.captureException(error);
    return { posts: [], error: 'network' };
  }
}

export default async function HomePage() {
  const { posts, error } = await getPosts();
  const currentPost = posts[0];

  // WebSite structured data for Google sitelinks search box
  const websiteSchema = {
    '@context': 'https://schema.org',
    '@type': 'WebSite',
    name: SITE_NAME,
    url: SITE_URL,
    description:
      'Deep dives into AI, technology, and digital transformation. Explore our latest insights, expert analysis, and practical guides.',
    publisher: {
      '@type': 'Organization',
      name: SITE_NAME,
      url: SITE_URL,
    },
  };

  return (
    <>
      {/* Structured Data */}
      <OrganizationSchema />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(websiteSchema) }}
      />

      <div className="gl-atmosphere min-h-screen">
        {/* Hero — E3: eyebrow + uppercase display + amber accent word */}
        <section className="relative pt-20 pb-12 md:pt-32 md:pb-20 px-4 md:px-0">
          <div className="container mx-auto max-w-5xl">
            <Eyebrow>GLAD LABS · AI / HARDWARE / GAMING</Eyebrow>
            <Display xl>
              Explore the <Display.Accent>frontier.</Display.Accent>
            </Display>
            <p className="gl-body gl-body--lg mt-4 max-w-2xl">
              Deep dives into AI, hardware, and the edges where they meet.
              Locally-published, human-reviewed, free to read.
            </p>
            <div className="flex gap-3 mt-8">
              <Button as={Link} href="/archive" variant="primary">
                ▶ Browse the archive
              </Button>
              <Button as={Link} href="/about" variant="secondary">
                About Glad Labs
              </Button>
            </div>
          </div>
        </section>

        {/* #946: Distinct states for API outage vs empty content */}
        {error ? (
          <section className="py-12 px-4 md:px-0">
            <div className="container mx-auto max-w-6xl">
              <div className="h-96 bg-slate-800/50 rounded-xl flex flex-col items-center justify-center border border-amber-500/30">
                <svg
                  className="w-12 h-12 text-amber-400 mb-4"
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
                <p className="text-amber-300 font-medium text-lg mb-2">
                  Unable to load articles right now
                </p>
                <p className="text-slate-400 text-sm">
                  Our content service is temporarily unavailable. Please try
                  again shortly.
                </p>
              </div>
            </div>
          </section>
        ) : posts.length === 0 ? (
          <section className="py-12 px-4 md:px-0">
            <div className="container mx-auto max-w-6xl">
              <div className="h-96 bg-slate-800 rounded-xl flex flex-col items-center justify-center border border-slate-700">
                <p className="text-slate-400 text-lg mb-2">
                  No posts available yet.
                </p>
                <p className="text-slate-500 text-sm">
                  New articles are on the way — check back soon!
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
                        <span className="text-slate-400">
                          No image available
                        </span>
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
                          <p className="text-sm text-slate-300 line-clamp-2">
                            {post.excerpt ||
                              (post.content
                                ? post.content.substring(0, 100) + '...'
                                : '')}
                          </p>
                          {post.published_at && (
                            <time
                              dateTime={post.published_at}
                              className="text-xs text-slate-400 mt-3 block"
                            >
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
            <p className="text-lg text-slate-300 mb-8">
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
      </div>
    </>
  );
}
