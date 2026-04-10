import { cache } from 'react';
import { Metadata } from 'next';
import Image from 'next/image';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import {
  BlogPostingSchema,
  BreadcrumbSchema,
} from '../../../components/StructuredData';
import { generateBlogPostingSchema } from '../../../lib/structured-data';
import { GiscusWrapper } from '../../../components/GiscusWrapper';
import AdUnit from '../../../components/AdUnit';
import { ViewTracker } from '../../../components/ViewTracker';
import sanitizeHtml from 'sanitize-html';
import {
  buildMetaDescription,
  buildSEOTitle,
  generateCanonicalURL,
} from '../../../lib/seo';
import {
  getPostBySlug,
  getRelatedPosts,
  getAllPublishedPosts,
  type Post,
} from '../../../lib/posts';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';

// #945: Bounded generateStaticParams — pre-generate recent post pages at build time
// for faster first-hit latency and better SEO indexing. Long-tail slugs still
// work via ISR fallback (dynamicParams defaults to true in Next.js 15).
export async function generateStaticParams(): Promise<{ slug: string }[]> {
  try {
    const posts = await getAllPublishedPosts();
    return posts
      .slice(0, 50)
      .filter((p) => p.slug)
      .map((p) => ({ slug: p.slug }));
  } catch {
    return [];
  }
}

