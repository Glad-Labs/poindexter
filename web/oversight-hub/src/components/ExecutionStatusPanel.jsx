/**
 * ExecutionStatusPanel
 *
 * Renders the execution lifecycle view inside WorkflowCanvas:
 * - Current execution ID, status chip, progress bar
 * - Per-phase results with output preview
 * - Final output indicator
 * - Recent execution history list
 *
 * Extracted from WorkflowCanvas.jsx (#295).
 */
import React from 'react';
import {
  Box,
  Button,
  Stack,
  Chip,
  Typography,
  Alert,
  Divider,
  LinearProgress,
  CircularProgress,
} from '@mui/material';

// ---- helpers (moved from WorkflowCanvas) ------------------------------------

const normalizeExecutionStatus = (status) =>
  typeof status === 'string' ? status.toLowerCase() : 'pending';

const getPhaseOutputPreview = (phaseResult = {}) => {
  const output = phaseResult?.output;

  if (typeof output === 'string') {
    return output;
  }

  if (output && typeof output === 'object') {
    if (typeof output.output === 'string') {
      return output.output;
    }
    if (typeof output.content === 'string') {
      return output.content;
    }
    if (typeof output.draft_content === 'string') {
      return output.draft_content;
    }
    return JSON.stringify(output);
  }

  if (phaseResult?.error) {
    return String(phaseResult.error);
  }

  return '';
};

const getPhaseExecutionMode = (phaseResult = {}) => {
  const outputMeta = phaseResult?.output?._phase_metadata;
  if (outputMeta && typeof outputMeta === 'object') {
    return outputMeta.execution_mode || null;
  }

  const metadata = phaseResult?.metadata;
  if (metadata && typeof metadata === 'object') {
    return metadata.execution_mode || null;
  }

  return null;
};

const statusColor = (s) => {
  if (s === 'completed') return 'success';
  if (s === 'failed') return 'error';
  if (s === 'cancelled') return 'warning';
  return 'default';
};

// ---- component --------------------------------------------------------------

/**
 * @param {object} props
 * @param {string|null}  props.executionId
 * @param {string|null}  props.executionStatus
 * @param {number}       props.executionProgress
 * @param {object}       props.executionResults       - { [phaseName]: phaseResult }
 * @param {any}          props.executionFinalOutput
 * @param {string}       props.executionErrorMessage
 * @param {string}       props.executionPollingError
 * @param {Array}        props.executionHistory
 * @param {boolean}      props.executionHistoryLoading
 * @param {string}       props.executionHistoryError
 * @param {Function}     props.onSelectExecution      - called with executionId string
 * @param {Function}     props.onRefreshHistory
 */
const ExecutionStatusPanel = ({
  executionId,
  executionStatus,
  executionProgress,
  executionResults = {},
  executionFinalOutput,
  executionErrorMessage,
  executionPollingError,
  executionHistory = [],
  executionHistoryLoading,
  executionHistoryError,
  onSelectExecution,
  onRefreshHistory,
}) => {
  if (!executionId) {
    return null;
  }

  return (
    <>
      <Divider />
      <Box>
        <Typography variant="subtitle2" gutterBottom>
          Execution Status
        </Typography>

        <Stack spacing={1}>
          <Typography variant="caption" color="text.secondary">
            Execution ID: {executionId}
          </Typography>

          <Stack direction="row" spacing={1} alignItems="center">
            <Chip
              size="small"
              label={executionStatus || 'pending'}
              color={statusColor(executionStatus)}
            />
            <Typography variant="caption" color="text.secondary">
              {executionProgress}%
            </Typography>
          </Stack>

          <LinearProgress
            variant="determinate"
            value={Math.max(0, Math.min(100, executionProgress || 0))}
          />

          {executionPollingError && (
            <Alert severity="warning">{executionPollingError}</Alert>
          )}

          {executionErrorMessage && (
            <Alert severity="error">{executionErrorMessage}</Alert>
          )}

          {Object.entries(executionResults || {}).map(
            ([phaseName, phaseResult]) => {
              const phaseStatus = normalizeExecutionStatus(phaseResult?.status);
              const preview = getPhaseOutputPreview(phaseResult);
              const executionMode = getPhaseExecutionMode(phaseResult);

              return (
                <Box
                  key={phaseName}
                  sx={{
                    border: '1px solid',
                    borderColor: 'divider',
                    borderRadius: 1,
                    p: 1,
                  }}
                >
                  <Stack
                    direction="row"
                    alignItems="center"
                    justifyContent="space-between"
                    spacing={1}
                  >
                    <Typography variant="caption" fontWeight={600}>
                      {phaseName}
                    </Typography>
                    <Chip
                      size="small"
                      label={phaseStatus || 'unknown'}
                      color={statusColor(phaseStatus)}
                    />
                  </Stack>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: 'block', mt: 0.5 }}
                  >
                    Execution mode: {executionMode || 'pending'}
                  </Typography>
                  {preview && (
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{
                        display: '-webkit-box',
                        WebkitLineClamp: 3,
                        WebkitBoxOrient: 'vertical',
                        overflow: 'hidden',
                        mt: 0.5,
                      }}
                    >
                      {preview}
                    </Typography>
                  )}
                </Box>
              );
            }
          )}

          {executionFinalOutput && (
            <Alert severity="info">
              Final output is available for this execution.
            </Alert>
          )}

          <Divider />

          {/* Recent execution history */}
          <Box>
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              spacing={1}
            >
              <Typography variant="subtitle2">Recent Executions</Typography>
              <Button
                size="small"
                onClick={onRefreshHistory}
                disabled={executionHistoryLoading}
              >
                Refresh
              </Button>
            </Stack>

            {executionHistoryLoading && (
              <Stack
                direction="row"
                spacing={1}
                alignItems="center"
                sx={{ mt: 1 }}
              >
                <CircularProgress size={14} />
                <Typography variant="caption" color="text.secondary">
                  Loading execution history...
                </Typography>
              </Stack>
            )}

            {executionHistoryError && (
              <Alert severity="warning" sx={{ mt: 1 }}>
                {executionHistoryError}
              </Alert>
            )}

            {!executionHistoryLoading &&
              !executionHistoryError &&
              executionHistory.length === 0 && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: 'block', mt: 1 }}
                >
                  No execution history yet.
                </Typography>
              )}

            <Stack spacing={0.5} sx={{ mt: 1 }}>
              {executionHistory.map((item) => {
                const itemStatus = normalizeExecutionStatus(
                  item?.execution_status
                );
                const itemId = item?.id;
                return (
                  <Box
                    key={itemId}
                    sx={{
                      border: '1px solid',
                      borderColor:
                        executionId === itemId ? 'primary.main' : 'divider',
                      borderRadius: 1,
                      px: 1,
                      py: 0.75,
                    }}
                  >
                    <Stack
                      direction="row"
                      alignItems="center"
                      justifyContent="space-between"
                      spacing={1}
                    >
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ flex: 1 }}
                      >
                        {itemId}
                      </Typography>
                      <Chip
                        size="small"
                        label={itemStatus}
                        color={statusColor(itemStatus)}
                      />
                    </Stack>
                    <Button
                      size="small"
                      sx={{ mt: 0.5, textTransform: 'none', p: 0 }}
                      onClick={() => onSelectExecution(itemId)}
                    >
                      View details
                    </Button>
                  </Box>
                );
              })}
            </Stack>
          </Box>
        </Stack>
      </Box>
    </>
  );
};

export default ExecutionStatusPanel;
