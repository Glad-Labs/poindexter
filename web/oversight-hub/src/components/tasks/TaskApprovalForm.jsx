/**
 * TaskApprovalForm - Approval and publishing workflow
 *
 * Features:
 * - Approval feedback textarea
 * - Reviewer ID input
 * - Three step workflow:
 *   1. awaiting_approval: Approve/Reject buttons
 *   2. approved: Publish button
 *   3. rejected: Re-review option
 */

import React from 'react';
import PropTypes from 'prop-types';
import { Box, Button, TextField } from '@mui/material';

const TaskApprovalForm = ({
  task,
  approvalFeedback,
  reviewerId,
  approvalLoading,
  publishLoading,
  onApprove,
  onPublish,
  onReject,
  onReReview,
  onFeedbackChange,
  onReviewerIdChange,
}) => {
  if (!task) {
    return null;
  }

  const isAwaitingApproval = task.status === 'awaiting_approval';
  const isApproved = task.status === 'approved';
  // Backend sets 'failed' (no revisions) or 'failed_revisions_requested' (revisions allowed)
  const isRejected =
    task.status === 'failed' || task.status === 'failed_revisions_requested';
  const isRevisionsRequested = task.status === 'failed_revisions_requested';

  return (
    <Box>
      {/* Approval Notes - Show for all statuses that need approval */}
      {(isAwaitingApproval || isRejected) && (
        <Box
          sx={{
            background: 'linear-gradient(135deg, #1a2a3a 0%, #1a2a1a 100%)',
            padding: 2,
            borderRadius: 1,
            border: '1px solid #ff6b6b',
            marginBottom: 2,
          }}
        >
          <h3 style={{ marginTop: 0, color: '#ff6b6b' }}>
            <span aria-hidden="true">📝 </span>Approval Notes
          </h3>
          <TextField
            fullWidth
            multiline
            rows={4}
            label="Feedback for creator"
            value={approvalFeedback}
            onChange={(e) => onFeedbackChange(e.target.value)}
            placeholder="Provide feedback or suggestions for improvement..."
            sx={{
              mb: 2,
              '& .MuiOutlinedInput-root': {
                backgroundColor: '#0f0f0f',
                borderColor: '#333',
                color: '#e0e0e0',
                '&:hover fieldset': {
                  borderColor: '#ff6b6b',
                },
              },
              '& .MuiInputBase-input::placeholder': {
                color: '#666',
                opacity: 1,
              },
              '& .MuiInputLabel-root': {
                color: '#999',
              },
            }}
          />

          <TextField
            fullWidth
            size="small"
            label="Reviewer ID"
            value={reviewerId}
            onChange={(e) => onReviewerIdChange(e.target.value)}
            placeholder="Your identifier"
            sx={{
              '& .MuiOutlinedInput-root': {
                backgroundColor: '#0f0f0f',
                borderColor: '#333',
                color: '#e0e0e0',
                '&:hover fieldset': {
                  borderColor: '#ff6b6b',
                },
              },
              '& .MuiInputBase-input::placeholder': {
                color: '#666',
                opacity: 1,
              },
              '& .MuiInputLabel-root': {
                color: '#999',
              },
            }}
          />
        </Box>
      )}

      {/* STEP 1: Awaiting Approval */}
      {isAwaitingApproval && (
        <Box
          sx={{
            background: 'linear-gradient(135deg, #2a3a1a 0%, #1a3a2a 100%)',
            padding: 2,
            borderRadius: 1,
            border: '1px solid #4ade80',
            marginBottom: 2,
          }}
        >
          <h3 style={{ marginTop: 0, color: '#4ade80' }}>
            <span aria-hidden="true">✅ </span>Step 1: Review &amp; Approve
          </h3>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            <Button
              variant="contained"
              sx={{
                backgroundColor: '#4ade80',
                color: '#000',
                fontWeight: 'bold',
                '&:hover': { backgroundColor: '#22c55e' },
              }}
              onClick={onApprove}
              disabled={approvalLoading}
            >
              {approvalLoading ? '⟳ Approving...' : '✓ Approve (Step 1)'}
            </Button>
            <Button
              variant="outlined"
              sx={{
                borderColor: '#ef4444',
                color: '#ef4444',
                '&:hover': {
                  backgroundColor: 'rgba(239, 68, 68, 0.1)',
                },
              }}
              onClick={onReject}
              disabled={approvalLoading}
            >
              ✕ Reject
            </Button>
          </Box>
        </Box>
      )}

      {/* STEP 2: Approved - Ready for Publishing */}
      {isApproved && (
        <Box
          sx={{
            background: 'linear-gradient(135deg, #2a4a3a 0%, #1a3a4a 100%)',
            padding: 2,
            borderRadius: 1,
            border: '1px solid #0ea5e9',
            marginBottom: 2,
          }}
        >
          <h3 style={{ marginTop: 0, color: '#0ea5e9' }}>
            <span aria-hidden="true">📤 </span>Step 2: Publish to Site
          </h3>
          <p style={{ marginTop: '0', color: '#e0e0e0', fontSize: '0.95rem' }}>
            Content approved! Ready to publish to the public site.
          </p>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            <Button
              variant="contained"
              sx={{
                backgroundColor: '#0ea5e9',
                color: '#fff',
                fontWeight: 'bold',
                '&:hover': { backgroundColor: '#0284c7' },
              }}
              onClick={onPublish}
              disabled={publishLoading}
            >
              {publishLoading ? '⟳ Publishing...' : '📤 Publish (Step 2)'}
            </Button>
          </Box>
        </Box>
      )}

      {/* REJECTED: Re-review Option */}
      {isRevisionsRequested && (
        <Box
          sx={{
            background: 'linear-gradient(135deg, #3a1a1a 0%, #2a1a3a 100%)',
            padding: 2,
            borderRadius: 1,
            border: '1px solid #ef4444',
            marginBottom: 2,
          }}
        >
          <h3 style={{ marginTop: 0, color: '#ef4444' }}>
            <span aria-hidden="true">⚠️ </span>Content Rejected
          </h3>
          <p style={{ marginTop: '0', color: '#e0e0e0', marginBottom: '12px' }}>
            This content was rejected and cannot be published as-is.
          </p>
          {task.reviewer_feedback && (
            <Box
              sx={{
                backgroundColor: 'rgba(239, 68, 68, 0.1)',
                padding: 1.5,
                borderRadius: 0.5,
                border: '1px solid rgba(239, 68, 68, 0.3)',
                marginBottom: 2,
              }}
            >
              <p
                style={{
                  marginTop: 0,
                  marginBottom: '4px',
                  color: '#ffb3b3',
                  fontWeight: 'bold',
                  fontSize: '0.85rem',
                }}
              >
                Reviewer feedback:
              </p>
              <p
                style={{
                  marginTop: 0,
                  marginBottom: 0,
                  color: '#e0e0e0',
                  fontSize: '0.9rem',
                }}
              >
                {task.reviewer_feedback}
              </p>
            </Box>
          )}
          <Button
            variant="outlined"
            onClick={onReReview}
            disabled={approvalLoading}
            sx={{
              borderColor: '#8b5cf6',
              color: '#8b5cf6',
              '&:hover': {
                backgroundColor: 'rgba(139, 92, 246, 0.1)',
              },
            }}
          >
            {approvalLoading ? 'Sending...' : 'Re-review Rejected Task'}
          </Button>
        </Box>
      )}
    </Box>
  );
};

TaskApprovalForm.propTypes = {
  task: PropTypes.shape({
    status: PropTypes.string.isRequired,
    reviewer_feedback: PropTypes.string,
  }),
  approvalFeedback: PropTypes.string.isRequired,
  reviewerId: PropTypes.string.isRequired,
  approvalLoading: PropTypes.bool.isRequired,
  publishLoading: PropTypes.bool.isRequired,
  onApprove: PropTypes.func.isRequired,
  onPublish: PropTypes.func.isRequired,
  onReject: PropTypes.func.isRequired,
  onReReview: PropTypes.func,
  onFeedbackChange: PropTypes.func.isRequired,
  onReviewerIdChange: PropTypes.func.isRequired,
};

export default TaskApprovalForm;
