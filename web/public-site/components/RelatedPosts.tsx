import Link from 'next/link';
import Image from 'next/image';
import { formatDate } from '../lib/content-utils';

/**
 * Simple markdown text renderer for excerpts
 * Handles: **bold**, *italic*, ***bold italic***
 */
const MarkdownText = ({ text }: { text?: string }) => {
  if (!text) return null;

  // Split text by markdown patterns while preserving the markdown markers
  const parts = text.split(
    /(\*\*\*[\s\S]*?\*\*\*|\*\*[\s\S]*?\*\*|\*[\s\S]*?\*)/
  );

  return parts.map((part, index) => {
    if (!part) return null;

    // Bold italic: ***text***
    if (part.startsWith('***') && part.endsWith('***')) {
      return (
        <strong key={index} className="italic font-semibold text-cyan-600">
          {part.slice(3, -3)}
        </strong>
      );
    }

    // Bold: **text**
    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <strong key={index} className="font-semibold text-gray-900">
          {part.slice(2, -2)}
        </strong>
      );
    }

    // Italic: *text*
    if (part.startsWith('*') && part.endsWith('*')) {
      return (
        <em key={index} className="italic text-gray-700">
          {part.slice(1, -1)}
        </em>
      );
    }

    // Regular text
    return <span key={index}>{part}</span>;
  });
};

interface PostImage {
  data?: {
    attributes?: { url?: string };
  };
}

interface PostCategory {
  data?: {
    attributes?: { slug?: string; name?: string };
  };
}

interface Post {
  id: string | number;
  title: string;
  excerpt?: string;
  slug: string;
  publishedAt?: string;
  coverImage?: PostImage;
  category?: PostCategory;
  tags?: unknown[];
}

interface RelatedPostsProps {
  posts?: Post[];
  onPostClick?: ((post: Post) => void) | null;
}

/**
 * Related Posts Component
 * Displays a grid of related articles at the bottom of article pages
 */
export default function RelatedPosts({
  posts = [],
  onPostClick = null,
}: RelatedPostsProps) {
  if (!posts || posts.length === 0) {
    return null;
  }

  const handlePostClick = (post: Post) => {
    if (onPostClick) {
      onPostClick(post);
    }
  };

  return (
    <section
      className="mt-16 pt-12 border-t border-gray-200"
      aria-labelledby="related-posts-heading"
      role="region"
    >
      <h2
        id="related-posts-heading"
        className="text-3xl font-bold text-gray-900 mb-8"
      >
        Related Articles
      </h2>

      <div
        className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"
        role="list"
      >
        {posts.map((post) => (
          <div key={post.id} role="listitem">
            <RelatedPostCard post={post} onPostClick={handlePostClick} />
          </div>
        ))}
      </div>
    </section>
  );
}

interface RelatedPostCardProps {
  post: Post;
  onPostClick?: ((post: Post) => void) | null;
}

/**
 * Individual related post card
 */
function RelatedPostCard({ post, onPostClick = null }: RelatedPostCardProps) {
  const { title, excerpt, slug, publishedAt, coverImage, category, tags } =
    post;

  const imageUrl = coverImage?.data?.attributes?.url || null;

  const displayDate = publishedAt ? formatDate(publishedAt) : null;

  const handleClick = () => {
    if (onPostClick) {
      onPostClick(post);
    }
  };

  return (
    <article
      className="group h-full bg-white rounded-lg shadow-md hover:shadow-lg focus-within:ring-2 focus-within:ring-cyan-500 transition-all duration-300 overflow-hidden"
      aria-labelledby={`post-title-${slug}`}
    >
      <Link
        href={`/posts/${slug}`}
        onClick={handleClick}
        className="block h-full focus:outline-none"
      >
        {/* Image Container */}
        {imageUrl && (
          <div className="relative h-40 w-full overflow-hidden bg-gray-200">
            <Image
              src={imageUrl}
              alt={`Cover image for: ${title}`}
              fill
              className="object-cover group-hover:scale-110 transition-transform duration-300"
              sizes="(max-width: 768px) 100vw, (max-width: 1200px) 50vw, 33vw"
            />
          </div>
        )}

        {/* Content Container */}
        <div className="p-4 flex flex-col h-full">
          {/* Category Badge */}
          {category?.data?.attributes?.slug && (
            <div className="mb-2">
              <span
                className="inline-block text-xs font-medium px-2 py-1 rounded bg-cyan-100 text-cyan-700"
                aria-label={`Category: ${category.data.attributes.name}`}
              >
                {category.data.attributes.name}
              </span>
            </div>
          )}

          {/* Title */}
          <h3
            id={`post-title-${slug}`}
            className="text-lg font-bold text-gray-900 mb-2 line-clamp-2 group-hover:text-cyan-600 group-focus-visible:text-cyan-600 transition-colors"
          >
            {title}
          </h3>

          {/* Excerpt */}
          <p className="text-sm text-gray-600 mb-3 line-clamp-2 flex-grow">
            <MarkdownText text={excerpt} />
          </p>

          {/* Meta Information */}
          <div className="flex items-center justify-between text-xs text-gray-500 mt-auto pt-3 border-t border-gray-100">
            {displayDate && publishedAt && (
              <time
                dateTime={publishedAt.split('T')[0]}
                className="font-medium"
              >
                {displayDate}
              </time>
            )}

            {/* Tag Count */}
            {tags && tags.length > 0 && (
              <span
                aria-label={`${tags.length} tag${tags.length !== 1 ? 's' : ''}`}
              >
                {tags.length} tag{tags.length !== 1 ? 's' : ''}
              </span>
            )}
          </div>
        </div>
      </Link>
    </article>
  );
}

interface RelatedPostsListProps {
  posts?: Post[];
  maxItems?: number;
}

/**
 * Alternative: Minimal Related Posts (used in sidebars)
 */
export function RelatedPostsList({
  posts = [],
  maxItems = 5,
}: RelatedPostsListProps) {
  if (!posts || posts.length === 0) {
    return null;
  }

  return (
    <nav className="space-y-3" aria-label="Related articles" role="navigation">
      <h3 className="sr-only">Related Articles</h3>
      <ul className="list-none">
        {posts.slice(0, maxItems).map((post) => (
          <li key={post.id}>
            <Link
              href={`/posts/${post.slug}`}
              className="block group focus:outline-none"
            >
              <p className="text-sm font-medium text-gray-900 group-hover:text-cyan-600 group-focus-visible:text-cyan-600 group-focus-visible:ring-2 group-focus-visible:ring-cyan-500 group-focus-visible:rounded transition-all line-clamp-2">
                {post.title}
              </p>
              {post.publishedAt && (
                <time
                  dateTime={post.publishedAt.split('T')[0]}
                  className="text-xs text-gray-500 mt-1 block"
                >
                  {formatDate(post.publishedAt)}
                </time>
              )}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/**
 * Alternative: Featured Related Posts (larger, 2-column layout)
 */
export function RelatedPostsFeatured({
  posts = [],
  maxItems = 2,
}: RelatedPostsListProps) {
  if (!posts || posts.length === 0) {
    return null;
  }

  return (
    <div
      className="grid grid-cols-1 md:grid-cols-2 gap-6"
      aria-label="Featured related articles"
      role="region"
    >
      {posts.slice(0, maxItems).map((post) => (
        <RelatedPostCard key={post.id} post={post} />
      ))}
    </div>
  );
}
