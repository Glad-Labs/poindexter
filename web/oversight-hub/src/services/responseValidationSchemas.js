import logger from '@/lib/logger';
/**
 * API Response Validation Schemas
 *
 * Zod schemas for validating API responses and ensuring type safety.
 * Use these to validate responses from cofounderAgentClient endpoints.
 *
 * Example usage:
 *   const result = await getCostMetrics();
 *   const validated = CostMetricsSchema.parse(result);
 */

// Simple validation helpers (non-Zod, for lightweight implementation)
// If you want full Zod, install: npm install zod

/**
 * Validates cost metrics API response
 * @param {any} data - Response data to validate
 * @returns {Object} Validated cost metrics
 * @throws {Error} If validation fails
 */
export const validateCostMetrics = (data) => {
  if (!data || typeof data !== 'object') {
    throw new Error('Cost metrics must be an object');
  }

  const { total_cost, avg_cost_per_task, total_tasks } = data;

  if (typeof total_cost !== 'number' || total_cost < 0) {
    throw new Error('total_cost must be a non-negative number');
  }
  if (typeof avg_cost_per_task !== 'number' || avg_cost_per_task < 0) {
    throw new Error('avg_cost_per_task must be a non-negative number');
  }
  if (typeof total_tasks !== 'number' || total_tasks < 0) {
    throw new Error('total_tasks must be a non-negative number');
  }

  return { total_cost, avg_cost_per_task, total_tasks };
};

/**
 * Validates cost breakdown by phase response
 * @param {any} data - Response data to validate
 * @returns {Object} Validated phase data
 */
export const validateCostsByPhase = (data) => {
  if (!data || typeof data !== 'object') {
    throw new Error('Cost breakdown must be an object');
  }

  const phases = data.phases || {};

  // Validate that all phase values are numbers
  Object.entries(phases).forEach(([phase, cost]) => {
    if (typeof cost !== 'number' || cost < 0) {
      throw new Error(`Phase "${phase}" cost must be a non-negative number`);
    }
  });

  return { phases };
};

/**
 * Validates cost breakdown by model response
 * @param {any} data - Response data to validate
 * @returns {Object} Validated model data
 */
export const validateCostsByModel = (data) => {
  if (!data || typeof data !== 'object') {
    throw new Error('Cost breakdown must be an object');
  }

  const models = data.models || {};

  // Validate that all model values are numbers
  Object.entries(models).forEach(([model, cost]) => {
    if (typeof cost !== 'number' || cost < 0) {
      throw new Error(`Model "${model}" cost must be a non-negative number`);
    }
  });

  return { models };
};

/**
 * Validates cost history response
 * @param {any} data - Response data to validate
 * @returns {Object} Validated history data
 */
export const validateCostHistory = (data) => {
  if (!data || typeof data !== 'object') {
    throw new Error('Cost history must be an object');
  }

  const daily_data = data.daily_data || [];

  if (!Array.isArray(daily_data)) {
    throw new Error('daily_data must be an array');
  }

  daily_data.forEach((item, idx) => {
    if (!item.date || typeof item.cost !== 'number') {
      throw new Error(
        `daily_data[${idx}] must have date and numeric cost fields`
      );
    }
  });

  return { daily_data };
};

/**
 * Validates budget status response
 * @param {any} data - Response data to validate
 * @returns {Object} Validated budget data
 */
export const validateBudgetStatus = (data) => {
  if (!data || typeof data !== 'object') {
    throw new Error('Budget status must be an object');
  }

  const { monthly_budget, amount_spent, amount_remaining, percent_used } = data;

  if (typeof monthly_budget !== 'number' || monthly_budget < 0) {
    throw new Error('monthly_budget must be a non-negative number');
  }
  if (typeof amount_spent !== 'number' || amount_spent < 0) {
    throw new Error('amount_spent must be a non-negative number');
  }
  if (typeof amount_remaining !== 'number' || amount_remaining < 0) {
    throw new Error('amount_remaining must be a non-negative number');
  }
  if (
    typeof percent_used !== 'number' ||
    percent_used < 0 ||
    percent_used > 100
  ) {
    throw new Error('percent_used must be a number between 0 and 100');
  }

  return { monthly_budget, amount_spent, amount_remaining, percent_used };
};

/**
 * Validates task response
 * @param {any} data - Response data to validate
 * @returns {Object} Validated task data
 */
export const validateTask = (data) => {
  if (!data || typeof data !== 'object') {
    throw new Error('Task must be an object');
  }

  const { id, topic, status } = data;

  if (!id) {
    throw new Error('Task must have an id');
  }
  if (typeof topic !== 'string' || !topic.trim()) {
    throw new Error('Topic must be a non-empty string');
  }
  if (!['pending', 'in_progress', 'completed', 'failed'].includes(status)) {
    throw new Error(
      'Status must be one of: pending, in_progress, completed, failed'
    );
  }

  return data;
};

/**
 * Validates task list response
 * @param {any} data - Response data to validate
 * @returns {Object} Validated task list data
 */
export const validateTaskList = (data) => {
  if (!data || typeof data !== 'object') {
    throw new Error('Task list must be an object');
  }

  if (!Array.isArray(data.tasks)) {
    throw new Error('tasks must be an array');
  }

  data.tasks.forEach((task, idx) => {
    try {
      validateTask(task);
    } catch (err) {
      throw new Error(`tasks[${idx}]: ${err.message}`);
    }
  });

  return data;
};

/**
 * Validates settings response
 * @param {any} data - Response data to validate
 * @returns {Object} Validated settings data
 */
export const validateSettings = (data) => {
  if (!data || typeof data !== 'object') {
    throw new Error('Settings must be an object');
  }

  // Common settings keys
  const validKeys = [
    'theme',
    'auto_refresh',
    'desktop_notifications',
    'mercury_api_key',
    'gcp_api_key',
  ];

  Object.keys(data).forEach((key) => {
    if (!validKeys.includes(key)) {
      // Allow unknown keys for extensibility, but log warning
      logger.warn(`Unknown settings key: ${key}`);
    }
  });

  return data;
};

/**
 * Validates image generation response
 * @param {any} data - Response data to validate
 * @returns {Object} Validated image data
 */
export const validateGeneratedImage = (data) => {
  if (!data || typeof data !== 'object') {
    throw new Error('Image data must be an object');
  }

  if (!data.image_url || typeof data.image_url !== 'string') {
    throw new Error('image_url must be a non-empty string');
  }

  return { image_url: data.image_url };
};

/**
 * Safe parse wrapper - returns data or null on validation error
 * @param {Function} validator - Validation function
 * @param {any} data - Data to validate
 * @param {string} label - Label for error logging
 * @returns {any} Validated data or null
 */
export const safeValidate = (validator, data, label = 'Response') => {
  try {
    return validator(data);
  } catch (err) {
    logger.error(`${label} validation failed:`, err.message);
    return null;
  }
};
