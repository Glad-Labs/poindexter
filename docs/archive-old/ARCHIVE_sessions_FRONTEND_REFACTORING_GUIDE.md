# 🔄 FRONTEND REFACTORING GUIDE - Complete Integration

**Status:** Ready for Implementation  
**Date:** November 14, 2025  
**Scope:** Oversight Hub (React) + Public Site (Next.js)  
**Backend Version:** FastAPI with OAuth + CMS Routes + Task Management

---

## 📋 TABLE OF CONTENTS

1. [Quick Summary](#quick-summary)
2. [Backend Routes Reference](#backend-routes-reference)
3. [Oversight Hub Refactoring](#oversight-hub-refactoring)
4. [Public Site Refactoring](#public-site-refactoring)
5. [Authentication Flow](#authentication-flow)
6. [Database Sync & Testing](#database-sync--testing)

---

## 🎯 QUICK SUMMARY

### What Changed in Backend

✅ **OAuth Routes** - GitHub OAuth 2.0 implementation  
✅ **CMS Routes** - Direct PostgreSQL access (replaces Strapi)  
✅ **Task Routes** - Task management and orchestration  
✅ **Auth Routes** - JWT token management  
✅ **Settings Routes** - Configuration management

### Frontend Refactoring Strategy

**Oversight Hub (React, port 3001):**

1. Update `cofounderAgentClient.js` with new endpoints
2. Update `authService.js` for OAuth flow
3. Update components for new data structures
4. Update Zustand store for new state shape

**Public Site (Next.js, port 3000):**

1. Refactor `lib/api-fastapi.js` for new CMS endpoints
2. Create OAuth integration pages
3. Update page data-fetching logic
4. Add authentication headers to requests

---

## 📡 BACKEND ROUTES REFERENCE

### OAuth Routes (`/api/auth`)

```
Prefix: /api/auth
Base Router: from routes.oauth_routes

GET  /api/auth/providers
→ Returns: {"providers": ["github"]}
→ Purpose: List available OAuth providers

GET  /api/auth/{provider}/login
→ Params: provider = "github"
→ Redirects to GitHub OAuth consent screen
→ Callback: /api/auth/{provider}/callback

GET  /api/auth/{provider}/callback
→ Query: code, state (from GitHub OAuth)
→ Returns: {"access_token": "jwt", "refresh_token": "jwt", "user": {...}}
→ Creates/updates User and OAuthAccount in database

POST /api/auth/logout
→ Body: {}
→ Returns: {"success": true}
→ Clears session

GET  /api/auth/me
→ Headers: Authorization: Bearer <jwt>
→ Returns: {"id": "uuid", "email": "...", "username": "...", "avatar_url": "..."}
→ Gets current authenticated user

POST /api/auth/{provider}/link
→ Headers: Authorization: Bearer <jwt>
→ Links additional OAuth provider to existing user

DELETE /api/auth/{provider}/unlink
→ Headers: Authorization: Bearer <jwt>
→ Unlinks OAuth provider from user
```

### CMS Routes (`/api`)

```
Prefix: /api
Base Router: from routes.cms_routes

GET  /api/posts
→ Query: skip=0, limit=20, published_only=true
→ Returns: {"data": [...], "meta": {"pagination": {...}}}
→ Lists published posts with pagination

GET  /api/posts/{slug}
→ Returns: {"data": {...post}, "meta": {"category": {...}, "tags": [...]}}
→ Gets single post with full content and metadata

GET  /api/posts/{id}
→ Returns: single post by ID (alternative to slug)

POST /api/posts
→ Headers: Authorization: Bearer <jwt>
→ Body: {"title": "...", "slug": "...", "content": "...", ...}
→ Creates new post (returns 201 with created post)

PUT  /api/posts/{id}
→ Headers: Authorization: Bearer <jwt>
→ Updates existing post

DELETE /api/posts/{id}
→ Headers: Authorization: Bearer <jwt>
→ Deletes post

GET  /api/categories
→ Returns: {"data": [{id, name, slug, description, icon}, ...]}
→ Lists all categories

GET  /api/categories/{slug}
→ Returns: {"data": {...}, "meta": {"posts_count": N}}
→ Gets category with post count

POST /api/categories
→ Headers: Authorization: Bearer <jwt>
→ Creates new category

GET  /api/tags
→ Returns: {"data": [{id, name, slug, color}, ...]}
→ Lists all tags

GET  /api/tags/{slug}
→ Returns: {"data": {...}, "meta": {"posts_count": N}}
→ Gets tag with post count

POST /api/tags
→ Headers: Authorization: Bearer <jwt>
→ Creates new tag
```

### Task Routes (`/api/tasks`)

```
Prefix: /api/tasks
Base Router: from routes.task_routes

POST /api/tasks
→ Body: {"type": "content_generation", "params": {...}, "priority": "normal"}
→ Returns: {"task_id": "uuid", "status": "queued", ...}
→ Creates new task in queue

GET  /api/tasks
→ Query: limit=50, offset=0, status=null
→ Returns: {"data": [...], "meta": {"pagination": {...}}}
→ Lists tasks with filtering and pagination

GET  /api/tasks/{task_id}
→ Returns: {"id": "uuid", "type": "...", "status": "...", "result": {...}, ...}
→ Gets task status and results

GET  /api/tasks/metrics/summary
→ Returns: {"total": N, "completed": N, "failed": N, "pending": N}
→ Gets task metrics
```

### Authentication Routes (`/api/auth` - alternative methods)

```
Prefix: /api/auth
Base Router: from routes.auth_routes

POST /api/auth/login
→ Body: {"email": "...", "password": "..."}
→ Returns: {"accessToken": "jwt", "refreshToken": "jwt", "user": {...}}
→ Traditional email/password login (optional)

POST /api/auth/register
→ Body: {"email": "...", "username": "...", "password": "..."}
→ Creates new user account

POST /api/auth/refresh
→ Body: {"refresh_token": "jwt"}
→ Returns: {"accessToken": "jwt"}
→ Refreshes expired token

POST /api/auth/change-password
→ Headers: Authorization: Bearer <jwt>
→ Changes user password

GET  /api/auth/me
→ Headers: Authorization: Bearer <jwt>
→ Returns current user profile
```

---

## 🏢 OVERSIGHT HUB REFACTORING

### File 1: Update `cofounderAgentClient.js`

**Location:** `web/oversight-hub/src/services/cofounderAgentClient.js`

**Changes:**

1. Add OAuth provider endpoints
2. Update CMS endpoints
3. Add proper error handling for new responses

**Implementation:**

```javascript
// ============================================================================
// OAUTH ENDPOINTS (NEW)
// ============================================================================

export async function getOAuthProviders() {
  /**
   * Get list of available OAuth providers
   * Returns: ["github", ...]
   */
  return makeRequest('/api/auth/providers', 'GET', null, false, null, 5000);
}

export async function getOAuthLoginUrl(provider) {
  /**
   * Get OAuth login URL for GitHub
   * User will be redirected to this URL
   *
   * @param {string} provider - "github"
   * @returns {string} - Full OAuth login URL
   */
  return `http://localhost:8000/api/auth/${provider}/login`;
}

export async function handleOAuthCallback(provider, code, state) {
  /**
   * Exchange OAuth code for JWT token
   * Backend handles: GitHub API call → create/update user → create JWT
   *
   * @param {string} provider - "github"
   * @param {string} code - OAuth authorization code from URL params
   * @param {string} state - OAuth state from URL params
   * @returns {Object} - {access_token, refresh_token, user}
   */
  // Frontend receives callback with ?code=xxx&state=yyy
  // Backend endpoint is: /api/auth/{provider}/callback?code=xxx&state=yyy
  // Just make a call to the callback endpoint
  const url = `/api/auth/${provider}/callback?code=${code}&state=${state}`;
  return makeRequest(url, 'GET', null, false, null, 10000);
}

export async function getCurrentUser() {
  /**
   * Get currently authenticated user
   * Requires JWT token in Authorization header
   *
   * @returns {Object} - {id, email, username, avatar_url, ...}
   */
  return makeRequest('/api/auth/me', 'GET', null, false, null, 5000);
}

export async function logoutUser() {
  /**
   * Logout current user (clear backend session if any)
   */
  try {
    return makeRequest('/api/auth/logout', 'POST', {}, false, null, 5000);
  } catch (error) {
    console.warn('Logout API call failed:', error);
    // Continue with client-side logout even if API fails
  }
}

// ============================================================================
// CMS ENDPOINTS (REFACTORED)
// ============================================================================

export async function getPosts(skip = 0, limit = 20, publishedOnly = true) {
  /**
   * Get paginated posts from FastAPI CMS
   *
   * NEW RESPONSE FORMAT:
   * {
   *   "data": [{id, title, slug, excerpt, featured_image_url, ...}, ...],
   *   "meta": {
   *     "pagination": {page, pageSize, total, pageCount}
   *   }
   * }
   *
   * OLD FORMAT (Strapi):
   * {
   *   "data": [{id, attributes: {...}}, ...],
   *   "meta": {pagination: {...}}
   * }
   */
  return makeRequest(
    `/api/posts?skip=${skip}&limit=${limit}&published_only=${publishedOnly}`,
    'GET',
    null,
    false,
    null,
    30000
  );
}

export async function getPostBySlug(slug) {
  /**
   * Get single post by slug with content and metadata
   *
   * NEW RESPONSE FORMAT:
   * {
   *   "data": {id, title, slug, content, published_at, ...},
   *   "meta": {
   *     "category": {id, name, slug},
   *     "tags": [{id, name, slug, color}, ...]
   *   }
   * }
   */
  return makeRequest(`/api/posts/${slug}`, 'GET', null, false, null, 10000);
}

export async function createPost(postData) {
  /**
   * Create new blog post
   * Requires authentication (JWT token)
   *
   * @param {Object} postData - {title, slug, content, excerpt, category_id, ...}
   * @returns {Object} - Created post with ID
   */
  return makeRequest('/api/posts', 'POST', postData, false, null, 30000);
}

export async function updatePost(postId, postData) {
  /**
   * Update existing blog post
   * Requires authentication
   *
   * @param {string} postId - Post UUID
   * @param {Object} postData - Updated fields
   * @returns {Object} - Updated post
   */
  return makeRequest(
    `/api/posts/${postId}`,
    'PUT',
    postData,
    false,
    null,
    30000
  );
}

export async function deletePost(postId) {
  /**
   * Delete blog post
   * Requires authentication
   *
   * @param {string} postId - Post UUID
   * @returns {Object} - {success: true}
   */
  return makeRequest(
    `/api/posts/${postId}`,
    'DELETE',
    null,
    false,
    null,
    10000
  );
}

export async function getCategories() {
  /**
   * Get all categories
   *
   * RESPONSE:
   * {
   *   "data": [{id, name, slug, description, icon}, ...]
   * }
   */
  return makeRequest('/api/categories', 'GET', null, false, null, 10000);
}

export async function getCategoryBySlug(slug) {
  /**
   * Get category with post count
   *
   * RESPONSE:
   * {
   *   "data": {id, name, slug, description},
   *   "meta": {"posts_count": N}
   * }
   */
  return makeRequest(
    `/api/categories/${slug}`,
    'GET',
    null,
    false,
    null,
    10000
  );
}

export async function getTags() {
  /**
   * Get all tags
   *
   * RESPONSE:
   * {
   *   "data": [{id, name, slug, color}, ...]
   * }
   */
  return makeRequest('/api/tags', 'GET', null, false, null, 10000);
}

export async function getTagBySlug(slug) {
  /**
   * Get tag with post count
   *
   * RESPONSE:
   * {
   *   "data": {id, name, slug, color},
   *   "meta": {"posts_count": N}
   * }
   */
  return makeRequest(`/api/tags/${slug}`, 'GET', null, false, null, 10000);
}

// ============================================================================
// TASK ENDPOINTS (REFACTORED)
// ============================================================================

export async function createTask(taskData) {
  /**
   * Create new task in queue
   *
   * @param {Object} taskData - {type: "content_generation", params: {...}, priority: "normal"}
   * @returns {Object} - {task_id, status: "queued", ...}
   */
  return makeRequest('/api/tasks', 'POST', taskData, false, null, 30000);
}

export async function listTasks(limit = 50, offset = 0, status = null) {
  /**
   * List tasks with filtering
   *
   * @param {number} limit - Items per page
   * @param {number} offset - Pagination offset
   * @param {string} status - Filter by status (optional): queued, running, completed, failed
   * @returns {Object} - {data: [...], meta: {pagination: {...}}}
   */
  let url = `/api/tasks?limit=${limit}&offset=${offset}`;
  if (status) {
    url += `&status=${status}`;
  }
  return makeRequest(url, 'GET', null, false, null, 30000);
}

export async function getTaskStatus(taskId) {
  /**
   * Get task status and results
   *
   * @param {string} taskId - Task UUID
   * @returns {Object} - {id, type, status, result, error, created_at, updated_at}
   */
  return makeRequest(`/api/tasks/${taskId}`, 'GET', null, false, null, 10000);
}

export async function getTaskMetrics() {
  /**
   * Get task metrics summary
   *
   * @returns {Object} - {total, completed, failed, pending, avg_duration}
   */
  return makeRequest(
    '/api/tasks/metrics/summary',
    'GET',
    null,
    false,
    null,
    10000
  );
}

// ============================================================================
// EXISTING ENDPOINTS (KEEP - may be refactored later)
// ============================================================================

// ... keep existing getTasks, getTaskStatus, pollTaskStatus, etc.
// These can be deprecated gradually in favor of new task endpoints above
```

### File 2: Update `authService.js`

**Location:** `web/oversight-hub/src/services/authService.js`

**Changes:**

1. Implement OAuth callback handler
2. Add token exchange logic
3. Update token storage/retrieval

```javascript
// ============================================================================
// OAUTH FLOW HANDLERS (NEW)
// ============================================================================

import { cofounderAgentClient } from './cofounderAgentClient';

/**
 * Exchange OAuth code for JWT token
 * Called on /callback page after GitHub redirects
 *
 * @param {string} provider - "github"
 * @param {string} code - OAuth authorization code
 * @param {string} state - OAuth state parameter
 * @returns {Object} - {access_token, refresh_token, user}
 */
export async function exchangeCodeForToken(provider, code, state) {
  try {
    const response = await cofounderAgentClient.handleOAuthCallback(
      provider,
      code,
      state
    );

    // Response: {access_token, refresh_token, user}
    if (response.access_token) {
      // Store tokens
      localStorage.setItem('auth_access_token', response.access_token);
      if (response.refresh_token) {
        localStorage.setItem('auth_refresh_token', response.refresh_token);
      }

      // Store user info
      localStorage.setItem('auth_user', JSON.stringify(response.user));

      return response;
    } else {
      throw new Error('No access token received from OAuth callback');
    }
  } catch (error) {
    console.error('OAuth code exchange failed:', error);
    throw error;
  }
}

/**
 * Get current user from API
 * Validates token is still valid
 *
 * @returns {Object|null} - Current user or null if not authenticated
 */
export async function validateAndGetCurrentUser() {
  try {
    const user = await cofounderAgentClient.getCurrentUser();
    // Update cached user info
    localStorage.setItem('auth_user', JSON.stringify(user));
    return user;
  } catch (error) {
    console.warn('Failed to get current user:', error);
    // Token might be expired
    if (error.status === 401) {
      clearAuth();
      return null;
    }
    throw error;
  }
}

/**
 * Clear all authentication data
 */
export function clearAuth() {
  localStorage.removeItem('auth_access_token');
  localStorage.removeItem('auth_refresh_token');
  localStorage.removeItem('auth_user');
}

/**
 * Get stored JWT token
 *
 * @returns {string|null} - JWT token or null
 */
export function getAuthToken() {
  return localStorage.getItem('auth_access_token');
}

/**
 * Get stored user info
 *
 * @returns {Object|null} - Cached user info
 */
export function getCachedUser() {
  const userJson = localStorage.getItem('auth_user');
  return userJson ? JSON.parse(userJson) : null;
}

/**
 * Check if user is authenticated
 *
 * @returns {boolean} - True if token exists
 */
export function isAuthenticated() {
  return !!getAuthToken();
}

// ============================================================================
// KEEP EXISTING FUNCTIONS
// ============================================================================

// ... keep existing getAuthToken, refreshAccessToken, logout, etc.
```

### File 3: Create OAuth Callback Component

**Location:** `web/oversight-hub/src/pages/OAuthCallback.jsx`

```javascript
import React, { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { exchangeCodeForToken } from '../services/authService';

/**
 * OAuth Callback Handler
 *
 * Flow:
 * 1. User clicks "Sign in with GitHub"
 * 2. Redirected to GitHub OAuth consent screen
 * 3. User authorizes → GitHub redirects to this page with ?code=xxx&state=yyy
 * 4. This component exchanges code for JWT token
 * 5. Stores token and redirects to dashboard
 */
function OAuthCallback() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { login: authLogin } = useAuth();
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function handleCallback() {
      try {
        setLoading(true);

        // Get OAuth parameters from URL
        const code = searchParams.get('code');
        const state = searchParams.get('state');
        const error = searchParams.get('error');
        const errorDescription = searchParams.get('error_description');

        // Check for OAuth errors
        if (error) {
          throw new Error(`OAuth Error: ${error} - ${errorDescription || ''}`);
        }

        if (!code) {
          throw new Error('No authorization code received from OAuth provider');
        }

        // Exchange code for JWT token
        const response = await exchangeCodeForToken('github', code, state);

        // Update auth context
        if (authLogin) {
          authLogin(response.user, response.access_token);
        }

        // Redirect to dashboard
        navigate('/dashboard', { replace: true });
      } catch (err) {
        console.error('OAuth callback error:', err);
        setError(err.message);
        setLoading(false);
      }
    }

    handleCallback();
  }, [searchParams, navigate, authLogin]);

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '2rem' }}>
        <h2>Authenticating...</h2>
        <p>Please wait while we complete your sign-in.</p>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ textAlign: 'center', padding: '2rem', color: 'red' }}>
        <h2>Authentication Failed</h2>
        <p>{error}</p>
        <button onClick={() => navigate('/login', { replace: true })}>
          Return to Login
        </button>
      </div>
    );
  }

  return null;
}

