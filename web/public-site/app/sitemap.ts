import logger from '@/lib/logger';
import type { MetadataRoute } from 'next';

/**
 * Type definitions for sitemap content
 */
interface Post {
  slug: string;
  updatedAt?: string;
  publishedAt?: string;
}

interface Category {
  slug: string;
}

interface Tag {
  slug: string;
}

/**
 * Dynamic Sitemap Generation for Next.js 15
 *
 * This generates yourdomain.com/sitemap.xml from Postgres data.
 * Automatically indexes all published posts, categories, and tags.
 *
 * Google will crawl this immediately on deployment.
 */

// Import FastAPI client to query published posts
async function fetchPublishedContent() {
  // During Vercel builds the backend (Railway) is not reliably reachable.
  // Fetching here causes 60s+ hangs → OOM crashes. Sitemap will be populated
  // at runtime via ISR/on-demand revalidation instead.
  if (process.env.VERCEL || process.env.CI) {
    logger.log(
      'Build environment detected — skipping sitemap API fetch. Sitemap uses static pages only.'
    );
    return { allPosts: [], allCategories: [], allTags: [] };
  }

  const FASTAPI_URL =
    process.env.NEXT_PUBLIC_API_BASE_URL ||
    process.env.NEXT_PUBLIC_FASTAPI_URL ||
    'http://localhost:8000';

  // Validate that FASTAPI_URL is a valid absolute URL
  let isValidUrl = false;
  try {
    new URL(FASTAPI_URL);
    isValidUrl = true;
  } catch {
    logger.warn(
      'Invalid NEXT_PUBLIC_FASTAPI_URL during build. Using static fallback.'
    );
  }

  // If URL is invalid or not set, return empty results (use static pages only)
  if (!isValidUrl || FASTAPI_URL === 'http://localhost:8000') {
    logger.log(
      'NEXT_PUBLIC_FASTAPI_URL not properly configured for Vercel build. Skipping dynamic content fetch.'
    );
    return { allPosts: [], allCategories: [], allTags: [] };
  }

  const API_BASE = `${FASTAPI_URL}/api`;
  const FETCH_TIMEOUT = 10_000; // 10s timeout per request — avoid 60s build hangs

  try {
    // Fetch all published posts with pagination (API max limit is 100)
    let allPosts: Post[] = [];
    let skip = 0;
    const limit = 100;
    let hasMore = true;

    while (hasMore) {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT);
      const postsResponse = await fetch(
        `${API_BASE}/posts?offset=${skip}&limit=${limit}&published_only=true`,
        {
          headers: {
            'Content-Type': 'application/json',
          },
          signal: controller.signal,
        }
      );
      clearTimeout(timeoutId);

      if (!postsResponse.ok) break;

      const pageJson = await postsResponse.json();
      const pageData = pageJson.posts || pageJson.data || [];
      if (pageData.length === 0) {
        hasMore = false;
      } else {
        allPosts = [...allPosts, ...pageData];
        skip += limit;
      }
    }

    // Fetch all categories
    const catController = new AbortController();
    const catTimeoutId = setTimeout(() => catController.abort(), FETCH_TIMEOUT);
    const categoriesResponse = await fetch(`${API_BASE}/categories`, {
      headers: {
        'Content-Type': 'application/json',
      },
      signal: catController.signal,
    });
    clearTimeout(catTimeoutId);
    const catJson = categoriesResponse.ok
      ? await categoriesResponse.json()
      : {};
    const allCategories = catJson.categories || catJson.data || [];

    // Fetch all tags
    const tagController = new AbortController();
    const tagTimeoutId = setTimeout(() => tagController.abort(), FETCH_TIMEOUT);
    const tagsResponse = await fetch(`${API_BASE}/tags`, {
      headers: {
        'Content-Type': 'application/json',
      },
      signal: tagController.signal,
    });
    clearTimeout(tagTimeoutId);
    const tagJson = tagsResponse.ok ? await tagsResponse.json() : {};
    const allTags = tagJson.tags || tagJson.data || [];

    return { allPosts, allCategories, allTags };
  } catch (error) {
    logger.error('Error fetching content for sitemap:', error);
    return { allPosts: [], allCategories: [], allTags: [] };
  }
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const baseUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://yourdomain.com';
  const { allPosts, allCategories, allTags } = await fetchPublishedContent();

  // Static pages
  const staticPages: MetadataRoute.Sitemap = [
    {
      url: baseUrl,
      lastModified: new Date(),
      changeFrequency: 'weekly',
      priority: 1,
    },
    {
      url: `${baseUrl}/about`,
      lastModified: new Date(),
      changeFrequency: 'monthly',
      priority: 0.8,
    },
    {
      url: `${baseUrl}/privacy-policy`,
      lastModified: new Date(),
      changeFrequency: 'yearly',
      priority: 0.5,
    },
    {
      url: `${baseUrl}/terms-of-service`,
      lastModified: new Date(),
      changeFrequency: 'yearly',
      priority: 0.5,
    },
  ];

  // Blog posts
  const postPages: MetadataRoute.Sitemap = (allPosts || []).map(
    (post: Post) => ({
      url: `${baseUrl}/posts/${post.slug}`,
      lastModified: post.updatedAt
        ? new Date(post.updatedAt)
        : new Date(post.publishedAt || new Date()),
      changeFrequency: 'monthly' as const,
      priority: 0.8,
    })
  );

  // Category pages
  const categoryPages: MetadataRoute.Sitemap = (allCategories || []).map(
    (category: Category) => ({
      url: `${baseUrl}/category/${category.slug}`,
      lastModified: new Date(),
      changeFrequency: 'weekly' as const,
      priority: 0.7,
    })
  );

  // Tag pages
  const tagPages: MetadataRoute.Sitemap = (allTags || []).map((tag: Tag) => ({
    url: `${baseUrl}/tag/${tag.slug}`,
    lastModified: new Date(),
    changeFrequency: 'weekly' as const,
    priority: 0.6,
  }));

  return [...staticPages, ...postPages, ...categoryPages, ...tagPages];
}
