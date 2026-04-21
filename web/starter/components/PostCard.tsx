import Link from 'next/link';
import type { Post } from '@/lib/api';
import { formatDate } from '@/lib/api';

/**
 * One row in the post list. Fork-users: restyle the image, add tag
 * chips, add reading-time badges, etc. Keep the `<Link>` wrapping so
 * Next.js prefetches on hover.
 */
export function PostCard({ post }: { post: Post }) {
  return (
    <Link
      href={`/posts/${post.slug}`}
      className="group block rounded-lg border border-gray-200 bg-white p-5 transition hover:border-brand hover:shadow-sm"
    >
      {post.featured_image_url ? (
        <div className="-m-5 mb-4 overflow-hidden rounded-t-lg bg-gray-100">
          {/* Using a plain <img> keeps the starter dependency-free. Swap to
              next/image if you want Next's automatic optimization. */}
          <img
            src={post.featured_image_url}
            alt=""
            className="aspect-[16/9] w-full object-cover transition group-hover:scale-[1.02]"
            loading="lazy"
          />
        </div>
      ) : null}
      <h2 className="text-xl font-semibold tracking-tight text-brand group-hover:text-brand-accent">
        {post.title}
      </h2>
      {post.excerpt ? (
        <p className="mt-2 line-clamp-3 text-sm text-brand-muted">
          {post.excerpt}
        </p>
      ) : null}
      <div className="mt-3 flex items-center gap-2 text-xs text-brand-muted">
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
      </div>
    </Link>
  );
}
