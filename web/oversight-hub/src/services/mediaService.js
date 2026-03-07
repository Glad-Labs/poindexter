import logger from '@/lib/logger';
/**
 * mediaService.js (Phase 2.2)
 *
 * Service wrapper for media management API endpoints
 * Handles image generation, uploads, and media tracking
 */

import { makeRequest } from './cofounderAgentClient';

/**
 * Generate or search for a featured image
 * @param {Object} params - Generation parameters
 * @param {string} params.prompt - Image search/generation prompt
 * @param {string} params.title - Optional: Post title for context
 * @param {boolean} params.use_pexels - Whether to search Pexels
 * @param {boolean} params.use_generation - Whether to generate custom image
 * @returns {Promise<Object>} Generated/found image URL and metadata
 */
export const generateImages = async (params) => {
  try {
    const response = await makeRequest(
      `/api/media/generate-image`,
      'POST',
      params,
      false,
      null,
      30000 // 30s timeout for image generation
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    logger.error('Failed to generate image:', error);
    throw error;
  }
};

/**
 * Get list of uploaded/generated media
 * @param {Object} options - Query options
 * @param {number} options.limit - Max results to return
 * @param {number} options.offset - Pagination offset
 * @param {string} options.type - Filter by type: 'uploaded', 'generated', or 'all'
 * @returns {Promise<Object>} List of media with metadata
 */
export const listMedia = async (options = {}) => {
  try {
    const params = new URLSearchParams();
    if (options.limit) params.append('limit', options.limit);
    if (options.offset) params.append('offset', options.offset);
    if (options.type) params.append('type', options.type);

    const queryString = params.toString() ? `?${params.toString()}` : '';
    const response = await makeRequest(
      `/api/media/list${queryString}`,
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
    logger.error('Failed to list media:', error);
    throw error;
  }
};

/**
 * Get media health/validation status
 * @returns {Promise<Object>} Media service health status
 */
export const getMediaHealth = async () => {
  try {
    const response = await makeRequest(
      `/api/media/health`,
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
    logger.error('Failed to check media health:', error);
    throw error;
  }
};

/**
 * Delete a media item by ID
 * @param {string} mediaId - Media ID to delete
 * @returns {Promise<Object>} Deletion response
 */
export const deleteMedia = async (mediaId) => {
  try {
    const response = await makeRequest(
      `/api/media/${mediaId}`,
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
    logger.error('Failed to delete media:', error);
    throw error;
  }
};

/**
 * Get media metrics and usage statistics
 * @param {string} range - Time range: '7d', '30d', '90d', 'all'
 * @returns {Promise<Object>} Media usage metrics
 */
export const getMediaMetrics = async (range = '30d') => {
  try {
    const response = await makeRequest(
      `/api/media/metrics?range=${range}`,
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
    logger.error('Failed to fetch media metrics:', error);
    throw error;
  }
};
