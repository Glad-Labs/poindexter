import logger from '@/lib/logger';
/**
 * socialService.js (Phase 2.3)
 *
 * Service wrapper for social media management API endpoints
 * Handles platform connections, post creation, and social analytics
 */

import { makeRequest } from './cofounderAgentClient';

/**
 * Get list of connected social platforms
 * @returns {Promise<Object>} Platform connection statuses
 */
export const getPlatforms = async () => {
  try {
    const response = await makeRequest(
      `/api/social/platforms`,
      'GET',
      null,
      false,
      null,
      5000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('Failed to fetch social platforms:', error);
    }
    throw error;
  }
};

/**
 * Connect a social media platform
 * @param {Object} params - Connection parameters
 * @param {string} params.platform - Platform name (twitter, facebook, instagram, etc.)
 * @param {Object} params.credentials - Optional: Platform credentials/tokens
 * @returns {Promise<Object>} Connection status
 */
export const connectPlatform = async (params) => {
  try {
    const response = await makeRequest(
      `/api/social/connect`,
      'POST',
      params,
      false,
      null,
      20000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('Failed to connect platform:', error);
    }
    throw error;
  }
};

/**
 * Disconnect a social media platform
 * @param {string} platform - Platform name to disconnect
 * @returns {Promise<Object>} Disconnection status
 */
export const disconnectPlatform = async (platform) => {
  try {
    const response = await makeRequest(
      `/api/social/disconnect`,
      'POST',
      { platform },
      false,
      null,
      10000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('Failed to disconnect platform:', error);
    }
    throw error;
  }
};

/**
 * Get all social media posts
 * @param {Object} options - Query options
 * @param {string} options.status - Filter by status: 'published', 'scheduled', 'draft'
 * @param {string} options.platform - Filter by platform
 * @param {number} options.limit - Max results
 * @returns {Promise<Object>} List of posts and analytics
 */
export const getPosts = async (options = {}) => {
  try {
    const params = new URLSearchParams();
    if (options.status) params.append('status', options.status);
    if (options.platform) params.append('platform', options.platform);
    if (options.limit) params.append('limit', options.limit);

    const queryString = params.toString() ? `?${params.toString()}` : '';
    const response = await makeRequest(
      `/api/social/posts${queryString}`,
      'GET',
      null,
      false,
      null,
      10000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('Failed to fetch social posts:', error);
    }
    throw error;
  }
};

/**
 * Create a new social media post
 * @param {Object} postData - Post creation parameters
 * @param {string} postData.content - Post content/text
 * @param {Array<string>} postData.platforms - Target platforms
 * @param {string} postData.scheduled_time - Optional: Schedule time for posting
 * @param {string} postData.tone - Post tone/style
 * @param {boolean} postData.include_hashtags - Whether to include hashtags
 * @param {boolean} postData.include_emojis - Whether to include emojis
 * @returns {Promise<Object>} Created post details
 */
export const createPost = async (postData) => {
  try {
    const response = await makeRequest(
      `/api/social/posts`,
      'POST',
      postData,
      false,
      null,
      15000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('Failed to create post:', error);
    }
    throw error;
  }
};

/**
 * Update an existing social media post
 * @param {string} postId - Post ID to update
 * @param {Object} updates - Fields to update
 * @returns {Promise<Object>} Updated post details
 */
export const updatePost = async (postId, updates) => {
  try {
    const response = await makeRequest(
      `/api/social/posts/${postId}`,
      'PUT',
      updates,
      false,
      null,
      10000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('Failed to update post:', error);
    }
    throw error;
  }
};

/**
 * Delete a social media post
 * @param {string} postId - Post ID to delete
 * @returns {Promise<Object>} Deletion response
 */
export const deletePost = async (postId) => {
  try {
    const response = await makeRequest(
      `/api/social/posts/${postId}`,
      'DELETE',
      null,
      false,
      null,
      10000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('Failed to delete post:', error);
    }
    throw error;
  }
};

/**
 * Get social analytics for all posts
 * @param {string} range - Time range: '7d', '30d', '90d', 'all'
 * @returns {Promise<Object>} Aggregated social analytics
 */
export const getSocialAnalytics = async (range = '30d') => {
  try {
    const response = await makeRequest(
      `/api/social/analytics?range=${range}`,
      'GET',
      null,
      false,
      null,
      10000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('Failed to fetch social analytics:', error);
    }
    throw error;
  }
};

/**
 * Get detailed analytics for a specific post
 * @param {string} postId - Post ID
 * @returns {Promise<Object>} Post-specific analytics
 */
export const getPostAnalytics = async (postId) => {
  try {
    const response = await makeRequest(
      `/api/social/posts/${postId}/analytics`,
      'GET',
      null,
      false,
      null,
      10000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('Failed to fetch post analytics:', error);
    }
    throw error;
  }
};
