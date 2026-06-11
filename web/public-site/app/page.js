import Link from 'next/link';
import Image from 'next/image';
import * as Sentry from '@sentry/nextjs';
import { Button, Card, Display, Eyebrow } from '@glad-labs/brand';
import { OrganizationSchema } from '../components/StructuredData';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';
import {
  postFeaturedImage,
  sortPostsNewestFirst,
  cleanPostTitle,
  postExcerpt,
} from '@/lib/posts';

// Time-based ISR backstop (1h). Primary refresh is on-demand
// revalidateTag('posts') on publish; this floor self-heals the index if a
// publish path ever skips the on-demand revalidate (poindexter#575).
export const revalidate = 3600;

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
      // Tag-based cache — invalidated by revalidateTag('posts') on publish.
      // No TTL: a stale index would otherwise persist for 300s after a new
      // post goes live (#967).
      next: { tags: ['posts', 'post-index'] },
    });

    if (!response.ok) {
      Sentry.captureMessage(
        `Failed to fetch static posts: ${response.status}`,
        'error'
      );
      return { posts: [], error: 'fetch_error' };
    }

    const data = await response.json();
    // Issue #1 (audit): index.json order is pipeline-dependent; the featured
    // slot showed a stale post above newer ones. Same defensive sort as
    // lib/posts.ts so "FEATURED · LATEST" is actually the latest.
    const allPosts = sortPostsNewestFirst(data.posts || []);
    // Exclude dev_diary from the main feed — it has its own /dev-diary page
    // and publishing daily would otherwise flood the homepage (#1339).
    const posts = allPosts.filter((p) => p.niche_slug !== 'dev_diary');
    return { posts, error: null };
  } catch (error) {
    Sentry.captureException(error);
    return { posts: [], error: 'network' };
  }
}

