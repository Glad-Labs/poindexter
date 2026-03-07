import logger from '@/lib/logger';
import { Metadata } from 'next';
import Image from 'next/image';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import {
  BlogPostingSchema,
  BreadcrumbSchema,
} from '../../../components/StructuredData';
import { generateBlogPostingSchema } from '../../../lib/structured-data';
import { AuthorCard } from '../../../components/AuthorCard';
import { GiscusWrapper } from '../../../components/GiscusWrapper';
import { PostMetadata } from '../../../components/PostMetadata';
import { PostNavigation } from '../../../components/PostNavigation';
import { ShareButtons } from '../../../components/ShareButtons';
import { TableOfContents } from '../../../components/TableOfContents';
import {
  buildMetaDescription,
  buildSEOTitle,
  generateCanonicalURL,
} from '../../../lib/seo';
import { generateTableOfContents } from '../../../lib/content-utils';
import {
  getPreviousPost,
  getNextPost,
  getRelatedPosts,
} from '../../../lib/posts';

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_FASTAPI_URL ||
  'http://localhost:8000';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://glad-labs.com';

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

// Fetch post data
async function getPost(slug: string): Promise<Post | null> {
  try {
    // Use direct endpoint for single post by slug (much faster than fetching all posts)
    const response = await fetch(`${API_BASE}/api/posts/${slug}`, {
      next: { revalidate: 86400 }, // ISR: revalidate every 24 hours - on-demand revalidation via webhook triggers updates
    });

    if (!response.ok) {
      if (response.status === 404) {
        return null;
      }
      logger.error(`Failed to fetch post: ${response.status}`);
      return null;
    }

    const data = await response.json();
    const post = data.data || data;

    return post || null;
  } catch (error) {
    logger.error(`Error fetching post "${slug}":`, error);
    return null;
  }
}

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

  // Fetch previous and next posts for navigation
  const [previousPost, nextPost] = await Promise.all([
    getPreviousPost(slug),
    getNextPost(slug),
  ]);

  // Fetch related posts (by category if available)
  let relatedPosts: Post[] = [];
  if (post.category_id) {
    relatedPosts = await getRelatedPosts(post.category_id, post.id, 3);
  }

  // Generate table of contents from post content
  const toc = generateTableOfContents(post.content);

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
              <Image
                src={imageUrl}
                alt={post.title}
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
              <PostMetadata
                publishedAt={post.published_at}
                createdAt={post.created_at}
                content={post.content}
                viewCount={post.view_count}
              />
              <div className="mb-4"></div>

              {/* Share Buttons */}
              <ShareButtons
                title={post.seo_title || post.title}
                description={post.seo_description || post.excerpt}
                slug={post.slug}
                siteUrl={SITE_URL}
              />
              <div className="mb-8"></div>

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
            {toc && <TableOfContents headings={toc} />}
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
                  __html: post.content,
                }}
              />
            </article>

            {/* Author Card */}
            <AuthorCard authorId={post.author_id} authorName={post.title} />

            {/* Bottom Navigation */}
            <PostNavigation previousPost={previousPost} nextPost={nextPost} />
          </div>
        </div>

        {/* AdSense Placeholder */}
        <div className="px-4 sm:px-6 lg:px-8 pb-12">
          <div className="max-w-4xl mx-auto bg-slate-800/50 border border-slate-700 rounded-lg p-8 text-center">
            <p className="text-slate-400 text-sm">Advertisement</p>
          </div>
        </div>

        {/* Related Posts */}
        {relatedPosts && relatedPosts.length > 0 && (
          <div className="px-4 sm:px-6 lg:px-8 pb-16">
            <div className="max-w-4xl mx-auto">
              <h2 className="text-3xl font-bold text-white mb-8">
                Related Articles
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {relatedPosts.map((relatedPost) => (
                  <Link
                    key={relatedPost.id}
                    href={`/posts/${relatedPost.slug}`}
                    className="group p-4 rounded-lg border border-slate-700 hover:border-cyan-400/50 transition-all duration-300 hover:shadow-lg hover:shadow-cyan-400/10"
                  >
                    <h3 className="text-lg font-semibold text-cyan-400 group-hover:text-cyan-300 transition-colors line-clamp-2 mb-2">
                      {relatedPost.title}
                    </h3>
                    {relatedPost.published_at && (
                      <div className="text-xs text-slate-400">
                        {new Date(relatedPost.published_at).toLocaleDateString(
                          'en-US',
                          {
                            year: 'numeric',
                            month: 'short',
                            day: 'numeric',
                          }
                        )}
                      </div>
                    )}
                  </Link>
                ))}
              </div>
            </div>
          </div>
        )}

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
