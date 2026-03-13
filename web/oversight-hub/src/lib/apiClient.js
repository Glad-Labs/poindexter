/**
 * Refactored API Client for Oversight Hub
 * Matches new FastAPI endpoint structure
 *
 * TASK ENDPOINTS:
 * - GET  /api/tasks - List tasks with pagination & filtering
 * - POST /api/tasks - Create new content generation task
 * - GET  /api/tasks/{id} - Get specific task details
 * - PATCH /api/tasks/{id} - Update task status/metadata
 * - GET  /api/tasks/{id}/result - Get generated content
 * - GET  /api/tasks/{id}/preview - Preview content before publishing
 * - POST /api/tasks/{id}/publish - Publish task result as post
 * - GET  /api/tasks/metrics - Get task execution metrics
 * - POST /api/tasks/batch - Get multiple tasks
 * - GET  /api/tasks/export - Export tasks as CSV/JSON
 *
 * POST ENDPOINTS:
 * - GET  /api/posts - List posts with pagination
 * - POST /api/posts - Create new blog post
 * - GET  /api/posts/{id} - Get post details
 * - PATCH /api/posts/{id} - Update post
 * - DELETE /api/posts/{id} - Delete post
 * - GET  /api/categories - List categories
 * - GET  /api/tags - List tags
 *
 * SYSTEM ENDPOINTS:
 * - GET  /api/health - System health check
 * - GET  /api/metrics - Overall system metrics
 * - GET  /api/models - List available AI models
 * - POST /api/models/test - Test model connectivity
 * - GET  /api/models/status - Get provider status
 */

import axios from 'axios';
import { clearPersistedAuthState } from '../services/authService';

// ============================================================================
// API CLIENT CONFIGURATION
// ============================================================================

const API_BASE_URL =
  process.env.REACT_APP_API_BASE_URL || 'http://localhost:8000';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor: Add authorization token
