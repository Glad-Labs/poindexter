import logger from '@/lib/logger';
import React, { useState, useCallback, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Tabs,
  Tab,
  Box,
  Button,
  Divider,
  Snackbar,
  Alert,
} from '@mui/material';
import { useShallow } from 'zustand/react/shallow';
import useStore from '../../store/useStore';
import {
  approveTask,
  rejectTask,
  publishTask,
  getContentTask,
  updateTask,
} from '../../services/taskService';
import { generateTaskImage } from '../../services/cofounderAgentClient';
import {
  StatusAuditTrail,
  StatusTimeline,
  ValidationFailureUI,
  StatusDashboardMetrics,
} from './StatusComponents.jsx';
import TaskContentPreview from './TaskContentPreview';
import TaskImageManager from './TaskImageManager';
import TaskApprovalForm from './TaskApprovalForm';
import TaskMetadataDisplay from './TaskMetadataDisplay';
import TaskControlPanel from './TaskControlPanel';

function TabPanel(props) {
  const { children, value, index, ...other } = props;
  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`taskdetail-tabpanel-${index}`}
      aria-labelledby={`taskdetail-tab-${index}`}
      {...other}
    >
      {value === index && <Box sx={{ p: 2 }}>{children}</Box>}
    </div>
  );
}

const TaskDetailModal = ({ onClose, onUpdate }) => {
  const { selectedTask, setSelectedTask } = useStore(
    useShallow((s) => ({
      selectedTask: s.selectedTask,
      setSelectedTask: s.setSelectedTask,
    }))
  );
  const [tabValue, setTabValue] = useState(0);
  const [approvalLoading, setApprovalLoading] = useState(false);
  const [approvalFeedback, setApprovalFeedback] = useState('');
  const [reviewerId, setReviewerId] = useState('oversight_hub_user');
  const [imageSource, setImageSource] = useState('pexels');
  const [selectedImageUrl, setSelectedImageUrl] = useState('');
  const [imageGenerating, setImageGenerating] = useState(false);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success',
  });

  // Fetch fresh task data when modal opens to avoid stale status
  useEffect(() => {
    if (selectedTask?.id) {
      getContentTask(selectedTask.id)
        .then((freshTask) => {
          if (freshTask && freshTask.status !== selectedTask.status) {
            setSelectedTask({ ...selectedTask, ...freshTask });
          }
        })
        .catch(() => {
          // Silently fail — modal will show stale data which is still usable
        });
    }
  }, [selectedTask?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const showSuccess = (message) =>
    setSnackbar({ open: true, message, severity: 'success' });
  const showError = (message) =>
    setSnackbar({ open: true, message, severity: 'error' });
  const showInfo = (message) =>
    setSnackbar({ open: true, message, severity: 'info' });
  const handleSnackbarClose = () =>
    setSnackbar((prev) => ({ ...prev, open: false }));

  const handleTabChange = (event, newValue) => {
    setTabValue(newValue);
  };

  // Handle task update from content preview
  const handleTaskUpdate = useCallback(
    (updatedTask) => {
      setSelectedTask(updatedTask);
      if (onUpdate) onUpdate(updatedTask);
    },
    [setSelectedTask, onUpdate]
  );

  // Handle image generation
  const handleGenerateImage = useCallback(
    async (source) => {
      setImageGenerating(true);
      try {
        const result = await generateTaskImage(selectedTask.id, {
          source: source || imageSource,
          topic: selectedTask.topic,
          content_summary:
            selectedTask.task_metadata?.content?.substring(0, 500) || '',
        });

        if (result.image_url) {
          setSelectedImageUrl(result.image_url);
          showSuccess('Image generated successfully');
        } else {
          throw new Error('No image URL in response');
        }
      } catch (error) {
        logger.error('❌ Image generation error:', error);
        showError(`Error generating image: ${error.message}`);
      } finally {
        setImageGenerating(false);
      }
    },
    [selectedTask, imageSource]
  );

  // Handle task approval (WITHOUT publishing)
  const handleApproveTask = useCallback(
    async (_updatedTask) => {
      setApprovalLoading(true);
      try {
        // Use the proper taskService method which handles auth headers correctly
        const result = await approveTask(
          selectedTask.id,
          approvalFeedback || 'Approved from oversight hub'
        );

        showSuccess(
          `Task approved (${result.status}). Ready to publish when you are.`
        );
        // Notify parent for optimistic update before closing
        if (onUpdate) onUpdate(selectedTask.id, 'approved');
        setApprovalFeedback('');
        setReviewerId('oversight_hub_user');
        setImageSource('pexels');
        setSelectedImageUrl('');
        setSelectedTask(null);
        onClose();
      } catch (error) {
        logger.error('❌ Approval error:', error);
        showError(`Error approving task: ${error.message}`);
      } finally {
        setApprovalLoading(false);
      }
    },
    [selectedTask, approvalFeedback, setSelectedTask, onClose]
  );

  // Handle task publishing (separate step after approval)
  const handlePublishTask = useCallback(async () => {
    setApprovalLoading(true);
    try {
      const result = await publishTask(selectedTask.id);

      const publishedUrl =
        result.published_url ||
        `${window.location.origin}/posts/${result.post_slug || 'published'}`;
      showSuccess(`Task published! URL: ${publishedUrl}`);
      // Notify parent for optimistic update before closing
      if (onUpdate) onUpdate(selectedTask.id, 'published');
      setApprovalFeedback('');
      setReviewerId('oversight_hub_user');
      setImageSource('pexels');
      setSelectedImageUrl('');
      setSelectedTask(null);
      onClose();
    } catch (error) {
      logger.error('❌ Publishing error:', error);
      showError(`Error publishing task: ${error.message}`);
    } finally {
      setApprovalLoading(false);
    }
  }, [selectedTask, setSelectedTask, onClose]);

  const handleRejectTask = useCallback(
    async (feedback) => {
      setApprovalLoading(true);
      try {
        // Guard against stale modal state by re-syncing current status first.
        const latestTask = await getContentTask(selectedTask.id);
        const latestStatus = latestTask?.status?.toLowerCase();

        if (latestStatus && latestStatus !== 'awaiting_approval') {
          handleTaskUpdate(latestTask);
          showInfo(
            `Task is already '${latestTask.status}'. Reject is only available when status is 'awaiting_approval'.`
          );
          return;
        }

        // Use the proper taskService method which handles auth headers correctly
        const rejectedTask = await rejectTask(
          selectedTask.id,
          feedback || 'Rejected from oversight hub'
        );

        // Sync UI state immediately so status changes are visible without waiting for polling.
        if (rejectedTask && typeof rejectedTask === 'object') {
          handleTaskUpdate(rejectedTask);
        }

        showSuccess('Task rejected successfully');
        // Reset form state
        setApprovalFeedback('');
        setReviewerId('oversight_hub_user');
        setImageSource('pexels');
        setSelectedImageUrl('');
        setSelectedTask(null);
        onClose();
      } catch (error) {
        // If backend rejects due to stale status, re-fetch and sync task state immediately.
        if (error?.status === 400) {
          try {
            const latestTask = await getContentTask(selectedTask.id);
            if (latestTask) {
              handleTaskUpdate(latestTask);
            }
          } catch {
            // Ignore secondary refresh errors and surface original rejection error.
          }
        }
        logger.error('❌ Rejection error:', error);
        showError(`Error rejecting task: ${error.message}`);
      } finally {
        setApprovalLoading(false);
      }
    },
    [selectedTask, setSelectedTask, onClose, handleTaskUpdate]
  );

  // Handle re-review: reset rejected task back to pending for another review cycle (#197)
  const handleReReview = useCallback(async () => {
    setApprovalLoading(true);
    try {
      await updateTask(selectedTask.id, { status: 'pending' });
      const updatedTask = await getContentTask(selectedTask.id);
      if (updatedTask) {
        handleTaskUpdate(updatedTask);
      }
      showSuccess('Task sent back for re-review. Status reset to pending.');
      setApprovalFeedback('');
      setSelectedTask(null);
      onClose();
    } catch (error) {
      logger.error('Re-review error:', error);
      showError(`Error resetting task for re-review: ${error.message}`);
    } finally {
      setApprovalLoading(false);
    }
  }, [selectedTask, setSelectedTask, onClose, handleTaskUpdate]);

  // Return null after all hooks have been called
  if (!selectedTask) return null;

  const getRetryCount = (task) => {
    const metadata = task?.task_metadata;
    if (!metadata) return 0;

    if (typeof metadata === 'object' && metadata !== null) {
      return Number(metadata.retry_count || 0);
    }

    if (typeof metadata === 'string') {
      try {
        const parsed = JSON.parse(metadata);
        return Number(parsed?.retry_count || 0);
      } catch {
        return 0;
      }
    }

    return 0;
  };

  const retryCount = getRetryCount(selectedTask);

  // Extract task metadata for progress display
  const getTaskMetadata = () => {
    const metadata = selectedTask?.task_metadata;
    if (!metadata) return {};
    if (typeof metadata === 'object' && metadata !== null) return metadata;
    if (typeof metadata === 'string') {
      try {
        const parsed = JSON.parse(metadata);
        return parsed && typeof parsed === 'object' ? parsed : {};
      } catch {
        return {};
      }
    }
    return {};
  };

  const taskMetadata = getTaskMetadata();
  const taskStage = taskMetadata.stage || taskMetadata.status || '';
  const taskMessage = taskMetadata.message || '';
  const taskPercentage =
    typeof taskMetadata.percentage === 'number'
      ? taskMetadata.percentage
      : selectedTask.progress || 0;
  const isActiveTask = ['pending', 'in_progress', 'running'].includes(
    selectedTask.status?.toLowerCase()
  );

  return (
    <Dialog
      open={!!selectedTask}
      onClose={onClose}
      maxWidth="lg"
      fullWidth
      slotProps={{
        backdrop: {
          sx: {
            backgroundColor: 'rgba(0, 0, 0, 0.7)',
          },
        },
      }}
      PaperProps={{
        sx: {
          maxHeight: '90vh',
          backgroundColor: '#1a1a1a',
          color: '#e0e0e0',
          backgroundImage: 'linear-gradient(135deg, #1a1a1a 0%, #242424 100%)',
        },
      }}
    >
      <DialogTitle
        sx={{
          color: '#00d9ff',
          borderBottom: '1px solid #333',
          fontWeight: 'bold',
        }}
      >
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            gap: 1.5,
          }}
        >
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              flexWrap: 'wrap',
            }}
          >
            <span>
              Task Details:{' '}
              {selectedTask.topic || selectedTask.task_name || 'Untitled'}
            </span>
            {retryCount > 0 && (
              <Box
                component="span"
                sx={{
                  display: 'inline-block',
                  px: 1,
                  py: 0.3,
                  borderRadius: '10px',
                  border: '1px solid #00d9ff',
                  color: '#00d9ff',
                  backgroundColor: 'rgba(0, 217, 255, 0.12)',
                  fontSize: '0.72rem',
                  fontWeight: 700,
                  letterSpacing: '0.2px',
                }}
                title={`Retry attempts: ${retryCount}`}
              >
                Retry #{retryCount}
              </Box>
            )}
          </Box>

          {/* Progress Bar Section */}
          {isActiveTask && taskPercentage > 0 && (
            <Box sx={{ width: '100%' }}>
              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  mb: 0.5,
                }}
              >
                <Box
                  component="span"
                  aria-live="polite"
                  sx={{
                    fontSize: '0.8rem',
                    color: '#aaa',
                    fontWeight: 500,
                  }}
                >
                  {taskMessage || taskStage || 'Processing...'}
                </Box>
                <Box
                  component="span"
                  sx={{
                    fontSize: '0.8rem',
                    color: '#00d9ff',
                    fontWeight: 700,
                  }}
                >
                  {taskPercentage}%
                </Box>
              </Box>
              <Box
                role="progressbar"
                aria-valuenow={taskPercentage}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={`Task progress: ${taskMessage || taskStage || 'Processing'}`}
                sx={{
                  width: '100%',
                  height: '6px',
                  backgroundColor: '#2a2a2a',
                  borderRadius: '3px',
                  overflow: 'hidden',
                }}
              >
                <Box
                  sx={{
                    width: `${taskPercentage}%`,
                    height: '100%',
                    backgroundColor: '#00d9ff',
                    transition: 'width 0.3s ease-in-out',
                    borderRadius: '3px',
                    boxShadow: '0 0 10px rgba(0, 217, 255, 0.5)',
                  }}
                />
              </Box>
            </Box>
          )}
        </Box>
      </DialogTitle>

      <DialogContent
        dividers
        sx={{ backgroundColor: '#0f0f0f', borderColor: '#333' }}
      >
        <Box sx={{ borderBottom: 1, borderColor: '#333', mb: 2 }}>
          <Tabs
            value={tabValue}
            onChange={handleTabChange}
            aria-label="task details tabs"
            sx={{
              '& .MuiTab-root': {
                color: '#999',
                '&.Mui-selected': {
                  color: '#00d9ff',
                },
              },
              '& .MuiTabs-indicator': {
                backgroundColor: '#00d9ff',
              },
            }}
          >
            <Tab label="Content & Approval" id="taskdetail-tab-0" />
            <Tab
              label={
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  Timeline
                  {isActiveTask && (
                    <Box
                      component="span"
                      sx={{
                        width: 6,
                        height: 6,
                        borderRadius: '50%',
                        backgroundColor: '#00d9ff',
                        animation: 'pulse 2s infinite',
                        '@keyframes pulse': {
                          '0%, 100%': { opacity: 1 },
                          '50%': { opacity: 0.3 },
                        },
                      }}
                    />
                  )}
                </Box>
              }
              id="taskdetail-tab-1"
            />
            <Tab label="History" id="taskdetail-tab-2" />
            <Tab label="Validation" id="taskdetail-tab-3" />
            <Tab label="Metrics" id="taskdetail-tab-4" />
          </Tabs>
        </Box>

        {/* Tab 0: Content & Approval */}
        <TabPanel value={tabValue} index={0}>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {/* Task Control Panel (Phase 1.2) - Pause/Resume/Cancel/Delete */}
            <Box
              sx={{
                p: 2,
                backgroundColor: '#1a1a1a',
                borderRadius: 1,
                border: '1px solid #333',
              }}
            >
              <TaskControlPanel
                task={selectedTask}
                onTaskUpdated={(updatedTask) => {
                  if (updatedTask === null) {
                    // Task was deleted
                    setSelectedTask(null);
                    onClose();
                  } else {
                    handleTaskUpdate(updatedTask);
                  }
                }}
              />
            </Box>

            <Divider sx={{ borderColor: '#333' }} />

            {/* Content Preview Component */}
            <TaskContentPreview
              task={selectedTask}
              onTaskUpdate={handleTaskUpdate}
            />

            {/* Image Manager Component */}
            <TaskImageManager
              task={selectedTask}
              imageSource={imageSource}
              selectedImageUrl={selectedImageUrl}
              imageGenerating={imageGenerating}
              onImageSourceChange={setImageSource}
              onImageUrlChange={setSelectedImageUrl}
              onGenerateImage={handleGenerateImage}
            />

            {/* Metadata Display Component */}
            <TaskMetadataDisplay task={selectedTask} />

            {/* Approval Form Component */}
            <TaskApprovalForm
              task={selectedTask}
              approvalFeedback={approvalFeedback}
              reviewerId={reviewerId}
              approvalLoading={approvalLoading}
              publishLoading={approvalLoading}
              onApprove={() =>
                handleApproveTask({
                  ...selectedTask,
                  featured_image_url:
                    selectedImageUrl ||
                    selectedTask.task_metadata?.featured_image_url,
                })
              }
              onPublish={handlePublishTask}
              onReject={() => handleRejectTask(approvalFeedback)}
              onReReview={handleReReview}
              onFeedbackChange={setApprovalFeedback}
              onReviewerIdChange={setReviewerId}
            />
          </Box>
        </TabPanel>

        {/* Tab 1: Timeline */}
        <TabPanel value={tabValue} index={1}>
          {/* Current Execution Status */}
          {isActiveTask && (taskStage || taskMessage) && (
            <Box
              sx={{
                mb: 3,
                p: 2,
                backgroundColor: '#1a1a1a',
                borderRadius: 1,
                border: '1px solid #00d9ff',
              }}
            >
              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  mb: 1,
                }}
              >
                <Box
                  component="h4"
                  sx={{
                    margin: 0,
                    color: '#00d9ff',
                    fontSize: '0.9rem',
                    fontWeight: 600,
                  }}
                >
                  🔄 Current Execution Stage
                </Box>
                <Box
                  component="span"
                  sx={{
                    px: 1.5,
                    py: 0.5,
                    borderRadius: '12px',
                    backgroundColor: 'rgba(0, 217, 255, 0.15)',
                    color: '#00d9ff',
                    fontSize: '0.75rem',
                    fontWeight: 700,
                  }}
                >
                  {taskPercentage}% Complete
                </Box>
              </Box>
              <Box
                sx={{
                  color: '#e0e0e0',
                  fontSize: '0.85rem',
                  fontStyle: 'italic',
                  mt: 1,
                }}
              >
                {taskMessage || taskStage || 'Processing task...'}
              </Box>
            </Box>
          )}

          <StatusTimeline
            currentStatus={selectedTask.status}
            statusHistory={selectedTask.statusHistory || []}
            compact={false}
          />
        </TabPanel>

        {/* Tab 2: History */}
        <TabPanel value={tabValue} index={2}>
          <StatusAuditTrail taskId={selectedTask.id} limit={100} />
        </TabPanel>

        {/* Tab 3: Validation Failures */}
        <TabPanel value={tabValue} index={3}>
          <ValidationFailureUI
            task={selectedTask}
            taskId={selectedTask.id}
            limit={50}
          />
        </TabPanel>

        {/* Tab 4: Metrics */}
        <TabPanel value={tabValue} index={4}>
          <StatusDashboardMetrics
            statusHistory={selectedTask.statusHistory || [selectedTask.status]}
            compact={false}
          />
        </TabPanel>
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose} variant="contained">
          Close
        </Button>
      </DialogActions>

      {/* Toast notifications (replaces native alert() calls) */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={handleSnackbarClose}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          role="alert"
          aria-live={snackbar.severity === 'error' ? 'assertive' : 'polite'}
          onClose={handleSnackbarClose}
          severity={snackbar.severity}
          variant="filled"
          sx={{ width: '100%' }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Dialog>
  );
};

export default TaskDetailModal;
