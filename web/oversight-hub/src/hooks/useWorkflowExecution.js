/**
 * useWorkflowExecution
 *
 * Manages execution lifecycle state for a workflow:
 * - Current execution ID, status, progress, results, final output, error messages
 * - Polls /api/workflows/{executionId}/status every 2 s until terminal state
 * - Stops polling automatically on unmount or when execution reaches a terminal state
 *
 * Extracted from WorkflowCanvas.jsx (#295).
 */
import { useState, useEffect, useCallback } from 'react';
import * as workflowBuilderService from '../services/workflowBuilderService';

const TERMINAL_EXECUTION_STATUSES = new Set([
  'completed',
  'failed',
  'cancelled',
]);

const normalizeExecutionStatus = (status) =>
  typeof status === 'string' ? status.toLowerCase() : 'pending';

const parseExecutionStatusCode = (error) => {
  const statusCode =
    error?.status ||
    error?.statusCode ||
    error?.response?.status ||
    error?.response?.statusCode;

  if (Number.isFinite(statusCode)) {
    return Number(statusCode);
  }

  const message =
    typeof error?.message === 'string' ? error.message.toLowerCase() : '';
  if (message.includes('404') || message.includes('not found')) {
    return 404;
  }

  return null;
};

/**
 * @param {object} options
 * @param {Function} options.onHistoryRefresh - called with the persisted workflow id
 *   when a new execution starts, so the caller can refresh execution history.
 */
const useWorkflowExecution = ({ onHistoryRefresh } = {}) => {
  const [executionId, setExecutionId] = useState(null);
  const [executionStatus, setExecutionStatus] = useState(null);
  const [executionProgress, setExecutionProgress] = useState(0);
  const [executionResults, setExecutionResults] = useState({});
  const [executionFinalOutput, setExecutionFinalOutput] = useState(null);
  const [executionErrorMessage, setExecutionErrorMessage] = useState('');
  const [executionPollingError, setExecutionPollingError] = useState('');

  // ---- Polling effect -------------------------------------------------------
  useEffect(() => {
    if (!executionId) {
      return undefined;
    }

    let active = true;
    let intervalId;
    const controller = new AbortController();
    let pollInterval = 2000; // Start at 2s, increase on errors

    const pollExecutionStatus = async () => {
      if (controller.signal.aborted) return;
      try {
        const execution =
          await workflowBuilderService.getExecutionStatus(executionId);

        if (!active) {
          return;
        }

        const nextStatus = normalizeExecutionStatus(
          execution?.execution_status || execution?.status
        );
        const nextProgress = Number.isFinite(execution?.progress_percent)
          ? execution.progress_percent
          : nextStatus === 'completed'
            ? 100
            : 0;

        setExecutionStatus(nextStatus);
        setExecutionProgress(nextProgress);
        setExecutionResults(execution?.phase_results || {});
        setExecutionFinalOutput(execution?.final_output ?? null);
        setExecutionErrorMessage(execution?.error_message || '');
        setExecutionPollingError('');

        if (TERMINAL_EXECUTION_STATUSES.has(nextStatus) && intervalId) {
          clearInterval(intervalId);
        }
      } catch (pollError) {
        if (!active) {
          return;
        }

        const statusCode = parseExecutionStatusCode(pollError);
        if (statusCode === 404) {
          setExecutionStatus((currentStatus) => currentStatus || 'pending');
          setExecutionPollingError('');
          return;
        }

        setExecutionPollingError(
          pollError?.message || 'Failed to refresh execution status'
        );
        // Back off on errors: 2s → 4s → 8s → 15s max
        pollInterval = Math.min(pollInterval * 2, 15000);
        clearInterval(intervalId);
        intervalId = setInterval(pollExecutionStatus, pollInterval);
      }
    };

    pollExecutionStatus();
    intervalId = setInterval(pollExecutionStatus, pollInterval);

    return () => {
      active = false;
      controller.abort();
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  }, [executionId]);

  // ---- Start a new execution ------------------------------------------------
  /**
   * Persist (create or update) the workflow, then execute it.
   *
   * @param {object} params
   * @param {object} params.workflow  - current workflow object (may have .id)
   * @param {boolean} params.isPersistedWorkflow
   * @param {object} params.definition - workflow definition to save/run
   * @returns {Promise<void>}
   */
  const startExecution = useCallback(
    async ({ workflow, isPersistedWorkflow, definition }) => {
      const persistedWorkflow = workflow?.id
        ? isPersistedWorkflow
          ? await workflowBuilderService.updateWorkflow(workflow.id, definition)
          : await workflowBuilderService.createWorkflow(definition)
        : await workflowBuilderService.createWorkflow(definition);

      const execution = await workflowBuilderService.executeWorkflow(
        persistedWorkflow.id,
        {
          topic: definition.name,
          source: 'workflow_canvas',
        }
      );

      setExecutionId(execution.execution_id || null);
      setExecutionStatus(normalizeExecutionStatus(execution.status));
      setExecutionProgress(
        Number.isFinite(execution?.progress_percent)
          ? execution.progress_percent
          : 0
      );
      setExecutionResults({});
      setExecutionFinalOutput(null);
      setExecutionErrorMessage('');
      setExecutionPollingError('');

      if (onHistoryRefresh) {
        onHistoryRefresh(persistedWorkflow?.id || null);
      }

      return execution.execution_id || null;
    },
    [onHistoryRefresh]
  );

  return {
    executionId,
    setExecutionId,
    executionStatus,
    executionProgress,
    executionResults,
    executionFinalOutput,
    executionErrorMessage,
    executionPollingError,
    startExecution,
  };
};

export default useWorkflowExecution;
