import logger from '@/lib/logger';
/**
 * Sentry Utilities
 *
 * Isolated module for Sentry error capture — kept separate from
 * errorLoggingService and cofounderAgentClient to avoid circular imports.
 */

import * as Sentry from '@sentry/react';

/**
 * Log an error to Sentry.
 *
 * Uses the @sentry/react SDK directly — no window.__SENTRY__ sentinel needed.
 * captureException() is a no-op when Sentry was not initialized (i.e. when
 * REACT_APP_SENTRY_DSN is unset), so this is always safe to call.
 *
 * @param {Error} error - The error object
 * @param {Object} context - Additional context
 */
export const logErrorToSentry = (error, context = {}) => {
  try {
    Sentry.captureException(error, {
      contexts: {
        react: {
          componentStack: context.componentStack,
          severity: context.severity,
        },
        custom: context.customContext,
      },
    });
  } catch (err) {
    logger.error('Failed to log error to Sentry:', err);
  }
};
