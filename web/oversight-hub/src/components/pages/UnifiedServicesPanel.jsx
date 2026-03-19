/**
 * Unified Services Panel
 *
 * Modern React dashboard with multiple tabs:
 * 1. WORKFLOW EDITOR: Visual workflow builder with drag-drop canvas
 * 2. My Workflows: List of user-created custom workflows
 * 3. Templates: Persistent CRUD for reusable workflow templates
 *
 * @component
 */

import React, { useState, useEffect, useRef } from 'react';
import phase4Client from '../../services/phase4Client';
import WorkflowCanvas from '../WorkflowCanvas';
import * as workflowBuilderService from '../../services/workflowBuilderService';
import { logError } from '../../services/errorLoggingService';
import {
  Box,
  Tabs,
  Tab,
  CircularProgress,
  LinearProgress,
  Alert,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Button,
  Chip,
  Stack,
  Typography,
  IconButton,
  Paper,
} from '@mui/material';
import { Play, Trash, FileText } from 'lucide-react';
import '../../styles/UnifiedServicesPanel.css';

const normalizePhaseName = (value) => {
  if (!value || typeof value !== 'string') return '';

  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
};

const formatPhaseLabel = (phaseName) => {
  if (!phaseName) return '';

  return phaseName
    .split('_')
    .filter(Boolean)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join(' ');
};

const getGranularPhasesForService = (service) => {
  const phases = Array.isArray(service?.phases) ? service.phases : [];
  const capabilities = Array.isArray(service?.capabilities)
    ? service.capabilities
    : [];

  return Array.from(
    new Set(
      [...phases, ...capabilities]
        .map((value) => normalizePhaseName(value))
        .filter(Boolean)
    )
  );
};

const buildCapabilityDerivedPhases = (services = []) => {
  const phaseMap = new Map();

  services.forEach((service) => {
    const capabilities = Array.isArray(service?.capabilities)
      ? service.capabilities
      : [];

    capabilities.forEach((capability) => {
      const phaseName = normalizePhaseName(capability);
      if (!phaseName) return;

      const existing = phaseMap.get(phaseName) || {
        name: phaseName,
        description: `Capability-derived phase: ${formatPhaseLabel(phaseName)}`,
        category: service?.category || 'general',
        default_timeout_seconds: 180,
        compatible_agents: [],
        capabilities: [],
        default_retries: 2,
        version: 'derived',
      };

      existing.compatible_agents = Array.from(
        new Set(
          [...(existing.compatible_agents || []), service?.name].filter(Boolean)
        )
      );
      existing.capabilities = Array.from(
        new Set([...(existing.capabilities || []), capability].filter(Boolean))
      );

      phaseMap.set(phaseName, existing);
    });
  });

  return Array.from(phaseMap.values());
};

/**
 * Main Unified Services Panel Component
 */
