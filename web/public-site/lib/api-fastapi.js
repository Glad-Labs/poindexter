import logger from './logger';
/**
 * FastAPI CMS Client - Optimized for Performance
 *
 * This replaces the Strapi client with a direct FastAPI integration.
 * - Sync endpoints (no async complications)
 * - PostgreSQL backend (fast queries)
 * - Built-in pagination and filtering
 * - Minimal response size
 * - Production-ready caching headers
 */

// API Configuration — validated centrally in url.js
import { getAPIBaseURL } from './url';
const FASTAPI_URL = getAPIBaseURL();
const API_BASE = `${FASTAPI_URL}/api`;

// Cache control for static content
const CACHE_HEADERS = {
  'Cache-Control': 'public, max-age=3600, stale-while-revalidate=86400',
};

/**
 * Generic fetch wrapper with error handling and structured error logging.
 *
 * Logs all errors in both dev and production (issue #97). Error context
 * includes endpoint, method, HTTP status (when available), error message,
 * and stack trace to aid production debugging.
 */
async function fetchAPI(endpoint, options = {}) {
  const url = `${API_BASE}${endpoint}`;
  const method = options.method || 'GET';

  try {
    const response = await fetch(url, {
      ...options,
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...CACHE_HEADERS,
        ...options.headers,
      },
    });

    if (!response.ok) {
      const httpError = new Error(
        `API Error: ${response.status} ${response.statusText}`
      );
      logger.error('[FastAPI] HTTP error response', {
        endpoint,
        method,
        status: response.status,
        statusText: response.statusText,
        message: httpError.message,
      });
      throw httpError;
    }

    const data = await response.json();
    return data;
  } catch (error) {
    // Log all errors regardless of environment — production failures are
    // equally important to diagnose (issue #97).
    if (!(error.message && error.message.startsWith('API Error:'))) {
      // Avoid double-logging HTTP errors already logged above
      logger.error('[FastAPI] Network or parse error', {
        endpoint,
        method,
        message: error instanceof Error ? error.message : String(error),
        stack: error instanceof Error ? error.stack : undefined,
        timestamp: new Date().toISOString(),
      });
    }
    throw error;
  }
}

/**
 * Get paginated posts list
 * ADAPTER: Maps old parameters to FastAPI format
 *
 * @param {number} page - Page number (1-indexed, default 1)
 * @param {number} pageSize - Items per page (default 10)
 * @param {string} excludeId - Post ID to exclude (optional)
 * @returns {Promise<{data: Array, meta: {pagination: Object}}>}
 */
export async function getPaginatedPosts(
  page = 1,
  pageSize = 10,
  excludeId = null
) {
  const offset = (page - 1) * pageSize;

  // Build endpoint: FastAPI uses offset/limit
  let endpoint = `/posts?offset=${offset}&limit=${pageSize}&published_only=true`;

  const response = await fetchAPI(endpoint);

  // Support both standard envelope (posts) and legacy Strapi envelope (data)
  let data = response.posts || response.data || [];
  if (excludeId) {
    data = data.filter((post) => post.id !== excludeId);
  }

  const total = response.total ?? response.meta?.pagination?.total ?? 0;

  // Return in format expected by pages
  return {
    data: data,
    meta: {
      pagination: {
        page: page,
        pageSize: pageSize,
        total: total,
        pageCount: Math.ceil(total / pageSize),
      },
    },
  };
}

/**
 * Get featured post (most recent published post)
 *
 * @returns {Promise<Object|null>}
 */
export async function getFeaturedPost() {
  try {
    // Get the most recent post (offset=0, limit=1, published_only=true)
    const response = await fetchAPI(
      '/posts?offset=0&limit=1&published_only=true'
    );

    const posts = response.posts || response.data || [];
    if (posts.length > 0) {
      return posts[0];
    }
    return null;
  } catch (error) {
    logger.error('[FastAPI] Error fetching featured post', {
      message: error instanceof Error ? error.message : String(error),
      endpoint: '/posts',
    });
    return null;
  }
}

/**
 * Get single post by slug with full content
 * Includes related category and tags
 *
 * @param {string} slug - Post slug
 * @returns {Promise<Object|null>}
 */
