import { notFound } from 'next/navigation';
import type { Metadata } from 'next';
import { getPostBySlug, formatDate } from '@/lib/api';

export const revalidate = 300;

interface PageProps {
  params: Promise<{ slug: string }>;
}

/**
 * Single-post page. Receives the slug from the route and fetches one post.
 *
 * Content is HTML-rendered with `dangerouslySetInnerHTML` because the
 * backend already generates sanitized HTML. If you'd rather parse markdown
 * yourself, swap this for a markdown renderer (marked / react-markdown).
 */
export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const post = await getPostBySlug(slug);
  if (!post) return { title: 'Not found' };
  return {
    title: post.seo_title || post.title,
    description: post.seo_description || post.excerpt || undefined,
    openGraph: {
      title: post.seo_title || post.title,
      description: post.seo_description || post.excerpt || undefined,
      images: post.featured_image_url ? [post.featured_image_url] : undefined,
      type: 'article',
      publishedTime: post.published_at || undefined,
    },
  };
}

export default async function PostPage({ params }: PageProps) {
  const { slug } = await params;
  const post = await getPostBySlug(slug);
  if (!post) notFound();

  return (
    <article className="prose-custom">
      {post.featured_image_url ? (
        <img
          src={post.featured_image_url}
          alt=""
          className="mb-6 w-full rounded-lg object-cover aspect-[16/9]"
        />
      ) : null}
      <h1 className="!mt-0">{post.title}</h1>
      <div className="mt-2 flex items-center gap-3 text-sm text-brand-muted">
        {post.published_at ? (
          <time dateTime={post.published_at}>
            {formatDate(post.published_at)}
          </time>
        ) : null}
        {post.reading_time ? (
          <>
            <span aria-hidden>·</span>
            <span>{post.reading_time} min read</span>
          </>
        ) : null}
        {post.author ? (
          <>
            <span aria-hidden>·</span>
            <span>{post.author}</span>
          </>
        ) : null}
      </div>
      {post.content ? (
        <div
          className="mt-8"
          dangerouslySetInnerHTML={{ __html: post.content }}
        />
      ) : (
        <p className="mt-8 italic text-brand-muted">Content unavailable.</p>
      )}
    </article>
  );
}
