/**
 * MessageFormatters.js
 *
 * Formatting utilities for messages, execution data, and display values.
 */

/**
 * Truncate text to a maximum length, appending '...' if truncated.
 */
export const truncateText = (text, maxLength = 500) => {
  if (typeof text !== 'string') return '';
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength) + '...';
};

/**
 * Truncate text with a custom suffix.
 */
export const truncateTextCustom = (text, maxLength = 500, suffix = '...') => {
  if (typeof text !== 'string') return '';
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength) + suffix;
};

/**
 * Capitalize the first letter of a string.
 */
export const capitalizeFirst = (str) => {
  if (!str) return '';
  return str.charAt(0).toUpperCase() + str.slice(1);
};

/**
 * Format a word count for display. Values >= 1000 shown as X.XK.
 */
export const formatWordCount = (count) => {
  if (typeof count !== 'number' || count < 0) return '0';
  if (count >= 1000) return `${(count / 1000).toFixed(1)}K`;
  return String(count);
};

/**
 * Format a cost value as a dollar amount with 3 decimal places.
 */
export const formatCost = (cost) => {
  if (typeof cost !== 'number' || cost < 0) return '$0.00';
  return `$${cost.toFixed(3)}`;
};

/**
 * Format a quality score. Values 0-1 are normalized to 0-100 scale.
 */
export const formatQualityScore = (score) => {
  if (typeof score !== 'number') return 'N/A';
  const normalized = score <= 1 ? Math.round(score * 100) : Math.round(score);
  return `${normalized}/100`;
};

/**
 * Format a decimal (0-1) as a percentage string.
 */
export const formatPercentage = (value) => {
  if (typeof value !== 'number') return '0%';
  return `${Math.round(value * 100)}%`;
};

/**
 * Format execution time in seconds to a human-readable string.
 */
export const formatExecutionTime = (seconds) => {
  if (typeof seconds !== 'number' || seconds < 0) return '0s';
  if (seconds === 0) return '0s';

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hours > 0) {
    return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  }
  if (minutes > 0) {
    return secs > 0 ? `${minutes}m ${secs}s` : `${minutes}m`;
  }
  return `${secs}s`;
};

/**
 * Format a timestamp for display. Accepts Date objects, ISO strings, etc.
 */
export const formatTimestamp = (timestamp) => {
  if (timestamp == null) return 'N/A';
  const date = timestamp instanceof Date ? timestamp : new Date(timestamp);
  return date.toLocaleString();
};

/**
 * Format a timestamp as relative time (e.g. "5m ago", "2h ago").
 */
export const formatRelativeTime = (timestamp) => {
  if (timestamp == null) return 'N/A';

  const date = new Date(timestamp);
  const now = Date.now();
  const diffMs = now - date.getTime();
  const diffSeconds = Math.floor(diffMs / 1000);
  const diffMinutes = Math.floor(diffSeconds / 60);
  const diffHours = Math.floor(diffMinutes / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffSeconds < 60) return 'just now';
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays <= 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
};

/**
 * Format estimated time based on number of phases.
 */
export const formatEstimatedTime = (phases, avgPerPhase = 2) => {
  if (typeof phases !== 'number' || phases < 0) return '0 min';
  return `~${phases * avgPerPhase} min`;
};

/**
 * Format a phase status for display.
 */
export const formatPhaseStatus = (status) => {
  const statusMap = {
    complete: '✓ Done',
    current: '⏳ Running',
    pending: '⏸ Waiting',
  };
  return statusMap[status] || status;
};

/**
 * Format command parameters for display, filtering out internal fields.
 */
export const formatCommandParameters = (params) => {
  if (!params || typeof params !== 'object') return '';

  const internalFields = ['commandType', 'rawInput', 'additionalInstructions'];

  const entries = Object.entries(params).filter(
    ([key, value]) =>
      !internalFields.includes(key) && value !== '' && value != null
  );

  if (entries.length === 0) return '';

  return entries
    .map(([key, value]) => `${capitalizeFirst(key)}: ${value}`)
    .join(' • ');
};

/**
 * Format an error severity level for display.
 */
export const formatErrorSeverity = (severity) => {
  const severityMap = {
    error: '❌ Error',
    warning: '⚠️ Warning',
    info: 'ℹ️ Info',
  };
  return severityMap[severity] || severity;
};

/**
 * Format a phase label with an emoji prefix.
 */
export const formatPhaseLabel = (phase, emojiMap) => {
  const emoji = (emojiMap && emojiMap[phase]) || '⏳';
  return `${emoji} ${phase}`;
};

/**
 * Format a progress value (0-100) as a percentage string.
 */
export const formatProgress = (value) => {
  if (typeof value !== 'number' || value < 0 || value > 100) return '0%';
  return `${Math.round(value)}%`;
};

/**
 * Format an execution summary object for display.
 */
export const formatExecutionSummary = (execution) => {
  if (!execution) return 'No execution data';

  const status = execution.status || 'unknown';
  const phases = `${execution.currentPhaseIndex || 0}/${execution.totalPhases || 0} phases`;
  const duration = formatExecutionTime(execution.totalDuration || 0);

  return `${status} — ${phases} — ${duration}`;
};

/**
 * Format result metadata for display.
 */
export const formatResultMetadata = (metadata) => {
  if (!metadata) return {};

  return {
    words: formatWordCount(metadata.wordCount || 0),
    quality: formatQualityScore(
      metadata.qualityScore != null ? metadata.qualityScore : null
    ),
    cost: formatCost(metadata.cost || 0),
    time: formatExecutionTime(metadata.executionTime || 0),
    model: metadata.model || 'Unknown',
    provider: metadata.provider || 'Unknown',
  };
};

/**
 * Check if a value is formattable (not null, not undefined, not empty string).
 */
export const isFormattable = (value) => {
  if (value === null || value === undefined) return false;
  if (value === '') return false;
  return true;
};

/**
 * Safely apply a formatter function, returning a default value on error or null input.
 */
export const safeFormat = (formatter, value, defaultValue = 'N/A') => {
  if (value === null || value === undefined) return defaultValue;
  try {
    return formatter(value);
  } catch {
    return defaultValue;
  }
};