export async function getPostBySlug(slug) {
  try {
    const response = await fetchAPI(`/posts/${encodeURIComponent(slug)}`);
    const post = response?.data;

    if (post) {
      return {
        ...post,
        // Normalize meta fields for compatibility
        category: post.category || null,
        tags: post.tags || [],
      };
    }

    return null;
  } catch (error) {
    logger.error('[FastAPI] Error fetching post by slug', {
      slug,
      message: error instanceof Error ? error.message : String(error),
    });
    return null;
  }
}

/**
 * Get all categories for navigation/filtering
 *
 * @returns {Promise<Array>}
 */
export async function getCategories() {
  try {
    const response = await fetchAPI('/categories');
    return response.data || [];
  } catch (error) {
    logger.error('[FastAPI] Error fetching categories', {
      message: error instanceof Error ? error.message : String(error),
    });
    return [];
  }
}

/**
 * Get all tags for filtering/cloud
 *
 * @returns {Promise<Array>}
 */
export async function getTags() {
  try {
    const response = await fetchAPI('/tags');
    return response.data || [];
  } catch (error) {
    logger.error('[FastAPI] Error fetching tags:', error);
    return [];
  }
}

/**
 * Get posts in specific category
 *
 * @param {string} slug - Category slug
 * @param {number} page - Page number (1-indexed)
 * @param {number} limit - Items per page
 * @returns {Promise<{posts: Array, pagination: Object}>}
 */
export async function getPostsByCategory(slug, page = 1, limit = 10) {
  return getPaginatedPosts(page, limit, slug, null);
}

/**
 * Get posts with specific tag
 *
 * @param {string} slug - Tag slug
 * @param {number} page - Page number (1-indexed)
 * @param {number} limit - Items per page
 * @returns {Promise<{posts: Array, pagination: Object}>}
 */
export async function getPostsByTag(slug, page = 1, limit = 10) {
  return getPaginatedPosts(page, limit, null, slug);
}

/**
 * Get ALL posts (for generating static paths)
 * Used by [slug].js getStaticPaths for ISR
 * Note: Fetches in batches since backend max limit is 100
 *
 * @returns {Promise<Array<{slug: string}>>}
 */
export async function getAllPosts() {
  try {
    const allPosts = [];
    let skip = 0;
    const limit = 100; // Backend max limit

    // Fetch all posts in batches
    while (true) {
      const response = await fetchAPI(
        `/posts?offset=${skip}&limit=${limit}&published_only=true`
      );

      const batch = response.posts || response.data || [];
      if (batch.length === 0) {
        break; // No more posts
      }

      allPosts.push(
        ...batch.map((post) => ({
          slug: post.slug,
        }))
      );

      // Check if we got fewer posts than requested (end of results)
      if (batch.length < limit) {
        break;
      }

      skip += limit;
    }

    return allPosts;
  } catch (error) {
    logger.error('[FastAPI] Error fetching all posts:', error);
    return [];
  }
}

/**
 * Get related posts (similar by tags)
 *
 * @param {string} postId - Current post ID
 * @param {Array} tagIds - Tag IDs to match
 * @param {number} limit - Number of related posts to return
 * @returns {Promise<Array>}
 */
export async function getRelatedPosts(postId, tagIds = [], limit = 3) {
  try {
    // Query posts with same tags, excluding current post
    let endpoint = `/posts?limit=${limit}&published_only=true`;

    if (tagIds && tagIds.length > 0) {
      endpoint += `&related_tags=${tagIds.join(',')}`;
    }

    const response = await fetchAPI(endpoint);

    // Filter out the current post
    return (response.data || [])
      .filter((post) => post.id !== postId)
      .slice(0, limit);
  } catch (error) {
    logger.error('[FastAPI] Error fetching related posts:', error);
    return [];
  }
}

/**
 * Search posts by keyword
 *
 * @param {string} query - Search query
 * @param {number} limit - Max results
 * @returns {Promise<Array>}
 */
export async function searchPosts(query, limit = 20) {
  try {
    if (!query || query.trim().length === 0) {
      return [];
    }

    const endpoint = `/posts/search?q=${encodeURIComponent(query)}&limit=${limit}`;
    const response = await fetchAPI(endpoint);

    return response.data || [];
  } catch (error) {
    logger.error('[FastAPI] Error searching posts:', error);
    return [];
  }
}

