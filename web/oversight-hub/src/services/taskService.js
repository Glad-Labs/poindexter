/**
 * Task Service - Uses FastAPI backend (PostgreSQL)
 *
 * This service communicates with the Co-Founder Agent backend API
 * which stores tasks in PostgreSQL database.
 *
 * All functions use the centralized makeRequest() API client from cofounderAgentClient.js
 * This ensures consistent auth, timeout handling, and error management across the app.
 */

import { makeRequest } from './cofounderAgentClient';

const API_TIMEOUT = 30000; // 30 seconds

/**
 * Fetch all tasks from the backend API
 * Uses database-level pagination for performance
 *
 * @param {number} offset - Pagination offset
 * @param {number} limit - Number of tasks to return
 * @param {object} filters - Optional filters (status, category, etc.)
 * @returns {Promise<Array>} Array of task objects
 * @throws {Error} If API call fails
 */
export const getTasks = async (offset = 0, limit = 20, filters = {}) => {
  const params = new URLSearchParams({
    offset: offset.toString(),
    limit: limit.toString(),
    ...(filters.status && { status: filters.status }),
    ...(filters.category && { category: filters.category }),
  });

  const result = await makeRequest(
    `/api/tasks?${params}`,
    'GET',
    null,
    false,
    null,
    API_TIMEOUT
  );

  if (result.error) {
    throw new Error(`Could not fetch tasks: ${result.error}`);
  }

  return result.tasks || [];
};

/**
 * Get a single task by ID
 *
 * @param {string} taskId - Task ID to fetch
 * @returns {Promise<object>} Task object
 * @throws {Error} If task not found or API fails
 */
export const getTask = async (taskId) => {
  const result = await makeRequest(
    `/api/tasks/${taskId}`,
    'GET',
    null,
    false,
    null,
    API_TIMEOUT
  );

  if (result.error) {
    throw new Error(`Could not fetch task: ${result.error}`);
  }

  return result;
};

/**
 * Creates a new task via the Service Layer API
 *
 * Routes through unified service backend:
 * POST /api/services/tasks/actions/create_task
 *
 * Supports both:
 * - Manual creation (CreateTaskModal) → taskService.js → Service Layer
 * - NLP creation (Agent) → nlp_intent_recognizer → Service Layer
 *
 * @param {object} taskData - Task data to create
 * @returns {Promise<string>} Created task ID
 * @throws {Error} If creation fails
 */
export const createTask = async (taskData) => {
  // Service layer expects action request format: {params, context}
  const serviceRequest = {
    params: taskData,
    context: {
      source: 'manual_form',
      timestamp: new Date().toISOString(),
    },
  };

  const result = await makeRequest(
    '/api/services/tasks/actions/create_task',
    'POST',
    serviceRequest,
    false,
    null,
    API_TIMEOUT
  );

  if (result.error) {
    throw new Error(`Could not create task: ${result.error}`);
  }

  // Service layer returns ActionResult with .data property
  return result.data?.id || result.id || result;
};

/**
 * Update task status via the backend API
 *
 * @param {string} taskId - Task ID to update
 * @param {object} updates - Fields to update
 * @returns {Promise<object>} Updated task object
 * @throws {Error} If update fails
 */
export const updateTask = async (taskId, updates) => {
  const result = await makeRequest(
    `/api/tasks/${taskId}`,
    'PATCH',
    updates,
    false,
    null,
    API_TIMEOUT
  );

  if (result.error) {
    throw new Error(`Could not update task: ${result.error}`);
  }

  return result;
};

/**
 * Approve a task (WITHOUT publishing)
 * Task will be moved to 'approved' status and WAIT for manual publishing
 * Call publishTask() separately to publish an approved task
 *
 * @param {string} taskId - Task ID to approve
 * @param {string} feedback - Optional approval feedback
 * @returns {Promise<object>} Updated task object (status: 'approved')
 * @throws {Error} If approval fails
 */
export const approveTask = async (taskId, feedback = '') => {
  const result = await makeRequest(
    `/api/tasks/${taskId}/approve`,
    'POST',
    { feedback, auto_publish: false }, // ✅ CRITICAL: Approval does NOT publish
    false,
    null,
    API_TIMEOUT
  );

  if (result.error) {
    throw new Error(`Could not approve task: ${result.error}`);
  }

  return result;
};