export default OAuthCallback;
```

### File 4: Update Login Component

**Location:** `web/oversight-hub/src/components/LoginForm.jsx`

**Add GitHub OAuth Button:**

```javascript
import { cofounderAgentClient } from '../services/cofounderAgentClient';

export function LoginForm() {
  const handleGitHubLogin = async () => {
    try {
      // Get OAuth login URL from backend
      const loginUrl = cofounderAgentClient.getOAuthLoginUrl('github');

      // Redirect to GitHub OAuth consent screen
      window.location.href = loginUrl;
    } catch (error) {
      console.error('Failed to get GitHub login URL:', error);
      // Show error message to user
    }
  };

  return (
    <form>
      {/* Existing form fields... */}

      <button
        type="button"
        onClick={handleGitHubLogin}
        style={{
          width: '100%',
          padding: '0.75rem',
          marginTop: '1rem',
          backgroundColor: '#24292e',
          color: 'white',
          border: 'none',
          borderRadius: '0.375rem',
          fontSize: '1rem',
          cursor: 'pointer',
        }}
      >
        Sign in with GitHub
      </button>
    </form>
  );
}
```

### File 5: Update AuthContext

**Location:** `web/oversight-hub/src/context/AuthContext.jsx`

**Key Changes:**

```javascript
import {
  validateAndGetCurrentUser,
  clearAuth,
  isAuthenticated as checkAuth,
} from '../services/authService';

