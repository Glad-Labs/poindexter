import logger from '@/lib/logger';
/**
 * capabilityService.js (Phase 3.1)
 *
 * Service wrapper for agent capabilities and service discovery
 */

import { makeRequest } from './cofounderAgentClient';

/**
 * Get complete service registry with all capabilities
 * @returns {Promise<Object>} Service registry with all available capabilities
 */
export const getServiceRegistry = async () => {
  try {
    const response = await makeRequest(
      `/api/services/registry`,
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
    logger.error('Failed to fetch service registry:', error);
    throw error;
  }
};

/**
 * List all available services
 * @returns {Promise<Array>} List of service names
 */
export const listServices = async () => {
  try {
    const response = await makeRequest(
      `/api/services/list`,
      'GET',
      null,
      false,
      null,
      5000
    );
    if (Array.isArray(response)) {
      return response;
    }
    return response.services || [];
  } catch (error) {
    logger.error('Failed to list services:', error);
    throw error;
  }
};

/**
 * Get detailed metadata for a specific service
 * @param {string} serviceName - Name of the service
 * @returns {Promise<Object>} Service metadata including actions and schemas
 */
export const getServiceMetadata = async (serviceName) => {
  try {
    const response = await makeRequest(
      `/api/services/${serviceName}`,
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
    logger.error(`Failed to fetch metadata for service ${serviceName}:`, error);
    throw error;
  }
};
