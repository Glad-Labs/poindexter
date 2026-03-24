/**
 * taskDataFormatter.js
 *
 * Utility functions for formatting task data for display.
 */

/**
 * Format a date/time value for display using locale formatting with 12-hour time.
 */
export const formatDateTime = (dateInput) => {
  if (dateInput == null) return '';
  try {
    const date = dateInput instanceof Date ? dateInput : new Date(dateInput);
    return date.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    });
  } catch {
    return '';
  }
};

/**
 * Format a date value for display (date only, no time).
 */
export const formatDate = (dateInput) => {
  if (dateInput == null) return '';
  try {
    const date = dateInput instanceof Date ? dateInput : new Date(dateInput);
    return date.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return '';
  }
};

const STATUS_DISPLAY = {
  pending: 'Pending',
  in_progress: 'In Progress',
  completed: 'Completed',
  failed: 'Failed',
  awaiting_approval: 'Awaiting Approval',
  approved: 'Approved',
  published: 'Published',
  cancelled: 'Cancelled',
};

/**
 * Format a task object for display, computing derived fields.
 */
export const formatTaskForDisplay = (task) => {
  if (!task) return null;

  const status = task.status || 'pending';
  const metadata = task.task_metadata || {};

  return {
    id: task.id || task.task_id,
    taskId: task.task_id || task.id,
    title: task.title || task.task_name || '',
    topic: task.topic || '',
    category: task.category || '',
    status,
    displayStatus: STATUS_DISPLAY[status] || status,
    qualityScore: task.quality_score ?? null,
    createdAt: task.created_at ? formatDateTime(task.created_at) : '',
    updatedAt: task.updated_at ? formatDateTime(task.updated_at) : '',
    completedAt: task.completed_at ? formatDateTime(task.completed_at) : '',

    // Status flags
    isInProgress: status === 'in_progress',
    isAwaitingApproval: status === 'awaiting_approval',
    isApproved: status === 'approved',
    isPublished: status === 'published',
    isCompleted: status === 'completed',
    isFailed: status === 'failed',

    // Content
    content: metadata.content || '',
    hasFeaturedImage: !!metadata.featured_image_url,
    featuredImageUrl: metadata.featured_image_url || null,
  };
};

/**
 * Get a quality badge (label, color, backgroundColor) for a quality score.
 */
export const getQualityBadge = (score) => {
  const numScore = typeof score === 'string' ? parseFloat(score) : score;
  if (typeof numScore !== 'number' || isNaN(numScore)) {
    return { label: 'N/A', color: '#666', backgroundColor: '#f0f0f0' };
  }

  if (numScore >= 90)
    return { label: 'Excellent', color: '#166534', backgroundColor: '#dcfce7' };
  if (numScore >= 75)
    return { label: 'Good', color: '#15803d', backgroundColor: '#d1fae5' };
  if (numScore >= 60)
    return { label: 'Fair', color: '#854d0e', backgroundColor: '#fef9c3' };
  return { label: 'Poor', color: '#991b1b', backgroundColor: '#fee2e2' };
};

/**
 * Get a human-readable duration display between two dates.
 */
export const getDurationDisplay = (start, end) => {
  if (!start || !end) return '';

  const startDate = start instanceof Date ? start : new Date(start);
  const endDate = end instanceof Date ? end : new Date(end);
  const diffMs = endDate.getTime() - startDate.getTime();
  const totalMinutes = Math.floor(diffMs / 60000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;

  if (hours > 0 && minutes > 0) return `${hours}h ${minutes}m`;
  if (hours > 0) return `${hours} hour${hours > 1 ? 's' : ''}`;
  return `${totalMinutes} minute${totalMinutes !== 1 ? 's' : ''}`;
};
