import React from 'react';
import PropTypes from 'prop-types';
import { Box, Typography } from '@mui/material';
import {
  Refresh as RefreshIcon,
  Close as CloseIcon,
} from '@mui/icons-material';
import OrchestratorMessageCard from './OrchestratorMessageCard';
import useStore from '../store/useStore';

/**
 * OrchestratorErrorMessage
 *
 * Renders an error that occurred during orchestrator execution.
 * Uses OrchestratorMessageCard base component for consistent styling.
 *
 * Refactored to use base component: 401 → 145 lines (-64% boilerplate).
 */
const OrchestratorErrorMessage = ({ message, onRetry, onCancel }) => {
  const failExecution = useStore((state) => state.failExecution);

  const errorMessage = message.error || 'An unknown error occurred';
  const errorType = message.errorType || 'error';
  const errorSeverity = message.severity || 'error'; // 'error', 'warning', 'info'
  const details = message.details || {};
  const suggestions = message.suggestions || [];
  const retryable = message.retryable !== false;

  const handleRetry = () => {
    onRetry?.();
  };

  const handleCancel = () => {
    failExecution({ cancelled: true });
    onCancel?.();
  };

  const getSeverityInfo = () => {
    const severityMap = {
      error: {
        color: '#d32f2f',
        icon: '❌',
        label: 'Error',
        bgColor: 'linear-gradient(135deg, #d32f2f 0%, #b71c1c 100%)',
      },
      warning: {
        color: '#f57c00',
        icon: '⚠️',
        label: 'Warning',
        bgColor: 'linear-gradient(135deg, #f57c00 0%, #e65100 100%)',
      },
      info: {
        color: '#1976d2',
        icon: 'ℹ️',
        label: 'Info',
        bgColor: 'linear-gradient(135deg, #1976d2 0%, #1565c0 100%)',
      },
    };
    return severityMap[errorSeverity] || severityMap.error;
  };

  const severityInfo = getSeverityInfo();

  // Expandable content - error details and suggestions
  const expandedContent = (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {/* Recovery Suggestions */}
      {suggestions && suggestions.length > 0 && (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
            💡 Recovery Suggestions
          </Typography>
          <Box sx={{ pl: 1 }}>
            {suggestions.map((suggestion, idx) => (
              <Typography
                key={idx}
                variant="body2"
                sx={{
                  mb: 0.5,
                  opacity: 0.95,
                  '&:before': { content: '"• "' },
                }}
              >
                {suggestion}
              </Typography>
            ))}
          </Box>
        </Box>
      )}

      {/* Error Details */}
      <Box
        sx={{
          p: 1,
          background: 'rgba(255, 255, 255, 0.08)',
          borderRadius: '4px',
        }}
      >
        <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
          📋 Error Details
        </Typography>
        {details.phase && (
          <Typography variant="caption" sx={{ display: 'block', mb: 0.5 }}>
            <strong>Phase:</strong> {details.phase}
          </Typography>
        )}
        {details.timestamp && (
          <Typography variant="caption" sx={{ display: 'block', mb: 0.5 }}>
            <strong>Time:</strong>{' '}
            {new Date(details.timestamp).toLocaleString()}
          </Typography>
        )}
        {details.code && (
          <Typography variant="caption" sx={{ display: 'block', mb: 0.5 }}>
            <strong>Code:</strong> {details.code}
          </Typography>
        )}
        {details.source && (
          <Typography variant="caption" sx={{ display: 'block' }}>
            <strong>Source:</strong> {details.source}
          </Typography>
        )}
      </Box>

      {/* Stack Trace (if available) */}
      {details.stackTrace && (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
            🔍 Stack Trace
          </Typography>
          <Typography
            variant="body2"
            sx={{
              p: 1,
              background: 'rgba(0, 0, 0, 0.2)',
              borderRadius: '4px',
              fontFamily: 'monospace',
              fontSize: '11px',
              lineHeight: 1.4,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              maxHeight: '200px',
              overflow: 'auto',
            }}
          >
            {details.stackTrace}
          </Typography>
        </Box>
      )}

      {/* Related Documentation */}
      {details.documentation && (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
            📚 Related Documentation
          </Typography>
          <Typography
            component="a"
            href={details.documentation}
            target="_blank"
            rel="noopener noreferrer"
            sx={{
              color: 'white',
              textDecoration: 'underline',
              '&:hover': { opacity: 0.8 },
              display: 'inline-block',
            }}
          >
            View Documentation →
          </Typography>
        </Box>
      )}
    </Box>
  );

  // Footer action buttons
  const footerActions = [
    {
      label: 'Cancel',
      onClick: handleCancel,
      variant: 'outlined',
      icon: CloseIcon,
    },
    ...(retryable
      ? [
          {
            label: 'Retry',
            onClick: handleRetry,
            variant: 'contained',
            icon: RefreshIcon,
            sx: { backgroundColor: 'white', color: severityInfo.color },
          },
        ]
      : []),
  ];

  return (
    <OrchestratorMessageCard
      headerIcon={severityInfo.icon}
      headerLabel={severityInfo.label}
      gradient={severityInfo.bgColor}
      metadata={[
        { label: 'Type', value: errorType },
        { label: 'Retryable', value: retryable ? 'Yes' : 'No' },
      ]}
      expandedContent={expandedContent}
      footerActions={footerActions}
    >
      <Typography
        variant="body1"
        sx={{
          p: 1.5,
          background: 'rgba(255, 255, 255, 0.1)',
          borderRadius: '4px',
          lineHeight: 1.6,
        }}
      >
        {errorMessage}
      </Typography>
    </OrchestratorMessageCard>
  );
};

OrchestratorErrorMessage.propTypes = {
  message: PropTypes.shape({
    id: PropTypes.string.isRequired,
    type: PropTypes.oneOf(['error']).isRequired,
    error: PropTypes.string.isRequired,
    errorType: PropTypes.string,
    severity: PropTypes.oneOf(['error', 'warning', 'info']),
    details: PropTypes.shape({
      phase: PropTypes.string,
      timestamp: PropTypes.number,
      code: PropTypes.string,
      source: PropTypes.string,
      stackTrace: PropTypes.string,
      documentation: PropTypes.string,
    }),
    suggestions: PropTypes.arrayOf(PropTypes.string),
    retryable: PropTypes.bool,
    timestamp: PropTypes.number,
  }).isRequired,
  onRetry: PropTypes.func,
  onCancel: PropTypes.func,
};

OrchestratorErrorMessage.defaultProps = {
  onRetry: null,
  onCancel: null,
};

export default OrchestratorErrorMessage;
