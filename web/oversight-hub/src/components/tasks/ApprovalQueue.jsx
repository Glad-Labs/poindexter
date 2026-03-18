import logger from '@/lib/logger';
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
 *
 * State is delegated to useApprovalQueue (fetching / single actions) and
 * useBulkSelection (checkbox selection / bulk approve/reject). (#311)
 */

import React, { useState } from 'react';
import {
  Box,
  Button,
  Card,
  CardContent,
  CardActions,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  Grid,
  InputLabel,
  MenuItem,
  Pagination,
  Rating,
  Select,
  Stack,
  TextField,
  Typography,
  Alert,
} from '@mui/material';
import { CheckCircle, Close, Info, Visibility } from '@mui/icons-material';
import useApprovalQueue from '../../hooks/useApprovalQueue';
import useBulkSelection from '../../hooks/useBulkSelection';

// ---- Utility helpers ---------------------------------------------------------

/**
 * Get API base URL from validated config (no localhost fallback)
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

const getContentPreview = (content) => {
  if (!content) return 'No content';
  return content.substring(0, 150) + (content.length > 150 ? '...' : '');
};

// ---- ApprovalItemCard --------------------------------------------------------

/**
 * Renders a single task card with checkbox, metadata, content preview, and
 * approve / reject / preview action buttons.
 *
 * @param {object} props
 * @param {object}   props.task
 * @param {boolean}  props.isSelected
 * @param {Function} props.onToggleSelect  - (taskId) => void
 * @param {boolean}  props.processingTaskId
 * @param {Function} props.onPreviewOpen   - (task) => void
 * @param {Function} props.onApproveClick  - (task) => void
 * @param {Function} props.onRejectClick   - (task) => void
 */
const ApprovalItemCard = ({
  task,
  isSelected,
  onToggleSelect,
  processingTaskId,
  onPreviewOpen,
  onApproveClick,
  onRejectClick,
}) => (
  <Card
    sx={{
      mb: 2,
      border: isSelected ? '2px solid #1976d2' : 'none',
      backgroundColor: isSelected ? '#f0f4ff' : 'white',
    }}
  >
    <CardContent>
      <Stack spacing={2}>
        {/* Header */}
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'start',
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.5 }}>
            <FormControlLabel
              control={
                <input
                  type="checkbox"
                  checked={isSelected || false}
                  onChange={() => onToggleSelect(task.task_id)}
                  style={{ marginTop: 4 }}
                />
              }
              label=""
            />
            <Box>
              <Typography variant="h6" sx={{ mb: 1 }}>
                {task.task_name || 'Untitled Task'}
              </Typography>
              <Typography variant="body2" color="textSecondary" sx={{ mb: 1 }}>
                Topic: {task.topic || 'N/A'} | Created:{' '}
                {formatDate(task.created_at)}
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
                    color={
                      task.quality_score >= 7.5
                        ? 'success'
                        : task.quality_score >= 5
                          ? 'warning'
                          : 'error'
                    }
                  />
                )}
              </Box>
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
            {task.content_preview ||
              getContentPreview(task.metadata?.content || '')}
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
          startIcon={<Visibility />}
          onClick={() => onPreviewOpen(task)}
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
          onClick={() => onApproveClick(task)}
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
          onClick={() => onRejectClick(task)}
          disabled={processingTaskId === task.task_id}
        >
          {processingTaskId === task.task_id ? 'Processing...' : 'Reject'}
        </Button>
      </Box>
    </CardActions>
  </Card>
);

// ---- FullTaskPreviewDialog ---------------------------------------------------

/**
 * Full Task Preview Dialog Component
 */
