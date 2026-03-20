import React, { useState } from 'react';
import {
  Box,
  Button,
  CircularProgress,
  Alert,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from '@mui/material';
import {
  Pause as PauseIcon,
  PlayArrow as PlayIcon,
  Stop as StopIcon,
  Delete as DeleteIcon,
} from '@mui/icons-material';
import {
  deleteContentTask,
  pauseTask,
  resumeTask,
  cancelTask,
} from '../../services/taskService';
import useStore from '../../store/useStore';

/**
 * TaskControlPanel Component
 *
 * Provides controls for managing task execution:
 * - Pause: Temporarily stop task execution
 * - Resume: Continue paused task
 * - Cancel: Stop execution and mark as failed
 * - Delete: Remove task permanently
 *
 * Actions are only shown when valid for current task status.
 * Shows confirmation dialogs for destructive actions (Cancel, Delete).
 * Displays loading state and error messages.
 */
export const TaskControlPanel = ({ task, onTaskUpdated }) => {
  const [confirmDialog, setConfirmDialog] = useState({
    open: false,
    action: null,
    title: '',
    description: '',
  });

  // Scope selectors to this task's ID — returns scalar, so Zustand only
  // re-renders when THIS task's loading/error state changes (not all tasks)
  const isLoading = useStore(
    (state) => state.taskActionLoading[task.id] ?? false
  );
  const error = useStore((state) => state.taskActionError[task.id] ?? null);
  const setTaskActionLoading = useStore((state) => state.setTaskActionLoading);
  const setTaskActionError = useStore((state) => state.setTaskActionError);
  const clearTaskAction = useStore((state) => state.clearTaskAction);

  // Determine which actions are valid based on task status
  const canPause = task.status === 'in_progress' || task.status === 'pending';
  const canResume = task.status === 'paused';
  const canCancel = ['pending', 'in_progress', 'paused'].includes(task.status);
  const canDelete =
    ['completed', 'failed', 'cancelled'].includes(task.status) ||
    task.status === 'created';

  const handlePause = async () => {
    setTaskActionLoading(task.id, true);
    setTaskActionError(task.id, null);

    try {
      const updatedTask = await pauseTask(task.id);
      if (onTaskUpdated) {
        onTaskUpdated(updatedTask);
      }
    } catch (err) {
      setTaskActionError(task.id, `Failed to pause task: ${err.message}`);
    } finally {
      clearTaskAction(task.id);
    }
  };

  const handleResume = async () => {
    setTaskActionLoading(task.id, true);
    setTaskActionError(task.id, null);

    try {
      const updatedTask = await resumeTask(task.id);
      if (onTaskUpdated) {
        onTaskUpdated(updatedTask);
      }
    } catch (err) {
      setTaskActionError(task.id, `Failed to resume task: ${err.message}`);
    } finally {
      clearTaskAction(task.id);
    }
  };

  const handleCancel = async () => {
    setTaskActionLoading(task.id, true);
    setTaskActionError(task.id, null);
    setConfirmDialog({ open: false, action: null, title: '', description: '' });

    try {
      const updatedTask = await cancelTask(task.id);
      if (onTaskUpdated) {
        onTaskUpdated(updatedTask);
      }
    } catch (err) {
      setTaskActionError(task.id, `Failed to cancel task: ${err.message}`);
    } finally {
      clearTaskAction(task.id);
    }
  };

  const handleDelete = async () => {
    setTaskActionLoading(task.id, true);
    setTaskActionError(task.id, null);
    setConfirmDialog({ open: false, action: null, title: '', description: '' });

    try {
      await deleteContentTask(task.id);
      if (onTaskUpdated) {
        onTaskUpdated(null); // Signal task deletion
      }
    } catch (err) {
      setTaskActionError(task.id, `Failed to delete task: ${err.message}`);
    } finally {
      clearTaskAction(task.id);
    }
  };

  const openConfirmDialog = (action, title, description) => {
    setConfirmDialog({
      open: true,
      action,
      title,
      description,
    });
  };

  const closeConfirmDialog = () => {
    setConfirmDialog({
      open: false,
      action: null,
      title: '',
      description: '',
    });
  };

  const handleConfirmAction = () => {
    switch (confirmDialog.action) {
      case 'cancel':
        handleCancel();
        break;
      case 'delete':
        handleDelete();
        break;
      default:
        closeConfirmDialog();
    }
  };

  return (
    <Box sx={{ width: '100%' }}>
      {/* Error Message */}
      {error && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          onClose={() => setTaskActionError(task.id, null)}
        >
          {error}
        </Alert>
      )}

      {/* Status Info */}
      <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
        Current Status: <strong>{task.status || 'unknown'}</strong>
      </Typography>

      {/* Action Buttons */}
      <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 1 }}>
        {/* Pause Button */}
        {canPause && (
          <Button
            variant="outlined"
            startIcon={
              isLoading ? <CircularProgress size={20} /> : <PauseIcon />
            }
            onClick={handlePause}
            disabled={isLoading}
            size="small"
          >
            Pause
          </Button>
        )}

        {/* Resume Button */}
        {canResume && (
          <Button
            variant="outlined"
            startIcon={
              isLoading ? <CircularProgress size={20} /> : <PlayIcon />
            }
            onClick={handleResume}
            disabled={isLoading}
            size="small"
            color="success"
          >
            Resume
          </Button>
        )}

        {/* Cancel Button */}
        {canCancel && (
          <Button
            variant="outlined"
            startIcon={
              isLoading ? <CircularProgress size={20} /> : <StopIcon />
            }
            onClick={() =>
              openConfirmDialog(
                'cancel',
                'Cancel Task?',
                'Are you sure you want to cancel this task? This action cannot be undone.'
              )
            }
            disabled={isLoading}
            size="small"
            color="warning"
          >
            Cancel
          </Button>
        )}

        {/* Delete Button */}
        {canDelete && (
          <Button
            variant="outlined"
            startIcon={
              isLoading ? <CircularProgress size={20} /> : <DeleteIcon />
            }
            onClick={() =>
              openConfirmDialog(
                'delete',
                'Delete Task?',
                'Are you sure you want to permanently delete this task? This action cannot be undone.'
              )
            }
            disabled={isLoading}
            size="small"
            color="error"
          >
            Delete
          </Button>
        )}

        {/* No Actions Available */}
        {!canPause && !canResume && !canCancel && !canDelete && (
          <Typography variant="body2" color="textSecondary">
            No actions available for this task status
          </Typography>
        )}
      </Stack>

      {/* Confirmation Dialog */}
      <Dialog
        open={confirmDialog.open}
        onClose={closeConfirmDialog}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>{confirmDialog.title}</DialogTitle>
        <DialogContent>
          <Typography>{confirmDialog.description}</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={closeConfirmDialog} disabled={isLoading}>
            Cancel
          </Button>
          <Button
            onClick={handleConfirmAction}
            variant="contained"
            color={confirmDialog.action === 'delete' ? 'error' : 'warning'}
            disabled={isLoading}
          >
            {isLoading ? <CircularProgress size={24} /> : 'Confirm'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default TaskControlPanel;
