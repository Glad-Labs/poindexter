/**
 * LiveTaskMonitor.jsx (Phase 4)
 *
 * Component for monitoring live task execution
 * Displays real-time progress updates for running tasks
 */

import React, { useState, useCallback } from 'react';
import ErrorBoundary from '../ErrorBoundary';
import {
  Card,
  CardContent,
  CardHeader,
  Box,
  Typography,
  LinearProgress,
  Chip,
  Stack,
  Grid,
  Alert,
} from '@mui/material';
import {
  PlayArrow as PlayArrowIcon,
  Pause as PauseIcon,
  Done as DoneIcon,
  Error as ErrorIcon,
  Schedule as ScheduleIcon,
} from '@mui/icons-material';
import { useTaskProgress, useWebSocket } from '../../context/WebSocketContext';
import { notificationService } from '../../services/notificationService';

/**
 * LiveTaskMonitor Component
 * Shows real-time progress for a specific task
 */
export function LiveTaskMonitor({ taskId, taskName = 'Task' }) {
  const [progress, setProgress] = useState({
    taskId,
    status: 'PENDING', // PENDING, RUNNING, COMPLETED, FAILED, PAUSED
    progress: 0,
    currentStep: 'Initializing',
    totalSteps: 0,
    completedSteps: 0,
    message: 'Waiting to start',
    startTime: null,
    elapsedTime: 0,
    estimatedTimeRemaining: null,
    error: null,
  });

  const { isConnected } = useWebSocket();

  // Handle incoming progress updates
  const handleProgressUpdate = useCallback(
    (data) => {
      setProgress((prev) => {
        const updated = {
          ...prev,
          ...data,
        };

        // Detect status change for notifications
        if (data.status && data.status !== prev.status) {
          notifyStatusChange(data.status, taskName);
        }

        return updated;
      });
    },
    [taskName]
  );

  // Subscribe to task progress updates
  useTaskProgress(taskId, handleProgressUpdate);

  const notifyStatusChange = (newStatus, name) => {
    const statusConfig = {
      COMPLETED: {
        type: 'success',
        title: 'Task Completed',
        message: `${name} has completed successfully`,
      },
      FAILED: {
        type: 'error',
        title: 'Task Failed',
        message: `${name} encountered an error and was paused`,
      },
      RUNNING: {
        type: 'info',
        title: 'Task Started',
        message: `${name} is now running`,
      },
      PAUSED: {
        type: 'warning',
        title: 'Task Paused',
        message: `${name} has been paused`,
      },
    };

    const config = statusConfig[newStatus];
    if (config) {
      notificationService.notify({
        ...config,
        duration: 5000,
      });
    }
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'COMPLETED':
        return 'success';
      case 'FAILED':
      case 'ERROR':
        return 'error';
      case 'RUNNING':
        return 'info';
      case 'PAUSED':
        return 'warning';
      default:
        return 'default';
    }
  };

  const getStatusIcon = (status) => {
    switch (status) {
      case 'COMPLETED':
        return <DoneIcon fontSize="small" />;
      case 'FAILED':
      case 'ERROR':
        return <ErrorIcon fontSize="small" />;
      case 'RUNNING':
        return <PlayArrowIcon fontSize="small" />;
      case 'PAUSED':
        return <PauseIcon fontSize="small" />;
      default:
        return <ScheduleIcon fontSize="small" />;
    }
  };

  const formatTime = (seconds) => {
    if (!seconds) return '0s';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    if (hours > 0) return `${hours}h ${minutes}m ${secs}s`;
    if (minutes > 0) return `${minutes}m ${secs}s`;
    return `${secs}s`;
  };

  const progressPercent =
    progress.totalSteps > 0
      ? Math.round((progress.completedSteps / progress.totalSteps) * 100)
      : progress.progress || 0;

  return (
    <Card sx={{ height: '100%' }}>
      <CardHeader
        title={taskName}
        subheader={`Task ID: ${taskId.substring(0, 8)}...`}
        action={
          <Chip
            icon={getStatusIcon(progress.status)}
            label={progress.status}
            color={getStatusColor(progress.status)}
            variant="outlined"
          />
        }
      />
      <CardContent>
        <Stack spacing={2}>
          {/* Connection Status Alert */}
          {!isConnected && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              Real-time updates disconnected. Last update: {progress.message}
            </Alert>
          )}

          {/* Progress Bar */}
          <Box>
            <Box
              sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}
            >
              <Typography variant="body2">Progress</Typography>
              <Typography variant="body2" sx={{ fontWeight: 'bold' }}>
                {progressPercent}%
              </Typography>
            </Box>
            <LinearProgress
              variant="determinate"
              value={progressPercent}
              sx={{ height: 8, borderRadius: 4 }}
            />
          </Box>

          {/* Current Step */}
          <Box>
            <Typography
              variant="subtitle2"
              sx={{ fontWeight: 'bold', mb: 0.5 }}
            >
              Current Step
            </Typography>
            <Typography variant="body2" color="textSecondary">
              {progress.currentStep}
            </Typography>
            {progress.totalSteps > 0 && (
              <Typography variant="caption" color="textSecondary">
                Step {progress.completedSteps + 1} of {progress.totalSteps}
              </Typography>
            )}
          </Box>

          {/* Status Message */}
          {progress.message && (
            <Box>
              <Typography variant="body2" sx={{ wordBreak: 'break-word' }}>
                {progress.message}
              </Typography>
            </Box>
          )}

          {/* Error Message */}
          {progress.error && (
            <Alert severity="error">
              <Typography variant="body2">{progress.error}</Typography>
            </Alert>
          )}

          {/* Time Information */}
          {progress.elapsedTime > 0 && (
            <Grid container spacing={2} sx={{ mt: 1 }}>
              <Grid item xs={6}>
                <Typography variant="caption" color="textSecondary">
                  Elapsed
                </Typography>
                <Typography variant="body2">
                  {formatTime(progress.elapsedTime)}
                </Typography>
              </Grid>
              {progress.estimatedTimeRemaining && (
                <Grid item xs={6}>
                  <Typography variant="caption" color="textSecondary">
                    Estimated Remaining
                  </Typography>
                  <Typography variant="body2">
                    {formatTime(progress.estimatedTimeRemaining)}
                  </Typography>
                </Grid>
              )}
            </Grid>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}

/**
 * LiveTaskMonitorWithBoundary — default export wraps the component in its own
 * ErrorBoundary so a crash in the monitor card does not propagate up to the
 * page-level boundary (which would tear down the entire dashboard).
 */
function LiveTaskMonitorWithBoundary(props) {
  return (
    <ErrorBoundary name="LiveTaskMonitor">
      <LiveTaskMonitor {...props} />
    </ErrorBoundary>
  );
}

export default LiveTaskMonitorWithBoundary;
