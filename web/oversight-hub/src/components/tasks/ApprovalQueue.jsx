/**
 * ApprovalQueue Component
 * 
 * Displays tasks awaiting human approval before publishing.
 * Allows users to review, approve, or reject content with feedback.
 * 
 * Features:
 * - List pending approval tasks with pagination
 * - Task preview with content & featured image
 * - Approve/Reject buttons with forms
 * - Inline feedback/notes
 * - Sort & filter by task type
 * - Real-time status updates
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  Box,
  Button,
  Card,
  CardContent,
  CardActions,
  CardMedia,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Grid,
  MenuItem,
  Pagination,
  Select,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
  Alert,
  Stack,
  Rating,
} from '@mui/material';
import { CheckCircle, Close, Info, Edit, Visible } from '@mui/icons-material';
import { cofounderAgentClient } from '../../services/cofounderAgentClient';

/**
 * ApprovalQueue Component
 * Main component for displaying and managing approval tasks
 */
const ApprovalQueue = () => {
  // ============================================================================
  // STATE MANAGEMENT
  // ============================================================================

  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  // Pagination
  const [page, setPage] = useState(1);
  const [limit] = useState(10);
  const ITEMS_PER_PAGE = limit;

  // Filtering & Sorting
  const [sortBy, setSortBy] = useState('created_at'); // created_at, quality_score, topic
  const [sortOrder, setSortOrder] = useState('desc'); // asc, desc
  const [taskTypeFilter, setTaskTypeFilter] = useState(''); // Filter by task type

  // Dialog states
  const [previewOpen, setPreviewOpen] = useState(false);
  const [selectedTask, setSelectedTask] = useState(null);

  const [approveDialogOpen, setApproveDialogOpen] = useState(false);
  const [approveFeedback, setApproveFeedback] = useState('');

  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('Content quality');
  const [rejectFeedback, setRejectFeedback] = useState('');
  const [allowRevisions, setAllowRevisions] = useState(true);

  const [processingTaskId, setProcessingTaskId] = useState(null);

  // ============================================================================
  // FETCH & EFFECTS
  // ============================================================================

  /**
   * Fetch pending approval tasks from backend
   */
  const fetchPendingTasks = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const offset = (page - 1) * ITEMS_PER_PAGE;
      
      const params = new URLSearchParams({
        limit: ITEMS_PER_PAGE,
        offset: offset,
        sort_by: sortBy,
        sort_order: sortOrder,
      });

      if (taskTypeFilter) {
        params.append('task_type', taskTypeFilter);
      }

      console.log(`📋 [APPROVAL_QUEUE] Fetching pending approvals: page=${page}, sort=${sortBy}(${sortOrder})`);

      const response = await fetch(
        `http://localhost:8000/api/tasks/pending-approval?${params}`,
        {
          method: 'GET',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${localStorage.getItem('token')}`,
          },
        }
      );

      if (!response.ok) {
        if (response.status === 401) {
          throw new Error('Unauthorized - please log in');
        }
        throw new Error(`Failed to fetch pending approvals: ${response.statusText}`);
      }

      const data = await response.json();
      console.log(`✅ [APPROVAL_QUEUE] Fetched ${data.count} tasks (total: ${data.total})`);

      setTasks(data.tasks || []);
    } catch (err) {
      console.error('❌ [APPROVAL_QUEUE] Failed to fetch:', err);
      setError(err.message);
      setTasks([]);
    } finally {
      setLoading(false);
    }
  }, [page, sortBy, sortOrder, taskTypeFilter, ITEMS_PER_PAGE]);

  /**
   * Fetch tasks on mount and when filters change
   */
  useEffect(() => {
    fetchPendingTasks();
  }, [fetchPendingTasks]);

  // ============================================================================
  // APPROVAL HANDLERS
  // ============================================================================

  /**
   * Handle approve button click
   */
  const handleApproveClick = (task) => {
    setSelectedTask(task);
    setApproveFeedback('');
    setApproveDialogOpen(true);
  };

  /**
   * Submit approval
   */
  const handleApprovalSubmit = async () => {
    if (!selectedTask) return;

    setProcessingTaskId(selectedTask.task_id);
    setError(null);

    try {
      console.log(`✅ [APPROVAL] Approving task ${selectedTask.task_id}`);

      const response = await fetch(
        `http://localhost:8000/api/tasks/${selectedTask.task_id}/approve`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${localStorage.getItem('token')}`,
          },
          body: JSON.stringify({
            approved: true,
            feedback: approveFeedback || undefined,
            reviewer_notes: approveFeedback || undefined,
          }),
        }
      );

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail?.message || 'Failed to approve task');
      }

      const data = await response.json();
      console.log(`✅ [APPROVAL] Task approved:`, data);

      setSuccess(`Task approved: ${selectedTask.task_name}`);
      setApproveDialogOpen(false);

      // Refresh list
      setTimeout(() => {
        fetchPendingTasks();
        setSuccess(null);
      }, 1500);
    } catch (err) {
      console.error('❌ [APPROVAL] Failed to approve:', err);
      setError(err.message);
    } finally {
      setProcessingTaskId(null);
    }
  };

  /**
   * Handle reject button click
   */
  const handleRejectClick = (task) => {
    setSelectedTask(task);
    setRejectReason('Content quality');
    setRejectFeedback('');
    setAllowRevisions(true);
    setRejectDialogOpen(true);
  };

  /**
   * Submit rejection
   */
  const handleRejectionSubmit = async () => {
    if (!selectedTask || !rejectFeedback) {
      setError('Please provide feedback');
      return;
    }

    setProcessingTaskId(selectedTask.task_id);
    setError(null);

    try {
      console.log(`❌ [REJECTION] Rejecting task ${selectedTask.task_id}`);

      const response = await fetch(
        `http://localhost:8000/api/tasks/${selectedTask.task_id}/reject`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${localStorage.getItem('token')}`,
          },
          body: JSON.stringify({
            reason: rejectReason,
            feedback: rejectFeedback,
            allow_revisions: allowRevisions,
          }),
        }
      );

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail?.message || 'Failed to reject task');
      }

      const data = await response.json();
      console.log(`✅ [REJECTION] Task rejected:`, data);

      setSuccess(`Task rejected: ${selectedTask.task_name}`);
      setRejectDialogOpen(false);

      // Refresh list
      setTimeout(() => {
        fetchPendingTasks();
        setSuccess(null);
      }, 1500);
    } catch (err) {
      console.error('❌ [REJECTION] Failed to reject:', err);
      setError(err.message);
    } finally {
      setProcessingTaskId(null);
    }
  };

  // ============================================================================
  // HANDLERS
  // ============================================================================

  const handlePageChange = (event, newPage) => {
    setPage(newPage);
  };

  const handleSortChange = (e) => {
    setSortBy(e.target.value);
    setPage(1); // Reset to first page
  };

  const handleFilterChange = (e) => {
    setTaskTypeFilter(e.target.value);
    setPage(1); // Reset to first page
  };

  const handleTaskTypeFilterChange = (event) => {
    const value = event.target.value;
    setTaskTypeFilter(value === 'all' ? '' : value);
    setPage(1);
  };

  const handlePreviewOpen = (task) => {
    setSelectedTask(task);
    setPreviewOpen(true);
  };

  const handlePreviewClose = () => {
    setPreviewOpen(false);
    setSelectedTask(null);
  };

  // ============================================================================
  // RENDER HELPERS
  // ============================================================================

  /**
   * Get task type badge color
   */
  const getTaskTypeColor = (taskType) => {
    const colors = {
      blog_post: 'primary',
      email: 'info',
      newsletter: 'success',
      social_media: 'warning',
      market_research: 'secondary',
      financial_analysis: 'error',
      business_analytics: 'default',
      data_retrieval: 'default',
    };
    return colors[taskType] || 'default';
  };

  /**
   * Format date to readable string
   */
  const formatDate = (dateString) => {
    if (!dateString) return 'N/A';
    try {
      return new Date(dateString).toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch (e) {
      return dateString;
    }
  };

  /**
   * Truncate content for preview
   */
  const getContentPreview = (content) => {
    if (!content) return 'No content';
    return content.substring(0, 150) + (content.length > 150 ? '...' : '');
  };

  // ============================================================================
  // RENDER COMPONENTS
  // ============================================================================

  /**
   * Task Card Component (alternative to table)
   */
  const TaskCard = ({ task }) => (
    <Card sx={{ mb: 2 }}>
      <CardContent>
        <Stack spacing={2}>
          {/* Header with title and badges */}
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
            <Box>
              <Typography variant="h6" sx={{ mb: 1 }}>
                {task.task_name || 'Untitled Task'}
              </Typography>
              <Typography variant="body2" color="textSecondary" sx={{ mb: 1 }}>
                Topic: {task.topic || 'N/A'} | Created: {formatDate(task.created_at)}
              </Typography>
              <Box sx={{ display: 'flex', gap: 1 }}>
                <Chip
                  label={task.task_type || 'unknown'}
                  color={getTaskTypeColor(task.task_type)}
                  size="small"
                  variant="outlined"
                />
                {task.quality_score && (
                  <Chip
                    label={`Quality: ${task.quality_score.toFixed(1)}/10`}
                    size="small"
                    color={task.quality_score >= 7.5 ? 'success' : task.quality_score >= 5 ? 'warning' : 'error'}
                  />
                )}
              </Box>
            </Box>
          </Box>

          {/* Featured Image */}
          {task.featured_image_url && (
            <Box
              sx={{
                width: '100%',
                height: 200,
                backgroundImage: `url(${task.featured_image_url})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center',
                borderRadius: 1,
              }}
            />
          )}

          {/* Content Preview */}
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Content Preview:
            </Typography>
            <Typography
              variant="body2"
              sx={{
                bgcolor: '#f5f5f5',
                p: 1.5,
                borderRadius: 1,
                whiteSpace: 'pre-wrap',
                maxHeight: 150,
                overflow: 'auto',
              }}
            >
              {task.content_preview || getContentPreview(task.metadata?.content || '')}
            </Typography>
          </Box>
        </Stack>
      </CardContent>

      {/* Action Buttons */}
      <CardActions sx={{ justifyContent: 'space-between' }}>
        <Box>
          <Button
            size="small"
            variant="outlined"
            startIcon={<Visible />}
            onClick={() => handlePreviewOpen(task)}
            sx={{ mr: 1 }}
          >
            Preview
          </Button>
        </Box>
        <Box>
          <Button
            size="small"
            variant="contained"
            color="success"
            startIcon={<CheckCircle />}
            onClick={() => handleApproveClick(task)}
            disabled={processingTaskId === task.task_id}
            sx={{ mr: 1 }}
          >
            {processingTaskId === task.task_id ? 'Processing...' : 'Approve'}
          </Button>
          <Button
            size="small"
            variant="contained"
            color="error"
            startIcon={<Close />}
            onClick={() => handleRejectClick(task)}
            disabled={processingTaskId === task.task_id}
          >
            {processingTaskId === task.task_id ? 'Processing...' : 'Reject'}
          </Button>
        </Box>
      </CardActions>
    </Card>
  );

  // ============================================================================
  // RENDER MAIN COMPONENT
  // ============================================================================

  return (
    <Box sx={{ p: 3 }}>
      {/* Header */}
      <Box sx={{ mb: 3 }}>
        <Typography variant="h4" sx={{ mb: 2 }}>
          📋 Approval Queue
        </Typography>
        <Typography variant="body1" color="textSecondary">
          Review and approve content before it's published.
        </Typography>
      </Box>

      {/* Alerts */}
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess(null)}>{success}</Alert>}

      {/* Filters & Controls */}
      <Stack direction="row" spacing={2} sx={{ mb: 3 }}>
        <Select
          value={taskTypeFilter}
          onChange={handleTaskTypeFilterChange}
          size="small"
          sx={{ minWidth: 200 }}
          displayEmpty
        >
          <MenuItem value="">All Task Types</MenuItem>
          <MenuItem value="blog_post">Blog Posts</MenuItem>
          <MenuItem value="email">Emails</MenuItem>
          <MenuItem value="newsletter">Newsletters</MenuItem>
          <MenuItem value="social_media">Social Media</MenuItem>
          <MenuItem value="market_research">Market Research</MenuItem>
          <MenuItem value="financial_analysis">Financial Analysis</MenuItem>
        </Select>

        <Select
          value={sortBy}
          onChange={handleSortChange}
          size="small"
          sx={{ minWidth: 200 }}
        >
          <MenuItem value="created_at">Sort: Newest First</MenuItem>
          <MenuItem value="quality_score">Sort: Highest Quality</MenuItem>
          <MenuItem value="topic">Sort: Topic (A-Z)</MenuItem>
        </Select>

        <Button variant="outlined" onClick={fetchPendingTasks} disabled={loading}>
          {loading ? <CircularProgress size={24} /> : 'Refresh'}
        </Button>
      </Stack>

      {/* Loading State */}
      {loading && (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
          <CircularProgress />
        </Box>
      )}

      {/* Empty State */}
      {!loading && tasks.length === 0 && (
        <Alert severity="info" icon={<Info />}>
          No tasks awaiting approval. All caught up! 🎉
        </Alert>
      )}

      {/* Task List */}
      {!loading && tasks.length > 0 && (
        <>
          {/* Summary */}
          <Alert severity="info" sx={{ mb: 2 }}>
            Showing {tasks.length} of {tasks.length} pending approvals
          </Alert>

          {/* Task Cards */}
          <Box sx={{ mb: 3 }}>
            {tasks.map((task) => (
              <TaskCard key={task.task_id} task={task} />
            ))}
          </Box>

          {/* Pagination */}
          <Box sx={{ display: 'flex', justifyContent: 'center' }}>
            <Pagination
              count={Math.ceil(tasks.length / ITEMS_PER_PAGE) || 1}
              page={page}
              onChange={handlePageChange}
              color="primary"
            />
          </Box>
        </>
      )}

      {/* ===== DIALOGS ===== */}

      {/* Approve Dialog */}
      <Dialog open={approveDialogOpen} onClose={() => setApproveDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Approve Task</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 2 }}>
            <Typography variant="body2" color="textSecondary">
              Are you sure you want to approve <strong>{selectedTask?.task_name}</strong> for publishing?
            </Typography>
            <TextField
              label="Approval Notes (Optional)"
              placeholder="Add any notes or feedback..."
              multiline
              rows={3}
              value={approveFeedback}
              onChange={(e) => setApproveFeedback(e.target.value)}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setApproveDialogOpen(false)}>Cancel</Button>
          <Button
            onClick={handleApprovalSubmit}
            variant="contained"
            color="success"
            disabled={processingTaskId === selectedTask?.task_id}
          >
            {processingTaskId === selectedTask?.task_id ? 'Approving...' : 'Approve'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Reject Dialog */}
      <Dialog open={rejectDialogOpen} onClose={() => setRejectDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Reject Task</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 2 }}>
            <Typography variant="body2" color="textSecondary">
              Rejecting <strong>{selectedTask?.task_name}</strong>
            </Typography>

            <Select
              label="Reason"
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              fullWidth
            >
              <MenuItem value="Content quality">Content Quality</MenuItem>
              <MenuItem value="Factual errors">Factual Errors</MenuItem>
              <MenuItem value="Tone mismatch">Tone Mismatch</MenuItem>
              <MenuItem value="Style issues">Style Issues</MenuItem>
              <MenuItem value="Other">Other</MenuItem>
            </Select>

            <TextField
              label="Feedback"
              placeholder="Explain what needs to be fixed..."
              multiline
              rows={4}
              value={rejectFeedback}
              onChange={(e) => setRejectFeedback(e.target.value)}
              fullWidth
              error={!rejectFeedback && rejectDialogOpen}
              helperText={!rejectFeedback && rejectDialogOpen ? 'Feedback is required' : ''}
            />

            <FormControlLabel
              control={
                <input
                  type="checkbox"
                  checked={allowRevisions}
                  onChange={(e) => setAllowRevisions(e.target.checked)}
                />
              }
              label="Allow the team to submit revisions"
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRejectDialogOpen(false)}>Cancel</Button>
          <Button
            onClick={handleRejectionSubmit}
            variant="contained"
            color="error"
            disabled={processingTaskId === selectedTask?.task_id || !rejectFeedback}
          >
            {processingTaskId === selectedTask?.task_id ? 'Rejecting...' : 'Reject'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Preview Dialog */}
      <FullTaskPreviewDialog
        task={selectedTask}
        open={previewOpen}
        onClose={handlePreviewClose}
        onApprove={() => {
          handlePreviewClose();
          handleApproveClick(selectedTask);
        }}
        onReject={() => {
          handlePreviewClose();
          handleRejectClick(selectedTask);
        }}
      />
    </Box>
  );
};

