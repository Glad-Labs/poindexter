import logger from './logger';
/**
 * Posts API Functions
 *
 * Reads from static JSON on R2/CDN — no API server needed.
 * The content pipeline pushes updated JSON on every publish.
 */

const STATIC_URL =
  process.env.NEXT_PUBLIC_STATIC_URL ||
  'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';

export interface Post {
  id: string;
  title: string;
  slug: string;
  excerpt?: string;
  content: string;
  featured_image_url?: string;
  cover_image_url?: string;
  author_id?: string;
  category_id?: string;
  status: string;
  published_at?: string;
  created_at: string;
  updated_at: string;
  view_count: number;
  seo_title?: string;
  seo_description?: string;
  seo_keywords?: string;
}

export interface PostsResponse {
  posts: Post[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

const POSTS_PER_PAGE = 10;

/**
 * Canonical featured-image resolver. Same priority on list pages and
 * detail pages so the thumbnail a reader clicks matches the hero they
 * land on. Pages previously inlined this and drifted four different
 * ways (featured-only, cover-only, cover→featured, featured→cover);
 * import this helper instead.
 *
 * Returns `null` (not a placeholder URL) so callers decide whether to
 * render an <img> or omit the slot — matches the pre-existing
 * `{post.featured_image_url && ...}` conditional pattern.
 */
export function postFeaturedImage(
  post: Pick<Post, 'featured_image_url' | 'cover_image_url'>,
): string | null {
  return post.featured_image_url || post.cover_image_url || null;
}

/**
 * Fetch the full post index from static JSON.
 * Cached and reused by all listing functions.
 */
async function fetchPostIndex(): Promise<Post[]> {
  try {
    const response = await fetch(`${STATIC_URL}/posts/index.json`, {
      // Tag-based cache: invalidated by revalidateTag('posts') on publish.
      // No TTL — stays fresh until an explicit invalidation fires.
      next: { tags: ['posts', 'post-index'] },
    });

    if (!response.ok) {
      throw new Error(`Failed to fetch post index: ${response.statusText}`);
    }

    const data = await response.json();
    return data.posts || [];
  } catch (error) {
    logger.error('Error fetching post index:', error);
    return [];
  }
}

/**
 * Fetch paginated list of published posts
 */
export async function getPosts(page: number = 1): Promise<PostsResponse> {
  const allPosts = await fetchPostIndex();
  const offset = (page - 1) * POSTS_PER_PAGE;
  const paged = allPosts.slice(offset, offset + POSTS_PER_PAGE);

  return {
    posts: paged,
    total: allPosts.length,
    page,
    pageSize: POSTS_PER_PAGE,
    totalPages: Math.ceil(allPosts.length / POSTS_PER_PAGE),
  };
}

/**
 * Fetch a single post by slug (with full content)
 */
export async function getPostBySlug(slug: string): Promise<Post | null> {
  try {
    const response = await fetch(`${STATIC_URL}/posts/${slug}.json`, {
      // Tag-based cache: invalidated by revalidateTag('post:<slug>') on publish.
      // This fixes the "post not found for 5 minutes" issue where null
      // responses were TTL-cached for 300s after approval.
      next: { tags: ['posts', `post:${slug}`] },
    });

    if (!response.ok) {
      return null;
    }

    return await response.json();
  } catch (error) {
    logger.error(`Error fetching post with slug ${slug}:`, error);
    return null;
  }
}

/**
 * Fetch related posts from the same category
 */
export async function getRelatedPosts(
  categoryId: string,
  excludeId: string,
  limit: number = 3
): Promise<Post[]> {
  const allPosts = await fetchPostIndex();
  return allPosts
    .filter((p) => p.category_id === categoryId && p.id !== excludeId)
    .slice(0, limit);
}

/**
 * Fetch posts by category
 */
export async function getPostsByCategory(
  categoryId: string,
  page: number = 1
): Promise<PostsResponse> {
  const allPosts = await fetchPostIndex();
  const filtered = allPosts.filter((p) => p.category_id === categoryId);
  const offset = (page - 1) * POSTS_PER_PAGE;
  const paged = filtered.slice(offset, offset + POSTS_PER_PAGE);

  return {
    posts: paged,
    total: filtered.length,
    page,
    pageSize: POSTS_PER_PAGE,
    totalPages: Math.ceil(filtered.length / POSTS_PER_PAGE),
  };
}

/**
 * Fetch all published posts sorted by published_at (newest first)
 */
export async function getAllPublishedPosts(): Promise<Post[]> {
  return await fetchPostIndex();
}

/**
 * Fetch posts by author. The autonomous content site has one
 * primary byline (poindexter-ai); this still goes through the same
 * filter so /author/<id> can render a real list instead of a
 * "Coming soon" placeholder. Pages this with the same POSTS_PER_PAGE
 * cap as getPosts/getPostsByCategory so all listing pages feel the
 * same to the reader.
 */
export async function getPostsByAuthor(
  authorId: string,
  page: number = 1,
): Promise<PostsResponse> {
  const allPosts = await fetchPostIndex();
  const filtered = allPosts.filter((p) => p.author_id === authorId);
  const offset = (page - 1) * POSTS_PER_PAGE;
  const paged = filtered.slice(offset, offset + POSTS_PER_PAGE);

  return {
    posts: paged,
    total: filtered.length,
    page,
    pageSize: POSTS_PER_PAGE,
    totalPages: Math.ceil(filtered.length / POSTS_PER_PAGE),
  };
}

/**
 * Get both the previous and next posts in a single fetch.
 */
export async function getAdjacentPosts(currentSlug: string): Promise<{
  previous: Post | null;
  next: Post | null;
}> {
  try {
    const allPosts = await fetchPostIndex();
    const currentIndex = allPosts.findIndex((p) => p.slug === currentSlug);

    if (currentIndex === -1) {
      return { previous: null, next: null };
    }

    return {
      previous: currentIndex > 0 ? allPosts[currentIndex - 1] : null,
      next:
        currentIndex < allPosts.length - 1 ? allPosts[currentIndex + 1] : null,
    };
  } catch (error) {
    logger.error('Error fetching adjacent posts:', error);
    return { previous: null, next: null };
  }
}

/**
 * Get the next post (older) — prefer getAdjacentPosts() when both needed
 */
export async function getNextPost(currentSlug: string): Promise<Post | null> {
  const { next } = await getAdjacentPosts(currentSlug);
  return next;
}

/**
 * Get the previous post (newer) — prefer getAdjacentPosts() when both needed
 */
export async function getPreviousPost(
  currentSlug: string
): Promise<Post | null> {
  const { previous } = await getAdjacentPosts(currentSlug);
  return previous;
}
