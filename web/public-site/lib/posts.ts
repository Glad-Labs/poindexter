import * as Sentry from '@sentry/nextjs';
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
  featured_image_alt?: string;
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
 * Issue #1 (audit): the pipeline's index.json is not reliably ordered, so
 * "latest" surfaces (featured slot, archive page 1, prev/next links) showed
 * stale posts. Sort defensively here — newest first by published_at, falling
 * back to created_at — instead of trusting upstream order. Posts with no
 * parseable date sink to the end rather than masquerading as new.
 *
 * Exported so callers that fetch the index themselves (app/page.js does its
 * own fetch to keep Sentry error states) apply the identical order.
 */
// Structural types (not Pick<Post,…>) so route-local interfaces — e.g.
// feed.xml's trimmed Post — can use these helpers without created_at.
interface DatedPost {
  published_at?: string;
  created_at?: string;
}

function postTimestamp(post: DatedPost): number {
  const t = Date.parse(post.published_at || post.created_at || '');
  return Number.isNaN(t) ? 0 : t;
}

export function sortPostsNewestFirst<T extends DatedPost>(posts: T[]): T[] {
  // Copy before sorting — callers may hold the original array.
  return [...posts].sort((a, b) => postTimestamp(b) - postTimestamp(a));
}

/**
 * Issue #5 (audit): raw pipeline artifacts were reaching readers — literal
 * "Title:" prefixes, "--" surviving into headlines, wrapping quotes. This is
 * the display-layer guard; the pipeline should also clean at write time, but
 * the frontend must not render artifacts regardless of upstream state.
 */
export function cleanPostTitle(title?: string): string {
  if (!title) return '';
  let t = title.trim();
  // Strip a leading "Title:" / "Title -" label (case-insensitive).
  t = t.replace(/^title\s*[:\-–—]\s*/i, '');
  // "--" is a pipeline artifact (also produces ugly slugs); render as em dash.
  t = t.replace(/\s*--+\s*/g, ' — ');
  // Strip one matched pair of wrapping quotes.
  if (
    t.length > 1 &&
    ((t.startsWith('"') && t.endsWith('"')) ||
      (t.startsWith("'") && t.endsWith("'")))
  ) {
    t = t.slice(1, -1);
  }
  return t.trim();
}

/**
 * Issue #2 (audit): the homepage shipped a hardcoded fallback excerpt
 * ("Read this insightful article") and a raw content.substring() that could
 * leak HTML/markdown into cards. Single canonical excerpt resolver:
 *
 *   1. Use post.excerpt unless it's empty, a known placeholder, or just the
 *      title repeated (a recurring pipeline artifact).
 *   2. Otherwise derive from content: strip tags/markdown, drop a leading
 *      repeat of the title, truncate at a word boundary.
 *   3. Otherwise return null — callers omit the element. Never filler copy.
 */
const EXCERPT_PLACEHOLDERS = new Set(['read this insightful article']);

export function postExcerpt(
  post: { title: string; excerpt?: string; content?: string },
  maxLength: number = 200,
): string | null {
  const title = cleanPostTitle(post.title);
  let excerpt = (post.excerpt || '').trim();

  if (
    !excerpt ||
    EXCERPT_PLACEHOLDERS.has(excerpt.toLowerCase()) ||
    excerpt.toLowerCase() === title.toLowerCase()
  ) {
    excerpt = '';
  }

  if (!excerpt && post.content) {
    excerpt = post.content
      .replace(/<[^>]+>/g, ' ') // strip HTML tags
      .replace(/[#*_>`]/g, '') // strip common markdown punctuation
      .replace(/\s+/g, ' ')
      .trim();
    // Drop a leading repeat of the title so cards don't read title twice.
    if (title && excerpt.toLowerCase().startsWith(title.toLowerCase())) {
      excerpt = excerpt.slice(title.length).replace(/^[\s:—–-]+/, '');
    }
  }

  if (!excerpt) return null;

  if (excerpt.length > maxLength) {
    const cut = excerpt.slice(0, maxLength);
    const lastSpace = cut.lastIndexOf(' ');
    excerpt = cut.slice(0, lastSpace > 60 ? lastSpace : maxLength).trimEnd() + '…';
  }
  return excerpt;
}

/**
 * Fetch the full post index from static JSON.
 * Cached and reused by all listing functions.
 * Always returns newest-first (see sortPostsNewestFirst above) — pagination,
 * the featured slot, and prev/next adjacency all depend on this order.
 */
async function fetchPostIndex(): Promise<Post[]> {
  const response = await fetch(`${STATIC_URL}/posts/index.json`, {
    // Tag-based cache: invalidated by revalidateTag('posts') on publish.
    // No TTL — stays fresh until an explicit invalidation fires.
    next: { tags: ['posts', 'post-index'] },
  });

  if (response.status === 404) {
    // Index not yet published — treat as empty, not an error.
    return [];
  }

  if (!response.ok) {
    // 5xx or unexpected status: throw so ISR keeps the stale cache instead
    // of replacing it with an empty array (which would poison every listing page).
    const err = new Error(
      `fetchPostIndex: R2 returned ${response.status} ${response.statusText}`,
    );
    console.error('[posts] fetchPostIndex failed:', err.message);
    Sentry.captureException(err);
    throw err;
  }

  const data = await response.json();
  return sortPostsNewestFirst(data.posts || []);
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
  const response = await fetch(`${STATIC_URL}/posts/${slug}.json`, {
    // Tag-based cache: invalidated by revalidateTag('post:<slug>') on publish.
    // This fixes the "post not found for 5 minutes" issue where null
    // responses were TTL-cached for 300s after approval.
    next: { tags: ['posts', `post:${slug}`] },
  });

  if (response.status === 404) {
    // Genuinely missing post — caller renders notFound().
    return null;
  }

  if (!response.ok) {
    // 5xx or unexpected status: throw so ISR keeps the stale cached post
    // instead of serving a 404 to readers during an R2 outage.
    const err = new Error(
      `getPostBySlug(${slug}): R2 returned ${response.status} ${response.statusText}`,
    );
    console.error('[posts] getPostBySlug failed:', err.message);
    Sentry.captureException(err);
    throw err;
  }

  return await response.json();
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