// Fetch post data.
// Wrapped with React.cache() so that generateMetadata and PostPage share a
// single fetch result within the same server-side render request (issue #521).
const getPost = cache(async function getPost(
  slug: string
): Promise<Post | null> {
  return getPostBySlug(slug);
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
      title: `Post Not Found | ${SITE_NAME}`,
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
      ? String(post.seo_keywords)
          .split(/[,\s]+/)
          .map((k: string) => k.trim())
          .filter((k: string) => k.length > 0)
      : undefined,
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

  // Fetch related posts from the same category
  const relatedPosts: Post[] = post.category_id
    ? await getRelatedPosts(post.category_id, post.id, 3)
    : [];

  const imageUrl = post.cover_image_url || post.featured_image_url;
  const publishDate = post.published_at || post.created_at;

  const breadcrumbs = [
    { label: 'Home', url: '/' },
    { label: 'Articles', url: '/archive/1' },
    { label: post.title, url: `/posts/${post.slug}` },
  ];

  // Reading time estimate (avg 238 words/min for technical content)
  const wordCount = post.content.split(/\s+/).length;
  const readingTime = Math.max(1, Math.round(wordCount / 238));

  // Decode HTML entities in text (e.g., &rsquo; → ', &mdash; → —)
  function decodeEntities(str: string): string {
    return str
      .replace(/&rsquo;/g, "'")
      .replace(/&lsquo;/g, "'")
      .replace(/&rdquo;/g, '"')
      .replace(/&ldquo;/g, '"')
      .replace(/&mdash;/g, '-')
      .replace(/&ndash;/g, '-')
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&hellip;/g, '...')
      .replace(/&nbsp;/g, ' ')
      .replace(/&#(\d+);/g, (_m, code) => String.fromCharCode(parseInt(code)))
      .replace(/&#x([0-9a-f]+);/gi, (_m, code) =>
        String.fromCharCode(parseInt(code, 16))
      );
  }

  // Extract headings for table of contents
  const headingRegex = /<h([23])[^>]*(?:id="([^"]*)")?[^>]*>(.*?)<\/h\1>/gi;
  const tocEntries: { level: number; text: string; id: string }[] = [];
  let match;
  const contentWithIds = post.content.replace(
    /<h([23])([^>]*)>(.*?)<\/h\1>/gi,
    (_m: string, level: string, attrs: string, text: string) => {
      const plainText = decodeEntities(text.replace(/<[^>]+>/g, '')).trim();
      const id = plainText
        .toLowerCase()
        .replace(/[^\w]+/g, '-')
        .slice(0, 60);
      tocEntries.push({ level: parseInt(level), text: plainText, id });
      return `<h${level}${attrs} id="${id}">${text}</h${level}>`;
    }
  );

  const shareUrl = `${SITE_URL}/posts/${post.slug}`;
  const shareTitle = encodeURIComponent(post.title);

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
                  {new Date(publishDate).toLocaleDateString('en-US', {
                    year: 'numeric',
                    month: 'long',
                    day: 'numeric',
                  })}
                </time>
                <span>•</span>
                <span>{readingTime} min read</span>
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
            {/* Table of Contents */}
            {tocEntries.length >= 3 && (
              <nav
                className="mb-10 p-6 bg-slate-800/50 rounded-xl border border-slate-700"
                aria-label="Table of contents"
              >
                <h2 className="text-lg font-semibold text-white mb-3">
                  In this article
                </h2>
                <ul className="space-y-2">
                  {tocEntries.map((entry) => (
                    <li
                      key={entry.id}
                      className={entry.level === 3 ? 'ml-4' : ''}
                    >
                      <a
                        href={`#${entry.id}`}
                        className="text-cyan-400 hover:text-cyan-300 transition-colors text-sm"
                      >
                        {entry.text}
                      </a>
                    </li>
                  ))}
                </ul>
              </nav>
            )}

            {/* Social Share Buttons */}
            <div className="flex items-center gap-3 mb-8">
              <span className="text-sm text-slate-400">Share:</span>
              <a
                href={`https://twitter.com/intent/tweet?text=${shareTitle}&url=${encodeURIComponent(shareUrl)}`}
                target="_blank"
                rel="noopener noreferrer"
                className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white rounded-lg text-sm transition-colors border border-slate-700"
                aria-label="Share on X/Twitter"
              >
                X / Twitter
              </a>
              <a
                href={`https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(shareUrl)}`}
                target="_blank"
                rel="noopener noreferrer"
                className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white rounded-lg text-sm transition-colors border border-slate-700"
                aria-label="Share on LinkedIn"
              >
                LinkedIn
              </a>
              <a
                href={`https://news.ycombinator.com/submitlink?u=${encodeURIComponent(shareUrl)}&t=${shareTitle}`}
                target="_blank"
                rel="noopener noreferrer"
                className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white rounded-lg text-sm transition-colors border border-slate-700"
                aria-label="Share on Hacker News"
              >
                HN
              </a>
            </div>

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
                       prose-img:rounded-lg prose-img:my-6 prose-img:h-auto prose-img:aspect-auto prose-img:w-full
                       prose-hr:border-slate-700"
            >
              <div
                dangerouslySetInnerHTML={{
                  __html: sanitizeHtml(contentWithIds, {
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

        {/* Related Posts */}
        {relatedPosts.length > 0 && (
          <div className="px-4 sm:px-6 lg:px-8 pb-12">
            <div className="max-w-4xl mx-auto">
              <h2 className="text-2xl font-bold text-white mb-6">
                More from {SITE_NAME}
              </h2>
              <div className="grid gap-6 md:grid-cols-3">
                {relatedPosts.map((rp) => (
                  <Link
                    key={rp.slug}
                    href={`/posts/${rp.slug}`}
                    className="group block bg-slate-800/50 rounded-xl overflow-hidden border border-slate-700 hover:border-cyan-500/50 transition-all"
                  >
                    {(rp.featured_image_url || rp.cover_image_url) && (
                      <div className="relative h-36 overflow-hidden">
                        <Image
                          src={
                            rp.featured_image_url || rp.cover_image_url || ''
                          }
                          alt=""
                          fill
                          className="object-cover group-hover:scale-105 transition-transform duration-300"
                          sizes="(max-width: 768px) 100vw, 33vw"
                        />
                      </div>
                    )}
                    <div className="p-4">
                      <h3 className="text-sm font-semibold text-white group-hover:text-cyan-400 transition-colors line-clamp-2">
                        {rp.title}
                      </h3>
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* View tracking beacon */}
        <ViewTracker slug={post.slug} />

        {/* AdSense — Bottom of article */}
        <div className="px-4 sm:px-6 lg:px-8 py-8">
          <div className="max-w-4xl mx-auto">
            <AdUnit slot="" format="auto" className="my-4" />
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
