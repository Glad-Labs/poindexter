import { cache } from 'react';
import { Metadata } from 'next';
import Image from 'next/image';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import * as Sentry from '@sentry/nextjs';
import {
  BlogPostingSchema,
  BreadcrumbSchema,
} from '../../../components/StructuredData';
import { generateBlogPostingSchema } from '../../../lib/structured-data';
import { GiscusWrapper } from '../../../components/GiscusWrapper';
import AdUnit from '../../../components/AdUnit';
import sanitizeHtml from 'sanitize-html';
import {
  buildMetaDescription,
  buildSEOTitle,
  generateCanonicalURL,
} from '../../../lib/seo';

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_FASTAPI_URL ||
  'http://localhost:8000';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://glad-labs.com';

// #945: Bounded generateStaticParams — pre-generate recent post pages at build time
// for faster first-hit latency and better SEO indexing. Long-tail slugs still
// work via ISR fallback (dynamicParams defaults to true in Next.js 15).
export async function generateStaticParams(): Promise<{ slug: string }[]> {
  try {
    const response = await fetch(
      `${API_BASE}/api/posts?offset=0&limit=50&published_only=true`,
      { next: { revalidate: 3600 } }
    );
    if (!response.ok) return [];
    const data = await response.json();
    const posts = Array.isArray(data?.posts)
      ? data.posts
      : Array.isArray(data?.data)
        ? data.data
        : [];
    return posts
      .filter((p: { slug?: string }) => p.slug)
      .map((p: { slug: string }) => ({ slug: p.slug }));
  } catch (error) {
    Sentry.captureException(error);
    return [];
  }
}

interface Post {
  id: string;
  title: string;
  slug: string;
  content: string;
  excerpt?: string;
  featured_image_url?: string;
  cover_image_url?: string;
  author_id?: string;
  category_id?: string;
  seo_title?: string;
  seo_description?: string;
  seo_keywords?: string;
  published_at?: string;
  created_at: string;
  view_count: number;
}

// Fetch post data.
// Wrapped with React.cache() so that generateMetadata and PostPage share a
// single fetch result within the same server-side render request (issue #521).
// The underlying fetch still uses ISR revalidation (next: { revalidate: 86400 })
// for cross-request caching at the Next.js layer.
const getPost = cache(async function getPost(
  slug: string
): Promise<Post | null> {
  try {
    // Use direct endpoint for single post by slug (much faster than fetching all posts)
    const response = await fetch(`${API_BASE}/api/posts/${slug}`, {
      next: { revalidate: 3600 }, // ISR: revalidate every 1 hour (matches homepage) + on-demand revalidation via publish webhook
    });

    if (!response.ok) {
      if (response.status === 404) {
        return null;
      }
      // Non-404 API errors reported to Sentry so backend outages are visible
      Sentry.captureMessage(
        `Failed to fetch post "${slug}": HTTP ${response.status}`,
        'error'
      );
      return null;
    }

    const data = await response.json();
    const post = data.data || data;

    return post || null;
  } catch (error) {
    // Network/timeout errors reported to Sentry
    Sentry.captureException(error, { extra: { slug } });
    return null;
  }
});

// Generate metadata for the post
export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const post = await getPost(slug);

  if (!post) {
    return {
      title: 'Post Not Found | Glad Labs',
      description: 'The article you are looking for does not exist.',
    };
  }

  const imageUrl =
    post.cover_image_url || post.featured_image_url || '/og-image.jpg';
  const description = post.seo_description || post.excerpt || '';
  const title = post.seo_title || post.title;
  const canonicalUrl = generateCanonicalURL(post.slug, SITE_URL);
  const publishDate = post.published_at || post.created_at;

  return {
    title: buildSEOTitle(title),
    description: buildMetaDescription(description),
    keywords: post.seo_keywords
      ? post.seo_keywords.split(',').map((k) => k.trim())
      : [],
    alternates: {
      canonical: canonicalUrl,
    },
    openGraph: {
      type: 'article',
      url: canonicalUrl,
      title: title,
      description: buildMetaDescription(description),
      images: [
        {
          url: imageUrl,
          width: 1200,
          height: 630,
          alt: post.title,
        },
      ],
      publishedTime: publishDate,
      modifiedTime: publishDate,
    },
    twitter: {
      card: 'summary_large_image',
      title: title,
      description: buildMetaDescription(description),
      images: [imageUrl],
      creator: '@GladLabsAI',
    },
    robots: {
      index: true,
      follow: true,
      'max-snippet': -1,
      'max-image-preview': 'large',
      'max-video-preview': -1,
    },
  };
}

