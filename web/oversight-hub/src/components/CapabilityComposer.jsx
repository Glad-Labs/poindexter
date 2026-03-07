import logger from '@/lib/logger';
/**
 * Capability Composer Component
 *
 * Visual builder for composing capability-based tasks.
 * Users can:
 * - Browse available capabilities
 * - Drag capabilities onto canvas
 * - Configure inputs (with variable references)
 * - Set output keys for data flow
 * - Execute tasks and view results
 */

import React, { useState, useEffect } from 'react';
import {
  Box,
  Card,
  CardContent,
  CardHeader,
  Button,
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Chip,
  Stack,
  Typography,
  Alert,
  CircularProgress,
  Paper,
  IconButton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  Tab,
} from '@mui/material';
import { Trash2, Play, Settings } from 'lucide-react';
import CapabilityTasksService from '../services/capabilityTasksService';
import NaturalLanguageTaskComposer from './NaturalLanguageTaskComposer';

/**
 * Capability Card - Shows a single capability
 */
const CapabilityCard = ({ capability, onAdd }) => {
  const [showDetails, setShowDetails] = useState(false);

  return (
    <>
      <Card
        sx={{
          cursor: 'pointer',
          transition: 'all 0.2s',
          '&:hover': { boxShadow: 4, transform: 'translateY(-2px)' },
        }}
        onClick={() => setShowDetails(true)}
      >
        <CardHeader
          title={capability.name}
          subheader={capability.description}
          titleTypographyProps={{ variant: 'h6', fontSize: 14 }}
          subheaderTypographyProps={{ variant: 'caption' }}
          sx={{ pb: 1 }}
        />
        <CardContent sx={{ pt: 0 }}>
          <Stack direction="row" spacing={0.5} sx={{ mb: 1, minHeight: 24 }}>
            {capability.tags?.slice(0, 2).map((tag) => (
              <Chip key={tag} label={tag} size="small" variant="outlined" />
            ))}
          </Stack>
          <Typography variant="caption" color="textSecondary">
            {capability.cost_tier}
          </Typography>
        </CardContent>
      </Card>

      {/* Details Dialog */}
      <Dialog
        open={showDetails}
        onClose={() => setShowDetails(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>{capability.name}</DialogTitle>
        <DialogContent sx={{ pt: 2 }}>
          <Typography variant="body2" sx={{ mb: 2 }}>
            {capability.description}
          </Typography>

          <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
            Inputs
          </Typography>
          {capability.input_schema?.parameters?.length > 0 ? (
            <Box sx={{ mb: 2 }}>
              {capability.input_schema.parameters.map((param) => (
                <Box key={param.name} sx={{ mb: 1, fontSize: 12 }}>
                  <strong>{param.name}</strong>
                  {param.required && <span style={{ color: 'red' }}>*</span>}
                  <br />
                  <span style={{ color: '#666' }}>
                    {param.type}{' '}
                    {param.default ? `(default: ${param.default})` : ''}
                  </span>
                  <br />
                  <span style={{ color: '#999' }}>{param.description}</span>
                </Box>
              ))}
            </Box>
          ) : (
            <Typography variant="caption" color="textSecondary">
              No inputs
            </Typography>
          )}

          <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
            Output
          </Typography>
          <Typography variant="caption" color="textSecondary">
            {capability.output_schema?.description || 'Returns result object'}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setShowDetails(false)}>Close</Button>
          <Button
            variant="contained"
            onClick={() => {
              onAdd(capability);
              setShowDetails(false);
            }}
          >
            Add to Task
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

/**
 * Step Editor - Edit a single step in the task
 */
const StepEditor = ({
  step,
  stepIndex,
  allCapabilities,
  onUpdate,
  onRemove,
  previousOutputs,
}) => {
  const [editing, setEditing] = useState(false);
  const [inputs, setInputs] = useState(step.inputs || {});
  const [outputKey, setOutputKey] = useState(
    step.output_key || `output_${stepIndex}`
  );
  const [capability, setCapability] = useState(
    allCapabilities.find((c) => c.name === step.capability_name)
  );

  const handleSave = () => {
    onUpdate(stepIndex, {
      capability_name: capability.name,
      inputs,
      output_key: outputKey,
      order: stepIndex,
    });
    setEditing(false);
  };

  if (!editing) {
    return (
      <TableRow sx={{ '&:hover': { backgroundColor: '#f5f5f5' } }}>
        <TableCell align="center">{stepIndex + 1}</TableCell>
        <TableCell>{capability?.name || 'Unknown'}</TableCell>
        <TableCell>{outputKey}</TableCell>
        <TableCell align="right">
          <IconButton size="small" onClick={() => setEditing(true)}>
            <Settings size={18} />
          </IconButton>
          <IconButton size="small" onClick={() => onRemove(stepIndex)}>
            <Trash2 size={18} />
          </IconButton>
        </TableCell>
      </TableRow>
    );
  }

  // Editing mode
  return (
    <TableRow>
      <TableCell colSpan={4}>
        <Box sx={{ p: 2 }}>
          <Typography variant="subtitle2" sx={{ mb: 2 }}>
            Edit Step {stepIndex + 1}
          </Typography>

          {/* Capability selector */}
          <TextField
            select
            label="Capability"
            value={capability?.name || ''}
            onChange={(e) => {
              const cap = allCapabilities.find(
                (c) => c.name === e.target.value
              );
              setCapability(cap);
              setInputs({}); // Reset inputs
            }}
            fullWidth
            sx={{ mb: 2 }}
            SelectProps={{
              native: true,
            }}
          >
            <option value=""></option>
            {allCapabilities.map((cap) => (
              <option key={cap.name} value={cap.name}>
                {cap.name}
              </option>
            ))}
          </TextField>

          {/* Input fields */}
          {capability?.input_schema?.parameters?.map((param) => (
            <Box key={param.name} sx={{ mb: 2 }}>
              <TextField
                label={param.name}
                value={inputs[param.name] || ''}
                onChange={(e) => {
                  setInputs({
                    ...inputs,
                    [param.name]: e.target.value,
                  });
                }}
                fullWidth
                helperText={`${param.description} (type: ${param.type})`}
                placeholder={
                  param.default ? `default: ${param.default}` : undefined
                }
              />

              {/* Quick reference to previous outputs */}
              {stepIndex > 0 && (
                <Typography
                  variant="caption"
                  sx={{ display: 'block', mt: 1, color: '#666' }}
                >
                  Available refs: {previousOutputs.join(', ')}
                </Typography>
              )}
            </Box>
          ))}

          {/* Output key */}
          <TextField
            label="Output Key (for next steps)"
            value={outputKey}
            onChange={(e) => setOutputKey(e.target.value)}
            fullWidth
            sx={{ mb: 2 }}
            helperText="Other steps can reference this as $output_key"
          />

          {/* Save/Cancel */}
          <Stack direction="row" spacing={1} justifyContent="flex-end">
            <Button variant="outlined" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button variant="contained" onClick={handleSave}>
              Save Step
            </Button>
          </Stack>
        </Box>
      </TableCell>
    </TableRow>
  );
};

/**
 * Main Capability Composer Component
 */
export default function CapabilityComposer() {
  // Tabs
  const [currentTab, setCurrentTab] = useState(0);

  // State
  const [capabilities, setCapabilities] = useState([]);
  const [loadingCapabilities, setLoadingCapabilities] = useState(true);
  const [steps, setSteps] = useState([]);
  const [taskName, setTaskName] = useState('');
  const [taskDescription, setTaskDescription] = useState('');
  const [taskTags, setTaskTags] = useState([]);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [executing, setExecuting] = useState(false);
  const [executionResult, setExecutionResult] = useState(null);
  const [showResults, setShowResults] = useState(false);
  const [tagInput, setTagInput] = useState('');

  // Load capabilities on mount
  useEffect(() => {
    const loadCapabilities = async () => {
      try {
        setLoadingCapabilities(true);
        const response = await CapabilityTasksService.listCapabilities();
        setCapabilities(response.capabilities || []);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoadingCapabilities(false);
      }
    };

    loadCapabilities();
  }, []);

  // Handlers
  const handleAddCapability = (capability) => {
    const newStep = {
      capability_name: capability.name,
      inputs: {},
      output_key: `${capability.name}_output`,
      order: steps.length,
    };
    setSteps([...steps, newStep]);
  };

  const handleUpdateStep = (stepIndex, updatedStep) => {
    const newSteps = [...steps];
    newSteps[stepIndex] = updatedStep;
    setSteps(newSteps);
  };

  const handleRemoveStep = (stepIndex) => {
    setSteps(steps.filter((_, i) => i !== stepIndex));
  };

  const handleAddTag = () => {
    if (tagInput.trim() && !taskTags.includes(tagInput.trim())) {
      setTaskTags([...taskTags, tagInput.trim()]);
      setTagInput('');
    }
  };

  const handleRemoveTag = (tag) => {
    setTaskTags(taskTags.filter((t) => t !== tag));
  };

  const handleSaveTask = async () => {
    try {
      setError(null);
      if (!taskName.trim()) {
        setError('Task name is required');
        return;
      }
      if (steps.length === 0) {
        setError('At least one step is required');
        return;
      }

      const task = await CapabilityTasksService.createTask(
        taskName,
        taskDescription,
        steps,
        taskTags
      );

      setSuccess(`Task "${taskName}" created successfully!`);
      setExecutionResult(task);

      // Reset form
      setTimeout(() => {
        setTaskName('');
        setTaskDescription('');
        setSteps([]);
        setTaskTags([]);
        setSuccess(null);
      }, 2000);
    } catch (err) {
      setError(err.message);
    }
  };

  const handleExecuteTask = async () => {
    try {
      setError(null);
      setExecuting(true);

      const task = await CapabilityTasksService.createTask(
        taskName,
        taskDescription,
        steps,
        taskTags
      );

      const execution = await CapabilityTasksService.executeTask(task.id);
      setExecutionResult(execution);
      setShowResults(true);
      setSuccess(`Task executed! Execution ID: ${execution.execution_id}`);
    } catch (err) {
      setError(err.message);
    } finally {
      setExecuting(false);
    }
  };

  const previousOutputs = steps
    .map((_, i) => `$${steps[i].output_key}`)
    .slice(0, -1);

  return (
    <Box sx={{ p: 3, maxWidth: 1200, mx: 'auto' }}>
      {/* Header */}
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" sx={{ fontWeight: 600, mb: 1 }}>
          Capability Composer
        </Typography>
        <Typography variant="body2" color="textSecondary">
          Chain capabilities together to create powerful automated tasks
        </Typography>
      </Box>

      {/* Tab Navigation */}
      <Tabs
        value={currentTab}
        onChange={(_, val) => setCurrentTab(val)}
        sx={{ mb: 3, borderBottom: '1px solid #e0e0e0' }}
      >
        <Tab label="Manual Composition" id="tab-0" />
        <Tab label="Natural Language" id="tab-1" />
      </Tabs>

      {/* Tab 0: Manual Composition */}
      {currentTab === 0 && (
        <>
          {error && (
            <Alert
              severity="error"
              onClose={() => setError(null)}
              sx={{ mb: 2 }}
            >
              {error}
            </Alert>
          )}

          {success && (
            <Alert
              severity="success"
              onClose={() => setSuccess(null)}
              sx={{ mb: 2 }}
            >
              {success}
            </Alert>
          )}

          {/* Main Content */}
          {loadingCapabilities ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
              <CircularProgress />
            </Box>
          ) : (
            <Box
              sx={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 3 }}
            >
              {/* Left: Available Capabilities */}
              <Box>
                <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 2 }}>
                  Available Capabilities ({capabilities.length})
                </Typography>
                <Box
                  sx={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 2,
                    maxHeight: 'calc(100vh - 300px)',
                    overflowY: 'auto',
                  }}
                >
                  {capabilities.map((cap) => (
                    <CapabilityCard
                      key={cap.name}
                      capability={cap}
                      onAdd={handleAddCapability}
                    />
                  ))}
                </Box>
              </Box>

              {/* Right: Task Builder */}
              <Box>
                {/* Task Info */}
                <Card sx={{ mb: 3 }}>
                  <CardHeader title="Task Details" />
                  <CardContent>
                    <TextField
                      label="Task Name"
                      value={taskName}
                      onChange={(e) => setTaskName(e.target.value)}
                      fullWidth
                      sx={{ mb: 2 }}
                      required
                    />

                    <TextField
                      label="Description"
                      value={taskDescription}
                      onChange={(e) => setTaskDescription(e.target.value)}
                      fullWidth
                      multiline
                      rows={2}
                      sx={{ mb: 2 }}
                    />

                    {/* Tags */}
                    <Box sx={{ mb: 2 }}>
                      <Typography
                        variant="caption"
                        sx={{ display: 'block', fontWeight: 600, mb: 1 }}
                      >
                        Tags
                      </Typography>
                      <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
                        {taskTags.map((tag) => (
                          <Chip
                            key={tag}
                            label={tag}
                            onDelete={() => handleRemoveTag(tag)}
                            size="small"
                          />
                        ))}
                      </Stack>
                      <Stack direction="row" spacing={1}>
                        <TextField
                          size="small"
                          placeholder="Add tag..."
                          value={tagInput}
                          onChange={(e) => setTagInput(e.target.value)}
                          onKeyPress={(e) => {
                            if (e.key === 'Enter') {
                              handleAddTag();
                            }
                          }}
                          sx={{ flex: 1 }}
                        />
                        <Button size="small" onClick={handleAddTag}>
                          Add
                        </Button>
                      </Stack>
                    </Box>
                  </CardContent>
                </Card>

                {/* Steps */}
                <Card>
                  <CardHeader
                    title={`Steps (${steps.length})`}
                    action={
                      steps.length > 0 ? (
                        <Stack direction="row" spacing={1}>
                          <Button
                            size="small"
                            startIcon={<Play size={16} />}
                            onClick={handleExecuteTask}
                            disabled={executing || !taskName.trim()}
                            variant="contained"
                          >
                            {executing ? 'Executing...' : 'Execute'}
                          </Button>
                          <Button
                            size="small"
                            onClick={handleSaveTask}
                            disabled={!taskName.trim()}
                          >
                            Save Task
                          </Button>
                        </Stack>
                      ) : null
                    }
                  />
                  <CardContent>
                    {steps.length === 0 ? (
                      <Typography
                        variant="body2"
                        color="textSecondary"
                        align="center"
                      >
                        Click on a capability to add it to your task
                      </Typography>
                    ) : (
                      <TableContainer>
                        <Table size="small">
                          <TableHead>
                            <TableRow sx={{ backgroundColor: '#f5f5f5' }}>
                              <TableCell align="center" width={50}>
                                #
                              </TableCell>
                              <TableCell>Capability</TableCell>
                              <TableCell>Output</TableCell>
                              <TableCell align="right" width={100}>
                                Actions
                              </TableCell>
                            </TableRow>
                          </TableHead>
                          <TableBody>
                            {steps.map((step, idx) => (
                              <StepEditor
                                key={idx}
                                step={step}
                                stepIndex={idx}
                                allCapabilities={capabilities}
                                onUpdate={handleUpdateStep}
                                onRemove={handleRemoveStep}
                                previousOutputs={previousOutputs.slice(0, idx)}
                              />
                            ))}
                          </TableBody>
                        </Table>
                      </TableContainer>
                    )}
                  </CardContent>
                </Card>

                {/* Execution Results */}
                {showResults && executionResult && (
                  <Card sx={{ mt: 3 }}>
                    <CardHeader
                      title="Execution Results"
                      subheader={`Status: ${executionResult.status}`}
                    />
                    <CardContent>
                      <Typography
                        variant="caption"
                        sx={{ display: 'block', mb: 2 }}
                      >
                        Duration:{' '}
                        {executionResult.total_duration_ms?.toFixed(0)}ms |
                        Progress: {executionResult.progress_percent}%
                      </Typography>

                      {executionResult.step_results && (
                        <Box>
                          {executionResult.step_results.map((result, idx) => (
                            <Card
                              key={idx}
                              variant="outlined"
                              sx={{ mb: 1, p: 1.5 }}
                            >
                              <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
                                <Chip label={`Step ${idx + 1}`} size="small" />
                                <Chip
                                  label={result.capability_name}
                                  size="small"
                                  variant="outlined"
                                />
                                <Chip
                                  label={result.status}
                                  size="small"
                                  color={
                                    result.status === 'completed'
                                      ? 'success'
                                      : 'error'
                                  }
                                  variant="outlined"
                                />
                                <Typography
                                  variant="caption"
                                  sx={{ ml: 'auto' }}
                                >
                                  {result.duration_ms?.toFixed(0)}ms
                                </Typography>
                              </Stack>
                              {result.error && (
                                <Typography
                                  variant="caption"
                                  sx={{ color: 'red' }}
                                >
                                  Error: {result.error}
                                </Typography>
                              )}
                            </Card>
                          ))}
                        </Box>
                      )}

                      {executionResult.final_outputs && (
                        <Box sx={{ mt: 2 }}>
                          <Typography
                            variant="subtitle2"
                            sx={{ fontWeight: 600, mb: 1 }}
                          >
                            Final Outputs
                          </Typography>
                          <Paper
                            sx={{
                              p: 2,
                              backgroundColor: '#f5f5f5',
                              maxHeight: 300,
                              overflowY: 'auto',
                            }}
                          >
                            <pre style={{ margin: 0, fontSize: 12 }}>
                              {JSON.stringify(
                                executionResult.final_outputs,
                                null,
                                2
                              )}
                            </pre>
                          </Paper>
                        </Box>
                      )}
                    </CardContent>
                  </Card>
                )}
              </Box>
            </Box>
          )}
        </>
      )}

      {/* Tab 1: Natural Language Composition */}
      {currentTab === 1 && (
        <NaturalLanguageTaskComposer
          onTaskComposed={(task) => {
            logger.log('Task composed from NL:', task);
          }}
          onTaskExecuted={(result) => {
            logger.log('Task executed from NL:', result);
          }}
        />
      )}
    </Box>
  );
}
