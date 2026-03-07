import logger from '@/lib/logger';
import React, { useState } from 'react';

/**
 * Enhanced Error Display Component
 * Shows detailed error information from failed tasks
 * Handles different error formats (string, JSON, structured objects)
 */
const ErrorDetailPanel = ({ task }) => {
  const [isExpanded, setIsExpanded] = useState(false);

  if (!task || task.status !== 'failed') {
    return null;
  }

  // Extract error information from various possible locations
  const getErrorDetails = () => {
    const errors = {
      primary: null,
      secondary: [],
      metadata: {},
      stack: null,
    };

    // Check task_metadata for error info (PRIORITY: orchestrator output)
    if (task.task_metadata) {
      if (task.task_metadata.error_message) {
        errors.primary = task.task_metadata.error_message;
      }
      if (task.task_metadata.error_details) {
        if (typeof task.task_metadata.error_details === 'string') {
          try {
            errors.metadata = JSON.parse(task.task_metadata.error_details);
          } catch (parseErr) {
            logger.error('Failed to parse error_details:', parseErr);
            errors.secondary.push(task.task_metadata.error_details);
          }
        } else {
          errors.metadata = task.task_metadata.error_details;
        }
      }
      if (task.task_metadata.stage) {
        errors.metadata.failedAtStage = task.task_metadata.stage;
      }
      if (task.task_metadata.message) {
        errors.metadata.stageMessage = task.task_metadata.message;
      }
      // Extract other useful metadata
      if (task.task_metadata.error_code) {
        errors.metadata.errorCode = task.task_metadata.error_code;
      }
      if (task.task_metadata.error_type) {
        errors.metadata.errorType = task.task_metadata.error_type;
      }
    }

    // Check direct error_message field (database column)
    if (!errors.primary && task.error_message) {
      errors.primary = task.error_message;
    }

    // Check metadata field
    if (task.metadata) {
      if (typeof task.metadata === 'string') {
        try {
          const parsed = JSON.parse(task.metadata);
          if (parsed.error_message) {
            errors.secondary.push(parsed.error_message);
          }
          if (parsed.error) {
            errors.secondary.push(
              typeof parsed.error === 'string'
                ? parsed.error
                : JSON.stringify(parsed.error)
            );
          }
        } catch (parseErr) {
          logger.error('Failed to parse metadata field:', parseErr);
          if (task.metadata) errors.secondary.push(task.metadata);
        }
      } else if (task.metadata && typeof task.metadata === 'object') {
        if (task.metadata.error_message) {
          errors.secondary.push(task.metadata.error_message);
        }
        if (task.metadata.error) {
          errors.secondary.push(
            typeof task.metadata.error === 'string'
              ? task.metadata.error
              : JSON.stringify(task.metadata.error)
          );
        }
      }
    }

    // Check result for error info (legacy)
    if (task.result) {
      if (typeof task.result === 'string') {
        try {
          const parsed = JSON.parse(task.result);
          if (parsed.error) {
            errors.secondary.push(
              typeof parsed.error === 'string'
                ? parsed.error
                : JSON.stringify(parsed.error)
            );
          }
        } catch (parseErr) {
          logger.error('Failed to parse result field:', parseErr);
          if (task.result) errors.secondary.push(task.result);
        }
      } else if (typeof task.result === 'object') {
        if (task.result.error) {
          errors.secondary.push(
            typeof task.result.error === 'string'
              ? task.result.error
              : JSON.stringify(task.result.error)
          );
        }
      }
    }

    // If still no primary error, use status or fallback
    if (!errors.primary && !errors.secondary.length) {
      errors.primary = `Task failed (Status: ${task.status || 'unknown'})`;
    }

    return errors;
  };

  const errorDetails = getErrorDetails();

  // If no error details found, show basic message with task info
  if (!errorDetails.primary && errorDetails.secondary.length === 0) {
    return (
      <div className="space-y-3">
        <div className="bg-red-900/20 border border-red-500/50 rounded-lg p-4">
          <div className="flex items-center gap-3">
            <span className="text-2xl">❌</span>
            <div>
              <p className="font-semibold text-red-300">Task Failed</p>
              <p className="text-sm text-red-200 mt-1">
                No detailed error information available
              </p>
            </div>
          </div>
        </div>

        {/* Show what we do know */}
        <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-4 text-sm space-y-2">
          <p className="text-gray-300">
            <span className="text-gray-400">Status:</span>{' '}
            <span className="font-mono text-gray-200">{task.status}</span>
          </p>
          {task.topic && (
            <p className="text-gray-300">
              <span className="text-gray-400">Topic:</span>{' '}
              <span className="font-mono text-gray-200 truncate">
                {task.topic}
              </span>
            </p>
          )}
          {task.task_type && (
            <p className="text-gray-300">
              <span className="text-gray-400">Task Type:</span>{' '}
              <span className="font-mono text-gray-200">{task.task_type}</span>
            </p>
          )}
          {task.stage && (
            <p className="text-gray-300">
              <span className="text-gray-400">Failed at Stage:</span>{' '}
              <span className="font-mono text-yellow-300">{task.stage}</span>
            </p>
          )}
          {task.percentage !== undefined && (
            <p className="text-gray-300">
              <span className="text-gray-400">Progress:</span>{' '}
              <span className="font-mono text-gray-200">
                {task.percentage}%
              </span>
            </p>
          )}
          {task.started_at && task.completed_at && (
            <p className="text-gray-300">
              <span className="text-gray-400">Duration:</span>{' '}
              <span className="font-mono text-gray-200">
                {Math.round(
                  (new Date(task.completed_at) - new Date(task.started_at)) /
                    1000
                )}{' '}
                seconds
              </span>
            </p>
          )}
        </div>

        <div className="bg-blue-900/20 border border-blue-500/30 rounded-lg p-3 text-xs text-blue-200">
          <p>
            💡 If the error details are missing, check the backend logs to see
            what caused the failure.
          </p>
        </div>
      </div>
    );
  }

  const hasMetadata = Object.keys(errorDetails.metadata).length > 0;
  const hasSecondary = errorDetails.secondary.length > 0;

  return (
    <div className="space-y-3">
      {/* Primary Error Message */}
      {errorDetails.primary && (
        <div className="bg-red-900/30 border border-red-500/50 rounded-lg p-4">
          <div className="flex items-start gap-3">
            <span className="text-2xl flex-shrink-0">❌</span>
            <div className="flex-1 min-w-0">
              <p className="font-semibold text-red-300">Error</p>
              <p className="text-sm text-red-100 mt-2 break-words">
                {errorDetails.primary}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Detailed Metadata */}
      {hasMetadata && (
        <div className="bg-red-900/20 border border-red-500/30 rounded-lg p-4">
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="flex items-center gap-2 text-red-300 hover:text-red-200 font-medium transition w-full"
          >
            <span
              className={`transition transform ${isExpanded ? 'rotate-180' : ''}`}
            >
              ▼
            </span>
            <span>Detailed Information</span>
          </button>

          {isExpanded && (
            <div className="mt-3 space-y-2 text-sm">
              {errorDetails.metadata.failedAtStage && (
                <div className="bg-red-900/30 rounded p-2">
                  <p className="text-red-300 font-mono">
                    <span className="text-red-400">Stage:</span>{' '}
                    {errorDetails.metadata.failedAtStage}
                  </p>
                </div>
              )}

              {errorDetails.metadata.stageMessage && (
                <div className="bg-red-900/30 rounded p-2">
                  <p className="text-red-100 font-mono text-xs break-words">
                    {errorDetails.metadata.stageMessage}
                  </p>
                </div>
              )}

              {errorDetails.metadata.errorCode && (
                <div className="bg-red-900/30 rounded p-2">
                  <p className="text-red-300 font-mono text-sm">
                    <span className="text-red-400">Error Code:</span>{' '}
                    {errorDetails.metadata.errorCode}
                  </p>
                </div>
              )}

              {errorDetails.metadata.errorType && (
                <div className="bg-red-900/30 rounded p-2">
                  <p className="text-red-300 font-mono text-sm">
                    <span className="text-red-400">Error Type:</span>{' '}
                    {errorDetails.metadata.errorType}
                  </p>
                </div>
              )}

              {errorDetails.metadata.code && (
                <div className="bg-red-900/30 rounded p-2">
                  <p className="text-red-300 font-mono">
                    <span className="text-red-400">Code:</span>{' '}
                    {errorDetails.metadata.code}
                  </p>
                </div>
              )}

              {errorDetails.metadata.context && (
                <div className="bg-red-900/30 rounded p-2">
                  <p className="text-red-300 font-mono text-xs break-all">
                    <span className="text-red-400">Context:</span>{' '}
                    {errorDetails.metadata.context}
                  </p>
                </div>
              )}

              {errorDetails.metadata.timestamp && (
                <div className="bg-red-900/30 rounded p-2">
                  <p className="text-red-300 font-mono text-xs">
                    <span className="text-red-400">Failed at:</span>{' '}
                    {new Date(errorDetails.metadata.timestamp).toLocaleString()}
                  </p>
                </div>
              )}

              {/* Show other metadata fields */}
              {Object.entries(errorDetails.metadata).map(([key, value]) => {
                // Skip fields we've already displayed
                if (
                  [
                    'failedAtStage',
                    'stageMessage',
                    'code',
                    'context',
                    'timestamp',
                    'errorCode',
                    'errorType',
                  ].includes(key)
                ) {
                  return null;
                }

                // Skip null/undefined/empty values
                if (value === null || value === undefined || value === '') {
                  return null;
                }

                const displayValue =
                  typeof value === 'object'
                    ? JSON.stringify(value, null, 2)
                    : String(value);

                const truncatedValue =
                  displayValue.length > 300
                    ? displayValue.substring(0, 300) + '...'
                    : displayValue;

                return (
                  <div key={key} className="bg-red-900/30 rounded p-2">
                    <p className="text-red-300 font-mono text-xs break-all whitespace-pre-wrap">
                      <span className="text-red-400 capitalize block mb-1">
                        {key.replace(/_/g, ' ')}:
                      </span>
                      <span className="text-red-100">{truncatedValue}</span>
                    </p>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Secondary Errors */}
      {hasSecondary &&
        errorDetails.secondary.map((error, idx) => (
          <div
            key={idx}
            className="bg-red-900/20 border border-red-500/30 rounded-lg p-3"
          >
            <p className="text-xs text-red-200 font-mono break-words">
              <span className="text-red-400">Secondary Error {idx + 1}:</span>{' '}
              {error}
            </p>
          </div>
        ))}

      {/* Debug Info */}
      <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-3 text-xs">
        <p className="text-gray-400 mb-2">
          <strong>Task ID:</strong>{' '}
          <span className="font-mono text-gray-300">
            {task.id?.slice(0, 12)}...
          </span>
        </p>
        {task.completed_at && (
          <p className="text-gray-400">
            <strong>Failed:</strong>{' '}
            <span className="font-mono text-gray-300">
              {new Date(task.completed_at).toLocaleString()}
            </span>
          </p>
        )}
        {task.started_at && (
          <p className="text-gray-400 mt-1">
            <strong>Duration:</strong>{' '}
            <span className="font-mono text-gray-300">
              {Math.round(
                (new Date(task.completed_at) - new Date(task.started_at)) / 1000
              )}{' '}
              seconds
            </span>
          </p>
        )}
      </div>
    </div>
  );
};

export default ErrorDetailPanel;
