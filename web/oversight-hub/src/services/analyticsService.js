import logger from '@/lib/logger';
/**
 * analyticsService.js (Phase 2.1)
 *
 * Service wrapper for analytics API endpoints
 * Provides methods to fetch detailed metrics and KPIs
 */

import { makeRequest } from './cofounderAgentClient';

/**
 * Get KPI metrics for specified time range
 * @param {string} range - Time range: '7d', '30d', '90d', 'all'
 * @returns {Promise<Object>} KPI data including revenue, content, tasks, savings
 */
export const getKPIs = async (range = '30d') => {
  try {
    const response = await makeRequest(
      `/api/analytics/kpis?range=${range}`,
      'GET',
      null,
      false,
      null,
      15000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    logger.error('Failed to fetch KPIs:', error);
    throw error;
  }
};

/**
 * Get task execution metrics
 * @param {string} range - Time range: '7d', '30d', '90d', 'all'
 * @returns {Promise<Object>} Task execution metrics
 */
export const getTaskMetrics = async (range = '30d') => {
  try {
    const response = await makeRequest(
      `/api/analytics/tasks?range=${range}`,
      'GET',
      null,
      false,
      null,
      15000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    logger.error('Failed to fetch task metrics:', error);
    throw error;
  }
};

/**
 * Get cost breakdown by model provider
 * @param {string} range - Time range: '7d', '30d', '90d', 'all'
 * @returns {Promise<Object>} Cost breakdown by provider
 */
export const getCostBreakdown = async (range = '30d') => {
  try {
    const response = await makeRequest(
      `/api/analytics/costs?range=${range}`,
      'GET',
      null,
      false,
      null,
      15000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    logger.error('Failed to fetch cost breakdown:', error);
    throw error;
  }
};

/**
 * Get content publishing metrics
 * @param {string} range - Time range: '7d', '30d', '90d', 'all'
 * @returns {Promise<Object>} Content metrics including posts, engagement
 */
export const getContentMetrics = async (range = '30d') => {
  try {
    const response = await makeRequest(
      `/api/analytics/content?range=${range}`,
      'GET',
      null,
      false,
      null,
      15000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    logger.error('Failed to fetch content metrics:', error);
    throw error;
  }
};

/**
 * Get system health and performance metrics
 * @returns {Promise<Object>} System metrics
 */
export const getSystemMetrics = async () => {
  try {
    const response = await makeRequest(
      `/api/analytics/system`,
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
    logger.error('Failed to fetch system metrics:', error);
    throw error;
  }
};

/**
 * Get agent performance metrics
 * @param {string} range - Time range: '7d', '30d', '90d', 'all'
 * @returns {Promise<Object>} Agent performance data
 */
export const getAgentMetrics = async (range = '30d') => {
  try {
    const response = await makeRequest(
      `/api/analytics/agents?range=${range}`,
      'GET',
      null,
      false,
      null,
      15000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    logger.error('Failed to fetch agent metrics:', error);
    throw error;
  }
};

/**
 * Get quality metrics for content
 * @param {string} range - Time range: '7d', '30d', '90d', 'all'
 * @returns {Promise<Object>} Quality ratings and trends
 */
export const getQualityMetrics = async (range = '30d') => {
  try {
    const response = await makeRequest(
      `/api/analytics/quality?range=${range}`,
      'GET',
      null,
      false,
      null,
      15000
    );
    if (response.error) {
      throw new Error(response.error);
    }
    return response;
  } catch (error) {
    logger.error('Failed to fetch quality metrics:', error);
    throw error;
  }
};