apiClient.interceptors.request.use(
  (config) => {
    // Try to get token from Zustand persist storage first
    let token = null;

    const persistedData = localStorage.getItem('oversight-hub-storage');
    if (persistedData) {
      try {
        const parsed = JSON.parse(persistedData);
        token = parsed.state?.accessToken || parsed.state?.auth_token;
      } catch (e) {
        console.warn('Failed to parse Zustand persist storage:', e);
      }
    }

    // Fallback to direct localStorage key (for backwards compatibility)
    if (!token) {
      token = localStorage.getItem('auth_token');
    }

    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor: Handle common errors
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Token expired, clear auth
      clearPersistedAuthState();
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

// ============================================================================
// TASK MANAGEMENT ENDPOINTS
// ============================================================================

/**
 * List all tasks with pagination and filtering
 * @param {number} skip - Number of tasks to skip
 * @param {number} limit - Max tasks to return (1-100)
 * @param {string} status - Filter by status: pending, in_progress, completed, failed
 * @returns {Promise<Object>} List of tasks with pagination metadata
 */
export const listTasks = async (skip = 0, limit = 20, status = null) => {
  try {
    const params = new URLSearchParams({ skip, limit });
    if (status) {
      params.append('status', status);
    }

    const response = await apiClient.get(`/api/tasks?${params.toString()}`);
    return response.data;
  } catch (error) {
    console.error('Error listing tasks:', error);
    throw error;
  }
};

/**
 * Create a new content generation task
 * @param {Object} taskData - Task creation data
 * @param {string} taskData.task_name - Name of the task
 * @param {string} taskData.topic - Blog post topic
 * @param {string} taskData.primary_keyword - SEO keyword (optional)
 * @param {string} taskData.target_audience - Target audience (optional)
 * @returns {Promise<Object>} Created task with ID and metadata
 */
export const createTask = async (taskData) => {
  try {
    const response = await apiClient.post('/api/tasks', taskData);
    return response.data;
  } catch (error) {
    console.error('Error creating task:', error);
    throw error;
  }
};

/**
 * Get task details by ID
 * @param {string} taskId - Task ID
 * @returns {Promise<Object>} Task details including status and result
 */
export const getTask = async (taskId) => {
  try {
    const response = await apiClient.get(`/api/tasks/${taskId}`);
    return response.data;
  } catch (error) {
    console.error(`Error getting task ${taskId}:`, error);
    throw error;
  }
};

/**
 * Update task status or metadata
 * @param {string} taskId - Task ID
 * @param {Object} updates - Fields to update
 * @param {string} updates.status - New status: pending, in_progress, completed, failed
 * @param {Object} updates.metadata - Updated metadata
 * @returns {Promise<Object>} Updated task
 */
export const updateTask = async (taskId, updates) => {
  try {
    const response = await apiClient.patch(`/api/tasks/${taskId}`, updates);
    return response.data;
  } catch (error) {
    console.error(`Error updating task ${taskId}:`, error);
    throw error;
  }
};

/**
 * Pause a running task
 * @param {string} taskId - Task ID
 * @returns {Promise<Object>} Updated task with status 'paused'
 */
export const pauseTask = async (taskId) => {
  return updateTask(taskId, { status: 'paused' });
};

/**
 * Resume a paused task
 * @param {string} taskId - Task ID
 * @returns {Promise<Object>} Updated task with status 'in_progress'
 */
export const resumeTask = async (taskId) => {
  return updateTask(taskId, { status: 'in_progress' });
};

/**
 * Cancel a task
 * @param {string} taskId - Task ID
 * @returns {Promise<Object>} Updated task with status 'cancelled'
 */
export const cancelTask = async (taskId) => {
  return updateTask(taskId, { status: 'cancelled' });
};

// ============================================================================
// POST MANAGEMENT ENDPOINTS
// ============================================================================

/**
 * List all blog posts with pagination and filtering
 * @param {number} skip - Number of posts to skip
 * @param {number} limit - Max posts to return (1-100)
 * @param {boolean} published_only - Only return published posts
 * @returns {Promise<Object>} List of posts with pagination metadata
 */
export const listPosts = async (
  skip = 0,
  limit = 20,
  published_only = true
) => {
  try {
    const response = await apiClient.get('/api/posts', {
      params: { skip, limit, published_only },
    });
    return response.data;
  } catch (error) {
    console.error('Error listing posts:', error);
    throw error;
  }
};

// Alias for convenience
export const getPosts = listPosts;

/**
 * Create a new blog post
 * @param {Object} postData - Post creation data
 * @param {string} postData.title - Post title
 * @param {string} postData.slug - URL slug (auto-generated if not provided)
 * @param {string} postData.content - Post content (markdown)
 * @param {string} postData.excerpt - Short excerpt
 * @param {string} postData.category_id - Category ID (optional)
 * @param {Array} postData.tags - Array of tag IDs (optional)
 * @param {string} postData.status - Status: draft, published, archived
 * @param {Object} postData.seo - SEO metadata
 * @returns {Promise<Object>} Created post with ID
 */
export const createPost = async (postData) => {
  try {
    const response = await apiClient.post('/api/posts', postData);
    return response.data;
  } catch (error) {
    console.error('Error creating post:', error);
    throw error;
  }
};

/**
 * Get post details by ID
 * @param {string} postId - Post ID
 * @returns {Promise<Object>} Post details with content and metadata
 */
export const getPost = async (postId) => {
  try {
    const response = await apiClient.get(`/api/posts/${postId}`);
    return response.data;
  } catch (error) {
    console.error(`Error getting post ${postId}:`, error);
    throw error;
  }
};

/**
 * Get post by slug
 * @param {string} slug - Post slug
 * @returns {Promise<Object>} Post details
 */
export const getPostBySlug = async (slug) => {
  try {
    const response = await apiClient.get('/api/posts', {
      params: { slug },
    });
    return response.data?.data?.[0] || null;
  } catch (error) {
    console.error(`Error getting post by slug ${slug}:`, error);
    throw error;
  }
};

/**
 * Update post
 * @param {string} postId - Post ID
 * @param {Object} updates - Fields to update
 * @returns {Promise<Object>} Updated post
 */
export const updatePost = async (postId, updates) => {
  try {
    const response = await apiClient.patch(`/api/posts/${postId}`, updates);
    return response.data;
  } catch (error) {
    console.error(`Error updating post ${postId}:`, error);
    throw error;
  }
};

/**
 * Publish a post (draft → published)
 * @param {string} postId - Post ID
 * @returns {Promise<Object>} Updated post with status 'published'
 */
export const publishPost = async (postId) => {
  return updatePost(postId, {
    status: 'published',
    published_at: new Date().toISOString(),
  });
};

/**
 * Archive a post
 * @param {string} postId - Post ID
 * @returns {Promise<Object>} Updated post with status 'archived'
 */
export const archivePost = async (postId) => {
  return updatePost(postId, { status: 'archived' });
};

/**
 * Delete a post
 * @param {string} postId - Post ID
 * @returns {Promise<Object>} Deletion confirmation
 */
export const deletePost = async (postId) => {
  try {
    const response = await apiClient.delete(`/api/posts/${postId}`);
    return response.data;
  } catch (error) {
    console.error(`Error deleting post ${postId}:`, error);
    throw error;
  }
};

// ============================================================================
// CATEGORY & TAG ENDPOINTS
// ============================================================================

/**
 * List all categories
 * @returns {Promise<Object>} List of categories
 */
export const listCategories = async () => {
  try {
    const response = await apiClient.get('/api/categories');
    return response.data;
  } catch (error) {
    console.error('Error listing categories:', error);
    throw error;
  }
};

/**
 * List all tags
 * @returns {Promise<Object>} List of tags
 */
export const listTags = async () => {
  try {
    const response = await apiClient.get('/api/tags');
    return response.data;
  } catch (error) {
    console.error('Error listing tags:', error);
    throw error;
  }
};

// ============================================================================
// SYSTEM ENDPOINTS
// ============================================================================

/**
 * Get system health status
 * @returns {Promise<Object>} Health check with service statuses
 */
export const getHealth = async () => {
  try {
    const response = await apiClient.get('/api/health');
    return response.data;
  } catch (error) {
    console.error('Error getting health status:', error);
    throw error;
  }
};

/**
 * Get system metrics and statistics
 * @returns {Promise<Object>} System metrics
 */
export const getMetrics = async () => {
  try {
    const response = await apiClient.get('/api/metrics');
    return response.data;
  } catch (error) {
    console.error('Error getting metrics:', error);
    throw error;
  }
};

/**
 * Get task execution metrics
 * @returns {Promise<Object>} Task metrics (success rate, avg execution time, etc)
 */
export const getTaskMetrics = async () => {
  try {
    const response = await apiClient.get('/api/tasks/metrics');
    return response.data;
  } catch (error) {
    console.error('Error getting task metrics:', error);
    throw error;
  }
};

/**
 * Get metrics
 * @returns {Promise<Object>} Metrics (posts created, quality scores, etc)
 */
export const getContentMetrics = async () => {
  try {
    const response = await apiClient.get('/api/metrics');
    return response.data;
  } catch (error) {
    console.error('Error getting metrics:', error);
    throw error;
  }
};

// ============================================================================
// MODELS & PROVIDERS
// ============================================================================

/**
 * List available AI models
 * @returns {Promise<Object>} Available models grouped by provider
 */
export const listModels = async () => {
  try {
    const response = await apiClient.get('/api/models');
    return response.data;
  } catch (error) {
    console.error('Error listing models:', error);
    throw error;
  }
};

/**
 * Test model connectivity
 * @param {string} provider - Model provider (ollama, openai, anthropic, google)
 * @param {string} model - Model name
 * @returns {Promise<Object>} Connection test result
 */
export const testModel = async (provider, model) => {
  try {
    const response = await apiClient.post('/api/models/test', {
      provider,
      model,
    });
    return response.data;
  } catch (error) {
    console.error(`Error testing model ${provider}/${model}:`, error);
    throw error;
  }
};

/**
 * Get model provider status
 * @returns {Promise<Object>} Status of all configured providers
 */
export const getModelStatus = async () => {
  try {
    const response = await apiClient.get('/api/models/status');
    return response.data;
  } catch (error) {
    console.error('Error getting model status:', error);
    throw error;
  }
};

// ============================================================================
// CONTENT GENERATION ENDPOINTS
// ============================================================================

/**
 * Generate blog content from task
 * @param {string} taskId - Task ID to generate from
 * @returns {Promise<Object>} Generated content
 */
export const generateContent = async (taskId) => {
  try {
    const response = await apiClient.post(`/api/tasks/${taskId}/generate`);
    return response.data;
  } catch (error) {
    console.error(`Error generating content for task ${taskId}:`, error);
    throw error;
  }
};

/**
 * Get task result/generated content
 * @param {string} taskId - Task ID
 * @returns {Promise<Object>} Generated content and metadata
 */
export const getTaskResult = async (taskId) => {
  try {
    const response = await apiClient.get(`/api/tasks/${taskId}/result`);
    return response.data;
  } catch (error) {
    console.error(`Error getting task result ${taskId}:`, error);
    throw error;
  }
};

/**
 * Preview generated content
 * @param {string} taskId - Task ID
 * @returns {Promise<Object>} Preview data with rendering information
 */
export const previewContent = async (taskId) => {
  try {
    const response = await apiClient.get(`/api/tasks/${taskId}/preview`);
    return response.data;
  } catch (error) {
    console.error(`Error previewing content for task ${taskId}:`, error);
    throw error;
  }
};

/**
 * Publish task result as post
 * @param {string} taskId - Task ID
 * @param {Object} postData - Additional post data (category, tags, etc)
 * @returns {Promise<Object>} Created post from task result
 */
export const publishTaskAsPost = async (taskId, postData = {}) => {
  try {
    const response = await apiClient.post(
      `/api/tasks/${taskId}/publish`,
      postData
    );
    return response.data;
  } catch (error) {
    console.error(`Error publishing task ${taskId} as post:`, error);
    throw error;
  }
};

// ============================================================================
// BATCH OPERATIONS
// ============================================================================

/**
 * Get multiple tasks efficiently
 * @param {Array<string>} taskIds - Array of task IDs
 * @returns {Promise<Object>} List of tasks
 */
export const getTasksBatch = async (taskIds) => {
  try {
    const response = await apiClient.post('/api/tasks/batch', { ids: taskIds });
    return response.data;
  } catch (error) {
    console.error('Error getting tasks batch:', error);
    throw error;
  }
};

/**
 * Export tasks as CSV/JSON
 * @param {Object} filters - Filter options
 * @param {string} format - Export format: csv, json
 * @returns {Promise<Blob>} File blob
 */
export const exportTasks = async (filters = {}, format = 'csv') => {
  try {
    const response = await apiClient.get('/api/tasks/export', {
      params: { format, ...filters },
      responseType: 'blob',
    });
    return response.data;
  } catch (error) {
    console.error('Error exporting tasks:', error);
    throw error;
  }
};

// ============================================================================
// ERROR HANDLING UTILITIES
// ============================================================================

/**
 * Format API error for display
 * @param {Error} error - Axios error
 * @returns {string} User-friendly error message
 */
export const formatApiError = (error) => {
  if (error.response?.data?.detail) {
    return error.response.data.detail;
  }
  if (error.response?.statusText) {
    return error.response.statusText;
  }
  if (error.message) {
    return error.message;
  }
  return 'An unexpected error occurred';
};

/**
 * Check if error is recoverable
 * @param {Error} error - Axios error
 * @returns {boolean} True if retry is recommended
 */
export const isRecoverableError = (error) => {
  const status = error.response?.status;
  // 5xx errors and network errors are recoverable
  return (
    !status || (status >= 500 && status < 600) || error.code === 'ECONNABORTED'
  );
};

/**
 * Retry API call with exponential backoff
 * @param {Function} apiCall - API function to retry
 * @param {number} maxRetries - Maximum retry attempts
 * @returns {Promise<Object>} API response
 */
export const retryWithBackoff = async (apiCall, maxRetries = 3) => {
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await apiCall();
    } catch (error) {
      if (!isRecoverableError(error) || attempt === maxRetries - 1) {
        throw error;
      }
      const delay = Math.pow(2, attempt) * 1000; // Exponential backoff
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
};

// ============================================================================
// EXPORT ALL
// ============================================================================

const apiClientMethods = {
  // Tasks
  listTasks,
  createTask,
  getTask,
  updateTask,
  pauseTask,
  resumeTask,
  cancelTask,

  // Posts
  listPosts,
  createPost,
  getPost,
  getPostBySlug,
  updatePost,
  publishPost,
  archivePost,
  deletePost,

  // Categories & Tags
  listCategories,
  listTags,

  // System
  getHealth,
  getMetrics,
  getTaskMetrics,
  getContentMetrics,

  // Models
  listModels,
  testModel,
  getModelStatus,

  // Content Generation
  generateContent,
  getTaskResult,
  previewContent,
  publishTaskAsPost,

  // Batch Operations
  getTasksBatch,
  exportTasks,

  // Utilities
  formatApiError,
  isRecoverableError,
  retryWithBackoff,
};

export default apiClientMethods;
