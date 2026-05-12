import { cache } from 'react';
import { Metadata } from 'next';
import Image from 'next/image';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { Button, Card, Eyebrow } from '@glad-labs/brand';
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

  const relatedPosts: Post[] = post.category_id
    ? await getRelatedPosts(post.category_id, post.id, 3)
    : [];

  const imageUrl = post.featured_image_url || post.cover_image_url;
  const publishDate = post.published_at || post.created_at;

  const breadcrumbs = [
    { label: 'Home', url: '/' },
    { label: 'Articles', url: '/archive/1' },
    { label: post.title, url: `/posts/${post.slug}` },
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
      .replace(/&#(\d+);/g, (_m, code) => String.fromCharCode(parseInt(code)))
      .replace(/&#x([0-9a-f]+);/gi, (_m, code) =>
        String.fromCharCode(parseInt(code, 16))
      );
  }

  const tocEntries: { level: number; text: string; id: string }[] = [];
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
      {/* Schema */}
      <BlogPostingSchema
        headline={post.seo_title || post.title}
        description={post.seo_description || post.excerpt || ''}
        image={imageUrl || '/og-image.jpg'}
        datePublished={publishDate}
        dateModified={publishDate}
      />
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
              <div className="absolute inset-0 bg-gradient-to-t from-[#08090a] via-[#08090a]/70 to-transparent"></div>
            </div>
          )}

          {/* Title block — overlaps image when present */}
          <div
            className={`px-4 sm:px-6 lg:px-8 ${imageUrl ? '-mt-24 relative z-10' : 'pt-8'}`}
          >
            <div className="container mx-auto max-w-4xl">
              <Eyebrow>GLAD LABS · ARTICLE</Eyebrow>
              <h1
                className="mt-2 font-[family-name:var(--gl-font-display)] font-bold text-white text-3xl sm:text-4xl md:text-5xl leading-[1.05] tracking-[-0.02em]"
                style={{ letterSpacing: '-0.02em' }}
              >
                {post.title}
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

              {/* Excerpt — strip stale markdown artifacts from older posts */}
              {post.excerpt && (
                <p className="gl-body gl-body--lg gl-body--primary mt-6">
                  {post.excerpt
                    .replace(/^\s*[*\-#]+\s*/gm, '')
                    .replace(/\*\*/g, '')
                    .replace(/\n+/g, ' ')
                    .trim()}
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
              <span className="gl-mono gl-mono--upper text-xs opacity-70">
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

            <article
              className="prose prose-invert max-w-none
                       prose-headings:font-[family-name:var(--gl-font-display)]
                       prose-headings:font-bold
                       prose-h1:text-4xl prose-h1:text-white prose-h1:mt-8 prose-h1:mb-4
                       prose-h2:text-3xl prose-h2:text-white prose-h2:mt-10 prose-h2:mb-4
                       prose-h2:tracking-tight
                       prose-h3:text-2xl prose-h3:text-white prose-h3:mt-6 prose-h3:mb-3
                       prose-p:text-[color:var(--gl-text-muted)] prose-p:leading-relaxed prose-p:mb-6
                       prose-strong:text-white prose-strong:font-semibold
                       prose-a:text-[color:var(--gl-cyan)] prose-a:hover:text-[color:var(--gl-cyan)] prose-a:underline
                       prose-code:text-[color:var(--gl-cyan)] prose-code:bg-[color:var(--gl-hairline)] prose-code:px-2 prose-code:py-1 prose-code:rounded-none
                       prose-pre:bg-[color:var(--gl-hairline)] prose-pre:border prose-pre:border-[color:var(--gl-hairline-strong)] prose-pre:rounded-none
                       prose-blockquote:border-l-4 prose-blockquote:border-[color:var(--gl-cyan)] prose-blockquote:pl-4 prose-blockquote:text-[color:var(--gl-text-muted)] prose-blockquote:not-italic
                       prose-ul:text-[color:var(--gl-text-muted)] prose-ol:text-[color:var(--gl-text-muted)]
                       prose-li:text-[color:var(--gl-text-muted)] prose-li:marker:text-[color:var(--gl-cyan)]
                       prose-img:rounded-none prose-img:my-6 prose-img:h-auto prose-img:aspect-auto prose-img:w-full
                       prose-hr:border-[color:var(--gl-hairline-strong)]"
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

            {/* AI Disclosure — EU AI Act Article 50 compliance */}
            <div className="mt-10 border-l-2 border-[color:var(--gl-hairline-strong)] pl-4 py-2 gl-mono gl-mono--upper text-xs opacity-70">
              Generated with AI assistance · Reviewed by a human editor ·
              Published by{' '}
              <a
                href="https://www.gladlabs.io/about"
                className="text-[color:var(--gl-cyan)] hover:underline"
              >
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
                {relatedPosts.map((rp) => (
                  <Card
                    key={rp.slug}
                    className="group flex flex-col h-full overflow-hidden p-0"
                  >
                    {(rp.featured_image_url || rp.cover_image_url) && (
                      <div className="relative h-36 overflow-hidden bg-slate-800">
                        <Image
                          src={
                            rp.featured_image_url || rp.cover_image_url || ''
                          }
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
                          {rp.title}
                        </Link>
                      </Card.Title>
                    </div>
                  </Card>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* AdSense — Bottom of article */}
        <div className="px-4 sm:px-6 lg:px-8 py-8">
          <div className="container mx-auto max-w-4xl">
            <AdUnit slot="" format="auto" className="my-4" />
          </div>
        </div>

        {/* Comments */}
        <div className="px-4 sm:px-6 lg:px-8 pb-20">
          <div className="container mx-auto max-w-4xl">
            <GiscusWrapper postSlug={post.slug} postTitle={post.title} />
          </div>
        </div>
      </div>
    </>
  );
}