export default async function PostPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const post = await getPost(slug);

  if (!post) {
    notFound();
  }

  const imageUrl = post.cover_image_url || post.featured_image_url;
  const publishDate = post.published_at || post.created_at;

  const breadcrumbs = [
    { label: 'Home', url: '/' },
    { label: 'Articles', url: '/archive/1' },
    { label: post.title, url: `/posts/${post.slug}` },
  ];

  const canonicalUrl = generateCanonicalURL(post.slug, SITE_URL);
  const structuredData = generateBlogPostingSchema(
    {
      ...post,
      coverImage: imageUrl ? { url: imageUrl } : undefined,
      date: publishDate,
    },
    SITE_URL
  );

  return (
    <>
      {/* Schema Markup Components */}
      <BlogPostingSchema
        headline={post.seo_title || post.title}
        description={post.seo_description || post.excerpt || ''}
        image={imageUrl || '/og-image.jpg'}
        datePublished={publishDate}
        dateModified={publishDate}
      />
      <BreadcrumbSchema items={breadcrumbs} />

      {/* JSON-LD Structured Data */}
      {structuredData && (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify(structuredData),
          }}
        />
      )}

      <div className="min-h-screen bg-gradient-to-b from-slate-900 via-slate-800 to-slate-900">
        {/* Header with Featured Image */}
        <div className="pt-20 pb-12">
          {imageUrl && (
            <div className="relative w-full h-96 md:h-[500px] overflow-hidden">
              {/* Decorative featured image — alt="" prevents screen reader re-announcement
                  of the title already in the <h1> immediately below (WCAG 1.1.1). */}
              <Image
                src={imageUrl}
                alt=""
                fill
                priority
                className="object-cover"
                sizes="100vw"
              />
              <div className="absolute inset-0 bg-gradient-to-t from-slate-900 via-slate-900/50 to-transparent"></div>
            </div>
          )}

          {/* Title Section */}
          <div
            className={`px-4 sm:px-6 lg:px-8 ${imageUrl ? '-mt-24 relative z-10' : 'pt-12'}`}
          >
            <div className="max-w-4xl mx-auto">
              <h1 className="text-4xl md:text-5xl font-bold text-white mb-4 leading-tight">
                {post.title}
              </h1>

              {/* Meta Information */}
              <div className="flex flex-wrap items-center gap-4 text-slate-300 mb-8">
                <time dateTime={post.published_at || post.created_at}>
                  {publishDate}
                </time>
                {post.view_count > 0 && (
                  <>
                    <span>•</span>
                    <span>{post.view_count} views</span>
                  </>
                )}
              </div>

              {/* Excerpt */}
              {post.excerpt && (
                <p className="text-xl text-slate-300 mb-8 leading-relaxed">
                  {post.excerpt}
                </p>
              )}
            </div>
          </div>
        </div>

        {/* Article Content */}
        <div className="px-4 sm:px-6 lg:px-8 pb-20">
          <div className="max-w-4xl mx-auto">
            <article
              className="prose prose-invert max-w-none
                       prose-headings:font-bold
                       prose-h1:text-4xl prose-h1:text-white prose-h1:mt-8 prose-h1:mb-4
                       prose-h2:text-3xl prose-h2:text-cyan-400 prose-h2:mt-8 prose-h2:mb-4
                       prose-h3:text-2xl prose-h3:text-blue-400 prose-h3:mt-6 prose-h3:mb-3
                       prose-p:text-slate-300 prose-p:leading-relaxed prose-p:mb-6
                       prose-strong:text-white prose-strong:font-semibold
                       prose-a:text-cyan-400 prose-a:hover:text-cyan-300 prose-a:underline
                       prose-code:text-cyan-300 prose-code:bg-slate-800 prose-code:px-2 prose-code:py-1 prose-code:rounded
                       prose-pre:bg-slate-800 prose-pre:border prose-pre:border-slate-700
                       prose-blockquote:border-l-4 prose-blockquote:border-cyan-400 prose-blockquote:pl-4 prose-blockquote:text-slate-400 prose-blockquote:not-italic
                       prose-ul:text-slate-300 prose-ol:text-slate-300
                       prose-li:text-slate-300 prose-li:marker:text-cyan-400
                       prose-img:rounded-lg prose-img:my-6
                       prose-hr:border-slate-700"
            >
              <div
                dangerouslySetInnerHTML={{
                  __html: sanitizeHtml(post.content, {
                    allowedTags: sanitizeHtml.defaults.allowedTags.concat([
                      'img',
                      'h1',
                      'h2',
                      'details',
                      'summary',
                      'figure',
                      'figcaption',
                    ]),
                    allowedAttributes: {
                      ...sanitizeHtml.defaults.allowedAttributes,
                      img: [
                        'src',
                        'alt',
                        'title',
                        'width',
                        'height',
                        'loading',
                      ],
                      a: ['href', 'name', 'target', 'rel'],
                      '*': ['class', 'id'],
                    },
                  }),
                }}
              />
            </article>

            {/* Bottom Navigation */}
            <div className="mt-12 pt-8 border-t border-slate-700">
              <Link
                href="/archive/1"
                className="inline-flex items-center gap-2 text-cyan-400 hover:text-cyan-300 font-semibold transition-colors"
              >
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
                    d="M15 19l-7-7 7-7"
                  />
                </svg>
                Back to Archive
              </Link>
            </div>
          </div>
        </div>

        {/* AdSense — Bottom of article */}
        <div className="px-4 sm:px-6 lg:px-8 pb-12">
          <div className="max-w-4xl mx-auto">
            <AdUnit
              slot={process.env.NEXT_PUBLIC_ADSENSE_SLOT_ID || ''}
              format="horizontal"
              className="my-4"
            />
          </div>
        </div>

        {/* Comments Section */}
        <div className="px-4 sm:px-6 lg:px-8 pb-20 bg-slate-800/30">
          <div className="max-w-4xl mx-auto">
            <GiscusWrapper postSlug={post.slug} postTitle={post.title} />
          </div>
        </div>
      </div>
    </>
  );
}
