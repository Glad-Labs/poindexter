/**
 * Task Data Formatter Utilities
 *
 * Centralized functions for formatting task data for display
 * Reduces duplication across TaskTable, TaskDetailModal, StatusComponents, etc.
 */

import { getStatusConfig } from '../lib/statusConfig';

/**
 * Format a task object for UI display
 * Adds computed fields while preserving original data
 *
 * @param {object} task - Raw task object from API
 * @returns {object} Formatted task with display fields
 */
export const formatTaskForDisplay = (task) => {
  if (!task) return null;

  const statusConfig = getStatusConfig(task.status);

  return {
    ...task,
    // Status formatting
    displayStatus: statusConfig.label,
    statusIcon: statusConfig.icon,
    statusColor: statusConfig.color,
    statusDescription: statusConfig.description,
    statusBackgroundColor: statusConfig.backgroundColor,
    statusBorderColor: statusConfig.borderColor,
    statusTextColor: statusConfig.textColor,

    // Content formatting
    contentPreview: (task.task_metadata?.content || '').substring(0, 200),
    contentLength: (task.task_metadata?.content || '').length,

    // Image formatting
    hasFeaturedImage: !!task.task_metadata?.featured_image_url,
    featuredImageUrl: task.task_metadata?.featured_image_url,

    // Quality formatting
    qualityScore: task.quality_score || 0, // Already on 0-100 scale from backend
    qualityBadge: getQualityBadge(task.quality_score),

    // Metadata
    topicDisplay: task.topic || task.task_name || 'Untitled',
    categoryDisplay: task.category || 'Uncategorized',

    // Error information
    errorStage: task.task_metadata?.error_stage,
    errorMessage: task.task_metadata?.error_message,
    hasError: !!task.task_metadata?.error_message,

    // Timestamps
    createdAtDisplay: formatDate(task.created_at),
    updatedAtDisplay: formatDate(task.updated_at),
    completedAtDisplay: task.completed_at
      ? formatDate(task.completed_at)
      : null,

    // Computed flags
    isAwaitingApproval: task.status === 'awaiting_approval',
    isApproved: task.status === 'approved',
    isPublished: task.status === 'published',
    isFailed: task.status === 'failed',
    isRejected: task.status === 'rejected',
    isPending: task.status === 'pending',
    isInProgress: task.status === 'in_progress',
  };
};

/**
 * Extract metadata fields from task for grouped display
 *
 * @param {object} task - Task object
 * @returns {object} Extracted metadata
 */
export const extractTaskMetadata = (task) => {
  if (!task) return {};

  return {
    category: task.category,
    style: task.style,
    tone: task.tone,
    target_audience: task.target_audience,
    target_length: task.target_length,
    quality_preference: task.quality_preference,
    quality_score: task.quality_score,
    primary_keyword: task.primary_keyword,
    created_at: new Date(task.created_at).toLocaleDateString(),
    created_time: new Date(task.created_at).toLocaleTimeString(),
    updated_at: task.updated_at
      ? new Date(task.updated_at).toLocaleDateString()
      : null,
  };
};

/**
 * Extract SEO metadata from task
 *
 * @param {object} task - Task object
 * @returns {object} SEO fields
 */
export const extractSEOMetadata = (task) => {
  if (!task || !task.task_metadata) return {};

  return {
    seo_title: task.task_metadata.seo_title,
    seo_description: task.task_metadata.seo_description,
    seo_keywords: Array.isArray(task.task_metadata.seo_keywords)
      ? task.task_metadata.seo_keywords
      : (task.task_metadata.seo_keywords || '').split(',').filter(Boolean),
  };
};

/**
 * Format task for table display (minimal fields)
 *
 * @param {object} task - Task object
 * @returns {object} Table-friendly task data
 */
export const formatTaskForTable = (task) => {
  if (!task) return null;

  const formatted = formatTaskForDisplay(task);

  return {
    id: task.id,
    task_id: task.task_id,
    name: formatted.topicDisplay,
    type: task.task_type || 'blog_post',
    status: task.status,
    displayStatus: formatted.displayStatus,
    statusIcon: formatted.statusIcon,
    statusColor: formatted.statusColor,
    created_at: formatted.createdAtDisplay,
    quality_score: formatted.qualityScore,
    qualityBadge: formatted.qualityBadge,
  };
};

/**
 * Get quality badge/category for a score
 *
 * @param {number} score - Quality score (0-100)
 * @returns {object} { label, color, backgroundColor }
 */
export const getQualityBadge = (score) => {
  const numScore = parseFloat(score) || 0;

  if (numScore >= 90) {
    return {
      label: 'Excellent',
      color: '#059669',
      backgroundColor: '#dcfce7',
    };
  } else if (numScore >= 75) {
    return {
      label: 'Good',
      color: '#0891b2',
      backgroundColor: '#cffafe',
    };
  } else if (numScore >= 60) {
    return {
      label: 'Fair',
      color: '#d97706',
      backgroundColor: '#fef3c7',
    };
  } else {
    return {
      label: 'Poor',
      color: '#dc2626',
      backgroundColor: '#fee2e2',
    };
  }
};

/**
 * Format date for display
 *
 * @param {string|Date} date - ISO date string or Date object
 * @returns {string} Formatted date (e.g., "Jan 22, 2026")
 */
export const formatDate = (date) => {
  if (!date) return '';

  try {
    return new Date(date).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    console.warn('Invalid date:', date);
    return '';
  }
};

/**
 * Format date and time for display
 *
 * @param {string|Date} date - ISO date string or Date object
 * @returns {string} Formatted date and time (e.g., "Jan 22, 2026 at 2:30 PM")
 */
export const formatDateTime = (date) => {
  if (!date) return '';

  try {
    return new Date(date).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    });
  } catch {
    console.warn('Invalid date:', date);
    return '';
  }
};

/**
 * Get duration between two dates in readable format
 *
 * @param {string|Date} startDate - Start date
 * @param {string|Date} endDate - End date
 * @returns {string} Readable duration (e.g., "2 hours 30 minutes")
 */
export const getDurationDisplay = (startDate, endDate) => {
  if (!startDate || !endDate) return '';

  try {
    const start = new Date(startDate);
    const end = new Date(endDate);
    const diffMs = end - start;
    const diffMins = Math.round(diffMs / 60000);

    if (diffMins < 60) {
      return `${diffMins} minute${diffMins !== 1 ? 's' : ''}`;
    }

    const hours = Math.floor(diffMins / 60);
    const mins = diffMins % 60;

    if (mins === 0) {
      return `${hours} hour${hours !== 1 ? 's' : ''}`;
    }

    return `${hours}h ${mins}m`;
  } catch {
    if (process.env.NODE_ENV !== 'production') {
      console.warn('Invalid dates:', startDate, endDate);
    }
    return '';
  }
};
