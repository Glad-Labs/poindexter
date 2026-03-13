import Link from 'next/link';
import { Post } from '../lib/posts';

interface PostNavigationProps {
  previousPost: Post | null;
  nextPost: Post | null;
}

export function PostNavigation({
  previousPost,
  nextPost,
}: PostNavigationProps) {
  // If no navigation posts available, don't render
  if (!previousPost && !nextPost) {
    return null;
  }

  return (
    <nav className="mt-16 pt-8 border-t border-slate-700">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        {/* Previous Post */}
        {previousPost ? (
          <Link
            href={`/posts/${previousPost.slug}`}
            className="group p-4 rounded-lg border border-slate-700 hover:border-cyan-400/50 transition-all duration-300 hover:shadow-lg hover:shadow-cyan-400/10"
          >
            <div className="text-xs uppercase text-slate-500 font-semibold mb-2">
              <span aria-hidden="true">← </span>Previous Article
            </div>
            <h3 className="text-lg font-semibold text-cyan-400 group-hover:text-cyan-300 transition-colors line-clamp-2">
              {previousPost.title}
            </h3>
            {previousPost.published_at && (
              <div className="text-xs text-slate-400 mt-2">
                {new Date(previousPost.published_at).toLocaleDateString(
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
        ) : (
          <div />
        )}

        {/* Next Post */}
        {nextPost ? (
          <Link
            href={`/posts/${nextPost.slug}`}
            className="group p-4 rounded-lg border border-slate-700 hover:border-cyan-400/50 transition-all duration-300 hover:shadow-lg hover:shadow-cyan-400/10 text-right md:text-left"
          >
            <div className="text-xs uppercase text-slate-500 font-semibold mb-2">
              Next Article<span aria-hidden="true"> →</span>
            </div>
            <h3 className="text-lg font-semibold text-cyan-400 group-hover:text-cyan-300 transition-colors line-clamp-2">
              {nextPost.title}
            </h3>
            {nextPost.published_at && (
              <div className="text-xs text-slate-400 mt-2">
                {new Date(nextPost.published_at).toLocaleDateString('en-US', {
                  year: 'numeric',
                  month: 'short',
                  day: 'numeric',
                })}
              </div>
            )}
          </Link>
        ) : (
          <div />
        )}
      </div>
    </nav>
  );
}
