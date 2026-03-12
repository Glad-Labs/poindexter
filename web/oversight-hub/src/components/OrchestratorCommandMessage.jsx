import React, { useState } from 'react';
import PropTypes from 'prop-types';
import { Box, TextField, Typography } from '@mui/material';
import {
  PlayArrow as PlayArrowIcon,
  Close as CloseIcon,
  Edit as EditIcon,
} from '@mui/icons-material';
import OrchestratorMessageCard from './OrchestratorMessageCard';
import useStore from '../store/useStore';

/**
 * OrchestratorCommandMessage
 *
 * Renders an orchestrator command message with editable parameters.
 * Uses OrchestratorMessageCard base component for consistent styling.
 *
 * Refactored to use base component: 369 → 70 lines (-81% boilerplate).
 */
const OrchestratorCommandMessage = ({ message, onExecute, onCancel }) => {
  const [editMode, setEditMode] = useState(false);
  const [editedParams, setEditedParams] = useState(message.parameters || {});
  const startExecution = useStore((state) => state.startExecution);

  // Command type configuration
  const commandTypes = {
    generate: { emoji: '✨', label: 'Generate' },
    analyze: { emoji: '🔍', label: 'Analyze' },
    optimize: { emoji: '⚡', label: 'Optimize' },
    plan: { emoji: '📋', label: 'Plan' },
    export: { emoji: '�', label: 'Export' },
    delegate: { emoji: '👥', label: 'Delegate' },
  };

  const commandType = message.intent || 'generate';
  const commandConfig = commandTypes[commandType] || commandTypes.generate;

  const handleExecute = () => {
    startExecution(`exec_${Date.now()}`, message.intent, [
      { name: 'Research', status: 'pending' },
      { name: 'Analysis', status: 'pending' },
      { name: 'Generation', status: 'pending' },
      { name: 'Review', status: 'pending' },
      { name: 'Refinement', status: 'pending' },
      { name: 'Publishing', status: 'pending' },
    ]);

    onExecute?.({
      command: message.intent,
      parameters: editMode ? editedParams : message.parameters,
      mode: 'agent',
    });
  };

  const handleCancel = () => {
    setEditMode(false);
    setEditedParams(message.parameters || {});
    onCancel?.();
  };

  const handleParamChange = (key, value) => {
    setEditedParams((prev) => ({
      ...prev,
      [key]: value,
    }));
  };

  // Metadata for the card header
  const metadata = [
    { label: 'Type', value: `${commandConfig.emoji} ${commandConfig.label}` },
    { label: 'Model', value: message.modelHint || 'auto' },
  ];

  // Expandable content - parameter editor
  const expandedContent = (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      {editMode ? (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
            Edit Parameters
          </Typography>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            {Object.entries(editedParams).map(([key, value]) => (
              <TextField
                key={key}
                label={key.charAt(0).toUpperCase() + key.slice(1)}
                value={value}
                onChange={(e) => handleParamChange(key, e.target.value)}
                fullWidth
                size="small"
                variant="outlined"
              />
            ))}
          </Box>
        </Box>
      ) : (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
            Parameters
          </Typography>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
            {Object.entries(message.parameters || {}).map(([key, value]) => (
              <Typography key={key} variant="body2">
                <strong>{key}:</strong> {String(value)}
              </Typography>
            ))}
          </Box>
        </Box>
      )}
    </Box>
  );

  // Footer action buttons
  // NOTE: icon values must be JSX elements (e.g. <PlayArrowIcon />), not
  // component references — MUI Button.startIcon expects a React element.
  const footerActions = [
    {
      label: editMode ? 'Confirm' : 'Execute',
      onClick: handleExecute,
      variant: 'contained',
      icon: <PlayArrowIcon />,
      sx: { backgroundColor: '#22c55e' },
    },
    ...(editMode
      ? [
          {
            label: 'Cancel Edit',
            onClick: handleCancel,
            variant: 'outlined',
            icon: <CloseIcon />,
          },
        ]
      : [
          {
            label: 'Edit',
            onClick: () => setEditMode(true),
            variant: 'outlined',
            icon: <EditIcon />,
          },
          {
            label: 'Cancel',
            onClick: handleCancel,
            variant: 'outlined',
            icon: <CloseIcon />,
          },
        ]),
  ];

  return (
    <OrchestratorMessageCard
      headerIcon={commandConfig.emoji}
      headerLabel={`${commandConfig.label} Command`}
      gradient="linear-gradient(135deg, #667eea 0%, #764ba2 100%)"
      metadata={metadata}
      expandedContent={expandedContent}
      footerActions={footerActions}
    >
      <Typography variant="body2">{message.description}</Typography>
    </OrchestratorMessageCard>
  );
};

OrchestratorCommandMessage.propTypes = {
  message: PropTypes.shape({
    id: PropTypes.string,
    intent: PropTypes.string,
    description: PropTypes.string,
    parameters: PropTypes.object,
    modelHint: PropTypes.string,
  }).isRequired,
  onExecute: PropTypes.func,
  onCancel: PropTypes.func,
};

OrchestratorCommandMessage.defaultProps = {
  onExecute: null,
  onCancel: null,
};

export default OrchestratorCommandMessage;