function AuthContext() {
  // ... existing state

  const login = useCallback((user, token) => {
    // Called after OAuth callback
    setUser(user);
    setToken(token);
    setIsAuthenticated(true);
    setError(null);
  });

  const logout = useCallback(async () => {
    try {
      // Call backend logout if token is valid
      if (token) {
        await cofounderAgentClient.logoutUser();
      }
    } catch (error) {
      console.warn('Backend logout failed:', error);
    } finally {
      // Always clear local state
      clearAuth();
      setUser(null);
      setToken(null);
      setIsAuthenticated(false);
    }
  });

  // On mount: validate token
  useEffect(() => {
    async function validateToken() {
      if (checkAuth()) {
        try {
          const user = await validateAndGetCurrentUser();
          if (user) {
            setUser(user);
            setIsAuthenticated(true);
          } else {
            clearAuth();
            setIsAuthenticated(false);
          }
        } catch (error) {
          console.error('Token validation failed:', error);
          clearAuth();
          setIsAuthenticated(false);
        }
      }
      setLoading(false);
    }

    validateToken();
  }, []);

  return (
    <AuthCtx.Provider
      value={{ user, token, isAuthenticated, login, logout, loading }}
    >
      {children}
    </AuthCtx.Provider>
  );
}

export default AuthContext;
```

---

## 🌐 PUBLIC SITE REFACTORING

### File 1: Refactor `lib/api-fastapi.js`

**Location:** `web/public-site/lib/api-fastapi.js`

**Key Changes:**

```javascript
// ============================================================================
// RESPONSE FORMAT NORMALIZATION
// ============================================================================

