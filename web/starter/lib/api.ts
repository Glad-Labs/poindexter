/**
 * Thin wrapper around the Poindexter FastAPI backend.
 *
 * Reads from `NEXT_PUBLIC_POINDEXTER_API_URL` (default `http://localhost:8002`).
 * All fetches are server-side by default — no auth exposure to the client.
 *
 * Endpoints used (see `src/cofounder_agent/routes/cms_routes.py` in the main
 * repo for the full surface):
 *
 *   GET /api/posts?limit=N&offset=M       → paginated list
 *   GET /api/posts/{slug}                 → single post
 *   GET /api/categories                   → list categories
 *
 * If your deployment serves static JSON off a CDN instead (the pattern the
 * reference Glad Labs production site uses), replace `fetchJson` with your
 * own CDN-aware fetcher — the page components only need the shape below.
 */

const API_BASE =
  process.env.NEXT_PUBLIC_POINDEXTER_API_URL || 'http://localhost:8002';

export interface Post {
  id: string;
  slug: string;
  title: string;
  excerpt: string | null;
  content?: string;
  featured_image_url: string | null;
  published_at: string | null;
  reading_time?: number | null;
  word_count?: number | null;
  author?: string | null;
  category_id?: string | null;
  seo_title?: string | null;
  seo_description?: string | null;
}

export interface PostListResponse {
  items: Post[];
  total: number;
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    // Re-validate every 5 min. Tune per-page with `{ next: { revalidate } }`
    // if some pages need fresher data.
    next: { revalidate: 300 },
    ...init,
  });
  if (!res.ok) {
    throw new Error(
      `Poindexter API ${res.status} ${res.statusText} on ${path}`
    );
  }
  return (await res.json()) as T;
}

export async function listPosts(
  limit = 10,
  offset = 0
): Promise<PostListResponse> {
  return fetchJson<PostListResponse>(
    `/api/posts?limit=${limit}&offset=${offset}`
  );
}

export async function getPostBySlug(slug: string): Promise<Post | null> {
  try {
    return await fetchJson<Post>(`/api/posts/${encodeURIComponent(slug)}`);
  } catch (err) {
    if (err instanceof Error && err.message.includes(' 404 ')) {
      return null;
    }
    throw err;
  }
}

export function formatDate(iso: string | null): string {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  } catch {
    return iso;
  }
}
