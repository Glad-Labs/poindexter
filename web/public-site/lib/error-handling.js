import * as Sentry from '@sentry/nextjs';
import logger from './logger';
/**
 * Error Handling Utilities for Glad Labs
 * Provides error boundary components, error logging, and recovery strategies
 */

/**
 * Log error to external service (e.g., Sentry, LogRocket)
 * Currently logs to console in development, can be extended for production
 */
export function logError(error, context = {}) {
  const errorInfo = {
    message: error?.message || 'Unknown error',
    stack: error?.stack,
    context,
    timestamp: new Date().toISOString(),
    url: typeof window !== 'undefined' ? window.location.href : 'unknown',
  };

  if (process.env.NODE_ENV === 'production') {
    Sentry.captureException(error, { extra: context });
  }
  logger.error('Error:', errorInfo);

  return errorInfo;
}

/**
 * Determine error type for specific handling
 */
export function getErrorType(error) {
  if (!error) return 'unknown';

  const message = error.message?.toLowerCase() || '';
  const status = error.status || error.statusCode;

  if (status === 404) return 'not-found';
  if (status === 403) return 'forbidden';
  if (status >= 500) return 'server-error';
  if (message.includes('fetch') || message.includes('network'))
    return 'network';
  if (message.includes('timeout')) return 'timeout';
  if (message.includes('parse')) return 'parse-error';

  return 'unknown';
}

/**
 * Get user-friendly error message
 */
export function getErrorMessage(error, errorType = null) {
  const type = errorType || getErrorType(error);

  const messages = {
    'not-found': "The page or resource you're looking for doesn't exist.",
    forbidden: "You don't have permission to access this resource.",
    'server-error':
      'The server encountered an unexpected error. Please try again later.',
    network: 'Network connection error. Please check your internet connection.',
    timeout: 'The request took too long. Please try again.',
    'parse-error':
      'There was an error processing the response. Please try again.',
    unknown: 'An unexpected error occurred. Please try again.',
  };

  return messages[type] || messages.unknown;
}

/**
 * Create error boundary data
 */
export function createErrorBoundaryData(error, reset) {
  return {
    error,
    reset,
    type: getErrorType(error),
    message: getErrorMessage(error),
    timestamp: new Date().toISOString(),
  };
}

/**
 * Handle async errors with retry logic
 */
export async function withRetry(fn, options = {}) {
  const {
    maxRetries = 3,
    delayMs = 1000,
    backoffMultiplier = 2,
    onRetry = null,
  } = options;

  let lastError;
  let delay = delayMs;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;

      // Don't retry on certain error types
      const shouldRetry = isRetryableError(error) && attempt < maxRetries;

      if (shouldRetry) {
        onRetry?.(attempt + 1, maxRetries, delay, error);
        await sleep(delay);
        delay *= backoffMultiplier;
      } else {
        throw error;
      }
    }
  }

  throw lastError;
}

/**
 * Check if error is retryable
 */
export function isRetryableError(error) {
  const status = error?.status || error?.statusCode;

  // Retry on network errors and 5xx errors
  if (!status) return true; // Network error
  if (status >= 500) return true; // Server error
  if (status === 408) return true; // Request timeout
  if (status === 429) return true; // Rate limited

  // Don't retry on client errors (4xx)
  return false;
}

/**
 * Sleep utility for delays
 */
export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Safe JSON parse with error handling
 */
export function safeJsonParse(json, fallback = null) {
  try {
    return JSON.parse(json);
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.warn('JSON parse error:', error);
    }
    return fallback;
  }
}

/**
 * Safe JSON stringify with error handling
 */
export function safeJsonStringify(obj, fallback = '{}') {
  try {
    return JSON.stringify(obj);
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.warn('JSON stringify error:', error);
    }
    return fallback;
  }
}

/**
 * Fetch with built-in error handling and retry
 */
export async function fetchWithErrorHandling(url, options = {}) {
  const { retry = true, timeout = 10000, ...fetchOptions } = options;

  const fetchFn = async () => {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    try {
      const response = await fetch(url, {
        ...fetchOptions,
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        const error = new Error(
          `HTTP ${response.status}: ${response.statusText}`
        );
        error.status = response.status;
        throw error;
      }

      return response;
    } catch (error) {
      clearTimeout(timeoutId);
      throw error;
    }
  };

  if (retry) {
    return withRetry(fetchFn, {
      maxRetries: 2,
      delayMs: 500,
    });
  }

  return fetchFn();
}

/**
 * Validate data against schema
 */
export function validateData(data, schema) {
  const errors = [];

  for (const [key, rules] of Object.entries(schema)) {
    const value = data[key];

    if (
      rules.required &&
      (value === undefined || value === null || value === '')
    ) {
      errors.push(`${key} is required`);
      continue;
    }

    if (value && rules.type && typeof value !== rules.type) {
      errors.push(`${key} must be of type ${rules.type}`);
    }

    if (value && rules.minLength && value.length < rules.minLength) {
      errors.push(`${key} must be at least ${rules.minLength} characters`);
    }

    if (value && rules.maxLength && value.length > rules.maxLength) {
      errors.push(`${key} must be at most ${rules.maxLength} characters`);
    }

    if (value && rules.pattern && !rules.pattern.test(value)) {
      errors.push(`${key} format is invalid`);
    }

    if (value && rules.enum && !rules.enum.includes(value)) {
      errors.push(`${key} must be one of: ${rules.enum.join(', ')}`);
    }
  }

  return {
    valid: errors.length === 0,
    errors,
  };
}

/**
 * Error logger class for structured logging
 */
export class ErrorLogger {
  constructor(serviceName = 'Glad Labs') {
    this.serviceName = serviceName;
    this.errors = [];
  }

  log(error, context = {}) {
    const errorEntry = {
      service: this.serviceName,
      timestamp: new Date().toISOString(),
      message: error?.message || String(error),
      stack: error?.stack,
      context,
    };

    this.errors.push(errorEntry);

    // Keep only last 100 errors in memory
    if (this.errors.length > 100) {
      this.errors.shift();
    }

    return errorEntry;
  }

  clear() {
    this.errors = [];
  }

  getErrors() {
    return [...this.errors];
  }

  export() {
    return JSON.stringify(this.errors, null, 2);
  }
}

/**
 * Create default error logger instance
 */
export const defaultErrorLogger = new ErrorLogger('Glad Labs');