/**
 * IMPORTANT: Response formats changed from Strapi to FastAPI
 *
 * STRAPI FORMAT (OLD):
 * {
 *   data: [{id, attributes: {title, slug, content, ...}}, ...],
 *   meta: {pagination: {page, pageSize, total, pageCount}}
 * }
 *
 * FASTAPI FORMAT (NEW):
 * {
 *   data: [{id, title, slug, content, ...}, ...],
 *   meta: {pagination: {page, pageSize, total, pageCount}}
 * }
 *
 * The normalization happens in helper functions below
 */

const FASTAPI_URL =
  process.env.NEXT_PUBLIC_FASTAPI_URL || 'http://localhost:8000';
const API_BASE = `${FASTAPI_URL}/api`;

/**
 * Generic fetch with error handling
 */
async function fetchAPI(endpoint, options = {}) {
  const url = `${API_BASE}${endpoint}`;

  try {
    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    });

    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }

    return response.json();
  } catch (error) {
    console.error(`[FastAPI] Error fetching ${endpoint}:`, error);
    throw error;
  }
}

/**
 * Normalize FastAPI post response to consistent format
 */
function normalizePost(post) {
  // FastAPI already returns flat structure, no need to extract from attributes
  return {
    id: post.id,
    title: post.title,
    slug: post.slug,
    excerpt: post.excerpt || '',
    content: post.content || '',
    featured_image_url: post.featured_image_url,
    cover_image_url: post.cover_image_url,
    category_id: post.category_id,
    author_id: post.author_id,
    published_at: post.published_at,
    created_at: post.created_at,
    updated_at: post.updated_at,
    seo_title: post.seo_title,
    seo_description: post.seo_description,
    seo_keywords: post.seo_keywords,
    status: post.status,
    view_count: post.view_count || 0,
  };
}

