/**
 * Status Components for Task History and Metrics Display
 *
 * Includes:
 * - StatusAuditTrail: Displays full audit trail of status changes
 * - StatusTimeline: Visual timeline of status progression
 * - ValidationFailureUI: Shows validation failures
 * - StatusDashboardMetrics: Real-time status distribution metrics
 */

import React, { useState, useEffect } from 'react';
import { Box, Typography, CircularProgress, Paper, Chip } from '@mui/material';
import { unifiedStatusService } from '../../services/unifiedStatusService';
import { STATUS_COLORS } from '../../Constants/statusEnums';

/**
 * StatusAuditTrail Component
 * Displays detailed audit trail of all status changes for a task
 */
export const StatusAuditTrail = ({ taskId, limit = 100 }) => {
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const fetchHistory = async () => {
      try {
        setLoading(true);
        const data = await unifiedStatusService.getHistory(taskId, limit);
        setHistory(Array.isArray(data) ? data : []);
      } catch (err) {
        setError(err.message || 'Failed to load audit trail');
      } finally {
        setLoading(false);
      }
    };

    if (taskId) {
      fetchHistory();
    }
  }, [taskId, limit]);

  if (loading) return <CircularProgress size={24} />;
  if (error) return <Typography color="error">⚠️ {error}</Typography>;

  if (!history || history.length === 0) {
    return (
      <Typography color="textSecondary">
        No status changes recorded yet.
      </Typography>
    );
  }

  return (
    <Box sx={{ space: 2 }}>
      {history.map((entry, idx) => (
        <Paper
          key={idx}
          sx={{
            p: 2,
            mb: 2,
            backgroundColor: '#f5f5f5',
            borderLeft: `4px solid ${STATUS_COLORS[entry.new_status] || '#999'}`,
          }}
        >
          <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
            <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
              {entry.old_status} → {entry.new_status}
            </Typography>
            <Typography variant="caption" color="textSecondary">
              {new Date(entry.timestamp).toLocaleString()}
            </Typography>
          </Box>

          {entry.reason && (
            <Typography variant="body2" sx={{ mb: 1, fontStyle: 'italic' }}>
              <strong>Reason:</strong> {entry.reason}
            </Typography>
          )}

          {entry.metadata && (
            <Box sx={{ mt: 1, pt: 1, borderTop: '1px solid #ddd' }}>
              <Typography
                variant="caption"
                display="block"
                color="textSecondary"
              >
                <strong>Metadata:</strong>{' '}
                {JSON.stringify(entry.metadata, null, 2)}
              </Typography>
            </Box>
          )}
        </Paper>
      ))}
    </Box>
  );
};

/**
 * StatusTimeline Component
 * Visual representation of status progression over time
 */
export const StatusTimeline = ({
  currentStatus,
  statusHistory = [],
  compact = false,
}) => {
  if (!statusHistory || statusHistory.length === 0) {
    return (
      <Typography color="textSecondary">
        Current Status: <strong>{currentStatus}</strong>
      </Typography>
    );
  }

  return (
    <Box sx={{ space: 2 }}>
      <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 600 }}>
        Status Progression
      </Typography>

      {!compact && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {statusHistory.map((entry, idx) => (
            <Box
              key={idx}
              sx={{ display: 'flex', alignItems: 'center', gap: 2 }}
            >
              <Box
                sx={{
                  width: 12,
                  height: 12,
                  borderRadius: '50%',
                  backgroundColor: STATUS_COLORS[entry.new_status] || '#999',
                }}
              />
              <Typography variant="body2">{entry.new_status}</Typography>
              <Typography variant="caption" color="textSecondary">
                {new Date(entry.timestamp).toLocaleString()}
              </Typography>
            </Box>
          ))}
        </Box>
      )}

      {compact && (
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          {statusHistory.map((entry, idx) => (
            <Chip
              key={idx}
              label={entry.new_status}
              size="small"
              sx={{
                backgroundColor: STATUS_COLORS[entry.new_status] || '#999',
                color: 'white',
              }}
            />
          ))}
        </Box>
      )}
    </Box>
  );
};

/**
 * ValidationFailureUI Component
 * Displays validation failures for a task
 */
export const ValidationFailureUI = ({ taskId, limit = 50 }) => {
  const [failures, setFailures] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const fetchFailures = async () => {
      try {
        setLoading(true);
        const data = await unifiedStatusService.getFailures(taskId, limit);
        setFailures(Array.isArray(data) ? data : []);
      } catch (err) {
        setError(err.message || 'Failed to load validation failures');
      } finally {
        setLoading(false);
      }
    };

    if (taskId) {
      fetchFailures();
    }
  }, [taskId, limit]);

  if (loading) return <CircularProgress size={24} />;
  if (error) return <Typography color="error">⚠️ {error}</Typography>;

  if (!failures || failures.length === 0) {
    return (
      <Typography color="success.main">
        ✅ No validation failures recorded.
      </Typography>
    );
  }

  return (
    <Box sx={{ space: 2 }}>
      <Typography
        variant="subtitle2"
        sx={{ mb: 2, fontWeight: 600, color: '#f44336' }}
      >
        {failures.length} Validation Failure{failures.length !== 1 ? 's' : ''}
      </Typography>

      {failures.map((failure, idx) => (
        <Paper
          key={idx}
          sx={{
            p: 2,
            mb: 2,
            backgroundColor: '#ffebee',
            borderLeft: '4px solid #f44336',
          }}
        >
          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
            {failure.constraint_name || 'Validation Error'}
          </Typography>

          {failure.message && (
            <Typography variant="body2" sx={{ mb: 1 }}>
              <strong>Message:</strong> {failure.message}
            </Typography>
          )}

          {failure.details && (
            <Typography
              variant="caption"
              display="block"
              sx={{ whiteSpace: 'pre-wrap' }}
            >
              <strong>Details:</strong>{' '}
              {JSON.stringify(failure.details, null, 2)}
            </Typography>
          )}

          <Typography
            variant="caption"
            color="textSecondary"
            sx={{ display: 'block', mt: 1 }}
          >
            {new Date(failure.timestamp || Date.now()).toLocaleString()}
          </Typography>
        </Paper>
      ))}
    </Box>
  );
};

