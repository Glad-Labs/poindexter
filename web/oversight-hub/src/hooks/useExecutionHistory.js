/**
 * useExecutionHistory
 *
 * Fetches and caches the execution history for a given workflow ID.
 * Re-fetches whenever workflowId or executionId changes (so a new execution
 * automatically refreshes the list).
 *
 * Extracted from WorkflowCanvas.jsx (#295).
 */
import { useState, useEffect, useCallback } from 'react';
import * as workflowBuilderService from '../services/workflowBuilderService';

/**
 * @param {object} params
 * @param {string|null} params.workflowId - persisted workflow id
 * @param {string|null} params.executionId - current execution id (used as
 *   a refresh trigger so history updates when a new execution starts)
 */
const useExecutionHistory = ({ workflowId, executionId } = {}) => {
  const [executionHistory, setExecutionHistory] = useState([]);
  const [executionHistoryLoading, setExecutionHistoryLoading] = useState(false);
  const [executionHistoryError, setExecutionHistoryError] = useState('');

  const loadExecutionHistory = useCallback(
    async (workflowIdOverride = null) => {
      const targetWorkflowId = workflowIdOverride || workflowId;

      if (!targetWorkflowId) {
        setExecutionHistory([]);
        setExecutionHistoryError('');
        return;
      }

      try {
        setExecutionHistoryLoading(true);
        const result = await workflowBuilderService.getWorkflowExecutions(
          targetWorkflowId,
          { limit: 10, offset: 0 }
        );
        setExecutionHistory(result?.executions || []);
        setExecutionHistoryError('');
      } catch (historyError) {
        setExecutionHistoryError(
          historyError?.message || 'Failed to load execution history'
        );
      } finally {
        setExecutionHistoryLoading(false);
      }
    },
    [workflowId]
  );

  useEffect(() => {
    loadExecutionHistory();
  }, [loadExecutionHistory, executionId]);

  return {
    executionHistory,
    executionHistoryLoading,
    executionHistoryError,
    loadExecutionHistory,
  };
};

export default useExecutionHistory;
