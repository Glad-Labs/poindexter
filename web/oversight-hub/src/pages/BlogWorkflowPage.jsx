import logger from '@/lib/logger';
import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Box,
  Card,
  CardContent,
  CardHeader,
  CircularProgress,
  Alert,
  Stack,
  Button,
  Grid,
  Typography,
  Chip,
  TextField,
  Select,
  MenuItem,
  FormControl,
  FormLabel,
  FormGroup,
  FormControlLabel,
  Checkbox,
  Stepper,
  Step,
  StepLabel,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  LinearProgress,
  IconButton,
} from '@mui/material';
import {
  PlayArrow as PlayIcon,
  Stop as StopIcon,
  Refresh as RefreshIcon,
} from '@mui/icons-material';
import apiClient from '../lib/apiClient';

/**
 * Blog Workflow Page
 *
 * Allows users to:
 * - Create blog workflows by selecting phases
 * - Execute workflows with custom parameters
 * - Monitor workflow progress in real-time
 * - View workflow results
 */
function BlogWorkflowPage() {
  const [activeStep, setActiveStep] = useState(0); // 0: Design, 1: Configure, 2: Execute, 3: Results
  const [availablePhases, setAvailablePhases] = useState([]);
  const [selectedPhases, setSelectedPhases] = useState({
    blog_generate_content: true,
    blog_quality_evaluation: true,
    blog_search_image: true,
    blog_create_post: true,
  });
  const [workflowConfig, setWorkflowConfig] = useState({
    topic: 'Artificial Intelligence in Healthcare',
    style: 'balanced',
    tone: 'professional',
    target_length: 1500,
  });
  const [selectedModel, setSelectedModel] = useState('ollama-mistral');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [executionId, setExecutionId] = useState(null);
  const [executionProgress, setExecutionProgress] = useState(null);
  const [executionResults, setExecutionResults] = useState(null);
  const [isExecuting, setIsExecuting] = useState(false);
  const [workflowHistory, setWorkflowHistory] = useState([]);
  const pollIntervalRef = useRef(null);
  const abortControllerRef = useRef(null);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
    };
  }, []);

  // Load available phases on mount
  useEffect(() => {
    loadAvailablePhases();
    loadWorkflowHistory();
  }, []);

  const loadAvailablePhases = async () => {
    try {
      setLoading(true);
      const phases = await apiClient.getAvailablePhases();
      // Filter for blog phases
      const blogPhases = phases.filter((p) => p.tags?.includes('blog'));
      setAvailablePhases(blogPhases);
    } catch (err) {
      setError(`Failed to load phases: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const loadWorkflowHistory = async () => {
    try {
      const executions = await apiClient.listWorkflowExecutions({ limit: 10 });
      setWorkflowHistory(executions.executions || executions || []);
    } catch (err) {
      logger.error('Failed to load workflow history:', err);
    }
  };

  const handlePhaseToggle = (phaseName) => {
    setSelectedPhases((prev) => ({
      ...prev,
      [phaseName]: !prev[phaseName],
    }));
  };

  const handleConfigChange = (field, value) => {
    setWorkflowConfig((prev) => ({
      ...prev,
      [field]: value,
    }));
  };

  const buildWorkflowDefinition = () => {
    const phases = Object.entries(selectedPhases)
      .filter(([, selected]) => selected)
      .map(([phaseName], index) => ({
        index,
        name: phaseName,
        user_inputs: getPhaseDefaults(phaseName),
      }));

    return {
      name: `Blog Post: ${workflowConfig.topic}`,
      description: `Auto-generated blog post workflow for "${workflowConfig.topic}"`,
      phases,
      metadata: {
        topic: workflowConfig.topic,
        style: workflowConfig.style,
        tone: workflowConfig.tone,
        target_length: workflowConfig.target_length,
        model: selectedModel, // ADD MODEL TO METADATA
      },
    };
  };

  const getPhaseDefaults = (phaseName) => {
    const defaults = {
      blog_generate_content: {
        topic: workflowConfig.topic,
        style: workflowConfig.style,
        tone: workflowConfig.tone,
        target_length: workflowConfig.target_length,
        tags: [workflowConfig.topic],
      },
      blog_quality_evaluation: {
        topic: workflowConfig.topic,
        evaluation_method: 'pattern-based',
      },
      blog_search_image: {
        topic: workflowConfig.topic,
        image_count: 1,
        orientation: 'landscape',
      },
      blog_create_post: {
        topic: workflowConfig.topic,
        publish: true,
      },
    };
    return defaults[phaseName] || {};
  };

  const executeWorkflow = useCallback(async () => {
    try {
      setError(null);
      setIsExecuting(true);

      const workflowDef = buildWorkflowDefinition();
      logger.log('Executing workflow:', workflowDef);

      const result = await apiClient.executeWorkflow(workflowDef);
      setExecutionId(result.execution_id || result.id);
      setActiveStep(2); // Move to Execute step

      // Start polling for progress
      pollWorkflowProgress(result.execution_id || result.id);
    } catch (err) {
      setError(`Failed to execute workflow: ${err.message}`);
      setIsExecuting(false);
    }
  }, [workflowConfig, selectedPhases]);

  const pollWorkflowProgress = async (execId) => {
    // Clear any existing poll
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    pollIntervalRef.current = setInterval(async () => {
      try {
        abortControllerRef.current = new AbortController();
        const progress = await apiClient.getWorkflowProgress(execId, {
          signal: abortControllerRef.current.signal,
        });
        setExecutionProgress(progress);

        // If workflow is complete, get results
        if (progress.status === 'completed' || progress.status === 'failed') {
          clearInterval(pollIntervalRef.current);
          pollIntervalRef.current = null;
          const results = await apiClient.getWorkflowResults(execId);
          setExecutionResults(results);
          setActiveStep(3); // Move to Results step
          setIsExecuting(false);
          await loadWorkflowHistory();
        }
      } catch (err) {
        if (err.name === 'AbortError') return;
        logger.error('Error polling progress:', err);
      }
    }, 2000);
  };

  const handleCancelExecution = async () => {
    try {
      await apiClient.cancelWorkflowExecution(executionId);
      setIsExecuting(false);
      setExecutionProgress({ ...executionProgress, status: 'cancelled' });
    } catch (err) {
      setError(`Failed to cancel workflow: ${err.message}`);
    }
  };

  const getStatusColor = (status) => {
    switch (status?.toLowerCase()) {
      case 'completed':
        return 'success';
      case 'failed':
        return 'error';
      case 'running':
      case 'in_progress':
        return 'info';
      case 'pending':
        return 'warning';
      default:
        return 'default';
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h4" sx={{ mb: 3 }}>
        Blog Post Workflow Builder
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      <Stepper activeStep={activeStep} sx={{ mb: 4 }}>
        <Step>
          <StepLabel>Design Workflow</StepLabel>
        </Step>
        <Step>
          <StepLabel>Configure Parameters</StepLabel>
        </Step>
        <Step>
          <StepLabel>Execute</StepLabel>
        </Step>
        <Step>
          <StepLabel>Results</StepLabel>
        </Step>
      </Stepper>

      {/* Step 0: Design Workflow */}
      {activeStep === 0 && (
        <Card>
          <CardHeader title="Select Workflow Phases" />
          <CardContent>
            <Typography variant="subtitle2" sx={{ mb: 2 }}>
              Available blog post generation phases:
            </Typography>
            <FormGroup>
              {availablePhases.map((phase) => (
                <FormControlLabel
                  key={phase.name}
                  control={
                    <Checkbox
                      checked={selectedPhases[phase.name] || false}
                      onChange={() => handlePhaseToggle(phase.name)}
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {phase.name}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {phase.description}
                      </Typography>
                    </Box>
                  }
                />
              ))}
            </FormGroup>
            <Box sx={{ mt: 3 }}>
              <Button
                variant="contained"
                onClick={() => setActiveStep(1)}
                disabled={Object.values(selectedPhases).every((v) => !v)}
              >
                Next: Configure Parameters
              </Button>
            </Box>
          </CardContent>
        </Card>
      )}

      {/* Step 1: Configure Parameters */}
      {activeStep === 1 && (
        <Card>
          <CardHeader title="Configure Workflow Parameters" />
          <CardContent>
            <Grid container spacing={2}>
              <Grid item xs={12}>
                <TextField
                  fullWidth
                  label="Blog Topic"
                  value={workflowConfig.topic}
                  onChange={(e) => handleConfigChange('topic', e.target.value)}
                  placeholder="e.g., Artificial Intelligence in Healthcare"
                  helperText="This will be used to generate and search for content"
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <FormControl fullWidth>
                  <FormLabel>Content Style</FormLabel>
                  <Select
                    value={workflowConfig.style}
                    onChange={(e) =>
                      handleConfigChange('style', e.target.value)
                    }
                  >
                    <MenuItem value="balanced">Balanced</MenuItem>
                    <MenuItem value="technical">Technical</MenuItem>
                    <MenuItem value="narrative">Narrative</MenuItem>
                    <MenuItem value="listicle">Listicle</MenuItem>
                    <MenuItem value="thought-leadership">
                      Thought Leadership
                    </MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} sm={6}>
                <FormControl fullWidth>
                  <FormLabel>Content Tone</FormLabel>
                  <Select
                    value={workflowConfig.tone}
                    onChange={(e) => handleConfigChange('tone', e.target.value)}
                  >
                    <MenuItem value="professional">Professional</MenuItem>
                    <MenuItem value="casual">Casual</MenuItem>
                    <MenuItem value="academic">Academic</MenuItem>
                    <MenuItem value="inspirational">Inspirational</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} sm={6}>
                <FormControl fullWidth>
                  <FormLabel>LLM Model</FormLabel>
                  <Select
                    value={selectedModel}
                    onChange={(e) => setSelectedModel(e.target.value)}
                  >
                    <MenuItem value="ollama-mistral">
                      Ollama Mistral (Local)
                    </MenuItem>
                    <MenuItem value="gpt-4-turbo">
                      GPT-4 Turbo (OpenAI)
                    </MenuItem>
                    <MenuItem value="claude-opus">
                      Claude Opus (Anthropic)
                    </MenuItem>
                    <MenuItem value="gemini-pro">Gemini Pro (Google)</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  type="number"
                  label="Target Word Count"
                  value={workflowConfig.target_length}
                  onChange={(e) =>
                    handleConfigChange(
                      'target_length',
                      parseInt(e.target.value)
                    )
                  }
                  inputProps={{ min: 500, max: 5000, step: 100 }}
                />
              </Grid>
            </Grid>
            <Box sx={{ mt: 3, display: 'flex', gap: 2 }}>
              <Button variant="outlined" onClick={() => setActiveStep(0)}>
                Back
              </Button>
              <Button
                variant="contained"
                onClick={() => setActiveStep(2)}
                disabled={!workflowConfig.topic.trim()}
              >
                Execute Workflow
              </Button>
            </Box>
          </CardContent>
        </Card>
      )}

      {/* Step 2: Execute */}
      {activeStep === 2 && (
        <Card>
          <CardHeader title="Workflow Execution" />
          <CardContent>
            {!executionId ? (
              <Box>
                <Typography variant="body2" sx={{ mb: 2 }}>
                  Ready to execute workflow with the following configuration:
                </Typography>
                <Stack spacing={1} sx={{ mb: 3 }}>
                  <Typography variant="caption">
                    <strong>Topic:</strong> {workflowConfig.topic}
                  </Typography>
                  <Typography variant="caption">
                    <strong>Phases:</strong>{' '}
                    {
                      Object.keys(selectedPhases).filter(
                        (p) => selectedPhases[p]
                      ).length
                    }{' '}
                    selected
                  </Typography>
                  <Typography variant="caption">
                    <strong>Style:</strong> {workflowConfig.style} |{' '}
                    <strong>Tone:</strong> {workflowConfig.tone}
                  </Typography>
                </Stack>
                <Button
                  variant="contained"
                  size="large"
                  startIcon={<PlayIcon />}
                  onClick={executeWorkflow}
                  disabled={isExecuting}
                >
                  {isExecuting ? 'Executing...' : 'Start Workflow'}
                </Button>
              </Box>
            ) : (
              <Box>
                <Typography variant="body2" sx={{ mb: 2 }}>
                  Execution ID: <code>{executionId}</code>
                </Typography>
                {executionProgress && (
                  <Stack spacing={2}>
                    <Box>
                      <Typography variant="caption">
                        Status:{' '}
                        <Chip
                          label={executionProgress.status}
                          size="small"
                          color={getStatusColor(executionProgress.status)}
                          variant="outlined"
                        />
                      </Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption">Progress</Typography>
                      <LinearProgress
                        variant="determinate"
                        value={executionProgress.progress_percent || 0}
                      />
                      <Typography variant="caption" color="text.secondary">
                        {executionProgress.phase_name} (
                        {executionProgress.current_phase} /{' '}
                        {executionProgress.total_phases})
                      </Typography>
                    </Box>
                  </Stack>
                )}
                {isExecuting && (
                  <Box sx={{ mt: 3 }}>
                    <Button
                      variant="outlined"
                      size="small"
                      startIcon={<StopIcon />}
                      onClick={handleCancelExecution}
                      color="error"
                    >
                      Cancel Workflow
                    </Button>
                  </Box>
                )}
              </Box>
            )}
          </CardContent>
        </Card>
      )}

      {/* Step 3: Results */}
      {activeStep === 3 && (
        <Stack spacing={3}>
          <Card>
            <CardHeader title="Workflow Results" />
            <CardContent>
              {executionResults ? (
                <Stack spacing={2}>
                  <Typography variant="subtitle2">
                    Status:{' '}
                    <Chip
                      label={executionResults.status || 'completed'}
                      color="success"
                    />
                  </Typography>

                  {/* Display phase results */}
                  {executionResults.phase_results && (
                    <Card variant="outlined">
                      <CardHeader title="Phase Results" />
                      <CardContent>
                        <TableContainer>
                          <Table size="small">
                            <TableHead>
                              <TableRow>
                                <TableCell>
                                  <strong>Phase</strong>
                                </TableCell>
                                <TableCell>
                                  <strong>Status</strong>
                                </TableCell>
                                <TableCell>
                                  <strong>Duration</strong>
                                </TableCell>
                              </TableRow>
                            </TableHead>
                            <TableBody>
                              {Object.entries(
                                executionResults.phase_results
                              ).map(([phaseName, result]) => (
                                <TableRow key={phaseName}>
                                  <TableCell>{phaseName}</TableCell>
                                  <TableCell>
                                    <Chip
                                      label={result.status || 'completed'}
                                      size="small"
                                      color={getStatusColor(result.status)}
                                    />
                                  </TableCell>
                                  <TableCell>
                                    {result.execution_time_ms || '-'} ms
                                  </TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </TableContainer>
                      </CardContent>
                    </Card>
                  )}

                  {/* Display blog post link if created */}
                  {executionResults.phase_results?.blog_create_post?.output
                    ?.url && (
                    <Card variant="outlined" sx={{ bgcolor: '#e8f5e9' }}>
                      <CardContent>
                        <Typography variant="subtitle2" color="success.main">
                          ✓ Blog post created successfully!
                        </Typography>
                        <Button
                          variant="text"
                          href={
                            executionResults.phase_results.blog_create_post
                              .output.url
                          }
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          View Post
                        </Button>
                      </CardContent>
                    </Card>
                  )}
                </Stack>
              ) : (
                <CircularProgress aria-label="Loading blog workflow" />
              )}
            </CardContent>
          </Card>

          <Box sx={{ display: 'flex', gap: 2 }}>
            <Button variant="outlined" onClick={() => setActiveStep(0)}>
              Create New Workflow
            </Button>
            <Button variant="outlined" onClick={loadWorkflowHistory}>
              <RefreshIcon sx={{ mr: 1 }} />
              Refresh History
            </Button>
          </Box>
        </Stack>
      )}

      {/* Workflow History */}
      {workflowHistory.length > 0 && (
        <Card sx={{ mt: 4 }}>
          <CardHeader title="Recent Workflow Executions" />
          <CardContent>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>
                      <strong>Date</strong>
                    </TableCell>
                    <TableCell>
                      <strong>Workflow</strong>
                    </TableCell>
                    <TableCell>
                      <strong>Status</strong>
                    </TableCell>
                    <TableCell>
                      <strong>Duration</strong>
                    </TableCell>
                    <TableCell>
                      <strong>Actions</strong>
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {workflowHistory.map((execution) => (
                    <TableRow key={execution.id || execution.execution_id}>
                      <TableCell>
                        {new Date(
                          execution.created_at || execution.timestamp
                        ).toLocaleDateString()}
                      </TableCell>
                      <TableCell>
                        {execution.name || 'Blog Post Workflow'}
                      </TableCell>
                      <TableCell>
                        <Chip
                          label={execution.status}
                          size="small"
                          color={getStatusColor(execution.status)}
                        />
                      </TableCell>
                      <TableCell>
                        {execution.duration_ms
                          ? `${Math.round(execution.duration_ms / 1000)}s`
                          : '-'}
                      </TableCell>
                      <TableCell>
                        <Button size="small" variant="text">
                          View Details
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </CardContent>
        </Card>
      )}
    </Box>
  );
}

export default BlogWorkflowPage;
