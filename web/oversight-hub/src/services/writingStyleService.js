/**
 * Writing Style Service
 *
 * Provides API methods for managing user writing samples for RAG-based style matching.
 * Handles CRUD operations and retrieval of writing samples.
 */

import { makeRequest } from './cofounderAgentClient';

const API_BASE = '/api/writing-style';

/**
 * Upload a new writing sample
 * @param {string} title - Sample title
 * @param {string} description - Sample description
 * @param {string|File} content - Writing sample content (text or File object)
 * @param {boolean} setAsActive - Whether to set as active sample
 * @returns {Promise<Object>} Created sample data
 */
export const uploadWritingSample = async (
  title,
  description,
  content,
  setAsActive = false
) => {
  const formData = new FormData();
  formData.append('title', title);
  formData.append('description', description || '');
  formData.append('set_as_active', setAsActive);

  // Check if content is a File object (from file upload) or string (raw text)
  if (content instanceof File) {
    formData.append('file', content);
  } else {
    formData.append('content', content);
  }

  return makeRequest(`${API_BASE}/upload`, 'POST', formData);
};

/**
 * Get all writing samples for the current user
 * @returns {Promise<Object>} List of writing samples
 */
export const getUserWritingSamples = async () => {
  return makeRequest(`${API_BASE}/samples`, 'GET');
};

/**
 * Get the currently active writing sample
 * @returns {Promise<Object|null>} Active writing sample or null
 */
export const getActiveWritingSample = async () => {
  return makeRequest(`${API_BASE}/active`, 'GET');
};

/**
 * Set a writing sample as active
 * @param {string} sampleId - Sample ID to activate
 * @returns {Promise<Object>} Updated sample data
 */
export const setActiveWritingSample = async (sampleId) => {
  return makeRequest(`${API_BASE}/${sampleId}/activate`, 'POST');
};

/**
 * Update a writing sample
 * @param {string} sampleId - Sample ID to update
 * @param {Object} updates - Fields to update (title, description, content)
 * @returns {Promise<Object>} Updated sample data
 */
export const updateWritingSample = async (sampleId, updates) => {
  return makeRequest(`${API_BASE}/${sampleId}`, 'PUT', updates);
};

/**
 * Delete a writing sample
 * @param {string} sampleId - Sample ID to delete
 * @returns {Promise<Object>} Success response
 */
export const deleteWritingSample = async (sampleId) => {
  return makeRequest(`${API_BASE}/${sampleId}`, 'DELETE');
};
