/**
 * useWorkflowMonitor
 *
 * Owns all state and data-fetching for the Workflow Monitor accordion section
 * in UnifiedServicesPanel. Extracted from UnifiedServicesPanel.jsx (#304).
 *
 * State managed:
 *   monitorLoading, monitorError, executionHistory, statistics, performanceMetrics
 *
 * Exposes loadWorkflowMonitorData() so the parent can trigger a fetch when the
 * accordion expands.
 */
import { useState, useCallback } from 'react';
import logger from '@/lib/logger';
import * as workflowManagementService from '../services/workflowManagementService';

/**
 * @param {object} params
 * @param {Function} [params.onError] - optional; called with an error message
 *   string if needed for parent-level surfacing (monitor has its own error state,
 *   but this hook can escalate fatal errors upward).
 */
const useWorkflowMonitor = ({ onError } = {}) => {
  const [monitorLoading, setMonitorLoading] = useState(false);
  const [monitorError, setMonitorError] = useState(null);
  const [executionHistory, setExecutionHistory] = useState([]);
  const [statistics, setStatistics] = useState(null);
  const [performanceMetrics, setPerformanceMetrics] = useState(null);

  const loadWorkflowMonitorData = useCallback(async () => {
    setMonitorLoading(true);
    try {
      logger.log('[useWorkflowMonitor] Loading workflow monitor data...');

      const historyRes = await workflowManagementService.getWorkflowHistory({
        limit: 20,
      });
      setExecutionHistory(historyRes.executions || historyRes || []);

      const statsRes = await workflowManagementService.getWorkflowStatistics();
      setStatistics(statsRes.statistics || statsRes || {});

      const metricsRes =
        await workflowManagementService.getPerformanceMetrics();
      setPerformanceMetrics(metricsRes.metrics || metricsRes || {});

      setMonitorError(null);
    } catch (err) {
      const errorMsg =
        err?.message || String(err) || 'Unknown error loading monitor data';
      logger.error('[useWorkflowMonitor] Error loading monitor data:', err);
      setMonitorError(`Monitor Error: ${errorMsg}`);
      if (onError) {
        onError(`Monitor Error: ${errorMsg}`);
      }
    } finally {
      setMonitorLoading(false);
    }
  }, [onError]);

  return {
    monitorLoading,
    monitorError,
    executionHistory,
    statistics,
    performanceMetrics,
    loadWorkflowMonitorData,
  };
};

export default useWorkflowMonitor;