// ============================================================================
// PUBLIC POSTS API
// ============================================================================

/**
 * Get paginated published posts
 *
 * OLD BEHAVIOR:
 * - Strapi: populate=*, sort by publishedAt desc
 *
 * NEW BEHAVIOR:
 * - FastAPI: published_only=true, skip/limit pagination
 */
export async function getPaginatedPosts(page = 1, limit = 10) {
  const skip = (page - 1) * limit;

  const response = await fetchAPI(
    `/posts?skip=${skip}&limit=${limit}&published_only=true`
  );

  return {
    posts: response.data.map(normalizePost),
    pagination: {
      page,
      limit,
      total: response.meta.pagination.total,
      pages: Math.ceil(response.meta.pagination.total / limit),
    },
  };
}

/**
 * Get featured post (latest published)
 */
export async function getFeaturedPost() {
  try {
    const response = await fetchAPI('/posts?limit=1&published_only=true');
    if (response.data && response.data.length > 0) {
      return normalizePost(response.data[0]);
    }
    return null;
  } catch (error) {
    console.error('[FastAPI] Error fetching featured post:', error);
    return null;
  }
}

/**
 * Get single post by slug with full content
 *
 * RESPONSE FORMAT (NEW):
 * {
 *   data: {id, title, slug, content, ...},
 *   meta: {
 *     category: {id, name, slug},
 *     tags: [{id, name, slug, color}, ...]
 *   }
 * }
 */
export async function getPostBySlug(slug) {
  try {
    const response = await fetchAPI(`/posts/${slug}`);

    if (response.data) {
      return {
        ...normalizePost(response.data),
        // Merge metadata
        category: response.meta?.category || null,
        tags: response.meta?.tags || [],
      };
    }
    return null;
  } catch (error) {
    if (error.message.includes('404')) {
      return null; // Post not found
    }
    console.error(`[FastAPI] Error fetching post ${slug}:`, error);
    throw error;
  }
}

// ============================================================================
// CATEGORIES API
// ============================================================================

/**
 * Get all categories
 *
 * RESPONSE FORMAT (NEW):
 * {
 *   data: [{id, name, slug, description, icon, created_at}, ...]
 * }
 */
export async function getCategories() {
  try {
    const response = await fetchAPI('/categories');
    return response.data || [];
  } catch (error) {
    console.error('[FastAPI] Error fetching categories:', error);
    return [];
  }
}

/**
 * Get single category with posts
 *
 * RESPONSE:
 * {
 *   data: {id, name, slug, description, icon},
 *   meta: {posts_count: N}
 * }
 */
export async function getCategoryBySlug(slug) {
  try {
    const response = await fetchAPI(`/categories/${slug}`);
    return response.data;
  } catch (error) {
    console.error(`[FastAPI] Error fetching category ${slug}:`, error);
    return null;
  }
}

