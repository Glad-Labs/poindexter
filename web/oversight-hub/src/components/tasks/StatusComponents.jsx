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
        if (Array.isArray(data)) {
          setHistory(data);
        } else if (Array.isArray(data?.history)) {
          setHistory(data.history);
        } else {
          setHistory([]);
        }
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
 * Displays validation failures from task metadata (updated with validation_details)
 */
export const ValidationFailureUI = ({ task, taskId, limit = 50 }) => {
  const [failures, setFailures] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (task && task.task_metadata && task.task_metadata.validation_details) {
      // Use validation_details from task metadata (new approach)
      const details = task.task_metadata.validation_details;
      const failingGates = [];

      if (!details.base_content_valid) {
        failingGates.push({
          constraint_name: '❌ Content Validity',
          message: 'Content too short or empty',
          severity: 'error',
        });
      }

      if (!details.length_gate_passes && details.length_gate_detail) {
        const detail = details.length_gate_detail;
        failingGates.push({
          constraint_name: '❌ Length Gate',
          message: `Word count insufficient`,
          details: `Generated: ${detail.word_count} words | Target: ${detail.target} | Minimum: ${detail.minimum} (tolerance: ${detail.tolerance_percent}%)`,
          severity: 'error',
        });
      }

      if (!details.style_gate_passes && details.style_gate_detail) {
        failingGates.push({
          constraint_name: '❌ Style Gate',
          message: 'Style inconsistent',
          details: details.style_gate_detail,
          severity: 'warning',
        });
      }

      if (!details.seo_gate_passes && details.seo_gate_detail) {
        failingGates.push({
          constraint_name: '❌ SEO Gate',
          message: 'SEO issues detected',
          details: details.seo_gate_detail,
          severity: 'error',
        });
      }

      setFailures(failingGates);
      return;
    }

    // Fallback: Load from legacy failures endpoint
    const fetchFailures = async () => {
      try {
        setLoading(true);
        const data = await unifiedStatusService.getFailures(taskId, limit);
        if (Array.isArray(data)) {
          setFailures(data);
        } else if (Array.isArray(data?.failures)) {
          setFailures(data.failures);
        } else {
          setFailures([]);
        }
      } catch (err) {
        setError(err.message || 'Failed to load validation failures');
      } finally {
        setLoading(false);
      }
    };

    if (taskId) {
      fetchFailures();
    }
  }, [task, taskId, limit]);

  if (loading) return <CircularProgress size={24} />;
  if (error) return <Typography color="error">⚠️ {error}</Typography>;

  if (!failures || failures.length === 0) {
    return (
      <Box
        sx={{
          p: 2,
          backgroundColor: '#e8f5e9',
          border: '1px solid #4caf50',
          borderRadius: 1,
        }}
      >
        <Typography
          color="success.main"
          sx={{ display: 'flex', alignItems: 'center', gap: 1 }}
        >
          ✅ All validation gates passed!
        </Typography>
      </Box>
    );
  }

  const getSeverityColor = (severity) => {
    return severity === 'error'
      ? '#f44336'
      : severity === 'warning'
        ? '#ff9800'
        : '#2196f3';
  };

  const getBackgroundColor = (severity) => {
    return severity === 'error'
      ? '#ffebee'
      : severity === 'warning'
        ? '#fff3e0'
        : '#e3f2fd';
  };

  return (
    <Box sx={{ space: 2 }}>
      <Typography
        variant="subtitle2"
        sx={{ mb: 2, fontWeight: 600, color: '#f44336' }}
      >
        ⚠️ {failures.length} Validation Gate{failures.length !== 1 ? 's' : ''}{' '}
        Failed
      </Typography>

      <Box
        sx={{
          mb: 2,
          p: 2,
          backgroundColor: '#fffde7',
          border: '1px solid #fbc02d',
          borderRadius: 1,
        }}
      >
        <Typography variant="body2" sx={{ color: '#f57f17' }}>
          <strong>Work Preserved:</strong> Generated content (
          {task?.task_metadata?.word_count || task?.word_count || 'unknown'}{' '}
          words) has been saved to the database even though validation failed.
          You can review it below.
        </Typography>
      </Box>

      {failures.map((failure, idx) => (
        <Paper
          key={idx}
          sx={{
            p: 2,
            mb: 2,
            backgroundColor: getBackgroundColor(failure.severity || 'error'),
            borderLeft: `4px solid ${getSeverityColor(failure.severity || 'error')}`,
          }}
        >
          <Typography
            variant="subtitle2"
            sx={{
              mb: 1,
              fontWeight: 600,
              color: getSeverityColor(failure.severity || 'error'),
            }}
          >
            {failure.constraint_name || 'Validation Error'}
          </Typography>

          {failure.message && (
            <Typography variant="body2" sx={{ mb: 1 }}>
              {failure.message}
            </Typography>
          )}

          {typeof failure.details === 'string' ? (
            <Typography
              variant="caption"
              display="block"
              sx={{
                whiteSpace: 'pre-wrap',
                fontFamily: 'monospace',
                p: 1,
                backgroundColor: 'rgba(0,0,0,0.05)',
                borderRadius: 0.5,
              }}
            >
              {failure.details}
            </Typography>
          ) : failure.details && typeof failure.details === 'object' ? (
            <Typography
              variant="caption"
              display="block"
              sx={{
                whiteSpace: 'pre-wrap',
                fontFamily: 'monospace',
                p: 1,
                backgroundColor: 'rgba(0,0,0,0.05)',
                borderRadius: 0.5,
              }}
            >
              {JSON.stringify(failure.details, null, 2)}
            </Typography>
          ) : null}

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
export const StatusDashboardMetrics = () => {
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        setLoading(true);
        const data = await unifiedStatusService.getMetrics();
        setMetrics(data);
      } catch (err) {
        setError(err.message || 'Failed to load metrics');
      } finally {
        setLoading(false);
      }
    };

    fetchMetrics();
  }, []);

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
            {Math.round(metrics.average_processing_time / 60)} seconds
          </Typography>
        </Paper>
      )}

      {metrics.success_rate && (
        <Paper sx={{ p: 2, mt: 2, backgroundColor: '#f5f5f5' }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            Success Rate
          </Typography>
          <Typography variant="body2">
            {(metrics.success_rate * 100).toFixed(1)}%
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