/**
 * Trigger frontend cache revalidation for post pages
 * Called after a post is published to update the public site
 *
 * NOTE: Secret is handled server-side in the backend for security.
 * Never put secrets in browser code (they're exposed in minified JS)
 *
 * @param {Array<string>} paths - Specific paths to revalidate (optional)
 * @returns {Promise<object>} Revalidation result
 */
export const revalidatePublicSite = async (paths = []) => {
  try {
    // Call the FastAPI backend which has the real secret
    // Backend will safely call the public site revalidate endpoint
    const response = await fetch(
      `${process.env.REACT_APP_API_URL || 'http://localhost:8000'}/api/revalidate-cache`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ paths }),
      }
    );

    if (!response.ok) {
      console.warn(
        `⚠️  Frontend revalidation returned status ${response.status}`
      );
      // Don't throw - revalidation failure shouldn't break the publish flow
      return { success: false, status: response.status };
    }

    const data = await response.json();
    console.log('✅ Frontend cache revalidated:', data);
    return data;
  } catch (error) {
    console.warn('⚠️  Could not trigger frontend revalidation:', error.message);
    // Don't throw - publish should succeed even if revalidation fails
    return { success: false, error: error.message };
  }
};

/**
 * Publish an approved task
 * Changes status from 'approved' to 'published' and creates the post
 * Should only be called after approveTask() succeeds
 *
 * @param {string} taskId - Task ID to publish
 * @returns {Promise<object>} Updated task object (status: 'published')
 * @throws {Error} If publishing fails
 */
export const publishTask = async (taskId) => {
  const result = await makeRequest(
    `/api/tasks/${taskId}/publish`,
    'POST',
    {},
    false,
    null,
    API_TIMEOUT
  );

  if (result.error) {
    throw new Error(`Could not publish task: ${result.error}`);
  }

  // Trigger frontend cache revalidation after successful publish
  // This is non-blocking - doesn't fail the publish if it fails
  if (result && typeof result === 'object') {
    // Revalidate homepage and archive pages
    revalidatePublicSite(['/', '/archive']).catch((err) => {
      console.warn('Revalidation failed silently:', err);
    });
  }

  return result;
};

/**
 * Reject a task
 *
 * @param {string} taskId - Task ID to reject
 * @param {string} reason - Rejection reason
 * @returns {Promise<object>} Updated task object
 * @throws {Error} If rejection fails
 */
export const rejectTask = async (taskId, reason = '') => {
  const result = await makeRequest(
    `/api/tasks/${taskId}/reject`,
    'POST',
    { reason },
    false,
    null,
    API_TIMEOUT
  );

  if (result.error) {
    throw new Error(`Could not reject task: ${result.error}`);
  }

  return result;
};

/**
 * Delete a task
 *
 * @param {string} taskId - Task ID to delete
 * @returns {Promise<void>}
 * @throws {Error} If deletion fails
 */
export const deleteTask = async (taskId) => {
  const result = await makeRequest(
    `/api/tasks/${taskId}`,
    'DELETE',
    null,
    false,
    null,
    API_TIMEOUT
  );

  if (result.error) {
    throw new Error(`Could not delete task: ${result.error}`);
  }

  return result;
};

/**
 * Fetch task by ID
 * Returns detailed task info with generated content and metadata
 *
 * @param {string} taskId - Task ID
 * @returns {Promise<object>} Task object with content
 * @throws {Error} If task not found
 */
export const getContentTask = async (taskId) => {
  const result = await makeRequest(
    `/api/tasks/${taskId}`,
    'GET',
    null,
    false,
    null,
    API_TIMEOUT
  );

  if (result.error) {
    throw new Error(`Could not fetch task: ${result.error}`);
  }

  return result;
};

/**
 * Delete task
 *
 * @param {string} taskId - Task ID
 * @returns {Promise<void>}
 * @throws {Error} If deletion fails
 */
export const deleteContentTask = async (taskId) => {
  const result = await makeRequest(
    `/api/tasks/${taskId}`,
    'DELETE',
    null,
    false,
    null,
    API_TIMEOUT
  );

  if (result.error) {
    throw new Error(`Could not delete task: ${result.error}`);
  }

  return result;
};