const UnifiedServicesPanel = () => {
  // Tabs state
  const [currentTab, setCurrentTab] = useState(0);
  const pollingCancelledRef = useRef(false);

  // Cancel polling on unmount
  useEffect(() => {
    return () => {
      pollingCancelledRef.current = true;
    };
  }, []);

  // Services tab state
  const [services, setServices] = useState([]);
  const [error, setError] = useState(null);
  const [healthStatus, setHealthStatus] = useState(null);

  // Workflow builder state
  const [availablePhases, setAvailablePhases] = useState([]);
  const [workflows, setWorkflows] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loadingWorkflows, setLoadingWorkflows] = useState(false);
  const [selectedWorkflow, setSelectedWorkflow] = useState(null);
  const [operationStatus, setOperationStatus] = useState(null);
  const [executionMonitor, setExecutionMonitor] = useState(null);

  // Fetch services on mount
  useEffect(() => {
    const fetchData = async () => {
      try {
        setError(null);

        // Get health check
        const health = await phase4Client.healthCheck();
        setHealthStatus(health);

        // Get service registry (using agents registry since services are indexed as agents)
        const response =
          await phase4Client.serviceRegistryClient.listServices();

        // Extract agents from response - { agents: [...], categories: {...}, phases: {...} }
        const agentsList = response.agents || [];

        // Transform agent data to service format
        const transformedServices = agentsList.map((agent) => {
          const baseService = {
            id: agent.name,
            name: agent.name,
            category: agent.category || 'general',
            description: agent.description || 'No description',
            phases: agent.phases || [],
            capabilities: agent.capabilities || [],
            version: agent.version || '1.0.0',
            actions: agent.actions || [],
          };

          return {
            ...baseService,
            granularPhases: getGranularPhasesForService(baseService),
          };
        });

        setServices(transformedServices);
      } catch (err) {
        const errorMessage = err.message || 'Failed to load services';
        setError(`Error loading services: ${errorMessage}`);
        logError(err, {
          severity: 'warning',
          customContext: { component: 'UnifiedServicesPanel' },
        });
      }
    };

    fetchData();
  }, []);

  // Load workflow data when tab changes
  useEffect(() => {
    if (currentTab >= 0 && currentTab <= 2) {
      loadWorkflowData();
    }
  }, [currentTab, services]);

  const loadWorkflowData = async () => {
    setLoadingWorkflows(true);
    try {
      // Load available phases
      const phasesRes = await workflowBuilderService.getAvailablePhases();
      const backendPhases = Array.isArray(phasesRes.phases)
        ? phasesRes.phases
        : [];
      const capabilityDerivedPhases = buildCapabilityDerivedPhases(services);

      const backendPhaseNames = new Set(
        backendPhases
          .map((phase) => normalizePhaseName(phase.name))
          .filter(Boolean)
      );
      const mergedPhases = [
        ...backendPhases,
        ...capabilityDerivedPhases.filter(
          (phase) => !backendPhaseNames.has(normalizePhaseName(phase.name))
        ),
      ];

      setAvailablePhases(mergedPhases);

      // Load user workflows + templates (persisted)
      const workflowsRes = await workflowBuilderService.listWorkflows({
        limit: 100,
        include_templates: true,
      });
      const allPersistedWorkflows = Array.isArray(workflowsRes.workflows)
        ? workflowsRes.workflows
        : [];
      setWorkflows(
        allPersistedWorkflows.filter((workflow) => !workflow.is_template)
      );
      setTemplates(
        allPersistedWorkflows.filter((workflow) => workflow.is_template)
      );

      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingWorkflows(false);
    }
  };

  const toCanvasWorkflow = (workflowLike) => {
    const getPhaseDefinition = (phaseInput) => {
      const phaseName =
        typeof phaseInput === 'string' ? phaseInput : phaseInput?.name;

      const phaseMeta = availablePhases.find(
        (phase) => phase.name === phaseName
      );

      return {
        name: phaseName,
        agent:
          phaseInput?.agent || phaseMeta?.compatible_agents?.[0] || phaseName,
        description:
          phaseInput?.description ||
          phaseMeta?.description ||
          `${phaseName} phase`,
        timeout_seconds:
          phaseInput?.timeout_seconds ||
          phaseMeta?.default_timeout_seconds ||
          300,
        max_retries: phaseInput?.max_retries || phaseMeta?.default_retries || 3,
        skip_on_error: phaseInput?.skip_on_error || false,
        required: phaseInput?.required !== false,
        quality_threshold: phaseInput?.quality_threshold,
        metadata: phaseInput?.metadata || {},
      };
    };

    const phases = Array.isArray(workflowLike?.phases)
      ? workflowLike.phases.map((phase) =>
          getPhaseDefinition(
            typeof phase === 'string' ? { name: phase } : phase
          )
        )
      : [];

    return {
      ...workflowLike,
      phases,
      isPersisted: Boolean(
        workflowLike?.isPersisted ||
        workflowLike?.created_at ||
        workflowLike?.updated_at
      ),
    };
  };

  const handleEditWorkflow = async (workflow) => {
    try {
      const fullWorkflow = await workflowBuilderService.getWorkflow(
        workflow.id
      );
      setSelectedWorkflow(toCanvasWorkflow(fullWorkflow));
      setCurrentTab(1);
    } catch (err) {
      setOperationStatus({
        severity: 'error',
        message: err.message || 'Failed to load workflow details',
      });
    }
  };

  const waitForExecutionTerminalStatus = async (executionId, workflowLabel) => {
    const terminalStatuses = new Set(['completed', 'failed', 'cancelled']);
    const maxAttempts = 120;
    const intervalMs = 2000;

    setExecutionMonitor({
      executionId,
      workflowLabel,
      status: 'pending',
      progressPercent: 0,
      completedPhases: 0,
      totalPhases: 0,
      currentPhase: null,
      phaseResults: {},
      lastUpdatedAt: null,
      errorMessage: null,
    });

    pollingCancelledRef.current = false;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      if (pollingCancelledRef.current) return;
      try {
        const statusResponse =
          await workflowBuilderService.getExecutionStatus(executionId);
        const status = String(statusResponse?.status || '').toLowerCase();

        setExecutionMonitor({
          executionId,
          workflowLabel,
          status,
          progressPercent: statusResponse?.progress_percent || 0,
          completedPhases: statusResponse?.completed_phases || 0,
          totalPhases: statusResponse?.total_phases || 0,
          currentPhase: statusResponse?.current_phase || null,
          phaseResults: statusResponse?.phase_results || {},
          lastUpdatedAt: statusResponse?.last_updated_at || null,
          errorMessage: statusResponse?.error_message || null,
        });

        if (terminalStatuses.has(status)) {
          if (status === 'completed') {
            setOperationStatus({
              severity: 'success',
              message: `${workflowLabel} completed successfully.`,
            });
          } else {
            const errorMessage = statusResponse?.error_message
              ? ` (${statusResponse.error_message})`
              : '';
            setOperationStatus({
              severity: 'error',
              message: `${workflowLabel} ${status}.${errorMessage}`,
            });
          }
          return;
        }
      } catch (pollError) {
        if (attempt >= 3) {
          break;
        }
      }

      await new Promise((resolve) => setTimeout(resolve, intervalMs));
    }

    setOperationStatus({
      severity: 'info',
      message: `${workflowLabel} is still running. Execution ID: ${executionId}`,
    });
  };

  const handleExecuteWorkflow = async (workflow) => {
    try {
      const response = await workflowBuilderService.executeWorkflow(
        workflow.id,
        {
          topic: workflow.name,
          source: 'oversight_hub',
        }
      );

      setOperationStatus({
        severity: 'success',
        message: `Workflow execution started (${response.execution_id || 'queued'})`,
      });

      if (response.execution_id) {
        void waitForExecutionTerminalStatus(
          response.execution_id,
          `Workflow \"${workflow.name}\"`
        );
      }
    } catch (err) {
      setOperationStatus({
        severity: 'error',
        message: err.message || 'Failed to execute workflow',
      });
    }
  };

  const handleViewTemplate = (template) => {
    setSelectedWorkflow(
      toCanvasWorkflow({
        id: template.id,
        name: template.name,
        description: template.description,
        phases: template.phases || [],
        is_template: true,
        tags: template.tags || [],
        isPersisted: Boolean(template.created_at || template.updated_at),
      })
    );
    setCurrentTab(0);
  };

  const handleExecuteTemplate = async (template) => {
    try {
      const execution = await workflowBuilderService.executeWorkflow(
        template.id,
        {
          topic: template.name,
          source_template: template.id,
        }
      );

      setOperationStatus({
        severity: 'success',
        message: `Template execution started (${execution.execution_id || 'queued'})`,
      });

      if (execution.execution_id) {
        void waitForExecutionTerminalStatus(
          execution.execution_id,
          `Template \"${template.name}\"`
        );
      }

      await loadWorkflowData();
      setCurrentTab(1);
    } catch (err) {
      setOperationStatus({
        severity: 'error',
        message: err.message || `Failed to execute template ${template.name}`,
      });
    }
  };

  const handleDeleteTemplate = async (templateId) => {
    if (!window.confirm('Are you sure you want to delete this template?'))
      return;

    try {
      await workflowBuilderService.deleteWorkflow(templateId);
      setTemplates((existing) =>
        existing.filter((template) => template.id !== templateId)
      );
      setOperationStatus({
        severity: 'success',
        message: 'Template deleted successfully',
      });
    } catch (err) {
      setOperationStatus({
        severity: 'error',
        message: err.message || 'Failed to delete template',
      });
    }
  };

  const handleCreateTemplate = () => {
    setSelectedWorkflow(
      toCanvasWorkflow({
        name: '',
        description: '',
        phases: [],
        tags: ['template'],
        is_template: true,
      })
    );
    setCurrentTab(0);
  };

  const handleDeleteWorkflow = async (workflowId) => {
    if (!window.confirm('Are you sure you want to delete this workflow?'))
      return;

    try {
      await workflowBuilderService.deleteWorkflow(workflowId);
      setWorkflows((w) => w.filter((wf) => wf.id !== workflowId));
    } catch (err) {
      setError(err.message);
    }
  };

  const handleWorkflowSaved = (newWorkflow) => {
    loadWorkflowData();
    setCurrentTab(newWorkflow?.is_template ? 2 : 1);
  };

  const recoverActiveExecution = async (workflowId, workflowLabel) => {
    try {
      const response = await workflowBuilderService.listExecutions(workflowId, {
        limit: 10,
        offset: 0,
      });

      const executions = Array.isArray(response?.executions)
        ? response.executions
        : [];

      const activeExecution = executions.find((execution) =>
        ['pending', 'running'].includes(
          String(execution?.execution_status || '').toLowerCase()
        )
      );

      if (activeExecution?.id) {
        void waitForExecutionTerminalStatus(activeExecution.id, workflowLabel);
      }
    } catch (error) {
      // best-effort recovery only
    }
  };

  useEffect(() => {
    if (executionMonitor) return;
    if (!Array.isArray(workflows) || workflows.length === 0) return;

    const persistedWorkflow = workflows.find((workflow) => workflow?.id);
    if (!persistedWorkflow?.id) return;

    void recoverActiveExecution(
      persistedWorkflow.id,
      `Workflow \"${persistedWorkflow.name || persistedWorkflow.id}\"`
    );
  }, [workflows, executionMonitor]);

  const handleTabChange = (event, newValue) => {
    setCurrentTab(newValue);
  };

  return (
    <div className="unified-services-panel">
      {/* Tab Navigation */}
      <div className="tab-navigation">
        <Tabs
          value={currentTab}
          onChange={handleTabChange}
          aria-label="unified panel tabs"
          sx={{ borderBottom: '1px solid #e0e0e0' }}
        >
          <Tab label="WORKFLOW EDITOR" id="tab-0" />
          <Tab label="My Workflows" id="tab-1" />
          <Tab label="Templates" id="tab-2" />
        </Tabs>
      </div>

      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ m: 2 }}>
          {error}
        </Alert>
      )}

      {operationStatus && (
        <Alert
          severity={operationStatus.severity}
          onClose={() => setOperationStatus(null)}
          sx={{ m: 2 }}
        >
          {operationStatus.message}
        </Alert>
      )}

      {executionMonitor && (
        <Paper sx={{ m: 2, p: 2, borderRadius: 2 }}>
          <Stack spacing={1.5}>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
            >
              <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                {executionMonitor.workflowLabel}
              </Typography>
              <Chip
                size="small"
                color={
                  executionMonitor.status === 'completed'
                    ? 'success'
                    : executionMonitor.status === 'failed' ||
                        executionMonitor.status === 'cancelled'
                      ? 'error'
                      : 'warning'
                }
                label={executionMonitor.status || 'pending'}
              />
            </Stack>

            <Box>
              <LinearProgress
                variant="determinate"
                value={Math.min(
                  100,
                  Math.max(0, executionMonitor.progressPercent || 0)
                )}
                sx={{ height: 8, borderRadius: 8 }}
              />
              <Stack
                direction="row"
                justifyContent="space-between"
                sx={{ mt: 0.75 }}
              >
                <Typography variant="caption" color="textSecondary">
                  {executionMonitor.completedPhases || 0}/
                  {executionMonitor.totalPhases || 0} phases completed
                </Typography>
                <Typography variant="caption" color="textSecondary">
                  {executionMonitor.progressPercent || 0}%
                </Typography>
              </Stack>
            </Box>

            {executionMonitor.currentPhase && (
              <Typography variant="body2" color="textSecondary">
                Current phase: {executionMonitor.currentPhase}
              </Typography>
            )}

            {executionMonitor.errorMessage && (
              <Alert severity="error" sx={{ mt: 0.5 }}>
                {executionMonitor.errorMessage}
              </Alert>
            )}

            {Object.keys(executionMonitor.phaseResults || {}).length > 0 && (
              <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
                {Object.entries(executionMonitor.phaseResults).map(
                  ([phaseName, phaseDetails]) => {
                    const phaseStatus = String(
                      phaseDetails?.status || 'pending'
                    ).toLowerCase();
                    const phaseColor =
                      phaseStatus === 'completed'
                        ? 'success'
                        : phaseStatus === 'failed'
                          ? 'error'
                          : phaseStatus === 'running'
                            ? 'warning'
                            : 'default';

                    return (
                      <Chip
                        key={phaseName}
                        size="small"
                        color={phaseColor}
                        label={`${phaseName}: ${phaseStatus}`}
                        sx={{ mt: 0.5 }}
                      />
                    );
                  }
                )}
              </Stack>
            )}
          </Stack>
        </Paper>
      )}

      {/* Tab 0: Workflow Editor */}
      {currentTab === 0 && (
        <Box sx={{ p: 3 }}>
          {healthStatus && !healthStatus.healthy && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              Service issues detected. Saving and executing workflows may be
              degraded.
            </Alert>
          )}
          {loadingWorkflows ? (
            <Box
              sx={{
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'center',
                minHeight: 600,
              }}
              role="status"
              aria-label="Loading workflows"
            >
              <CircularProgress />
            </Box>
          ) : availablePhases.length > 0 ? (
            <WorkflowCanvas
              availablePhases={availablePhases}
              onSave={handleWorkflowSaved}
              workflow={selectedWorkflow}
            />
          ) : (
            <Alert severity="warning">Loading available phases...</Alert>
          )}
        </Box>
      )}

      {/* Tab 1: My Workflows */}
      {currentTab === 1 && (
        <Box sx={{ p: 3 }}>
          {workflows.length === 0 ? (
            <Typography color="textSecondary" align="center" sx={{ py: 4 }}>
              No custom workflows yet. Create one in the &quot;WORKFLOW
              EDITOR&quot; tab.
            </Typography>
          ) : (
            <TableContainer>
              <Table>
                <TableHead>
                  <TableRow sx={{ backgroundColor: '#f5f5f5' }}>
                    <TableCell>Name</TableCell>
                    <TableCell>Description</TableCell>
                    <TableCell align="center">Phases</TableCell>
                    <TableCell align="right">Created</TableCell>
                    <TableCell align="center">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {workflows.map((workflow) => (
                    <TableRow key={workflow.id}>
                      <TableCell>
                        <Typography
                          variant="subtitle2"
                          sx={{ fontWeight: 600 }}
                        >
                          {workflow.name}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" color="textSecondary">
                          {workflow.description}
                        </Typography>
                      </TableCell>
                      <TableCell align="center">
                        <Chip
                          label={
                            workflow.phase_count || workflow.phases?.length || 0
                          }
                          size="small"
                        />
                      </TableCell>
                      <TableCell align="right">
                        <Typography variant="body2" color="textSecondary">
                          {new Date(workflow.created_at).toLocaleDateString()}
                        </Typography>
                      </TableCell>
                      <TableCell align="center">
                        <Stack
                          direction="row"
                          spacing={0.5}
                          justifyContent="center"
                        >
                          <IconButton
                            size="small"
                            title="Edit"
                            onClick={() => handleEditWorkflow(workflow)}
                          >
                            <FileText size={18} />
                          </IconButton>
                          <IconButton
                            size="small"
                            title="Execute"
                            onClick={() => handleExecuteWorkflow(workflow)}
                          >
                            <Play size={18} />
                          </IconButton>
                          <IconButton
                            size="small"
                            title="Delete"
                            color="error"
                            onClick={() => handleDeleteWorkflow(workflow.id)}
                          >
                            <Trash size={18} />
                          </IconButton>
                        </Stack>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Box>
      )}

      {/* Tab 2: Templates */}
      {currentTab === 2 && (
        <Box sx={{ p: 3 }}>
          <Box sx={{ mb: 2, display: 'flex', justifyContent: 'space-between' }}>
            <Typography variant="h6">Template Library</Typography>
            <Button variant="contained" onClick={handleCreateTemplate}>
              Create Template
            </Button>
          </Box>
          <Stack spacing={2}>
            {templates.length === 0 && (
              <Alert severity="info">
                No persisted templates yet. Use "Create Template" to build one
                in the editor.
              </Alert>
            )}

            {templates.map((template) => (
              <Paper
                key={template.id}
                sx={{
                  p: 2,
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  '&:hover': { boxShadow: 3 },
                }}
              >
                <Box sx={{ flex: 1 }}>
                  <Typography variant="h6">{template.name}</Typography>
                  <Typography variant="body2" color="textSecondary">
                    {template.description}
                  </Typography>
                  <Box sx={{ mt: 1 }}>
                    <Chip
                      label={`${template.phase_count} phases`}
                      size="small"
                    />
                  </Box>
                </Box>
                <Stack direction="row" spacing={1}>
                  <Button
                    variant="contained"
                    size="small"
                    onClick={() => handleViewTemplate(template)}
                  >
                    Edit
                  </Button>
                  <Button
                    variant="contained"
                    color="success"
                    size="small"
                    onClick={() => handleExecuteTemplate(template)}
                  >
                    Execute
                  </Button>
                  <Button
                    variant="outlined"
                    color="error"
                    size="small"
                    onClick={() => handleDeleteTemplate(template.id)}
                  >
                    Delete
                  </Button>
                </Stack>
              </Paper>
            ))}
          </Stack>
        </Box>
      )}
    </div>
  );
};

export default UnifiedServicesPanel;
