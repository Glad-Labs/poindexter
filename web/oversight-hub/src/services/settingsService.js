import logger from '@/lib/logger';
/**
 * Settings Service
 *
 * Provides API methods for managing application settings.
 * Interfaces with the /api/settings/* endpoints on the backend.
 *
 * All settings are persisted to the PostgreSQL admin_db backend,
 * ensuring sync across different sessions and devices.
 */

import { makeRequest } from './cofounderAgentClient';

const API_BASE = '/api/settings';

/**
 * Get all settings
 * @returns {Promise<Object>} Object mapping setting keys to values
 */
export const listSettings = async () => {
  return makeRequest(`${API_BASE}`, 'GET');
};

/**
 * Get a specific setting by key
 * @param {string} key - Setting key (e.g., 'theme', 'api_key_mercury')
 * @returns {Promise<Object>} Setting object with key and value
 */
export const getSetting = async (key) => {
  return makeRequest(`${API_BASE}/${key}`, 'GET', null, false, null, 30000, {
    // Missing setting keys are expected during first-run bootstrap.
    // Components already apply defaults, so avoid error-level log noise.
    shouldSuppressErrorLog: ({ status }) => status === 404,
  });
};

/**
 * Create or update a setting
 * @param {string} key - Setting key
 * @param {any} value - Setting value (string, boolean, number, object, etc.)
 * @returns {Promise<Object>} Created/updated setting
 */
export const createOrUpdateSetting = async (key, value) => {
  return makeRequest(`${API_BASE}`, 'POST', {
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  });
};

/**
 * Delete a setting
 * @param {string} key - Setting key to delete
 * @returns {Promise<Object>} Success response
 */
export const deleteSetting = async (key) => {
  return makeRequest(`${API_BASE}/${key}`, 'DELETE');
};

/**
 * Bulk update multiple settings
 * @param {Object} settings - Object mapping keys to values
 * @returns {Promise<Object>} Updated settings
 */
export const bulkUpdateSettings = async (settings) => {
  return makeRequest(`${API_BASE}/bulk`, 'POST', settings);
};

/**
 * Get a setting with a default value if it doesn't exist
 * @param {string} key - Setting key
 * @param {any} defaultValue - Default value if setting not found
 * @returns {Promise<any>} Setting value or default
 */
export const getSettingWithDefault = async (key, defaultValue) => {
  try {
    const result = await getSetting(key);
    return result?.value ?? defaultValue;
  } catch (error) {
    logger.warn(`Setting ${key} not found, using default`, error);
    return defaultValue;
  }
};
