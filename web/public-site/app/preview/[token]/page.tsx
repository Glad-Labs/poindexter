import { Metadata } from 'next';
import Image from 'next/image';
import { notFound } from 'next/navigation';
import sanitizeHtml from 'sanitize-html';

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
    <div className="min-h-screen bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950">
      {/* Preview Banner */}
      <div className="fixed top-0 left-0 right-0 z-50 bg-amber-500 text-black text-center py-2 px-4 font-bold text-sm">
        PREVIEW MODE — This post is not published yet
        <span className="ml-4 text-xs font-normal opacity-75">
          Status: {post.status} | Podcast: {post.has_podcast ? 'Yes' : 'No'} |
          Video: {post.has_video ? 'Yes' : 'No'}
        </span>
      </div>

      <main className="container mx-auto px-4 sm:px-6 lg:px-8 pt-20 pb-16 max-w-4xl">
        {/* Featured Image */}
        {(post.featured_image_url || post.cover_image_url) && (
          <div className="relative w-full aspect-video mb-8 rounded-xl overflow-hidden">
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
        <h1 className="text-3xl md:text-5xl font-bold text-white mb-4 leading-tight">
          {post.title}
        </h1>

        {/* Excerpt */}
        {post.excerpt && (
          <p className="text-lg text-slate-400 mb-8 leading-relaxed">
            {post.excerpt}
          </p>
        )}

        {/* Media Badges */}
        <div className="flex gap-3 mb-8">
          {post.has_podcast && (
            <span className="px-3 py-1 bg-green-500/20 text-green-400 rounded-full text-sm border border-green-500/30">
              Podcast Ready
            </span>
          )}
          {post.has_video && (
            <span className="px-3 py-1 bg-blue-500/20 text-blue-400 rounded-full text-sm border border-blue-500/30">
              Video Ready
            </span>
          )}
          <span className="px-3 py-1 bg-amber-500/20 text-amber-400 rounded-full text-sm border border-amber-500/30">
            {post.status.toUpperCase()}
          </span>
        </div>

        {/* Podcast Player */}
        {post.podcast_url && (
          <div className="mb-8 p-4 bg-slate-800/50 rounded-xl border border-green-500/30">
            <h3 className="text-sm font-mono text-green-400 uppercase tracking-widest mb-3">
              Podcast Episode
            </h3>
            <audio controls className="w-full" preload="metadata">
              <source src={post.podcast_url} type="audio/mpeg" />
              Your browser does not support the audio element.
            </audio>
          </div>
        )}

        {/* Video Player */}
        {post.video_url && (
          <div className="mb-8 p-4 bg-slate-800/50 rounded-xl border border-blue-500/30">
            <h3 className="text-sm font-mono text-blue-400 uppercase tracking-widest mb-3">
              Video Episode
            </h3>
            <video
              controls
              className="w-full rounded-lg"
              preload="metadata"
              playsInline
            >
              <source src={post.video_url} type="video/mp4" />
              Your browser does not support the video element.
            </video>
          </div>
        )}

        {/* Content */}
        <article
          className="prose prose-invert prose-lg max-w-none
            prose-headings:text-white prose-headings:font-bold
            prose-p:text-slate-300 prose-p:leading-relaxed
            prose-a:text-cyan-400 prose-a:no-underline hover:prose-a:text-cyan-300
            prose-strong:text-white
            prose-code:text-cyan-300 prose-code:bg-slate-800 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded
            prose-pre:bg-slate-800/50 prose-pre:border prose-pre:border-slate-700
            prose-img:rounded-xl prose-img:mx-auto
            prose-blockquote:border-cyan-500/50 prose-blockquote:bg-slate-800/30 prose-blockquote:rounded-r-lg"
          dangerouslySetInnerHTML={{ __html: sanitizedContent }}
        />
      </main>
    </div>
  );
}
