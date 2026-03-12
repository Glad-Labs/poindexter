import logger from '@/lib/logger';
/**
 * Unified Status Service
 *
 * Single point of entry for all status operations across the application.
 * Abstracts approval workflow, validates transitions, handles errors,
 * and maintains backward compatibility with legacy status system.
 *
 * Usage:
 *   await unifiedStatusService.approve(taskId, feedback);
 *   await unifiedStatusService.reject(taskId, reason);
 *   const history = await unifiedStatusService.getHistory(taskId);
 */

import { makeRequest } from './cofounderAgentClient';
import { STATUS_ENUM } from '../Constants/statusEnums';

/**
 * Get current user ID from localStorage or auth context
 */
const getCurrentUserId = () => {
  try {
    const user = localStorage.getItem('currentUser');
    if (user) {
      const parsed = JSON.parse(user);
      return parsed.id || parsed.email || 'anonymous';
    }
  } catch (e) {
    logger.warn('Could not parse current user:', e);
  }
  return 'anonymous';
};

/**
 * Unified Status Service
 */
export const unifiedStatusService = {
  /**
   * Update task status with backend validation
   * @param {string} taskId - Task ID to update
   * @param {string} newStatus - New status value
   * @param {Object} options - Additional options
   * @param {string} options.reason - Reason for status change
   * @param {string} options.feedback - Feedback/notes for the change
   * @param {string} options.userId - User making the change (auto-fetched if not provided)
   * @param {Object} options.metadata - Additional metadata
   * @returns {Promise<Object>} - Result with status, history, and validation details
   * @throws {Error} - On validation failure or API error
   */
  async updateStatus(taskId, newStatus, options = {}) {
    const {
      reason = '',
      feedback = '',
      userId = null,
      metadata = {},
    } = options;

    try {
      const payload = {
        status: newStatus,
        updated_by: userId || getCurrentUserId(),
        reason,
        metadata: {
          ...metadata,
          timestamp: new Date().toISOString(),
          updated_from_ui: true,
          feedback,
        },
      };

      // Try new endpoint first
      const response = await makeRequest(
        `/api/tasks/${taskId}/status/validated`,
        'PUT',
        payload
      );
      return response;
    } catch (error) {
      throw new Error(
        error.message || 'Failed to update task status. Please try again.'
      );
    }
  },

  /**
   * Approve a task
   * @param {string} taskId - Task ID to approve
   * @param {string} feedback - Optional approval feedback/notes
   * @param {string} userId - Optional user ID (auto-fetched if not provided)
   * @returns {Promise<Object>} - Result object
   */
  async approve(taskId, feedback = '', userId = null) {
    if (!taskId) {
      throw new Error('Task ID is required');
    }

    const payload = {
      reason: 'Task approved',
      feedback,
      userId,
      metadata: {
        action: 'approve',
        approval_feedback: feedback,
      },
    };

    const result = await this.updateStatus(
      taskId,
      STATUS_ENUM.APPROVED,
      payload
    );
    return result;
  },

  /**
   * Reject a task
   * @param {string} taskId - Task ID to reject
   * @param {string} reason - Reason for rejection (required)
   * @param {string} userId - Optional user ID (auto-fetched if not provided)
   * @returns {Promise<Object>} - Result object
   * @throws {Error} - If reason is not provided
   */
  async reject(taskId, reason = '', userId = null) {
    if (!taskId) {
      throw new Error('Task ID is required');
    }
    if (!reason || !reason.trim()) {
      throw new Error('Rejection reason is required');
    }

    return this.updateStatus(taskId, STATUS_ENUM.REJECTED, {
      reason,
      userId,
      metadata: {
        action: 'reject',
        rejection_reason: reason,
      },
    });
  },

  /**
   * Hold a task (put on hold)
   * @param {string} taskId - Task ID to hold
   * @param {string} reason - Reason for holding
   * @param {string} userId - Optional user ID
   * @returns {Promise<Object>} - Result object
   */
  async hold(taskId, reason = '', userId = null) {
    if (!taskId) {
      throw new Error('Task ID is required');
    }

    return this.updateStatus(taskId, STATUS_ENUM.ON_HOLD, {
      reason,
      userId,
      metadata: {
        action: 'hold',
        hold_reason: reason,
      },
    });
  },

  /**
   * Resume a task that was on hold
   * @param {string} taskId - Task ID to resume
   * @param {string} reason - Reason for resuming
   * @param {string} userId - Optional user ID
   * @returns {Promise<Object>} - Result object
   */
  async resume(taskId, reason = '', userId = null) {
    if (!taskId) {
      throw new Error('Task ID is required');
    }

    return this.updateStatus(taskId, STATUS_ENUM.PENDING, {
      reason: reason || 'Resumed from on-hold',
      userId,
      metadata: {
        action: 'resume',
      },
    });
  },

  /**
   * Cancel a task
   * @param {string} taskId - Task ID to cancel
   * @param {string} reason - Reason for cancellation
   * @param {string} userId - Optional user ID
   * @returns {Promise<Object>} - Result object
   */
  async cancel(taskId, reason = '', userId = null) {
    if (!taskId) {
      throw new Error('Task ID is required');
    }

    return this.updateStatus(taskId, STATUS_ENUM.CANCELLED, {
      reason,
      userId,
      metadata: {
        action: 'cancel',
        cancellation_reason: reason,
      },
    });
  },

  /**
   * Get task status history
   * @param {string} taskId - Task ID
   * @param {number} limit - Maximum number of history entries to return
   * @returns {Promise<Object>} - History object with entries array
   */
  async getHistory(taskId, limit = 50) {
    if (!taskId) {
      throw new Error('Task ID is required');
    }

    try {
      const response = await makeRequest(
        `/api/tasks/${taskId}/status-history?limit=${limit}`,
        'GET'
      );
      return response;
    } catch (error) {
      return {
        task_id: taskId,
        history: [],
        total: 0,
        error: error.message,
      };
    }
  },

  /**
   * Get validation failures for a task
   * @param {string} taskId - Task ID
   * @param {number} limit - Maximum number of failures to return
   * @returns {Promise<Object>} - Failures object with entries array
   */
  async getFailures(taskId, limit = 50) {
    if (!taskId) {
      throw new Error('Task ID is required');
    }

    try {
      const response = await makeRequest(
        `/api/tasks/${taskId}/status-history/failures?limit=${limit}`,
        'GET'
      );
      return response;
    } catch (error) {
      return {
        task_id: taskId,
        failures: [],
        total: 0,
        error: error.message,
      };
    }
  },

  /**
   * Get task status metrics/dashboard data
   * @param {Object} options - Query options
   * @param {string} options.timeRange - Time range (e.g., '7d', '30d', '90d')
   * @param {string} options.status - Filter by status
   * @returns {Promise<Object>} - Metrics data
   */
  async getMetrics(options = {}) {
    const { timeRange = '7d', status = null } = options;

    try {
      const query = new URLSearchParams();
      query.append('time_range', timeRange);
      if (status) {
        query.append('status', status);
      }

      const response = await makeRequest(
        `/api/tasks/metrics/summary?${query.toString()}`,
        'GET'
      );
      return response;
    } catch (error) {
      return {
        metrics: {},
        error: error.message,
      };
    }
  },

  /**
   * Retry a failed task
   * @param {string} taskId - Task ID to retry
   * @param {string} reason - Reason for retry
   * @param {string} userId - Optional user ID
   * @returns {Promise<Object>} - Result object
   */
  async retry(taskId, reason = 'Manual retry', userId = null) {
    if (!taskId) {
      throw new Error('Task ID is required');
    }

    return this.updateStatus(taskId, STATUS_ENUM.PENDING, {
      reason,
      userId,
      metadata: {
        action: 'retry',
        retried_at: new Date().toISOString(),
      },
    });
  },

  /**
   * Batch approve multiple tasks
   * @param {string[]} taskIds - Array of task IDs
   * @param {string} feedback - Shared feedback for all tasks
   * @returns {Promise<Object[]>} - Array of result objects
   */
  async batchApprove(taskIds, feedback = '') {
    if (!Array.isArray(taskIds) || taskIds.length === 0) {
      throw new Error('At least one task ID is required');
    }

    return Promise.all(taskIds.map((id) => this.approve(id, feedback))).catch(
      (error) => {
        throw new Error('One or more approvals failed: ' + error.message);
      }
    );
  },

  /**
   * Batch reject multiple tasks
   * @param {string[]} taskIds - Array of task IDs
   * @param {string} reason - Shared reason for rejection
   * @returns {Promise<Object[]>} - Array of result objects
   */
  async batchReject(taskIds, reason) {
    if (!Array.isArray(taskIds) || taskIds.length === 0) {
      throw new Error('At least one task ID is required');
    }
    if (!reason || !reason.trim()) {
      throw new Error('Rejection reason is required');
    }

    return Promise.all(taskIds.map((id) => this.reject(id, reason))).catch(
      (error) => {
        throw new Error('One or more rejections failed: ' + error.message);
      }
    );
  },
};

export default unifiedStatusService;
