import logger from '@/lib/logger';
import React, { useState, useCallback } from 'react';
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
} from '@mui/material';
import useStore from '../../store/useStore';
import {
  approveTask,
  rejectTask,
  publishTask,
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
  const { selectedTask, setSelectedTask } = useStore();
  const [tabValue, setTabValue] = useState(0);
  const [approvalLoading, setApprovalLoading] = useState(false);
  const [approvalFeedback, setApprovalFeedback] = useState('');
  const [reviewerId, setReviewerId] = useState('oversight_hub_user');
  const [imageSource, setImageSource] = useState('pexels');
  const [selectedImageUrl, setSelectedImageUrl] = useState('');
  const [imageGenerating, setImageGenerating] = useState(false);

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
          alert('✅ Image generated successfully!');
        } else {
          throw new Error('No image URL in response');
        }
      } catch (error) {
        logger.error('❌ Image generation error:', error);
        alert(`❌ Error generating image: ${error.message}`);
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

        alert(
          `✅ Task approved!\n\nStatus: ${result.status}\n\nNow waiting for you to publish when ready.`
        );
        // Reset form state
        setApprovalFeedback('');
        setReviewerId('oversight_hub_user');
        setImageSource('pexels');
        setSelectedImageUrl('');
        setSelectedTask(null);
        onClose();
      } catch (error) {
        logger.error('❌ Approval error:', error);
        alert(`❌ Error approving task: ${error.message}`);
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
      alert(`✅ Task published!\n\nURL: ${publishedUrl}`);
      // Reset form state
      setApprovalFeedback('');
      setReviewerId('oversight_hub_user');
      setImageSource('pexels');
      setSelectedImageUrl('');
      setSelectedTask(null);
      onClose();
    } catch (error) {
      logger.error('❌ Publishing error:', error);
      alert(`❌ Error publishing task: ${error.message}`);
    } finally {
      setApprovalLoading(false);
    }
  }, [selectedTask, setSelectedTask, onClose]);

  const handleRejectTask = useCallback(
    async (feedback) => {
      setApprovalLoading(true);
      try {
        // Use the proper taskService method which handles auth headers correctly
        await rejectTask(
          selectedTask.id,
          feedback || 'Rejected from oversight hub'
        );

        alert('✅ Task rejected successfully');
        // Reset form state
        setApprovalFeedback('');
        setReviewerId('oversight_hub_user');
        setImageSource('pexels');
        setSelectedImageUrl('');
        setSelectedTask(null);
        onClose();
      } catch (error) {
        logger.error('❌ Rejection error:', error);
        alert(`❌ Error rejecting task: ${error.message}`);
      } finally {
        setApprovalLoading(false);
      }
    },
    [selectedTask, setSelectedTask, onClose]
  );

  // Return null after all hooks have been called
  if (!selectedTask) return null;

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
        Task Details:{' '}
        {selectedTask.topic || selectedTask.task_name || 'Untitled'}
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
            <Tab label="Timeline" id="taskdetail-tab-1" />
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
              onFeedbackChange={setApprovalFeedback}
              onReviewerIdChange={setReviewerId}
            />
          </Box>
        </TabPanel>

        {/* Tab 1: Timeline */}
        <TabPanel value={tabValue} index={1}>
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
          <ValidationFailureUI taskId={selectedTask.id} limit={50} />
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
    </Dialog>
  );
};

export default TaskDetailModal;