export default async function HomePage() {
  const { posts, error } = await getPosts();
  const currentPost = posts[0];
  const heroImage = currentPost ? postFeaturedImage(currentPost) : null;
  const featuredTitle = currentPost ? cleanPostTitle(currentPost.title) : '';
  // Issue #2 (audit): null when no real excerpt exists — we omit the
  // element rather than ship placeholder copy to production.
  const featuredExcerpt = currentPost ? postExcerpt(currentPost, 200) : null;

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
        <section className="relative pt-20 pb-12 md:pt-32 md:pb-20 px-4 sm:px-6 lg:px-8">
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
                <span aria-hidden>▶</span> Browse the archive
              </Button>
              <Button as={Link} href="/about" variant="secondary">
                About Glad Labs
              </Button>
            </div>
          </div>
        </section>

        {/* #946: Distinct states for API outage vs empty content */}
        {error ? (
          <section className="py-12 px-4 sm:px-6 lg:px-8">
            <div className="container mx-auto max-w-6xl">
              <Card accent="amber" className="text-center py-12">
                <Card.Meta>SERVICE UNAVAILABLE</Card.Meta>
                <h2 className="gl-h2 mt-2">
                  Unable to load articles right now.
                </h2>
                <p className="gl-body mt-3 max-w-md mx-auto">
                  Our content service is temporarily unavailable. Please try
                  again shortly.
                </p>
              </Card>
            </div>
          </section>
        ) : posts.length === 0 ? (
          <section className="py-12 px-4 sm:px-6 lg:px-8">
            <div className="container mx-auto max-w-6xl">
              <Card accent="amber" className="text-center py-12">
                <Card.Meta>NO ARTICLES YET</Card.Meta>
                <h2 className="gl-h2 mt-2">Nothing published yet.</h2>
                <p className="gl-body mt-3 max-w-md mx-auto">
                  New articles are on the way — check back soon.
                </p>
              </Card>
            </div>
          </section>
        ) : (
          <section className="py-12 px-4 sm:px-6 lg:px-8">
            <div className="container mx-auto max-w-6xl">
              {/* Featured Post */}
              <Card className="overflow-hidden p-0">
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-0">
                  {/* Featured Image */}
                  <div className="relative aspect-video lg:aspect-auto h-full min-h-96 bg-[var(--gl-surface)] overflow-hidden">
                    {heroImage ? (
                      <Image
                        src={heroImage}
                        alt=""
                        fill
                        sizes="(min-width: 1024px) 50vw, 100vw"
                        className="object-cover"
                        priority
                      />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center gl-mono gl-mono--upper text-[color:var(--gl-text-muted)]">
                        No image available
                      </div>
                    )}
                  </div>

                  {/* Post Content */}
                  <div className="flex flex-col justify-between p-8">
                    <div>
                      <Card.Meta>FEATURED · LATEST</Card.Meta>
                      {currentPost?.category && (
                        <div className="mt-2">
                          <Card.Tag>
                            {currentPost.category.name || 'Featured'}
                          </Card.Tag>
                        </div>
                      )}

                      <h2
                        className="gl-h2 mt-4"
                        style={{ fontSize: 'clamp(1.75rem, 3vw, 2.25rem)' }}
                      >
                        {featuredTitle}
                      </h2>

                      {featuredExcerpt && (
                        <p className="gl-body gl-body--lg mt-4">
                          {featuredExcerpt}
                        </p>
                      )}
                    </div>

                    <div
                      className="flex items-center justify-between pt-6 mt-6"
                      style={{ borderTop: '1px solid var(--gl-hairline)' }}
                    >
                      <div className="gl-mono gl-mono--upper text-[color:var(--gl-text-muted)] text-xs">
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

                      <Button
                        as={Link}
                        href={`/posts/${currentPost?.slug}`}
                        variant="primary"
                      >
                        Read article <span aria-hidden>→</span>
                      </Button>
                    </div>
                  </div>
                </div>
              </Card>

              {/* Recent Posts Grid */}
              {posts.length > 1 && (
                <div className="mt-16">
                  <Eyebrow>GLAD LABS · RECENT</Eyebrow>
                  <h2 className="gl-h2 mt-1 mb-6">Recent posts.</h2>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {posts.slice(1, 7).map((post) => {
                      const cardImage = postFeaturedImage(post);
                      const cardTitle = cleanPostTitle(post.title);
                      // Issue #2/#5 (audit): canonical excerpt resolver — no
                      // raw content.substring() leaking HTML into cards, no
                      // title-repeated-as-excerpt. Omits cleanly when empty.
                      const cardExcerpt = postExcerpt(post, 140);
                      return (
                      <Card
                        key={post.id || post.slug}
                        className="group flex flex-col h-full overflow-hidden p-0"
                      >
                        {cardImage && (
                          <div className="relative aspect-video overflow-hidden bg-[var(--gl-surface)]">
                            <Image
                              src={cardImage}
                              alt=""
                              fill
                              sizes="(min-width: 1024px) 33vw, (min-width: 768px) 50vw, 100vw"
                              className="object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                            />
                          </div>
                        )}

                        <div className="flex flex-col justify-between flex-1 p-6">
                          <div>
                            {post.published_at ? (
                              <Card.Meta>
                                <time dateTime={post.published_at}>
                                  {new Date(
                                    post.published_at
                                  ).toLocaleDateString('en-US', {
                                    year: 'numeric',
                                    month: 'short',
                                    day: 'numeric',
                                  })}
                                </time>
                              </Card.Meta>
                            ) : null}
                            <Card.Title>
                              <Link
                                href={`/posts/${post.slug}`}
                                className="hover:text-[color:var(--gl-cyan)] transition-colors"
                              >
                                {cardTitle}
                              </Link>
                            </Card.Title>
                            {cardExcerpt && (
                              <Card.Body className="line-clamp-3 mt-2">
                                {cardExcerpt}
                              </Card.Body>
                            )}
                          </div>
                          <div
                            className="pt-4 mt-4"
                            style={{ borderTop: '1px solid var(--gl-hairline)' }}
                          >
                            <Link
                              href={`/posts/${post.slug}`}
                              className="gl-mono gl-mono--accent gl-mono--upper inline-flex items-center gap-2 hover:opacity-80 transition-opacity"
                              aria-hidden="true"
                              tabIndex={-1}
                            >
                              Read article
                              <span aria-hidden>→</span>
                            </Link>
                          </div>
                        </div>
                      </Card>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </section>
        )}

        {/* Dev Diary teaser — links to the dedicated /dev-diary feed (#1339) */}
        <section className="py-6 px-4 sm:px-6 lg:px-8">
          <div className="container mx-auto max-w-6xl text-center">
            <a href="/dev-diary" className="text-[color:var(--gl-cyan)] hover:underline text-sm gl-mono">
              Read the Dev Diary — daily founder notes from building Glad Labs →
            </a>
          </div>
        </section>

        {/* Browse All Articles CTA */}
        <section className="py-16 px-4 sm:px-6 lg:px-8">
          <div className="container mx-auto max-w-5xl text-center">
            <Eyebrow>GLAD LABS · ARCHIVE</Eyebrow>
            <h2 className="gl-h2 mt-1">
              Every article we&apos;ve published.
            </h2>
            <p className="gl-body gl-body--lg mt-4 max-w-2xl mx-auto">
              Explore our complete collection of insights and analyses across
              AI, hardware, and gaming.
            </p>
            <div className="mt-8 flex justify-center">
              <Button as={Link} href="/archive/1" variant="primary">
                View all articles <span aria-hidden>→</span>
              </Button>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