const FullTaskPreviewDialog = ({
  task,
  open,
  onClose,
  onApprove,
  onReject,
}) => {
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
          {task.featured_image_url && (
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                Featured Image:
              </Typography>
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

          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Content:
            </Typography>
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

          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Metadata:
            </Typography>
            <Grid container spacing={1}>
              <Grid item xs={6}>
                <Typography variant="body2" color="textSecondary">
                  Topic:
                </Typography>
                <Typography variant="body2">{task.topic}</Typography>
              </Grid>
              <Grid item xs={6}>
                <Typography variant="body2" color="textSecondary">
                  Created:
                </Typography>
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

// ---- ApprovalQueue (coordinator) --------------------------------------------

/**
 * ApprovalQueue Component
 * Main component for displaying and managing approval tasks
 */
const ApprovalQueue = () => {
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  const queue = useApprovalQueue({
    onSuccess: setSuccess,
    onError: setError,
  });

  const bulk = useBulkSelection({
    items: queue.tasks,
    itemIdKey: 'task_id',
    onSuccess: setSuccess,
    onError: setError,
    onRefresh: queue.fetchPendingTasks,
  });

  return (
    <Box sx={{ p: 3 }}>
      {/* Header */}
      <Box sx={{ mb: 3 }}>
        <Typography variant="h4" sx={{ mb: 2 }}>
          Approval Queue
        </Typography>
        <Typography variant="body1" color="textSecondary">
          Review and approve content before it's published.
        </Typography>
      </Box>

      {/* Alerts */}
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {success && (
        <Alert
          severity="success"
          sx={{ mb: 2 }}
          onClose={() => setSuccess(null)}
        >
          {success}
        </Alert>
      )}

      {/* Filters & Controls */}
      <Stack direction="row" spacing={2} sx={{ mb: 3 }}>
        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel id="approval-task-type-label">Task Type</InputLabel>
          <Select
            labelId="approval-task-type-label"
            label="Task Type"
            value={queue.taskTypeFilter}
            onChange={queue.handleTaskTypeFilterChange}
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
        </FormControl>

        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel id="approval-sort-by-label">Sort By</InputLabel>
          <Select
            labelId="approval-sort-by-label"
            label="Sort By"
            value={queue.sortBy}
            onChange={queue.handleSortChange}
          >
            <MenuItem value="created_at">Sort: Newest First</MenuItem>
            <MenuItem value="quality_score">Sort: Highest Quality</MenuItem>
            <MenuItem value="topic">Sort: Topic (A-Z)</MenuItem>
          </Select>
        </FormControl>

        <Button
          variant="outlined"
          onClick={queue.fetchPendingTasks}
          disabled={queue.loading}
        >
          {queue.loading ? <CircularProgress size={24} /> : 'Refresh'}
        </Button>
      </Stack>

      {/* Bulk Actions & Selection */}
      {queue.tasks.length > 0 && (
        <Stack direction="row" spacing={2} sx={{ mb: 3, alignItems: 'center' }}>
          <Typography variant="body2" color="textSecondary">
            {bulk.selectedIds.size > 0
              ? `${bulk.selectedIds.size} task${bulk.selectedIds.size !== 1 ? 's' : ''} selected`
              : 'No tasks selected'}
          </Typography>
          {bulk.selectedIds.size > 0 && (
            <>
              <Button
                size="small"
                variant="contained"
                color="success"
                onClick={bulk.handleBulkApproveClick}
                disabled={bulk.bulkOperationLoading}
              >
                ✓ Bulk Approve ({bulk.selectedIds.size})
              </Button>
              <Button
                size="small"
                variant="contained"
                color="error"
                onClick={bulk.handleBulkRejectClick}
                disabled={bulk.bulkOperationLoading}
              >
                ✕ Bulk Reject ({bulk.selectedIds.size})
              </Button>
              <Button
                size="small"
                variant="outlined"
                onClick={bulk.handleClearSelection}
                disabled={bulk.bulkOperationLoading}
              >
                Clear Selection
              </Button>
            </>
          )}
          {queue.tasks.length > 1 && bulk.selectedIds.size === 0 && (
            <Button
              size="small"
              variant="outlined"
              onClick={bulk.handleSelectAll}
            >
              Select All
            </Button>
          )}
        </Stack>
      )}

      {/* Loading State */}
      {queue.loading && (
        <Box
          sx={{ display: 'flex', justifyContent: 'center', py: 4 }}
          role="status"
          aria-label="Loading approval queue"
        >
          <CircularProgress />
        </Box>
      )}

      {/* Empty State */}
      {!queue.loading && queue.tasks.length === 0 && (
        <Alert severity="info" icon={<Info />}>
          No tasks awaiting approval. All caught up!
        </Alert>
      )}

      {/* Task List */}
      {!queue.loading && queue.tasks.length > 0 && (
        <>
          <Alert severity="info" sx={{ mb: 2 }}>
            Showing {queue.tasks.length} of {queue.tasks.length} pending
            approvals
          </Alert>

          <Box sx={{ mb: 3 }}>
            {queue.tasks.map((task) => (
              <ApprovalItemCard
                key={task.task_id}
                task={task}
                isSelected={bulk.selectedIds.has(task.task_id)}
                onToggleSelect={bulk.handleToggleSelect}
                processingTaskId={queue.processingTaskId}
                onPreviewOpen={queue.handlePreviewOpen}
                onApproveClick={queue.handleApproveClick}
                onRejectClick={queue.handleRejectClick}
              />
            ))}
          </Box>

          <Box sx={{ display: 'flex', justifyContent: 'center' }}>
            <Pagination
              count={Math.ceil(queue.tasks.length / queue.ITEMS_PER_PAGE) || 1}
              page={queue.page}
              onChange={queue.handlePageChange}
              color="primary"
            />
          </Box>
        </>
      )}

      {/* ===== DIALOGS ===== */}

      {/* Approve Dialog */}
      <Dialog
        open={queue.approveDialogOpen}
        onClose={() => queue.setApproveDialogOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Approve Task</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 2 }}>
            <Typography variant="body2" color="textSecondary">
              Are you sure you want to approve{' '}
              <strong>{queue.selectedTask?.task_name}</strong> for publishing?
            </Typography>
            <TextField
              label="Approval Notes (Optional)"
              placeholder="Add any notes or feedback..."
              multiline
              rows={3}
              value={queue.approveFeedback}
              onChange={(e) => queue.setApproveFeedback(e.target.value)}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => queue.setApproveDialogOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={queue.handleApprovalSubmit}
            variant="contained"
            color="success"
            disabled={queue.processingTaskId === queue.selectedTask?.task_id}
          >
            {queue.processingTaskId === queue.selectedTask?.task_id
              ? 'Approving...'
              : 'Approve'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Reject Dialog */}
      <Dialog
        open={queue.rejectDialogOpen}
        onClose={() => queue.setRejectDialogOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Reject Task</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 2 }}>
            <Typography variant="body2" color="textSecondary">
              Rejecting <strong>{queue.selectedTask?.task_name}</strong>
            </Typography>

            <FormControl fullWidth>
              <InputLabel id="reject-reason-label">Reason</InputLabel>
              <Select
                labelId="reject-reason-label"
                label="Reason"
                value={queue.rejectReason}
                onChange={(e) => queue.setRejectReason(e.target.value)}
              >
                <MenuItem value="Content quality">Content Quality</MenuItem>
                <MenuItem value="Factual errors">Factual Errors</MenuItem>
                <MenuItem value="Tone mismatch">Tone Mismatch</MenuItem>
                <MenuItem value="Style issues">Style Issues</MenuItem>
                <MenuItem value="Other">Other</MenuItem>
              </Select>
            </FormControl>

            <TextField
              label="Feedback"
              placeholder="Explain what needs to be fixed..."
              multiline
              rows={4}
              value={queue.rejectFeedback}
              onChange={(e) => queue.setRejectFeedback(e.target.value)}
              fullWidth
              error={!queue.rejectFeedback && queue.rejectDialogOpen}
              helperText={
                !queue.rejectFeedback && queue.rejectDialogOpen
                  ? 'Feedback is required'
                  : ''
              }
            />

            <FormControlLabel
              control={
                <input
                  type="checkbox"
                  checked={queue.allowRevisions}
                  onChange={(e) => queue.setAllowRevisions(e.target.checked)}
                />
              }
              label="Allow the team to submit revisions"
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => queue.setRejectDialogOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={queue.handleRejectionSubmit}
            variant="contained"
            color="error"
            disabled={
              queue.processingTaskId === queue.selectedTask?.task_id ||
              !queue.rejectFeedback
            }
          >
            {queue.processingTaskId === queue.selectedTask?.task_id
              ? 'Rejecting...'
              : 'Reject'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Bulk Approve Dialog */}
      <Dialog
        open={bulk.bulkApproveDialogOpen}
        onClose={() => bulk.setBulkApproveDialogOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Bulk Approve Tasks</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 2 }}>
            <Typography variant="body2" color="textSecondary">
              Are you sure you want to approve{' '}
              <strong>
                {bulk.selectedIds.size} task
                {bulk.selectedIds.size !== 1 ? 's' : ''}
              </strong>{' '}
              for publishing?
            </Typography>
            <TextField
              label="Approval Notes (Optional)"
              placeholder="Add any notes or feedback..."
              multiline
              rows={3}
              value={bulk.bulkApproveFeedback}
              onChange={(e) => bulk.setBulkApproveFeedback(e.target.value)}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => bulk.setBulkApproveDialogOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={bulk.handleBulkApproveSubmit}
            variant="contained"
            color="success"
            disabled={bulk.bulkOperationLoading}
          >
            {bulk.bulkOperationLoading ? 'Approving...' : 'Approve All'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Bulk Reject Dialog */}
      <Dialog
        open={bulk.bulkRejectDialogOpen}
        onClose={() => bulk.setBulkRejectDialogOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Bulk Reject Tasks</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 2 }}>
            <Typography variant="body2" color="textSecondary">
              Rejecting{' '}
              <strong>
                {bulk.selectedIds.size} task
                {bulk.selectedIds.size !== 1 ? 's' : ''}
              </strong>
            </Typography>

            <FormControl fullWidth>
              <InputLabel id="bulk-reject-reason-label">Reason</InputLabel>
              <Select
                labelId="bulk-reject-reason-label"
                label="Reason"
                value={bulk.bulkRejectReason}
                onChange={(e) => bulk.setBulkRejectReason(e.target.value)}
              >
                <MenuItem value="Content quality">Content Quality</MenuItem>
                <MenuItem value="Factual errors">Factual Errors</MenuItem>
                <MenuItem value="Tone mismatch">Tone Mismatch</MenuItem>
                <MenuItem value="Style issues">Style Issues</MenuItem>
                <MenuItem value="Other">Other</MenuItem>
              </Select>
            </FormControl>

            <TextField
              label="Feedback"
              placeholder="Explain what needs to be fixed..."
              multiline
              rows={4}
              value={bulk.bulkRejectFeedback}
              onChange={(e) => bulk.setBulkRejectFeedback(e.target.value)}
              fullWidth
              error={!bulk.bulkRejectFeedback && bulk.bulkRejectDialogOpen}
              helperText={
                !bulk.bulkRejectFeedback && bulk.bulkRejectDialogOpen
                  ? 'Feedback is required'
                  : ''
              }
            />

            <FormControlLabel
              control={
                <input
                  type="checkbox"
                  checked={bulk.bulkAllowRevisions}
                  onChange={(e) => bulk.setBulkAllowRevisions(e.target.checked)}
                />
              }
              label="Allow the team to submit revisions"
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => bulk.setBulkRejectDialogOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={bulk.handleBulkRejectSubmit}
            variant="contained"
            color="error"
            disabled={bulk.bulkOperationLoading || !bulk.bulkRejectFeedback}
          >
            {bulk.bulkOperationLoading ? 'Rejecting...' : 'Reject All'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Preview Dialog */}
      <FullTaskPreviewDialog
        task={queue.selectedTask}
        open={queue.previewOpen}
        onClose={queue.handlePreviewClose}
        onApprove={() => {
          queue.handlePreviewClose();
          queue.handleApproveClick(queue.selectedTask);
        }}
        onReject={() => {
          queue.handlePreviewClose();
          queue.handleRejectClick(queue.selectedTask);
        }}
      />
    </Box>
  );
};

export default ApprovalQueue;
