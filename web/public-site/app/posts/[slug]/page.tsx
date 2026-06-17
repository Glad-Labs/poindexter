import { cache } from 'react';
import { Metadata } from 'next';
import Image from 'next/image';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { Button, Card, Eyebrow } from '@glad-labs/brand';
import { BreadcrumbSchema } from '../../../components/StructuredData';
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
  postFeaturedImage,
  cleanPostTitle,
  postExcerpt,
  type Post,
} from '../../../lib/posts';
import { SITE_NAME, SITE_URL, ADSENSE_SLOT_ID } from '@/lib/site.config';

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

// Wrapped with React.cache() so generateMetadata + PostPage share one fetch
// per request (issue #521).
const getPost = cache(async function getPost(
  slug: string
): Promise<Post | null> {
  return getPostBySlug(slug);
});

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
    post.featured_image_url || post.cover_image_url || '/og-image.jpg';
  // Audit #2/#5: same display-layer guards as the page body — no "Title:"
  // prefixes or placeholder excerpts in <title>, OG, or Twitter cards.
  const description =
    post.seo_description || postExcerpt(post, 200) || '';
  const title = cleanPostTitle(post.seo_title || post.title);
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
          // Prefer the vision-generated alt (describes the actual image);
          // fall back to the title when absent.
          alt: post.featured_image_alt || title,
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

  const relatedPosts: Post[] = post.category_id
    ? await getRelatedPosts(post.category_id, post.id, 3)
    : [];

  const imageUrl = postFeaturedImage(post);
  const publishDate = post.published_at || post.created_at;

  // Audit #5: one cleaned title, used everywhere the reader (or a crawler)
  // sees it — h1, breadcrumb, JSON-LD, share intents, comments.
  const title = cleanPostTitle(post.title);

  // Hero excerpt: content-derived fallback is intentionally disabled here
  // (content: '') — the article body starts right below, so a derived
  // excerpt would duplicate the opening paragraph. Real excerpts only;
  // placeholder/title-repeat artifacts resolve to null and the element
  // is omitted (audit #2).
  const heroExcerpt = postExcerpt(
    { title: post.title, excerpt: post.excerpt, content: '' },
    300
  );

  const breadcrumbs = [
    { label: 'Home', url: '/' },
    { label: 'Articles', url: '/archive/1' },
    { label: title, url: `/posts/${post.slug}` },
  ];

  // Reading time estimate (avg 238 words/min for technical content)
  const wordCount = post.content.split(/\s+/).length;
  const readingTime = Math.max(1, Math.round(wordCount / 238));

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
      // Use fromCodePoint (not fromCharCode) so astral-plane codepoints
      // (emoji, etc.) round-trip correctly (#1328 item 2).
      .replace(/&#(\d+);/g, (_m, code) => String.fromCodePoint(parseInt(code)))
      .replace(/&#x([0-9a-f]+);/gi, (_m, code) =>
        String.fromCodePoint(parseInt(code, 16))
      );
  }

  // The page renders the post title as the single document <h1>. Demote any
  // <h1> the writer emitted inside the body to <h2> so the heading outline has
  // exactly one h1 (WCAG 1.3.1 / 2.4.6, #978b). Done before TOC extraction so
  // demoted headings still pick up anchor ids.
  const contentDemotedHeadings = post.content.replace(
    /<(\/?)h1(\s[^>]*)?>/gi,
    (_m: string, slash: string, attrs: string) => `<${slash}h2${attrs || ''}>`
  );

  // Inject alt="" on raw <img> tags that ship without one, so screen readers
  // skip them as decorative instead of announcing the bare src/filename
  // (WCAG 1.1.1, #978b). Images that already carry an alt are left untouched.
  const contentWithAlts = contentDemotedHeadings.replace(
    /<img\b([^>]*)>/gi,
    (match: string, attrs: string) =>
      /\balt\s*=/i.test(attrs) ? match : `<img${attrs} alt="">`
  );

  // Dedup counter for heading IDs — if two headings produce the same slug,
  // the second gets a -1 suffix, the third -2, etc. (#1328 item 3).
  const seenHeadingIds: Record<string, number> = {};
  function uniqueHeadingId(id: string): string {
    if (!seenHeadingIds[id]) {
      seenHeadingIds[id] = 0;
    }
    seenHeadingIds[id]++;
    return seenHeadingIds[id] === 1 ? id : `${id}-${seenHeadingIds[id] - 1}`;
  }

  const tocEntries: { level: number; text: string; id: string }[] = [];
  // NOTE (#1328 item 4): the `.*?` capture already works for most headings.
  // Genuine multiline headings (line break inside an <h2>) are extremely
  // rare in pipeline output; the regex handles single-line headings only.
  // Add the `s` flag here if multiline headings ever appear in practice.
  const contentWithIds = contentWithAlts.replace(
    /<h([23])([^>]*)>(.*?)<\/h\1>/gi,
    (_m: string, level: string, attrs: string, text: string) => {
      const plainText = decodeEntities(text.replace(/<[^>]+>/g, '')).trim();
      const baseId = plainText
        .toLowerCase()
        .replace(/[^\w]+/g, '-')
        .slice(0, 60);
      const id = uniqueHeadingId(baseId);
      tocEntries.push({ level: parseInt(level), text: plainText, id });
      return `<h${level}${attrs} id="${id}">${text}</h${level}>`;
    }
  );

  const shareUrl = `${SITE_URL}/posts/${post.slug}`;
  const shareTitle = encodeURIComponent(title);

  const structuredData = generateBlogPostingSchema(
    {
      ...post,
      title,
      coverImage: imageUrl ? { url: imageUrl } : undefined,
      date: publishDate,
    },
    SITE_URL
  );

  return (
    <>
      {/* Own-analytics beacon — fires once on mount, writes to page_views */}
      <ViewTracker slug={post.slug} />
      {/* Schema — single BlogPosting node from generateBlogPostingSchema()
          below (the richer one). The hand-rendered <BlogPostingSchema>
          duplicate was removed to avoid emitting two BlogPosting JSON-LD
          blocks on the same page (#970). */}
      <BreadcrumbSchema items={breadcrumbs} />

      {structuredData && (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify(structuredData),
          }}
        />
      )}

      <div className="gl-atmosphere min-h-screen">
        {/* Hero with featured image */}
        <div className="pt-20 pb-8">
          {imageUrl && (
            <div className="relative w-full h-80 md:h-[460px] overflow-hidden">
              {/* alt="" prevents screen reader re-announcement of the title
                  already in the <h1> below (WCAG 1.1.1). */}
              <Image
                src={imageUrl}
                alt=""
                fill
                priority
                className="object-cover"
                sizes="100vw"
              />
              <div className="absolute inset-0 bg-gradient-to-t from-[var(--gl-base)] via-[var(--gl-base)]/70 to-transparent"></div>
            </div>
          )}

          {/* Title block — overlaps image when present */}
          <div
            className={`px-4 sm:px-6 lg:px-8 ${imageUrl ? '-mt-24 relative z-10' : 'pt-8'}`}
          >
            <div className="container mx-auto max-w-4xl">
              <Eyebrow>GLAD LABS · ARTICLE</Eyebrow>
              <h1 className="mt-2">
                {title}
              </h1>

              {/* Meta row */}
              <div className="gl-mono gl-mono--upper mt-5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                <time dateTime={post.published_at || post.created_at}>
                  {new Date(publishDate).toLocaleDateString('en-US', {
                    year: 'numeric',
                    month: 'long',
                    day: 'numeric',
                  })}
                </time>
                <span aria-hidden>·</span>
                <span>{readingTime} min read</span>
                {post.view_count > 0 && (
                  <>
                    <span aria-hidden>·</span>
                    <span>{post.view_count} views</span>
                  </>
                )}
              </div>

              {/* Excerpt — canonical resolver handles markdown artifacts,
                  placeholder copy, and title-repeats (audit #2/#5) */}
              {heroExcerpt && (
                <p className="gl-body gl-body--lg gl-body--primary mt-6">
                  {heroExcerpt}
                </p>
              )}
            </div>
          </div>
        </div>

        {/* Article body */}
        <div className="px-4 sm:px-6 lg:px-8 pb-16">
          <div className="container mx-auto max-w-4xl">
            {/* Table of Contents */}
            {tocEntries.length >= 3 && (
              <nav className="mb-10" aria-label="Table of contents">
                <Card>
                  <Card.Meta>IN THIS ARTICLE</Card.Meta>
                  <ul className="mt-3 space-y-2 list-none">
                    {tocEntries.map((entry) => (
                      <li
                        key={entry.id}
                        className={entry.level === 3 ? 'ml-4' : ''}
                      >
                        <a
                          href={`#${entry.id}`}
                          className="gl-body gl-body--sm text-[color:var(--gl-cyan)] hover:underline"
                        >
                          {entry.text}
                        </a>
                      </li>
                    ))}
                  </ul>
                </Card>
              </nav>
            )}

            {/* Social Share */}
            <div className="flex items-center gap-3 mb-8 flex-wrap">
              <span className="gl-mono gl-mono--upper text-xs text-[color:var(--gl-text-muted)]">
                Share:
              </span>
              <Button
                as="a"
                href={`https://twitter.com/intent/tweet?text=${shareTitle}&url=${encodeURIComponent(shareUrl)}`}
                target="_blank"
                rel="noopener noreferrer"
                variant="ghost"
                aria-label="Share on X/Twitter"
              >
                X / Twitter
              </Button>
              <Button
                as="a"
                href={`https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(shareUrl)}`}
                target="_blank"
                rel="noopener noreferrer"
                variant="ghost"
                aria-label="Share on LinkedIn"
              >
                LinkedIn
              </Button>
              <Button
                as="a"
                href={`https://news.ycombinator.com/submitlink?u=${encodeURIComponent(shareUrl)}&t=${shareTitle}`}
                target="_blank"
                rel="noopener noreferrer"
                variant="ghost"
                aria-label="Share on Hacker News"
              >
                HN
              </Button>
            </div>

            <article className="gl-prose max-w-none">
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

            {/* AI Disclosure — EU AI Act Article 50 compliance */}
            <div className="gl-log gl-mono--upper mt-10">
              Generated with AI assistance · Reviewed by a human editor ·
              Published by{' '}
              <a href="https://www.gladlabs.io/about" className="hl hover:underline">
                Glad Labs
              </a>
            </div>

            {/* Back Navigation */}
            <div className="mt-8 pt-8 border-t border-[color:var(--gl-hairline)]">
              <Button as={Link} href="/archive/1" variant="ghost">
                ← Back to archive
              </Button>
            </div>
          </div>
        </div>

        {/* Related Posts */}
        {relatedPosts.length > 0 && (
          <div className="px-4 sm:px-6 lg:px-8 pb-12">
            <div className="container mx-auto max-w-5xl">
              <h2 className="gl-h2 mb-6">More from {SITE_NAME}</h2>
              <div className="grid gap-6 md:grid-cols-3">
                {relatedPosts.map((rp) => {
                  const rpImage = postFeaturedImage(rp);
                  return (
                  <Card
                    key={rp.slug}
                    className="group flex flex-col h-full overflow-hidden p-0"
                  >
                    {rpImage && (
                      <div className="relative h-36 overflow-hidden bg-[var(--gl-surface)]">
                        <Image
                          src={rpImage}
                          alt=""
                          fill
                          className="object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                          sizes="(max-width: 768px) 100vw, 33vw"
                        />
                      </div>
                    )}
                    <div className="p-4">
                      <Card.Title>
                        <Link
                          href={`/posts/${rp.slug}`}
                          className="hover:text-[color:var(--gl-cyan)] transition-colors"
                        >
                          {cleanPostTitle(rp.title)}
                        </Link>
                      </Card.Title>
                    </div>
                  </Card>
                  );
                })}
              </div>
            </div>
          </div>
        )}

        {/* AdSense — Bottom of article */}
        <div className="px-4 sm:px-6 lg:px-8 py-8">
          <div className="container mx-auto max-w-4xl">
            <AdUnit slot={ADSENSE_SLOT_ID} format="auto" className="my-4" />
          </div>
        </div>

        {/* Comments */}
        <div className="px-4 sm:px-6 lg:px-8 pb-20">
          <div className="container mx-auto max-w-4xl">
            <GiscusWrapper postSlug={post.slug} postTitle={title} />
          </div>
        </div>
      </div>
    </>
  );
}
