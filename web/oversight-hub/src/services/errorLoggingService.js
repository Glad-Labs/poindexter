import logger from '@/lib/logger';
/**
 * Error Logging Service
 *
 * Centralized service for logging client-side errors to the backend.
 * Integrates with error monitoring services (Sentry, etc.) and backend aggregation.
 */

import { makeRequest } from './cofounderAgentClient';
import { logErrorToSentry } from './sentryUtils';

let backendErrorEndpointAvailable = true;

/**
 * Log an error to the backend for aggregation and monitoring
 * @param {Error} error - The error object
 * @param {Object} context - Additional context about the error
 * @param {string} context.componentStack - React component stack trace
 * @param {string} context.severity - Error severity ('critical', 'warning', 'info')
 * @returns {Promise<Object>} Server response
 */
export const logErrorToBackend = async (error, context = {}) => {
  if (!backendErrorEndpointAvailable) {
    return null;
  }

  try {
    const errorPayload = {
      type: 'client_error',
      message: error?.message || 'Unknown error',
      stack: error?.stack || '',
      componentStack: context.componentStack || '',
      severity: context.severity || 'warning',
      userAgent: navigator.userAgent,
      timestamp: new Date().toISOString(),
      url: window.location.href,
      environment: process.env.NODE_ENV,
      custom_context: context.customContext || null,
    };

    // Send to backend via centralized API client
    // This ensures proper auth headers are included
    return await makeRequest('/api/errors', 'POST', errorPayload);
  } catch (err) {
    if (err?.status === 404) {
      backendErrorEndpointAvailable = false;
      logger.warn(
        '[errorLoggingService] /api/errors endpoint not available; disabling backend error logging for this session'
      );
      return null;
    }
    logger.error('Failed to log error to backend:', err);
    // Don't throw - error logging should never break the app
    return null;
  }
};

// Re-export logErrorToSentry from sentryUtils (moved to break circular import)
export { logErrorToSentry } from './sentryUtils';

/**
 * Comprehensive error logging - sends to both Sentry and backend
 * @param {Error} error - The error object
 * @param {Object} context - Additional context
 * @returns {Promise<Object|null>} Server response or null if logging fails
 */
export const logError = async (error, context = {}) => {
  try {
    // Log to Sentry if available (non-blocking)
    logErrorToSentry(error, context);

    // Log to backend for aggregation
    return await logErrorToBackend(error, context);
  } catch (err) {
    // Ensure promise rejection is handled - log to console but don't throw
    logger.error('[errorLoggingService] Failed to log error:', err);
    return null;
  }
};

/**
 * Log a warning message to the backend
 * @param {string} message - Warning message
 * @param {string} component - Component that generated the warning
 * @param {Object} context - Additional context
 * @returns {Promise<Object|null>}
 */
export const logWarning = async (message, component = '', context = {}) => {
  if (!backendErrorEndpointAvailable) {
    return null;
  }

  try {
    return await makeRequest('/api/errors', 'POST', {
      type: 'client_warning',
      message,
      component,
      severity: 'warning',
      userAgent: navigator.userAgent,
      timestamp: new Date().toISOString(),
      url: window.location.href,
      environment: process.env.NODE_ENV,
      custom_context: context || null,
    });
  } catch (err) {
    logger.error('[errorLoggingService] Failed to log warning:', err);
    return null;
  }
};

/**
 * Log an info message to the backend
 * @param {string} message - Info message
 * @param {string} component - Component that generated the info
 * @param {Object} context - Additional context
 * @returns {Promise<Object|null>}
 */
export const logInfo = async (message, component = '', context = {}) => {
  if (!backendErrorEndpointAvailable) {
    return null;
  }

  try {
    return await makeRequest('/api/errors', 'POST', {
      type: 'client_info',
      message,
      component,
      severity: 'info',
      userAgent: navigator.userAgent,
      timestamp: new Date().toISOString(),
      url: window.location.href,
      environment: process.env.NODE_ENV,
      custom_context: context || null,
    });
  } catch (err) {
    logger.error('[errorLoggingService] Failed to log info:', err);
    return null;
  }
};

/**
 * Retrieve error logs from the backend
 * @returns {Promise<Array>} List of error logs
 */
export const getErrorLogs = async () => {
  if (!backendErrorEndpointAvailable) {
    return [];
  }

  try {
    const response = await makeRequest('/api/errors', 'GET');
    return Array.isArray(response) ? response : [];
  } catch (err) {
    if (err?.status === 404) {
      backendErrorEndpointAvailable = false;
      return [];
    }
    logger.error('[errorLoggingService] Failed to retrieve error logs:', err);
    return [];
  }
};

/**
 * Delete a specific error log
 * @param {string} errorId - Error log ID
 * @returns {Promise<boolean>} Success status
 */
export const deleteErrorLog = async (errorId) => {
  if (!backendErrorEndpointAvailable) {
    return false;
  }

  try {
    await makeRequest(`/api/errors/${errorId}`, 'DELETE');
    return true;
  } catch (err) {
    if (err?.status === 404) {
      backendErrorEndpointAvailable = false;
      return false;
    }
    logger.error('[errorLoggingService] Failed to delete error log:', err);
    return false;
  }
};

/**
 * Clear all error logs
 * @returns {Promise<boolean>} Success status
 */
export const clearAllLogs = async () => {
  if (!backendErrorEndpointAvailable) {
    return false;
  }

  try {
    await makeRequest('/api/errors', 'DELETE');
    return true;
  } catch (err) {
    if (err?.status === 404) {
      backendErrorEndpointAvailable = false;
      return false;
    }
    logger.error('[errorLoggingService] Failed to clear error logs:', err);
    return false;
  }
};