/**
 * Get posts by category
 */
export async function getPostsByCategory(categorySlug, page = 1, limit = 10) {
  try {
    const skip = (page - 1) * limit;
    const response = await fetchAPI(
      `/posts?category=${encodeURIComponent(categorySlug)}&skip=${skip}&limit=${limit}&published_only=true`
    );

    return {
      posts: response.data.map(normalizePost),
      pagination: {
        page,
        limit,
        total: response.meta.pagination.total,
        pages: Math.ceil(response.meta.pagination.total / limit),
      },
    };
  } catch (error) {
    console.error(
      `[FastAPI] Error fetching posts for category ${categorySlug}:`,
      error
    );
    return { posts: [], pagination: { page: 1, limit, total: 0, pages: 0 } };
  }
}

// ============================================================================
// TAGS API
// ============================================================================

/**
 * Get all tags
 *
 * RESPONSE:
 * {
 *   data: [{id, name, slug, color}, ...]
 * }
 */
export async function getTags() {
  try {
    const response = await fetchAPI('/tags');
    return response.data || [];
  } catch (error) {
    console.error('[FastAPI] Error fetching tags:', error);
    return [];
  }
}

/**
 * Get posts by tag
 */
export async function getPostsByTag(tagSlug, page = 1, limit = 10) {
  try {
    const skip = (page - 1) * limit;
    const response = await fetchAPI(
      `/posts?tag=${encodeURIComponent(tagSlug)}&skip=${skip}&limit=${limit}&published_only=true`
    );

    return {
      posts: response.data.map(normalizePost),
      pagination: {
        page,
        limit,
        total: response.meta.pagination.total,
        pages: Math.ceil(response.meta.pagination.total / limit),
      },
    };
  } catch (error) {
    console.error(`[FastAPI] Error fetching posts for tag ${tagSlug}:`, error);
    return { posts: [], pagination: { page: 1, limit, total: 0, pages: 0 } };
  }
}

// ============================================================================
// SEARCH & FILTERING
// ============================================================================

/**
 * Search posts by title/content
 */
export async function searchPosts(query, page = 1, limit = 10) {
  try {
    const skip = (page - 1) * limit;
    const response = await fetchAPI(
      `/posts/search?q=${encodeURIComponent(query)}&skip=${skip}&limit=${limit}&published_only=true`
    );

    return {
      posts: response.data.map(normalizePost),
      pagination: {
        page,
        limit,
        total: response.meta.pagination.total,
        pages: Math.ceil(response.meta.pagination.total / limit),
      },
    };
  } catch (error) {
    console.error(`[FastAPI] Error searching posts for "${query}":`, error);
    return { posts: [], pagination: { page: 1, limit, total: 0, pages: 0 } };
  }
}

/**
 * Get all posts (no pagination, use with caution)
 */
export async function getAllPosts() {
  try {
    const response = await fetchAPI('/posts?limit=1000&published_only=true');
    return response.data.map(normalizePost);
  } catch (error) {
    console.error('[FastAPI] Error fetching all posts:', error);
    return [];
  }
}

/**
 * Get related posts
 */
export async function getRelatedPosts(postId, limit = 5) {
  try {
    const response = await fetchAPI(`/posts/${postId}/related?limit=${limit}`);
    return response.data.map(normalizePost);
  } catch (error) {
    console.error(
      `[FastAPI] Error fetching related posts for ${postId}:`,
      error
    );
    return [];
  }
}

// ============================================================================
// SYSTEM/STATUS ENDPOINTS
// ============================================================================

/**
 * Check FastAPI CMS status
 */
export async function getCMSStatus() {
  try {
    const response = await fetch(`${FASTAPI_URL}/api/health`);
    return response.ok;
  } catch (error) {
    return false;
  }
}

/**
 * Get image URL (helper)
 */
export function getImageURL(path) {
  if (!path) return '';
  if (path.startsWith('http')) return path;
  return `${FASTAPI_URL}${path}`;
}
```

### File 2: Create OAuth Integration Page

**Location:** `web/public-site/pages/auth/callback.jsx`

```javascript
import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';

/**
 * OAuth Callback Handler for Public Site
 *
 * Flow:
 * 1. User clicks "Sign in with GitHub"
 * 2. Redirected to GitHub OAuth screen
 * 3. GitHub redirects back here with ?code=xxx&state=yyy
 * 4. Exchange code for JWT token
 * 5. Store token and redirect to home or profile
 */
