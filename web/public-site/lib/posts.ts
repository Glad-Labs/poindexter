import logger from './logger';
/**
 * Posts API Functions
 * Fetches post data from the FastAPI backend
 */

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  'http://localhost:8000';

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
 * Fetch paginated list of published posts
 */
export async function getPosts(page: number = 1): Promise<PostsResponse> {
  try {
    const offset = (page - 1) * POSTS_PER_PAGE;

    const response = await fetch(
      `${API_BASE_URL}/api/posts?offset=${offset}&limit=${POSTS_PER_PAGE}&status=published`,
      {
        next: { revalidate: 3600 }, // ISR: revalidate every hour
      }
    );

    if (!response.ok) {
      throw new Error(`Failed to fetch posts: ${response.statusText}`);
    }

    const data = await response.json();

    return {
      posts: data.posts || data.items || [],
      total: data.total || 0,
      page,
      pageSize: POSTS_PER_PAGE,
      totalPages: Math.ceil((data.total || 0) / POSTS_PER_PAGE),
    };
  } catch (error) {
    logger.error('Error fetching posts:', error);
    // Return empty result on error
    return {
      posts: [],
      total: 0,
      page,
      pageSize: POSTS_PER_PAGE,
      totalPages: 0,
    };
  }
}

/**
 * Fetch a single post by slug
 */
export async function getPostBySlug(slug: string): Promise<Post | null> {
  try {
    // Use direct slug endpoint for O(1) lookup instead of fetching all posts
    const response = await fetch(`${API_BASE_URL}/api/posts/${slug}`, {
      next: { revalidate: 3600 }, // ISR: revalidate every hour
    });

    if (!response.ok) {
      return null;
    }

    const data = await response.json();
    return data.data || data || null;
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
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/posts?category_id=${categoryId}&exclude_id=${excludeId}&limit=${limit}&status=published`,
      {
        next: { revalidate: 3600 }, // ISR: revalidate every hour
      }
    );

    if (!response.ok) {
      throw new Error('Failed to fetch related posts');
    }

    const data = await response.json();
    return data.posts || data.items || [];
  } catch (error) {
    logger.error('Error fetching related posts:', error);
    return [];
  }
}

/**
 * Fetch posts by category
 */
export async function getPostsByCategory(
  categoryId: string,
  page: number = 1
): Promise<PostsResponse> {
  try {
    const offset = (page - 1) * POSTS_PER_PAGE;

    const response = await fetch(
      `${API_BASE_URL}/api/posts?category_id=${categoryId}&offset=${offset}&limit=${POSTS_PER_PAGE}&status=published`,
      {
        next: { revalidate: 3600 }, // ISR: revalidate every hour
      }
    );

    if (!response.ok) {
      throw new Error('Failed to fetch posts by category');
    }

    const data = await response.json();

    return {
      posts: data.posts || data.items || [],
      total: data.total || 0,
      page,
      pageSize: POSTS_PER_PAGE,
      totalPages: Math.ceil((data.total || 0) / POSTS_PER_PAGE),
    };
  } catch (error) {
    logger.error('Error fetching posts by category:', error);
    return {
      posts: [],
      total: 0,
      page,
      pageSize: POSTS_PER_PAGE,
      totalPages: 0,
    };
  }
}

/**
 * Fetch all published posts sorted by published_at (newest first)
 * Used for navigation and discovery features
 */
export async function getAllPublishedPosts(): Promise<Post[]> {
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/posts?published_only=true&limit=1000`,
      {
        next: { revalidate: 3600 }, // ISR: revalidate every hour
      }
    );

    if (!response.ok) {
      return [];
    }

    const data = await response.json();
    const posts = data.posts || data.data || data.items || [];

    // Sort by published_at descending (newest first)
    return posts.sort(
      (a: Post, b: Post) =>
        new Date(b.published_at || b.created_at).getTime() -
        new Date(a.published_at || a.created_at).getTime()
    );
  } catch (error) {
    logger.error('Error fetching all published posts:', error);
    return [];
  }
}

/**
 * Get the next post in chronological order (older post)
 *
 * Prefer getAdjacentPosts() when both neighbors are needed — that function
 * fetches the post list only once instead of twice.
 */
export async function getNextPost(currentSlug: string): Promise<Post | null> {
  try {
    const allPosts = await getAllPublishedPosts();
    const currentIndex = allPosts.findIndex((p) => p.slug === currentSlug);

    if (currentIndex === -1 || currentIndex === allPosts.length - 1) {
      return null; // No next post
    }

    return allPosts[currentIndex + 1];
  } catch (error) {
    logger.error('Error fetching next post:', error);
    return null;
  }
}

/**
 * Get the previous post in chronological order (newer post)
 *
 * Prefer getAdjacentPosts() when both neighbors are needed — that function
 * fetches the post list only once instead of twice.
 */
export async function getPreviousPost(
  currentSlug: string
): Promise<Post | null> {
  try {
    const allPosts = await getAllPublishedPosts();
    const currentIndex = allPosts.findIndex((p) => p.slug === currentSlug);

    if (currentIndex <= 0) {
      return null; // No previous post
    }

    return allPosts[currentIndex - 1];
  } catch (error) {
    logger.error('Error fetching previous post:', error);
    return null;
  }
}

/**
 * Get both the previous and next posts in a single fetch.
 *
 * Use this instead of calling getNextPost() + getPreviousPost() independently
 * to avoid fetching the full post list twice per page render.
 *
 * @param currentSlug - Slug of the currently displayed post
 * @returns Object with `previous` (newer) and `next` (older) post or null
 */
export async function getAdjacentPosts(currentSlug: string): Promise<{
  previous: Post | null;
  next: Post | null;
}> {
  try {
    const allPosts = await getAllPublishedPosts();
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
