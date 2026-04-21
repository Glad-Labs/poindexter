import { Metadata } from 'next';
import Image from 'next/image';
import { notFound } from 'next/navigation';
import sanitizeHtml from 'sanitize-html';
import { Eyebrow } from '@glad-labs/brand';

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_FASTAPI_URL ||
  'http://localhost:8000';

interface PreviewPost {
  id: string;
  title: string;
  slug: string;
  content: string;
  excerpt?: string;
  featured_image_url?: string;
  cover_image_url?: string;
  seo_description?: string;
  status: string;
  has_podcast?: boolean;
  has_video?: boolean;
  podcast_url?: string;
  video_url?: string;
  is_preview: boolean;
  created_at: string;
}

async function getPreviewPost(token: string): Promise<PreviewPost | null> {
  try {
    const response = await fetch(`${API_BASE}/api/posts/preview/${token}`, {
      cache: 'no-store',
    });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ token: string }>;
}): Promise<Metadata> {
  const { token } = await params;
  const post = await getPreviewPost(token);
  return {
    title: post ? `[PREVIEW] ${post.title}` : 'Preview Not Found',
    robots: { index: false, follow: false },
  };
}

export default async function PreviewPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  const post = await getPreviewPost(token);

  if (!post) {
    notFound();
  }

  const sanitizedContent = sanitizeHtml(post.content || '', {
    allowedTags: sanitizeHtml.defaults.allowedTags.concat([
      'img',
      'h1',
      'h2',
      'h3',
      'h4',
      'h5',
      'h6',
      'pre',
      'code',
    ]),
    allowedAttributes: {
      ...sanitizeHtml.defaults.allowedAttributes,
      img: ['src', 'alt', 'width', 'height', 'loading'],
      code: ['class'],
      pre: ['class'],
    },
  });

  return (
    <div className="gl-atmosphere min-h-screen">
      {/* Preview banner — amber, never hidden, informs operator this is draft */}
      <div
        className="fixed top-0 left-0 right-0 z-50 gl-mono gl-mono--upper text-center py-2 px-4 flex flex-wrap items-center justify-center gap-x-3 gap-y-1"
        style={{
          background: 'var(--gl-amber)',
          color: '#0a0a0a',
          fontSize: '0.75rem',
          letterSpacing: 'var(--gl-tracking-wide)',
        }}
        role="status"
        aria-live="polite"
      >
        <span aria-hidden>⚠</span>
        <span>PREVIEW MODE · NOT PUBLISHED</span>
        <span className="opacity-70">
          Status: {post.status} · Podcast: {post.has_podcast ? 'Yes' : 'No'} ·
          Video: {post.has_video ? 'Yes' : 'No'}
        </span>
      </div>

      <main className="container mx-auto px-4 sm:px-6 lg:px-8 pt-20 pb-16 max-w-4xl">
        {/* Featured image */}
        {(post.featured_image_url || post.cover_image_url) && (
          <div className="relative w-full aspect-video mb-8 overflow-hidden bg-slate-800">
            <Image
              src={post.featured_image_url || post.cover_image_url || ''}
              alt={post.title}
              fill
              className="object-cover"
              sizes="(max-width: 1024px) 100vw, 1024px"
              priority
            />
          </div>
        )}

        {/* Title */}
        <Eyebrow>GLAD LABS · PREVIEW</Eyebrow>
        <h1
          className="mt-2 font-[family-name:var(--gl-font-display)] font-bold text-white text-3xl sm:text-4xl md:text-5xl leading-[1.05] tracking-[-0.02em]"
        >
          {post.title}
        </h1>

        {/* Excerpt */}
        {post.excerpt && (
          <p className="gl-body gl-body--lg gl-body--primary mt-6">
            {post.excerpt}
          </p>
        )}

        {/* Media badges — glyph + mono label, colorblind-safe */}
        <div className="flex flex-wrap gap-2 mt-6">
          {post.has_podcast && (
            <span
              className="gl-mono gl-mono--upper gl-mono--mint px-3 py-1 inline-flex items-center gap-2"
              style={{
                background: 'var(--gl-surface)',
                border: '1px solid var(--gl-hairline)',
                fontSize: '0.6875rem',
              }}
            >
              <span aria-hidden>✓</span> PODCAST READY
            </span>
          )}
          {post.has_video && (
            <span
              className="gl-mono gl-mono--upper gl-mono--accent px-3 py-1 inline-flex items-center gap-2"
              style={{
                background: 'var(--gl-surface)',
                border: '1px solid var(--gl-hairline)',
                fontSize: '0.6875rem',
              }}
            >
              <span aria-hidden>✓</span> VIDEO READY
            </span>
          )}
          <span
            className="gl-mono gl-mono--upper gl-mono--amber px-3 py-1 inline-flex items-center gap-2"
            style={{
              background: 'var(--gl-surface)',
              border: '1px solid var(--gl-hairline)',
              fontSize: '0.6875rem',
            }}
          >
            <span aria-hidden>⚠</span> {post.status.toUpperCase()}
          </span>
        </div>

        {/* Podcast player */}
        {post.podcast_url && (
          <div
            className="gl-tick-left gl-tick-left--mint mt-8 p-4"
            style={{
              background: 'var(--gl-surface)',
              border: '1px solid var(--gl-hairline)',
            }}
          >
            <p className="gl-mono gl-mono--upper gl-mono--mint text-xs mb-3">
              PODCAST EPISODE
            </p>
            <audio controls className="w-full" preload="metadata">
              <source src={post.podcast_url} type="audio/mpeg" />
              Your browser does not support the audio element.
            </audio>
          </div>
        )}

        {/* Video player */}
        {post.video_url && (
          <div
            className="gl-tick-left mt-8 p-4"
            style={{
              background: 'var(--gl-surface)',
              border: '1px solid var(--gl-hairline)',
            }}
          >
            <p className="gl-mono gl-mono--upper gl-mono--accent text-xs mb-3">
              VIDEO EPISODE
            </p>
            <video
              controls
              className="w-full"
              preload="metadata"
              playsInline
            >
              <source src={post.video_url} type="video/mp4" />
              Your browser does not support the video element.
            </video>
          </div>
        )}

        {/* Article content — brand-tokenized prose (same as /posts/[slug]) */}
        <article
          className="prose prose-invert max-w-none mt-8
            prose-headings:font-[family-name:var(--gl-font-display)]
            prose-headings:font-bold
            prose-h1:text-4xl prose-h1:text-white prose-h1:mt-8 prose-h1:mb-4
            prose-h2:text-3xl prose-h2:text-white prose-h2:mt-10 prose-h2:mb-4
            prose-h2:tracking-tight
            prose-h3:text-2xl prose-h3:text-white prose-h3:mt-6 prose-h3:mb-3
            prose-p:text-[color:var(--gl-text-muted)] prose-p:leading-relaxed prose-p:mb-6
            prose-strong:text-white prose-strong:font-semibold
            prose-a:text-[color:var(--gl-cyan)] prose-a:hover:opacity-80 prose-a:underline
            prose-code:text-[color:var(--gl-cyan)] prose-code:bg-[color:var(--gl-hairline)] prose-code:px-2 prose-code:py-1 prose-code:rounded-none
            prose-pre:bg-[color:var(--gl-hairline)] prose-pre:border prose-pre:border-[color:var(--gl-hairline-strong)] prose-pre:rounded-none
            prose-blockquote:border-l-[3px] prose-blockquote:border-[color:var(--gl-cyan)] prose-blockquote:pl-4 prose-blockquote:text-[color:var(--gl-text-muted)] prose-blockquote:not-italic
            prose-ul:text-[color:var(--gl-text-muted)] prose-ol:text-[color:var(--gl-text-muted)]
            prose-li:text-[color:var(--gl-text-muted)] prose-li:marker:text-[color:var(--gl-cyan)]
            prose-img:rounded-none prose-img:my-6 prose-img:h-auto prose-img:aspect-auto prose-img:w-full
            prose-hr:border-[color:var(--gl-hairline-strong)]"
          dangerouslySetInnerHTML={{ __html: sanitizedContent }}
        />
      </main>
    </div>
  );
}
