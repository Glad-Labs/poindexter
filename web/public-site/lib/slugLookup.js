/**
 * Slug-Based Content Lookup Utilities
 *
 * Centralized slug lookup functions for content types.
 * Consolidates repeated patterns from getCategoryBySlug, getTagBySlug, etc.
 *
 * Benefits:
 * - Single source of truth for slug lookups
 * - DRY (Don't Repeat Yourself) principle applied
 * - Consistent error handling and caching
 * - Easier to test and maintain
 * - Optional caching for frequently accessed items
 *
 * @module lib/slugLookup
 */

import qs from 'qs';

/**
 * Simple in-memory cache for slug lookups
 * Prevents repeated API calls for the same content
 * @private
 */
const lookupCache = new Map();

/**
 * Generic slug-based lookup function
 * Retrieves a single item from a collection by slug
 *
 * @param {string} endpoint - API endpoint name (e.g., 'posts', 'categories', 'tags')
 * @param {string} slug - Slug to look up
 * @param {function} fetchAPI - Fetch function to use (required for flexibility)
 * @param {object} options - Additional options
 * @param {boolean} options.useCache - Whether to use cache (default: true)
 * @param {string} options.slugField - Name of slug field (default: 'slug')
 * @param {object} options.populate - Additional populate options
 *
 * @returns {Promise<object|null>} - The found item or null
 *
 * @example
 * const category = await getBySlug('categories', 'tech-news', fetchAPI);
 * const tag = await getBySlug('tags', 'javascript', fetchAPI);
 * const post = await getBySlug('posts', 'my-article', fetchAPI, { populate: '*' });
 */
export async function getBySlug(endpoint, slug, fetchAPI, options = {}) {
  const { useCache = true, slugField = 'slug', populate = null } = options;

  // Check cache first
  const cacheKey = `${endpoint}:${slug}`;
  if (useCache && lookupCache.has(cacheKey)) {
    return lookupCache.get(cacheKey);
  }

  // Build query
  const query = qs.stringify(
    {
      filters: { [slugField]: { $eq: slug } },
      ...(populate && { populate }),
    },
    { encode: false }
  );

  try {
    const data = await fetchAPI(`/${endpoint}?${query}`);

    // Extract first result
    const item = data?.data?.[0] || null;

    // Cache the result
    if (useCache && item) {
      lookupCache.set(cacheKey, item);
    }

    return item;
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      console.error(`Error fetching ${endpoint} by slug "${slug}":`, error);
    }
    return null;
  }
}

/**
 * Get a category by slug
 * Consolidated from getCategoryBySlug
 *
 * @param {string} slug - Category slug to look up
 * @param {function} fetchAPI - Fetch function to use
 * @param {object} options - Additional options (populate, useCache, etc)
 *
 * @returns {Promise<object|null>} - The category object or null
 *
 * @example
 * const category = await getCategoryBySlug('tech-news', fetchAPI);
 * console.log(category.name); // 'Tech News'
 */
export async function getCategoryBySlug(slug, fetchAPI, options = {}) {
  return getBySlug('categories', slug, fetchAPI, {
    useCache: true,
    slugField: 'slug',
    ...options,
  });
}

/**
 * Get a tag by slug
 * Consolidated from getTagBySlug
 *
 * @param {string} slug - Tag slug to look up
 * @param {function} fetchAPI - Fetch function to use
 * @param {object} options - Additional options (populate, useCache, etc)
 *
 * @returns {Promise<object|null>} - The tag object or null
 *
 * @example
 * const tag = await getTagBySlug('javascript', fetchAPI);
 * console.log(tag.name); // 'JavaScript'
 */
export async function getTagBySlug(slug, fetchAPI, options = {}) {
  return getBySlug('tags', slug, fetchAPI, {
    useCache: true,
    slugField: 'slug',
    ...options,
  });
}

/**
 * Get a post by slug
 * Consolidated from getPostBySlug
 *
 * @param {string} slug - Post slug to look up
 * @param {function} fetchAPI - Fetch function to use
 * @param {object} options - Additional options (populate, useCache, etc)
 *
 * @returns {Promise<object|null>} - The post object or null
 *
 * @example
 * const post = await getPostBySlug('my-first-post', fetchAPI);
 * console.log(post.title); // 'My First Post'
 */
export async function getPostBySlug(slug, fetchAPI, options = {}) {
  return getBySlug('posts', slug, fetchAPI, {
    useCache: true,
    slugField: 'slug',
    populate: '*',
    ...options,
  });
}

/**
 * Get a page by slug (generic pages collection)
 * Useful for static pages like about, privacy policy, etc.
 *
 * @param {string} slug - Page slug to look up
 * @param {function} fetchAPI - Fetch function to use
 * @param {object} options - Additional options (populate, useCache, etc)
 *
 * @returns {Promise<object|null>} - The page object or null
 *
 * @example
 * const aboutPage = await getPageBySlug('about', fetchAPI);
 * console.log(aboutPage.content);
 */
export async function getPageBySlug(slug, fetchAPI, options = {}) {
  return getBySlug('pages', slug, fetchAPI, {
    useCache: true,
    slugField: 'slug',
    populate: '*',
    ...options,
  });
}

/**
 * Clear the lookup cache
 * Useful for testing or when you need fresh data
 *
 * @param {string} endpoint - Optional: clear cache for specific endpoint only
 *
 * @example
 * clearLookupCache(); // Clear all
 * clearLookupCache('posts'); // Clear only posts
 */
export function clearLookupCache(endpoint = null) {
  if (endpoint) {
    // Clear cache entries for specific endpoint
    for (const key of lookupCache.keys()) {
      if (key.startsWith(`${endpoint}:`)) {
        lookupCache.delete(key);
      }
    }
  } else {
    // Clear entire cache
    lookupCache.clear();
  }
}

/**
 * Get cache statistics (for debugging)
 *
 * @returns {object} - Cache stats including size and entries
 *
 * @example
 * const stats = getCacheStats();
 * console.log(`Cache size: ${stats.size}, Entries: ${JSON.stringify(stats.entries)}`);
 */
export function getCacheStats() {
  const entries = Array.from(lookupCache.keys());
  return {
    size: lookupCache.size,
    entries,
    byEndpoint: entries.reduce((acc, key) => {
      const endpoint = key.split(':')[0];
      acc[endpoint] = (acc[endpoint] || 0) + 1;
      return acc;
    }, {}),
  };
}

export default {
  getBySlug,
  getCategoryBySlug,
  getTagBySlug,
  getPostBySlug,
  getPageBySlug,
  clearLookupCache,
  getCacheStats,
};