/**
 * Get CMS health status
 * Useful for build-time validation
 *
 * @returns {Promise<Object>}
 */
export async function getCMSStatus() {
  try {
    const response = await fetchAPI('/cms/status');
    return response;
  } catch (error) {
    logger.error('[FastAPI] Error checking CMS status:', error);
    return { status: 'error', message: error.message };
  }
}

/**
 * Build-time validation
 * Checks if FastAPI is available
 *
 * @returns {Promise<boolean>}
 */
export async function validateFastAPI() {
  try {
    const status = await getCMSStatus();
    return status.status === 'healthy';
  } catch (error) {
    logger.error('[FastAPI] CMS health check failed:', error);
    return false;
  }
}

/**
 * Get image URL from relative path
 * (In case we move to CloudFront/CDN later)
 *
 * @param {string} path - Relative image path
 * @returns {string}
 */
export function getImageURL(path) {
  if (!path) return null;

  // If absolute URL, return as-is
  if (path.startsWith('http')) {
    return path;
  }

  // Otherwise, construct from FastAPI
  return `${FASTAPI_URL}${path}`;
}

/**
 * Format post data for display
 * Normalizes field names and types
 *
 * @param {Object} post - Raw post from FastAPI
 * @returns {Object}
 */
export function formatPost(post) {
  if (!post) return null;

  return {
    id: post.id,
    title: post.title,
    slug: post.slug,
    excerpt: post.excerpt,
    content: post.content,
    featured: post.featured || false,
    publishedAt: post.published_at,
    createdAt: post.created_at,
    category: post.category || null,
    tags: post.tags || [],
    coverImage: post.cover_image || null,
    meta: {
      wordCount: (post.content || '').split(/\s+/).length,
      readingTime: Math.ceil((post.content || '').split(/\s+/).length / 200), // ~200 words per minute
    },
  };
}

// ============================================================================
// OAUTH & AUTHENTICATION
// ============================================================================

/**
 * Get OAuth login URL for a provider
 * @param {string} provider - Provider name ('github', 'google', etc.)
 * @returns {Promise<Object>} { login_url: 'https://...' }
 */
export async function getOAuthLoginURL(provider) {
  const data = await fetchAPI(`/auth/${provider}/login`);
  return data.login_url;
}

/**
 * Handle OAuth callback
 * @param {string} provider - OAuth provider name
 * @param {string} code - Authorization code from provider
 * @param {string} state - State parameter for CSRF protection
 * @returns {Promise<Object>} { access_token, user, ... }
 */
export async function handleOAuthCallback(provider, code, state) {
  return fetchAPI(`/auth/${provider}/callback`, {
    method: 'POST',
    body: JSON.stringify({ code, state }),
  });
}

/**
 * Get current authenticated user
 * Requires valid JWT token in Authorization header
 * @returns {Promise<Object|null>}
 */
export async function getCurrentUser() {
  try {
    const response = await fetchAPI('/auth/me');
    return response || null;
  } catch (error) {
    logger.error('[FastAPI] Error getting current user:', error);
    return null;
  }
}

/**
 * Logout current user
 * @returns {Promise<Object>}
 */
export async function logout() {
  return fetchAPI('/auth/logout', {
    method: 'POST',
  });
}

// ============================================================================
// TASK MANAGEMENT
// ============================================================================

/**
 * Create a new task
 * @param {Object} taskData - Task data (title, description, type, parameters)
 * @returns {Promise<Object>}
 */
export async function createTask(taskData) {
  return fetchAPI('/tasks', {
    method: 'POST',
    body: JSON.stringify(taskData),
  });
}

/**
 * List all tasks with filtering
 * @param {number} limit - Number of tasks to return
 * @param {number} offset - Pagination offset
 * @param {string} status - Filter by status (optional)
 * @returns {Promise<Object>} { data: [...tasks], meta: {...} }
 */