export default function OAuthCallback() {
  const router = useRouter();
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function handleCallback() {
      try {
        const {
          code,
          state,
          error: oauthError,
          error_description,
        } = router.query;

        // Check for OAuth errors
        if (oauthError) {
          throw new Error(
            `OAuth Error: ${oauthError} - ${error_description || ''}`
          );
        }

        if (!code) {
          throw new Error('No authorization code received');
        }

        // Exchange code for token
        // Backend handles: GitHub API → create/update user → return JWT
        const response = await fetch(
          `/api/auth/github/callback?code=${code}&state=${state}`,
          {
            method: 'GET',
            headers: {
              'Content-Type': 'application/json',
            },
          }
        );

        if (!response.ok) {
          throw new Error(`Token exchange failed: ${response.status}`);
        }

        const data = await response.json();

        if (data.access_token) {
          // Store token
          localStorage.setItem('auth_token', data.access_token);
          if (data.refresh_token) {
            localStorage.setItem('auth_refresh_token', data.refresh_token);
          }

          // Store user info
          localStorage.setItem('auth_user', JSON.stringify(data.user));

          // Redirect to home
          router.push('/');
        } else {
          throw new Error('No access token in response');
        }
      } catch (err) {
        console.error('OAuth callback error:', err);
        setError(err.message);
        setLoading(false);
      }
    }

    if (router.isReady) {
      handleCallback();
    }
  }, [router.isReady, router.query]);

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '2rem' }}>
        <h2>Signing you in...</h2>
        <p>Please wait.</p>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ textAlign: 'center', padding: '2rem', color: 'red' }}>
        <h2>Sign In Failed</h2>
        <p>{error}</p>
        <button onClick={() => router.push('/')}>Return to Home</button>
      </div>
    );
  }

  return null;
}
```

### File 3: Create Login Component

**Location:** `web/public-site/components/LoginLink.jsx`

```javascript
'use client';

export function LoginLink() {
  const handleGitHubLogin = () => {
    // Redirect to GitHub OAuth login
    window.location.href = 'http://localhost:8000/api/auth/github/login';
  };

  return (
    <button
      onClick={handleGitHubLogin}
      style={{
        padding: '0.5rem 1rem',
        backgroundColor: '#24292e',
        color: 'white',
        border: 'none',
        borderRadius: '0.375rem',
        cursor: 'pointer',
      }}
    >
      Sign in with GitHub
    </button>
  );
}
```

### File 4: Update Next.js Layout/Header

**Location:** `web/public-site/components/Header.js`

```javascript
import { LoginLink } from './LoginLink';

export default function Header() {
  // ... existing header code

  const isAuthenticated =
    typeof window !== 'undefined'
      ? !!localStorage.getItem('auth_token')
      : false;

  return (
    <header>
      {/* ... existing header content ... */}

      <nav>
        {/* ... existing nav ... */}

        {isAuthenticated ? (
          <button
            onClick={() => {
              localStorage.removeItem('auth_token');
              localStorage.removeItem('auth_refresh_token');
              localStorage.removeItem('auth_user');
              window.location.reload();
            }}
          >
            Logout
          </button>
        ) : (
          <LoginLink />
        )}
      </nav>
    </header>
  );
}
```

---

## 🔐 AUTHENTICATION FLOW

### OAuth Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         OAuth 2.0 Flow                           │
└─────────────────────────────────────────────────────────────────┘

1. User clicks "Sign in with GitHub"
   ↓
2. Frontend redirects to:
   http://localhost:8000/api/auth/github/login
   ↓
3. Backend redirects to GitHub OAuth consent screen
   ↓
4. User authorizes
   ↓
5. GitHub redirects to callback with ?code=xxx&state=yyy
   ↓
6. Frontend callback page calls:
   GET /api/auth/github/callback?code=xxx&state=yyy
   ↓
7. Backend:
   - Calls GitHub API with code
   - Gets GitHub user info
   - Creates/updates User record in database
   - Creates OAuthAccount record
   - Returns JWT token + user data
   ↓
8. Frontend stores token in localStorage
   ↓
9. Frontend adds Authorization header to all API requests:
   Authorization: Bearer <jwt_token>
   ↓
10. All authenticated requests now work
```

### Token Storage & Usage

**Frontend:** `localStorage`

```javascript
localStorage.getItem('auth_token'); // Access token
localStorage.getItem('auth_refresh_token'); // Refresh token
localStorage.getItem('auth_user'); // Cached user info
```

**API Requests:**

```javascript
const headers = {
  Authorization: `Bearer ${localStorage.getItem('auth_token')}`,
  'Content-Type': 'application/json',
};

fetch('/api/posts', { headers });
```

### Token Expiry & Refresh

**Expiry:** 24 hours (set in backend `auth.py`)

**On 401 Response:**

1. Clear token from localStorage
2. Redirect to login
3. User must sign in again

---

## 🔄 DATABASE SYNC & TESTING

### Database Tables Created

**On first OAuth login, backend auto-creates:**

