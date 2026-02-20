import React, { useState } from 'react';
import PropTypes from 'prop-types';
import {
  Box,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
} from '@mui/material';
import {
  ThumbUp as ThumbUpIcon,
  ThumbDown as ThumbDownIcon,
  Edit as EditIcon,
  Download as DownloadIcon,
  ContentCopy as ContentCopyIcon,
} from '@mui/icons-material';
import OrchestratorMessageCard from './OrchestratorMessageCard';
import useStore from '../store/useStore';

/**
 * OrchestratorResultMessage
 *
 * Renders a completed orchestrator result with approval workflow.
 * Uses OrchestratorMessageCard base component for consistent styling.
 *
 * Refactored to use base component: 468 → 160 lines (-66% boilerplate).
 */
const OrchestratorResultMessage = ({
  message,
  onApprove,
  onReject,
  onEdit,
}) => {
  const [feedbackDialog, setFeedbackDialog] = useState({
    open: false,
    type: null,
    feedback: '',
  });
  const [copied, setCopied] = useState(false);
  const completeExecution = useStore((state) => state.completeExecution);

  const result = message.result || '';
  const metadata = message.metadata || {};
  const resultPreview =
    result.substring(0, 500) + (result.length > 500 ? '...' : '');

  const handleCopy = () => {
    navigator.clipboard.writeText(result);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleApprove = () => {
    setFeedbackDialog({ open: true, type: 'approve', feedback: '' });
  };

  const handleReject = () => {
    setFeedbackDialog({ open: true, type: 'reject', feedback: '' });
  };

  const handleFeedbackSubmit = () => {
    if (feedbackDialog.type === 'approve') {
      onApprove?.({ feedback: feedbackDialog.feedback });
      completeExecution({ approved: true, feedback: feedbackDialog.feedback });
    } else if (feedbackDialog.type === 'reject') {
      onReject?.({ feedback: feedbackDialog.feedback });
    }
    setFeedbackDialog({ open: false, type: null, feedback: '' });
  };

  const handleExport = () => {
    const element = document.createElement('a');
    const file = new Blob([result], { type: 'text/plain' });
    element.href = URL.createObjectURL(file);
    element.download = `result_${Date.now()}.txt`;
    document.body.appendChild(element);
    element.click();
    document.body.removeChild(element);
  };

  // Metadata for header
  const headerMetadata = [
    { label: 'Words', value: metadata.wordCount || 0 },
    {
      label: 'Quality',
      value: metadata.qualityScore ? `${metadata.qualityScore}/10` : 'N/A',
    },
    {
      label: 'Cost',
      value: metadata.cost ? `$${metadata.cost.toFixed(3)}` : 'N/A',
    },
  ];

  // Expandable content - full result and execution details
  const expandedContent = (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <Box>
        <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
          Full Result
        </Typography>
        <Typography
          variant="body2"
          sx={{
            p: 1.5,
            background: 'rgba(255, 255, 255, 0.08)',
            borderRadius: '4px',
            lineHeight: 1.7,
            fontFamily: 'monospace',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: '400px',
            overflow: 'auto',
          }}
        >
          {result}
        </Typography>
      </Box>
      {metadata.executionTime && (
        <Box
          sx={{
            p: 1,
            background: 'rgba(255, 255, 255, 0.08)',
            borderRadius: '4px',
          }}
        >
          <Typography variant="caption" sx={{ display: 'block', mb: 0.5 }}>
            <strong>Execution Time:</strong> {metadata.executionTime}s
          </Typography>
          <Typography variant="caption" sx={{ display: 'block', mb: 0.5 }}>
            <strong>Model:</strong> {metadata.model || 'Unknown'}
          </Typography>
          <Typography variant="caption" sx={{ display: 'block' }}>
            <strong>Provider:</strong> {metadata.provider || 'Unknown'}
          </Typography>
        </Box>
      )}
    </Box>
  );

  // Footer action buttons
  const footerActions = [
    {
      label: copied ? 'Copied!' : 'Copy',
      onClick: handleCopy,
      variant: 'outlined',
      icon: ContentCopyIcon,
    },
    {
      label: 'Export',
      onClick: handleExport,
      variant: 'outlined',
      icon: DownloadIcon,
    },
    {
      label: 'Edit',
      onClick: () => onEdit?.(result),
      variant: 'outlined',
      icon: EditIcon,
    },
    {
      label: 'Reject',
      onClick: handleReject,
      variant: 'outlined',
      icon: ThumbDownIcon,
    },
    {
      label: 'Approve',
      onClick: handleApprove,
      variant: 'contained',
      icon: ThumbUpIcon,
      sx: { backgroundColor: 'white', color: '#4caf50' },
    },
  ];

  return (
    <>
      <OrchestratorMessageCard
        headerIcon="✅"
        headerLabel="Result Ready"
        gradient="linear-gradient(135deg, #4caf50 0%, #45a049 100%)"
        metadata={headerMetadata}
        expandedContent={expandedContent}
        footerActions={footerActions}
      >
        <Typography
          variant="body2"
          sx={{
            p: 1.5,
            background: 'rgba(255, 255, 255, 0.1)',
            borderRadius: '4px',
            lineHeight: 1.6,
            maxHeight: '150px',
            overflow: 'hidden',
          }}
        >
          {resultPreview}
        </Typography>
      </OrchestratorMessageCard>

      {/* Feedback Dialog */}
      <Dialog
        open={feedbackDialog.open}
        onClose={() =>
          setFeedbackDialog({ open: false, type: null, feedback: '' })
        }
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {feedbackDialog.type === 'approve'
            ? '✅ Approve Result'
            : '❌ Reject Result'}
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 2, mt: 1 }}>
            {feedbackDialog.type === 'approve'
              ? 'Are you satisfied with this result? You can add optional feedback.'
              : 'Why are you rejecting this result? Your feedback will help improve future results.'}
          </Typography>
          <textarea
            value={feedbackDialog.feedback}
            onChange={(e) =>
              setFeedbackDialog((prev) => ({
                ...prev,
                feedback: e.target.value,
              }))
            }
            placeholder="Enter your feedback (optional)"
            style={{
              width: '100%',
              minHeight: '100px',
              padding: '8px',
              borderRadius: '4px',
              border: '1px solid #ddd',
              fontFamily: 'inherit',
              fontSize: '14px',
            }}
          />
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() =>
              setFeedbackDialog({ open: false, type: null, feedback: '' })
            }
          >
            Cancel
          </Button>
          <Button
            onClick={handleFeedbackSubmit}
            variant="contained"
            color={feedbackDialog.type === 'approve' ? 'success' : 'error'}
          >
            {feedbackDialog.type === 'approve' ? 'Approve' : 'Reject'}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

OrchestratorResultMessage.propTypes = {
  message: PropTypes.shape({
    id: PropTypes.string.isRequired,
    type: PropTypes.oneOf(['result']).isRequired,
    result: PropTypes.string.isRequired,
    metadata: PropTypes.shape({
      wordCount: PropTypes.number,
      qualityScore: PropTypes.number,
      cost: PropTypes.number,
      executionTime: PropTypes.number,
      model: PropTypes.string,
      provider: PropTypes.string,
    }),
    timestamp: PropTypes.number,
  }).isRequired,
  onApprove: PropTypes.func,
  onReject: PropTypes.func,
  onEdit: PropTypes.func,
};

OrchestratorResultMessage.defaultProps = {
  onApprove: null,
  onReject: null,
  onEdit: null,
};

export default OrchestratorResultMessage;
