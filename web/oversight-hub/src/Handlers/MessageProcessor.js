import logger from '@/lib/logger';
/**
 * MessageProcessor.js
 *
 * Middleware pattern for orchestrator message handling.
 * Enables extensible processing pipeline for all message types.
 *
 * Benefits:
 * - Middleware chain pattern for extensibility
 * - Centralized message processing logic
 * - Easy to add new processors without modifying existing code
 * - Separation of concerns (validation, transformation, etc.)
 * - Testable pipeline
 *
 * Previously: Message processing logic scattered across components
 * Now: Unified processor with middleware chain
 */

/**
 * MessageProcessor
 *
 * Implements middleware pattern for message processing.
 * Each middleware can validate, transform, or filter messages.
 *
 * Architecture:
 * - Message → Validation MW → Intent Detection MW → Error Recovery MW → Output
 * - Supports both sync and async middleware
 * - Short-circuits on error
 * - Transforms message data through pipeline
 */
export class MessageProcessor {
  /**
   * Create a new MessageProcessor
   */
  constructor() {
    this.middlewares = [];
  }

  /**
   * Add middleware to the processing chain
   *
   * @param {function} middleware - Function that receives (message, next) or (message, context, next)
   * @returns {this} For chaining
   *
   * @example
   * processor.use((message, next) => {
   *   logger.log('Processing:', message.type);
   *   const result = next(message);
   *   logger.log('Completed:', result);
   *   return result;
   * });
   */
  use(middleware) {
    this.middlewares.push(middleware);
    return this;
  }

  /**
   * Process message through middleware chain
   *
   * @param {object} message - Message to process
   * @param {object} context - Optional context for all middleware
   * @returns {Promise<object>} Processed message
   *
   * @example
   * const result = await processor.process(
   *   { type: 'status', phase: 2, total: 6 },
   *   { userId: '123' }
   * );
   */
  async process(message, context = {}) {
    let index = 0;

    const next = async (msg = message) => {
      if (index >= this.middlewares.length) {
        return msg;
      }

      const middleware = this.middlewares[index++];
      return middleware(msg, context, next);
    };

    return next();
  }

  /**
   * Clear all middleware
   */
  clear() {
    this.middlewares = [];
    return this;
  }
}

/**
 * Built-in middleware functions
 */

/**
 * Validation middleware
 * Ensures message has required fields based on type
 */
export const validationMiddleware = (requiredFields = {}) => {
  return (message, context, next) => {
    const { type } = message;
    const required = requiredFields[type] || [];

    const missingFields = required.filter((field) => !(field in message));

    if (missingFields.length > 0) {
      throw new Error(
        `Validation failed: missing fields [${missingFields.join(', ')}]`
      );
    }

    return next(message);
  };
};

/**
 * Intent detection middleware
 * Identifies user intent and adds to message context
 */
export const intentDetectionMiddleware = (intentMap = {}) => {
  return (message, context, next) => {
    const { type, command } = message;

    // Map command to intent if applicable
    if (command && intentMap[command]) {
      message.intent = intentMap[command];
    }

    // Or infer from message type
    if (!message.intent) {
      const typeIntentMap = {
        command: 'execute',
        status: 'track',
        result: 'approve',
        error: 'recover',
      };
      message.intent = typeIntentMap[type] || 'unknown';
    }

    return next(message);
  };
};

/**
 * Error recovery middleware
 * Handles and transforms errors in messages
 */
export const errorRecoveryMiddleware = (errorHandlers = {}) => {
  return (message, context, next) => {
    if (message.type === 'error' && message.error) {
      const { severity } = message;
      const handler = errorHandlers[severity] || errorHandlers.default;

      if (handler) {
        const recovery = handler(message);
        return next({
          ...message,
          recovery,
          recovered: true,
        });
      }
    }

    return next(message);
  };
};

/**
 * Transformation middleware
 * Transforms message format or normalizes data
 */
export const transformationMiddleware = (transformer) => {
  return (message, context, next) => {
    const transformed = transformer(message);
    return next(transformed);
  };
};

/**
 * Logging middleware
 * Logs message processing for debugging
 */
export const loggingMiddleware = (options = {}) => {
  const { verbose = false, prefix = '[MessageProcessor]' } = options;

  return (message, context, next) => {
    const startTime = performance.now();

    if (verbose) {
      logger.log(`${prefix} Start: ${message.type}`, message);
    }

    const result = next(message);

    const duration = performance.now() - startTime;
    logger.log(
      `${prefix} Complete: ${message.type} (${duration.toFixed(2)}ms)`,
      result
    );

    return result;
  };
};

/**
 * Caching middleware
 * Caches message processing results
 */
export const cachingMiddleware = (options = {}) => {
  const { ttl = 5000, maxSize = 100 } = options;
  const cache = new Map();
  let cacheSize = 0;

  return (message, context, next) => {
    // Create cache key from message
    const key = `${message.type}:${message.id || JSON.stringify(message)}`;

    // Check cache
    const cached = cache.get(key);
    if (cached && Date.now() - cached.time < ttl) {
      return cached.result;
    }

    // Process and cache
    const result = next(message);

    if (cacheSize >= maxSize) {
      // Simple LRU: remove oldest entry
      const firstKey = cache.keys().next().value;
      cache.delete(firstKey);
    } else {
      cacheSize++;
    }

    cache.set(key, { result, time: Date.now() });
    return result;
  };
};

/**
 * Rate limiting middleware
 * Limits message processing rate
 */
export const rateLimitingMiddleware = (options = {}) => {
  const { maxPerSecond = 100 } = options;
  let messageCount = 0;
  let windowStart = Date.now();

  return (message, context, next) => {
    const now = Date.now();

    // Reset window if needed
    if (now - windowStart > 1000) {
      messageCount = 0;
      windowStart = now;
    }

    messageCount++;

    if (messageCount > maxPerSecond) {
      throw new Error(
        `Rate limit exceeded: ${maxPerSecond} messages per second`
      );
    }

    return next(message);
  };
};

export default MessageProcessor;