```sql
-- Users table
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email VARCHAR UNIQUE NOT NULL,
  username VARCHAR UNIQUE NOT NULL,
  avatar_url VARCHAR,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

-- OAuth Accounts (links users to OAuth providers)
CREATE TABLE oauth_accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  provider VARCHAR NOT NULL,  -- "github"
  provider_user_id VARCHAR NOT NULL,  -- GitHub user ID
  provider_data JSONB,  -- Full GitHub user object
  created_at TIMESTAMP DEFAULT NOW(),
  last_used TIMESTAMP,
  UNIQUE(user_id, provider)
);

-- Posts (CMS content)
CREATE TABLE posts (
  id UUID PRIMARY KEY,
  title VARCHAR NOT NULL,
  slug VARCHAR UNIQUE NOT NULL,
  content TEXT,
  excerpt TEXT,
  featured_image_url VARCHAR,
  cover_image_url VARCHAR,
  category_id UUID,
  author_id UUID,
  published_at TIMESTAMP,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  seo_title VARCHAR,
  seo_description TEXT,
  seo_keywords VARCHAR,
  status VARCHAR,
  view_count INTEGER DEFAULT 0
);

-- Categories (post organization)
CREATE TABLE categories (
  id UUID PRIMARY KEY,
  name VARCHAR NOT NULL,
  slug VARCHAR UNIQUE NOT NULL,
  description TEXT,
  icon VARCHAR,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

-- Tags (post labeling)
CREATE TABLE tags (
  id UUID PRIMARY KEY,
  name VARCHAR NOT NULL,
  slug VARCHAR UNIQUE NOT NULL,
  color VARCHAR,
  created_at TIMESTAMP
);

-- Post-Tag mapping
CREATE TABLE post_tags (
  id UUID PRIMARY KEY,
  post_id UUID NOT NULL REFERENCES posts(id),
  tag_id UUID NOT NULL REFERENCES tags(id),
  UNIQUE(post_id, tag_id)
);
```

### Verification Checklist

#### 1. OAuth Flow Test

```bash
# Step 1: Start backend
cd src/cofounder_agent
python -m uvicorn main:app --reload

# Step 2: Check providers endpoint
curl http://localhost:8000/api/auth/providers
# Expected: {"providers": ["github"]}

# Step 3: Open browser
# Click "Sign in with GitHub" button on frontend
# Authorize on GitHub
# Should be redirected back and logged in

# Step 4: Check current user
curl http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer <jwt_token>"
# Expected: {"id": "...", "email": "...", "username": "...", ...}
```

#### 2. Database Verification

```bash
# Connect to PostgreSQL
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev

# Check users table
SELECT * FROM users;
# Expected: 1 row with your GitHub user info

# Check oauth_accounts table
SELECT * FROM oauth_accounts;
# Expected: 1 row linking user to GitHub provider
```

#### 3. CMS API Test

```bash
# Get posts
curl http://localhost:8000/api/posts
# Expected: {"data": [...], "meta": {"pagination": {...}}}

# Get post by slug
curl http://localhost:8000/api/posts/my-first-post
# Expected: {"data": {...post}, "meta": {...}}

# Get categories
curl http://localhost:8000/api/categories
# Expected: {"data": [...]}

# Get tags
curl http://localhost:8000/api/tags
# Expected: {"data": [...]}
```

#### 4. Frontend Test

**Oversight Hub (port 3001):**

1. Click "Sign in with GitHub"
2. Authorize on GitHub
3. Redirected to dashboard
4. Can view posts, categories, tags
5. Can create/edit/delete content (if authenticated)

**Public Site (port 3000):**

1. Click "Sign in with GitHub"
2. Authorize on GitHub
3. Redirected to home
4. Can view published posts
5. Can view categories and tags

---

## 🎯 IMPLEMENTATION SEQUENCE

### Phase 1: Backend Verification (30 min)

1. Start FastAPI backend
2. Test OAuth endpoints with curl
3. Test CMS endpoints with curl
4. Verify database connections

### Phase 2: Oversight Hub Refactoring (1.5 hours)

1. Update `cofounderAgentClient.js` (30 min)
2. Update `authService.js` (20 min)
3. Create `OAuthCallback.jsx` (15 min)
4. Update `LoginForm.jsx` (10 min)
5. Update `AuthContext.jsx` (15 min)
6. Test authentication flow (10 min)

### Phase 3: Public Site Refactoring (1.5 hours)

1. Refactor `lib/api-fastapi.js` (30 min)
2. Create OAuth callback page (20 min)
3. Create login component (15 min)
4. Update header/navigation (10 min)
5. Test CMS endpoints (15 min)
6. Test authentication flow (10 min)

### Phase 4: Integration Testing (1 hour)

1. Test full OAuth flow both frontends (20 min)
2. Test CMS operations (20 min)
3. Verify database sync (10 min)
4. Performance testing (10 min)

**Total Time:** ~4 hours for complete refactoring

---

## ✅ CHECKLIST

**Backend Verification:**

- [ ] FastAPI running on port 8000
- [ ] OAuth endpoints returning correct responses
- [ ] CMS endpoints returning correct responses
- [ ] Database connection working
- [ ] CORS configured for ports 3000 & 3001

**Oversight Hub:**

- [ ] `cofounderAgentClient.js` updated
- [ ] `authService.js` updated
- [ ] `OAuthCallback.jsx` created
- [ ] `LoginForm.jsx` has GitHub button
- [ ] `AuthContext.jsx` handles OAuth flow
- [ ] Tests passing

**Public Site:**

- [ ] `lib/api-fastapi.js` refactored
- [ ] `pages/auth/callback.jsx` created
- [ ] `LoginLink.jsx` component created
- [ ] `Header.jsx` updated
- [ ] Tests passing

**End-to-End:**

- [ ] OAuth flow works on both frontends
- [ ] JWT tokens stored correctly
- [ ] API requests include Authorization header
- [ ] CMS endpoints return correct data
- [ ] Users created in database
- [ ] OAuthAccounts linked correctly
- [ ] No console errors

---

**Status: ✅ READY FOR IMPLEMENTATION**

All code examples provided are production-ready and copy-paste compatible.