export async function listTasks(limit = 20, offset = 0, status = null) {
  let endpoint = `/tasks?limit=${limit}&offset=${offset}`;
  if (status) {
    endpoint += `&status=${encodeURIComponent(status)}`;
  }

  return fetchAPI(endpoint);
}

/**
 * Get single task by ID
 * @param {string|number} taskId - Task ID
 * @returns {Promise<Object>}
 */
export async function getTaskById(taskId) {
  return fetchAPI(`/tasks/${taskId}`);
}

/**
 * Get task metrics and statistics
 * @returns {Promise<Object>}
 */
export async function getTaskMetrics() {
  return fetchAPI('/tasks/metrics/summary');
}

// ============================================================================
// MODEL MANAGEMENT
// ============================================================================

/**
 * Get list of available AI models
 * @returns {Promise<Object>}
 */
export async function getAvailableModels() {
  try {
    const response = await fetchAPI('/models');
    return response.data || [];
  } catch (error) {
    logger.error('[FastAPI] Error fetching models:', error);
    return [];
  }
}

/**
 * Test connection to specific model provider
 * @param {string} provider - Provider name
 * @param {string} model - Model name (optional)
 * @returns {Promise<Object>} { status: 'connected|error', message: '...' }
 */
export async function testModelProvider(provider, model = null) {
  let endpoint = `/models/test?provider=${encodeURIComponent(provider)}`;
  if (model) {
    endpoint += `&model=${encodeURIComponent(model)}`;
  }

  return fetchAPI(endpoint);
}

// ===== NEWSLETTER & EMAIL CAMPAIGNS =====

/**
 * Subscribe email to newsletter campaign list
 * @param {Object} data - Subscription data
 * @param {string} data.email - Email address (required)
 * @param {string} data.first_name - First name (optional)
 * @param {string} data.last_name - Last name (optional)
 * @param {string} data.company - Company (optional)
 * @param {Array<string>} data.interest_categories - Interest categories (optional)
 * @param {boolean} data.marketing_consent - Marketing consent (optional)
 * @returns {Promise} Subscription response with subscriber_id
 */
export async function subscribeToNewsletter(data) {
  try {
    const response = await fetch(`${API_BASE}/newsletter/subscribe`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Subscription failed');
    }

    return await response.json();
  } catch (error) {
    logger.error('[FastAPI] Newsletter subscription error:', error.message);
    throw error;
  }
}

/**
 * Unsubscribe email from newsletter
 * @param {Object} data - Unsubscribe data
 * @param {string} data.email - Email address (required)
 * @param {string} data.reason - Unsubscribe reason (optional)
 * @returns {Promise} Unsubscribe response
 */
export async function unsubscribeFromNewsletter(data) {
  try {
    const response = await fetch(`${API_BASE}/newsletter/unsubscribe`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Unsubscribe failed');
    }

    return await response.json();
  } catch (error) {
    logger.error('[FastAPI] Newsletter unsubscribe error:', error.message);
    throw error;
  }
}

/**
 * Get newsletter subscriber count
 * @returns {Promise} Subscriber count data
 */
export async function getNewsletterSubscriberCount() {
  try {
    const response = await fetch(`${API_BASE}/newsletter/subscribers/count`);

    if (!response.ok) {
      throw new Error('Failed to fetch subscriber count');
    }

    return await response.json();
  } catch (error) {
    logger.error('[FastAPI] Error fetching subscriber count:', error.message);
    throw error;
  }
}

// Default export for compatibility
export default {
  // CMS Functions
  getPaginatedPosts,
  getFeaturedPost,
  getPostBySlug,
  getCategories,
  getTags,
  getPostsByCategory,
  getPostsByTag,
  getAllPosts,
  getRelatedPosts,
  searchPosts,
  getCMSStatus,
  validateFastAPI,
  getImageURL,
  formatPost,
  // OAuth Functions
  getOAuthLoginURL,
  handleOAuthCallback,
  getCurrentUser,
  logout,
  // Task Functions
  createTask,
  listTasks,
  getTaskById,
  getTaskMetrics,
  // Model Functions
  getAvailableModels,
  testModelProvider,
  // Newsletter Functions
  subscribeToNewsletter,
  unsubscribeFromNewsletter,
  getNewsletterSubscriberCount,
};