/**
 * StatusDashboardMetrics Component
 * Real-time status distribution and KPI metrics
 */
export const StatusDashboardMetrics = ({ tasks = [] }) => {
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const parseNumber = (value, fallback = 0) => {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string') {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : fallback;
    }
    return fallback;
  };

  const normalizeMetricsData = (rawMetrics) => {
    const raw = rawMetrics || {};
    const totalTasks = parseNumber(raw.total_tasks, 0);
    const completedTasks = parseNumber(raw.completed_tasks, 0);
    const failedTasks = parseNumber(raw.failed_tasks, 0);
    const pendingTasks = parseNumber(raw.pending_tasks, 0);

    const statusDistribution = raw.status_distribution || {
      completed: completedTasks,
      failed: failedTasks,
      pending: pendingTasks,
    };

    const rawSuccessRate = parseNumber(raw.success_rate, 0);
    const normalizedSuccessRate =
      rawSuccessRate > 1 ? rawSuccessRate : rawSuccessRate * 100;

    return {
      ...raw,
      total_tasks: totalTasks,
      completed_tasks: completedTasks,
      failed_tasks: failedTasks,
      pending_tasks: pendingTasks,
      status_distribution: statusDistribution,
      success_rate: normalizedSuccessRate,
      average_processing_time: parseNumber(
        raw.average_processing_time,
        parseNumber(raw.avg_execution_time, 0)
      ),
    };
  };

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        setLoading(true);
        const data = await unifiedStatusService.getMetrics();
        setMetrics(normalizeMetricsData(data));
        setError('');
      } catch (err) {
        setError(err.message || 'Failed to load metrics');
      } finally {
        setLoading(false);
      }
    };

    if (Array.isArray(tasks) && tasks.length > 0) {
      const completed = tasks.filter(
        (t) => t.status?.toLowerCase() === 'completed'
      ).length;
      const failed = tasks.filter(
        (t) => t.status?.toLowerCase() === 'failed'
      ).length;
      const pending = Math.max(tasks.length - completed - failed, 0);
      const successRate =
        tasks.length > 0 ? (completed / tasks.length) * 100 : 0;

      setMetrics(
        normalizeMetricsData({
          total_tasks: tasks.length,
          completed_tasks: completed,
          failed_tasks: failed,
          pending_tasks: pending,
          success_rate: successRate,
          avg_execution_time: 0,
        })
      );
      setLoading(false);
      setError('');
      return;
    }

    fetchMetrics();
  }, [tasks]);

  if (loading) return <CircularProgress size={24} />;
  if (error) return <Typography color="error">⚠️ {error}</Typography>;

  if (!metrics) {
    return <Typography color="textSecondary">No metrics available.</Typography>;
  }

  return (
    <Box sx={{ space: 2 }}>
      <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 600 }}>
        Status Distribution
      </Typography>

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
          gap: 2,
        }}
      >
        {metrics.status_distribution &&
          Object.entries(metrics.status_distribution).map(([status, count]) => (
            <Paper
              key={status}
              sx={{ p: 2, textAlign: 'center', backgroundColor: '#f5f5f5' }}
            >
              <Box
                sx={{
                  width: '100%',
                  height: 4,
                  backgroundColor: STATUS_COLORS[status] || '#999',
                  mb: 1,
                  borderRadius: 2,
                }}
              />
              <Typography
                variant="caption"
                display="block"
                sx={{ textTransform: 'capitalize' }}
              >
                {status}
              </Typography>
              <Typography variant="h6" sx={{ fontWeight: 600 }}>
                {count}
              </Typography>
            </Paper>
          ))}
      </Box>

      {metrics.average_processing_time && (
        <Paper sx={{ p: 2, mt: 2, backgroundColor: '#f5f5f5' }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            Average Processing Time
          </Typography>
          <Typography variant="body2">
            {Math.round(metrics.average_processing_time)} seconds
          </Typography>
        </Paper>
      )}

      {metrics.success_rate && (
        <Paper sx={{ p: 2, mt: 2, backgroundColor: '#f5f5f5' }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            Success Rate
          </Typography>
          <Typography variant="body2">
            {metrics.success_rate.toFixed(1)}%
          </Typography>
        </Paper>
      )}
    </Box>
  );
};

const StatusComponentsExport = {
  StatusAuditTrail,
  StatusTimeline,
  ValidationFailureUI,
  StatusDashboardMetrics,
};

export default StatusComponentsExport;