/**
 * Full Task Preview Dialog Component
 */
const FullTaskPreviewDialog = ({ task, open, onClose, onApprove, onReject }) => {
  if (!task) return null;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        {task.task_name || 'Task Preview'}
        <Box sx={{ mt: 1 }}>
          <Chip
            label={task.task_type}
            color="primary"
            size="small"
            sx={{ mr: 1 }}
          />
          {task.quality_score && (
            <Rating
              value={task.quality_score / 2}
              max={5}
              readOnly
              size="small"
              sx={{ my: 'auto' }}
            />
          )}
        </Box>
      </DialogTitle>

      <DialogContent dividers sx={{ bgcolor: '#f9f9f9' }}>
        <Stack spacing={3}>
          {/* Featured Image */}
          {task.featured_image_url && (
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>Featured Image:</Typography>
              <Box
                component="img"
                src={task.featured_image_url}
                alt="Featured"
                sx={{
                  width: '100%',
                  maxHeight: 400,
                  borderRadius: 1,
                  objectFit: 'cover',
                }}
              />
            </Box>
          )}

          {/* Content */}
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>Content:</Typography>
            <Box
              sx={{
                bgcolor: 'white',
                p: 2,
                borderRadius: 1,
                border: '1px solid #ddd',
                whiteSpace: 'pre-wrap',
                maxHeight: 500,
                overflow: 'auto',
                fontFamily: 'monospace',
              }}
            >
              {task.content_preview || task.metadata?.content || 'No content'}
            </Box>
          </Box>

          {/* Metadata */}
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>Metadata:</Typography>
            <Grid container spacing={1}>
              <Grid item xs={6}>
                <Typography variant="body2" color="textSecondary">Topic:</Typography>
                <Typography variant="body2">{task.topic}</Typography>
              </Grid>
              <Grid item xs={6}>
                <Typography variant="body2" color="textSecondary">Created:</Typography>
                <Typography variant="body2">
                  {new Date(task.created_at).toLocaleDateString()}
                </Typography>
              </Grid>
            </Grid>
          </Box>
        </Stack>
      </DialogContent>

      <DialogActions sx={{ p: 2 }}>
        <Button onClick={onClose}>Close</Button>
        <Button variant="contained" color="success" onClick={onApprove}>
          Approve
        </Button>
        <Button variant="contained" color="error" onClick={onReject}>
          Reject
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ApprovalQueue;
