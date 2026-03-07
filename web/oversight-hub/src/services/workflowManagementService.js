import logger from '@/lib/logger';
/**
 * workflowManagementService.js (Phase 3.3)
 *
 * Service wrapper for workflow management and execution
 */

import { makeRequest } from './cofounderAgentClient';

/**
 * Get workflow execution history
 * @param {Object} options - Query options
 * @param {number} options.limit - Max results (1-500, default: 50)
 * @param {number} options.offset - Pagination offset (default: 0)
 * @param {string} options.status - Filter by status (PENDING, RUNNING, COMPLETED, FAILED, PAUSED)
 * @returns {Promise<Object>} Workflow execution history
 */
export const getWorkflowHistory = async (options = {}) => {
  try {
    const params = new URLSearchParams();
    if (options.limit) params.append('limit', options.limit);
    if (options.offset) params.append('offset', options.offset);
    if (options.status) params.append('status', options.status);

    const queryString = params.toString() ? `?${params.toString()}` : '';
    const response = await makeRequest(
      `/api/workflows/history${queryString}`,
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
    logger.error('Failed to fetch workflow history:', error);
    throw error;
  }
};

/**
 * Get detailed information about a specific workflow execution
 * @param {string} executionId - ID of the workflow execution
 * @returns {Promise<Object>} Execution details including input, output, and results
 */
export const getExecutionDetails = async (executionId) => {
  try {
    const response = await makeRequest(
      `/api/workflow/${executionId}/details`,
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
    logger.error(
      `Failed to fetch execution details for ${executionId}:`,
      error
    );
    throw error;
  }
};

/**
 * Get workflow statistics for the user
 * @returns {Promise<Object>} Workflow statistics (total, completed, failed, etc.)
 */
export const getWorkflowStatistics = async () => {
  try {
    const response = await makeRequest(
      `/api/workflows/statistics`,
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
    logger.error('Failed to fetch workflow statistics:', error);
    throw error;
  }
};

/**
 * Get performance metrics for workflows
 * @param {string} range - Time range: '7d', '30d', '90d', 'all'
 * @returns {Promise<Object>} Performance metrics including execution times, success rates
 */
export const getPerformanceMetrics = async (range = '30d') => {
  try {
    const response = await makeRequest(
      `/api/workflows/performance-metrics?range=${range}`,
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
    logger.error('Failed to fetch performance metrics:', error);
    throw error;
  }
};

/**
 * Get execution history for a specific workflow
 * @param {string} workflowId - ID of the workflow
 * @param {Object} options - Query options (limit, offset)
 * @returns {Promise<Object>} Execution history for the workflow
 */
export const getWorkflowExecutionHistory = async (workflowId, options = {}) => {
  try {
    const params = new URLSearchParams();
    if (options.limit) params.append('limit', options.limit);
    if (options.offset) params.append('offset', options.offset);

    const queryString = params.toString() ? `?${params.toString()}` : '';
    const response = await makeRequest(
      `/api/workflows/${workflowId}/history${queryString}`,
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
    logger.error(`Failed to fetch history for workflow ${workflowId}:`, error);
    throw error;
  }
};
