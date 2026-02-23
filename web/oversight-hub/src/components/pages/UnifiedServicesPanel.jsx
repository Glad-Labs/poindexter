/**
 * Unified Services Panel (Consolidated)
 *
 * Accordion-based dashboard consolidating workflow creation, monitoring, and service discovery:
 * 
 * 1. Workflow Studio (Collapsed by default)
 *    - Create Custom Workflow: Visual workflow builder with drag-drop canvas
 *    - My Workflows: List of user-created custom workflows
 *    - Templates: Pre-built workflow templates
 *    - Capability Composer: Advanced capability composition tool
 *
 * 2. Workflow Monitor (Expanded by default)
 *    - Execution History: View past workflow executions
 *    - Statistics: Aggregate metrics and performance data
 *    - Performance Metrics: Detailed timing and resource usage
 *
 * 3. Service Discovery (Collapsed by default)
 *    - Phase 4 Services: Unified services with metadata and capabilities
 *    - Capabilities Browser: Browse and filter available capabilities
 *    - Service Explorer: Discover and inspect services
 *
 * @component
 */

import React, { useState, useEffect } from 'react';
import phase4Client from '../../services/phase4Client';
import WorkflowCanvas from '../WorkflowCanvas';
import CapabilityComposer from '../CapabilityComposer';
import CapabilitiesBrowser from '../marketplace/CapabilitiesBrowser';
import ServiceExplorer from '../marketplace/ServiceExplorer';
import * as workflowBuilderService from '../../services/workflowBuilderService';
import * as workflowManagementService from '../../services/workflowManagementService';
import {
  Box,
  Tabs,
  Tab,
  CircularProgress,
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
  Accordion,
  AccordionSummary,
  AccordionDetails,
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { Play, Trash, FileText } from 'lucide-react';
import '../../styles/UnifiedServicesPanel.css';

/**
 * Service Card Component
 * Displays metadata and actions for a single service
 */
const ServiceCard = ({ service, onExecuteAction }) => {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleExpandToggle = () => {
    setExpanded(!expanded);
  };

  const getCategoryBadgeColor = (category) => {
    const colors = {
      content: '#4CAF50',
      financial: '#2196F3',
      market: '#FF9800',
      compliance: '#E91E63',
    };
    return colors[category] || '#757575';
  };

  const handleExecuteAction = async () => {
    try {
      setLoading(true);
      setError(null);
      await onExecuteAction(service.name);
    } catch (err) {
      setError(err.message || 'Failed to execute action');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="service-card"
      style={{ borderLeftColor: getCategoryBadgeColor(service.category) }}
    >
      <div className="service-header" onClick={handleExpandToggle}>
        <div className="service-header-content">
          <div className="service-title-section">
            <h3 className="service-name">{service.name}</h3>
            <span
              className="service-category-badge"
              style={{
                backgroundColor: getCategoryBadgeColor(service.category),
              }}
            >
              {service.category}
            </span>
          </div>
          <p className="service-description">{service.description}</p>
        </div>
        <div className="service-expand-icon">{expanded ? '▼' : '▶'}</div>
      </div>

      {expanded && (
        <div className="service-details">
          {/* Phases Section */}
          <div className="details-section">
            <h4>Phases</h4>
            <div className="phases-grid">
              {service.phases && service.phases.length > 0 ? (
                service.phases.map((phase) => (
                  <span key={phase} className="phase-badge">
                    {phase}
                  </span>
                ))
              ) : (
                <p className="no-data">No phases defined</p>
              )}
            </div>
          </div>

          {/* Capabilities Section */}
          <div className="details-section">
            <h4>Capabilities</h4>
            <div className="capabilities-list">
              {service.capabilities && service.capabilities.length > 0 ? (
                <ul>
                  {service.capabilities.map((capability) => (
                    <li key={capability} className="capability-item">
                      {capability}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="no-data">No capabilities defined</p>
              )}
            </div>
          </div>

          {/* Metadata Section */}
          <div className="details-section">
            <h4>Details</h4>
            <div className="metadata-grid">
              <div className="metadata-item">
                <span className="label">Version:</span>
                <span className="value">{service.version || 'N/A'}</span>
              </div>
              <div className="metadata-item">
                <span className="label">Status:</span>
                <span className="value status-active">Active</span>
              </div>
            </div>
          </div>

          {error && <div className="error-message">{error}</div>}

          <div className="service-actions">
            <button
              className="btn btn-primary"
              onClick={handleExecuteAction}
              disabled={loading}
            >
              {loading ? 'Executing...' : 'Execute Action'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

/**
 * Capability Filter Component
 * Allows filtering services by capability
 */
const CapabilityFilter = ({
  allCapabilities,
  selectedCapabilities,
  onFilterChange,
}) => {
  return (
    <div className="filter-section">
      <h4>Filter by Capability</h4>
      <div className="filter-tags">
        {allCapabilities.map((capability) => (
          <button
            key={capability}
            className={`filter-tag ${selectedCapabilities.includes(capability) ? 'active' : ''}`}
            onClick={() => {
              const updated = selectedCapabilities.includes(capability)
                ? selectedCapabilities.filter((c) => c !== capability)
                : [...selectedCapabilities, capability];
              onFilterChange(updated);
            }}
          >
            {capability}
          </button>
        ))}
        {selectedCapabilities.length > 0 && (
          <button
            className="filter-tag clear"
            onClick={() => onFilterChange([])}
          >
            Clear All
          </button>
        )}
      </div>
    </div>
  );
};

/**
 * Phase Filter Component
 */
const PhaseFilter = ({ allPhases, selectedPhases, onFilterChange }) => {
  return (
    <div className="filter-section">
      <h4>Filter by Phase</h4>
      <div className="filter-tags">
        {allPhases.map((phase) => (
          <button
            key={phase}
            className={`filter-tag phase ${selectedPhases.includes(phase) ? 'active' : ''}`}
            onClick={() => {
              const updated = selectedPhases.includes(phase)
                ? selectedPhases.filter((p) => p !== phase)
                : [...selectedPhases, phase];
              onFilterChange(updated);
            }}
          >
            {phase}
          </button>
        ))}
        {selectedPhases.length > 0 && (
          <button
            className="filter-tag clear"
            onClick={() => onFilterChange([])}
          >
            Clear All
          </button>
        )}
      </div>
    </div>
  );
};

/**
 * Workflow Monitor Tabs Component
 * Nested tabs for Execution History, Statistics, and Performance Metrics
 */
const WorkflowMonitorTabs = ({
  executionHistory,
  statistics,
  performanceMetrics,
  loading,
  error,
}) => {
  const [monitorTab, setMonitorTab] = useState(0);

  const handleMonitorTabChange = (event, newValue) => {
    setMonitorTab(newValue);
  };

  return (
    <Box>
      <Tabs
        value={monitorTab}
        onChange={handleMonitorTabChange}
        aria-label="workflow monitor tabs"
        sx={{ borderBottom: '1px solid #e0e0e0', mb: 2 }}
      >
        <Tab label="Execution History" />
        <Tab label="Statistics" />
        <Tab label="Performance Metrics" />
      </Tabs>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
          <CircularProgress />
        </Box>
      ) : (
        <>
          {/* Execution History Tab */}
          {monitorTab === 0 && (
            <Box>
              {executionHistory && executionHistory.length > 0 ? (
                <TableContainer>
                  <Table size="small">
                    <TableHead>
                      <TableRow sx={{ backgroundColor: '#f5f5f5' }}>
                        <TableCell>Workflow</TableCell>
                        <TableCell>Status</TableCell>
                        <TableCell>Started</TableCell>
                        <TableCell>Duration</TableCell>
                        <TableCell>Actions</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {executionHistory.map((execution) => (
                        <TableRow key={execution.id}>
                          <TableCell>{execution.workflow_name}</TableCell>
                          <TableCell>
                            <Chip
                              label={execution.status}
                              size="small"
                              color={
                                execution.status === 'completed'
                                  ? 'success'
                                  : execution.status === 'failed'
                                    ? 'error'
                                    : 'warning'
                              }
                              variant="outlined"
                            />
                          </TableCell>
                          <TableCell>
                            {new Date(execution.started_at).toLocaleString()}
                          </TableCell>
                          <TableCell>{execution.duration_ms}ms</TableCell>
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
              ) : (
                <Typography color="textSecondary" align="center" sx={{ py: 3 }}>
                  No execution history available yet.
                </Typography>
              )}
            </Box>
          )}

          {/* Statistics Tab */}
          {monitorTab === 1 && (
            <Box>
              {statistics ? (
                <Stack spacing={2}>
                  <Paper sx={{ p: 2 }}>
                    <Typography variant="h6" gutterBottom>
                      Overview
                    </Typography>
                    <Stack direction="row" spacing={3}>
                      <Box>
                        <Typography variant="body2" color="textSecondary">
                          Total Executions
                        </Typography>
                        <Typography variant="h6">
                          {statistics.total_executions || 0}
                        </Typography>
                      </Box>
                      <Box>
                        <Typography variant="body2" color="textSecondary">
                          Success Rate
                        </Typography>
                        <Typography variant="h6">
                          {statistics.success_rate || '0'}%
                        </Typography>
                      </Box>
                      <Box>
                        <Typography variant="body2" color="textSecondary">
                          Avg Duration
                        </Typography>
                        <Typography variant="h6">
                          {statistics.avg_duration_ms || 0}ms
                        </Typography>
                      </Box>
                    </Stack>
                  </Paper>
                </Stack>
              ) : (
                <Typography color="textSecondary" align="center" sx={{ py: 3 }}>
                  No statistics available yet.
                </Typography>
              )}
            </Box>
          )}

          {/* Performance Metrics Tab */}
          {monitorTab === 2 && (
            <Box>
              {performanceMetrics ? (
                <Stack spacing={2}>
                  <Paper sx={{ p: 2 }}>
                    <Typography variant="h6" gutterBottom>
                      Performance Data
                    </Typography>
                    <Typography variant="body2" color="textSecondary">
                      {performanceMetrics.description ||
                        'Performance metrics for workflow executions'}
                    </Typography>
                  </Paper>
                </Stack>
              ) : (
                <Typography color="textSecondary" align="center" sx={{ py: 3 }}>
                  No performance metrics available yet.
                </Typography>
              )}
            </Box>
          )}
        </>
      )}
    </Box>
  );
};

/**
 * Main Unified Services Panel Component (Consolidated)
 * Accordion-based layout with 3 main sections:
 * 1. Workflow Studio (create, manage workflows)
 * 2. Workflow Monitor (execution history, stats, metrics) - DEFAULT EXPANDED
 * 3. Service Discovery (browse services and capabilities)
 */
const UnifiedServicesPanel = () => {
  // Accordion state
  const [expandedSection, setExpandedSection] = useState('monitor'); // Monitor is default expanded

  // Services tab state (for Service Discovery section)
  const [services, setServices] = useState([]);
  const [loadingServices, setLoadingServices] = useState(true);
  const [error, setError] = useState(null);
  const [selectedCapabilities, setSelectedCapabilities] = useState([]);
  const [selectedPhases, setSelectedPhases] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [healthStatus, setHealthStatus] = useState(null);

  // Workflow builder state (for Workflow Studio section)
  const [studioTab, setStudioTab] = useState(0);
  const [discoveryTab, setDiscoveryTab] = useState(0);
  const [availablePhases, setAvailablePhases] = useState([]);
  const [workflows, setWorkflows] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loadingWorkflows, setLoadingWorkflows] = useState(false);
  const [selectedWorkflow, setSelectedWorkflow] = useState(null);
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  const [templateModalOpen, setTemplateModalOpen] = useState(false);

  // Workflow monitor state (for Workflow Monitor section)
  const [monitorLoading, setMonitorLoading] = useState(false);
  const [monitorError, setMonitorError] = useState(null);
  const [executionHistory, setExecutionHistory] = useState([]);
  const [statistics, setStatistics] = useState(null);
  const [performanceMetrics, setPerformanceMetrics] = useState(null);

  // Fetch services on mount
  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoadingServices(true);
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
        const transformedServices = agentsList.map((agent) => ({
          id: agent.name,
          name: agent.name,
          category: agent.category || 'general',
          description: agent.description || 'No description',
          phases: agent.phases || [],
          capabilities: agent.capabilities || [],
          version: agent.version || '1.0.0',
          actions: agent.actions || [],
        }));

        setServices(transformedServices);
      } catch (err) {
        const errorMessage = err.message || 'Failed to load services';
        setError(`Error loading services: ${errorMessage}`);
        console.error('UnifiedServicesPanel error:', err);
      } finally {
        setLoadingServices(false);
      }
    };

    fetchData();
  }, []);

  // Load workflow and monitor data when Workflow Studio or Workflow Monitor sections expand
  useEffect(() => {
    if (expandedSection === 'studio') {
      loadWorkflowStudioData();
    } else if (expandedSection === 'monitor') {
      loadWorkflowMonitorData();
    }
  }, [expandedSection]);

  const loadWorkflowStudioData = async () => {
    setLoadingWorkflows(true);
    try {
      console.log('[UnifiedServicesPanel] Loading workflow studio data...');

      // Load available phases
      const phasesRes = await workflowBuilderService.getAvailablePhases();
      setAvailablePhases(phasesRes.phases || []);

      // Load user workflows
      const workflowsRes = await workflowBuilderService.listWorkflows({
        limit: 100,
      });
      setWorkflows(workflowsRes.workflows || []);

      // Load templates
      const templatesList = [
        {
          id: 'blog_post',
          name: 'Blog Post',
          description:
            'Full blog post generation with research, drafting, assessment, refinement',
          phase_count: 7,
          is_template: true,
        },
        {
          id: 'social_media',
          name: 'Social Media',
          description: 'Quick social media content generation',
          phase_count: 5,
          is_template: true,
        },
        {
          id: 'email',
          name: 'Email',
          description: 'Email content generation with assessment',
          phase_count: 4,
          is_template: true,
        },
      ];
      setTemplates(templatesList);

      setError(null);
    } catch (err) {
      const errorMsg =
        err?.message || String(err) || 'Unknown error loading workflow data';
      console.error('[UnifiedServicesPanel] Error loading workflow data:', err);
      setError(`Workflow Error: ${errorMsg}`);
    } finally {
      setLoadingWorkflows(false);
    }
  };

  const loadWorkflowMonitorData = async () => {
    setMonitorLoading(true);
    try {
      console.log('[UnifiedServicesPanel] Loading workflow monitor data...');

      // Load execution history
      const historyRes = await workflowManagementService.getWorkflowHistory({
        limit: 20,
      });
      setExecutionHistory(historyRes.executions || historyRes || []);

      // Load statistics
      const statsRes = await workflowManagementService.getWorkflowStatistics();
      setStatistics(statsRes.statistics || statsRes || {});

      // Load performance metrics
      const metricsRes =
        await workflowManagementService.getPerformanceMetrics();
      setPerformanceMetrics(metricsRes.metrics || metricsRes || {});

      setMonitorError(null);
    } catch (err) {
      const errorMsg =
        err?.message || String(err) || 'Unknown error loading monitor data';
      console.error('[UnifiedServicesPanel] Error loading monitor data:', err);
      setMonitorError(`Monitor Error: ${errorMsg}`);
    } finally {
      setMonitorLoading(false);
    }
  };

  // Get all unique capabilities and phases for filtering
  const allCapabilities = Array.from(
    new Set(services.flatMap((s) => s.capabilities))
  ).sort();

  const allPhases = Array.from(
    new Set(services.flatMap((s) => s.phases))
  ).sort();

  // Filter services based on selected filters and search
  const filteredServices = services.filter((service) => {
    const matchesSearch =
      searchQuery === '' ||
      service.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      service.description.toLowerCase().includes(searchQuery.toLowerCase());

    const matchesCapabilities =
      selectedCapabilities.length === 0 ||
      selectedCapabilities.some((cap) => service.capabilities.includes(cap));

    const matchesPhases =
      selectedPhases.length === 0 ||
      selectedPhases.some((phase) => service.phases.includes(phase));

    return matchesSearch && matchesCapabilities && matchesPhases;
  });

  // Handle action execution
  const handleExecuteAction = async (serviceName) => {
    console.log(`Execute action for service: ${serviceName}`);
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
    setWorkflows((w) => [...w, newWorkflow]);
    setStudioTab(1); // Switch to My Workflows tab
  };

  const handleAccordionChange = (section) => (event, isExpanded) => {
    setExpandedSection(isExpanded ? section : null);
  };

  const handleStudioTabChange = (event, newValue) => {
    setStudioTab(newValue);
  };

  const handleDiscoveryTabChange = (event, newValue) => {
    setDiscoveryTab(newValue);
  };

  if (loadingServices && services.length === 0) {
    return (
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          minHeight: 600,
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box className="unified-services-panel" sx={{ p: 3 }}>
      <Typography variant="h4" gutterBottom sx={{ mb: 3 }}>
        Unified Services & Workflows
      </Typography>

      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      {/* ========== ACCORDION SECTION 1: WORKFLOW STUDIO ========== */}
      <Accordion
        expanded={expandedSection === 'studio'}
        onChange={handleAccordionChange('studio')}
        sx={{ mb: 2 }}
      >
        <AccordionSummary
          expandIcon={<ExpandMoreIcon />}
          aria-controls="studio-content"
          id="studio-header"
        >
          <Typography variant="h6">📋 Workflow Studio</Typography>
          <Typography variant="body2" color="textSecondary" sx={{ ml: 2 }}>
            Create, edit, and manage custom workflows
          </Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Box sx={{ width: '100%' }}>
            {loadingWorkflows && expandedSection === 'studio' ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                <CircularProgress />
              </Box>
            ) : (
              <>
                {/* Studio Nested Tabs */}
                <Tabs
                  value={studioTab}
                  onChange={handleStudioTabChange}
                  aria-label="studio tabs"
                  sx={{ mb: 2, borderBottom: '1px solid #e0e0e0' }}
                >
                  <Tab label="Create Workflow" />
                  <Tab label="My Workflows" />
                  <Tab label="Templates" />
                  <Tab label="Capability Composer" />
                </Tabs>

                {/* Create Workflow Tab */}
                {studioTab === 0 && (
                  <Box>
                    {availablePhases.length > 0 ? (
                      <WorkflowCanvas
                        availablePhases={availablePhases}
                        onSave={handleWorkflowSaved}
                        workflow={selectedWorkflow}
                      />
                    ) : (
                      <Alert severity="warning">
                        Loading available phases...
                      </Alert>
                    )}
                  </Box>
                )}

                {/* My Workflows Tab */}
                {studioTab === 1 && (
                  <Box>
                    {workflows.length === 0 ? (
                      <Typography
                        color="textSecondary"
                        align="center"
                        sx={{ py: 4 }}
                      >
                        No custom workflows yet. Create one in the "Create
                        Workflow" tab.
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
                                  <Typography
                                    variant="body2"
                                    color="textSecondary"
                                  >
                                    {workflow.description}
                                  </Typography>
                                </TableCell>
                                <TableCell align="center">
                                  <Chip
                                    label={
                                      workflow.phase_count ||
                                      workflow.phases?.length ||
                                      0
                                    }
                                    size="small"
                                  />
                                </TableCell>
                                <TableCell align="right">
                                  <Typography
                                    variant="body2"
                                    color="textSecondary"
                                  >
                                    {new Date(
                                      workflow.created_at
                                    ).toLocaleDateString()}
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
                                      onClick={() => {
                                        setSelectedWorkflow(workflow);
                                        setStudioTab(0);
                                      }}
                                    >
                                      <FileText size={18} />
                                    </IconButton>
                                    <IconButton
                                      size="small"
                                      title="Execute"
                                      onClick={async () => {
                                        try {
                                          await workflowManagementService.executeWorkflow(workflow.id);
                                          console.log('Workflow execution started:', workflow.id);
                                        } catch (err) {
                                          console.error('Failed to execute workflow:', err);
                                          setError(err.message);
                                        }
                                      }}
                                    >
                                      <Play size={18} />
                                    </IconButton>
                                    <IconButton
                                      size="small"
                                      title="Delete"
                                      color="error"
                                      onClick={() =>
                                        handleDeleteWorkflow(workflow.id)
                                      }
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

                {/* Templates Tab */}
                {studioTab === 2 && (
                  <Box>
                    <Stack spacing={2}>
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
                            <Typography variant="h6">
                              {template.name}
                            </Typography>
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
                              onClick={() => {
                                setSelectedTemplate(template);
                                setTemplateModalOpen(true);
                              }}
                            >
                              View
                            </Button>
                            <Button
                              variant="contained"
                              color="success"
                              size="small"
                              onClick={async () => {
                                try {
                                  await workflowManagementService.executeWorkflow(template.id);
                                  console.log('Template execution started:', template.id);
                                } catch (err) {
                                  console.error('Failed to execute template:', err);
                                  setError(err.message);
                                }
                              }}
                            >
                              Execute
                            </Button>
                          </Stack>
                        </Paper>
                      ))}
                    </Stack>
                  </Box>
                )}

                {/* Capability Composer Tab */}
                {studioTab === 3 && <CapabilityComposer />}
              </>
            )}
          </Box>
        </AccordionDetails>
      </Accordion>

      {/* ========== ACCORDION SECTION 2: WORKFLOW MONITOR (DEFAULT EXPANDED) ========== */}
      <Accordion
        expanded={expandedSection === 'monitor'}
        onChange={handleAccordionChange('monitor')}
        sx={{ mb: 2 }}
      >
        <AccordionSummary
          expandIcon={<ExpandMoreIcon />}
          aria-controls="monitor-content"
          id="monitor-header"
        >
          <Typography variant="h6">📊 Workflow Monitor</Typography>
          <Typography variant="body2" color="textSecondary" sx={{ ml: 2 }}>
            View execution history, statistics, and performance metrics
          </Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Box sx={{ width: '100%' }}>
            <WorkflowMonitorTabs
              executionHistory={executionHistory}
              statistics={statistics}
              performanceMetrics={performanceMetrics}
              loading={monitorLoading}
              error={monitorError}
            />
          </Box>
        </AccordionDetails>
      </Accordion>

      {/* ========== ACCORDION SECTION 3: SERVICE DISCOVERY ========== */}
      <Accordion
        expanded={expandedSection === 'discovery'}
        onChange={handleAccordionChange('discovery')}
        sx={{ mb: 2 }}
      >
        <AccordionSummary
          expandIcon={<ExpandMoreIcon />}
          aria-controls="discovery-content"
          id="discovery-header"
        >
          <Typography variant="h6">🔍 Service Discovery</Typography>
          <Typography variant="body2" color="textSecondary" sx={{ ml: 2 }}>
            Browse Phase 4 services, capabilities, and integrations
          </Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Box sx={{ width: '100%' }}>
            {/* Discovery Nested Tabs */}
            <Tabs
              value={discoveryTab}
              onChange={handleDiscoveryTabChange}
              aria-label="discovery tabs"
              sx={{ mb: 2, borderBottom: '1px solid #e0e0e0' }}
            >
              <Tab label="Phase 4 Services" />
              <Tab label="Capabilities Browser" />
              <Tab label="Service Explorer" />
            </Tabs>

            {/* Phase 4 Services Tab */}
            {discoveryTab === 0 && (
              <Box>
                {/* Header */}
                <Box sx={{ mb: 3 }}>
                  <Typography variant="h6" gutterBottom>
                    Unified Services
                  </Typography>
                  <Typography
                    variant="body2"
                    color="textSecondary"
                    sx={{ mb: 2 }}
                  >
                    Phase 4 Architecture - Integrated service discovery and
                    execution
                  </Typography>

                  {healthStatus && (
                    <Box
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1,
                        py: 1,
                        px: 2,
                        borderRadius: 1,
                        backgroundColor: healthStatus.healthy
                          ? '#e8f5e9'
                          : '#ffebee',
                      }}
                    >
                      <span style={{ fontSize: '12px' }}>
                        {healthStatus.healthy
                          ? '✓ All systems operational'
                          : '⚠ Service issues detected'}
                      </span>
                    </Box>
                  )}
                </Box>

                {/* Search and Filters */}
                <Box sx={{ mb: 3 }}>
                  <TextField
                    fullWidth
                    placeholder="Search services by name or description..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    variant="outlined"
                    size="small"
                    sx={{ mb: 2 }}
                  />

                  <Stack
                    direction="row"
                    spacing={2}
                    sx={{ mb: 2, overflowX: 'auto' }}
                  >
                    <CapabilityFilter
                      allCapabilities={allCapabilities}
                      selectedCapabilities={selectedCapabilities}
                      onFilterChange={setSelectedCapabilities}
                    />
                    <PhaseFilter
                      allPhases={allPhases}
                      selectedPhases={selectedPhases}
                      onFilterChange={setSelectedPhases}
                    />
                  </Stack>
                </Box>

                {/* Services Display */}
                {filteredServices.length > 0 ? (
                  <>
                    <Typography
                      variant="body2"
                      color="textSecondary"
                      sx={{ mb: 2 }}
                    >
                      Showing {filteredServices.length} of {services.length}{' '}
                      services
                    </Typography>
                    <Stack spacing={2}>
                      {filteredServices.map((service) => (
                        <ServiceCard
                          key={service.id}
                          service={service}
                          onExecuteAction={handleExecuteAction}
                        />
                      ))}
                    </Stack>
                  </>
                ) : (
                  <Typography
                    color="textSecondary"
                    align="center"
                    sx={{ py: 4 }}
                  >
                    No services found. Try adjusting your search or filters.
                  </Typography>
                )}
              </Box>
            )}

            {/* Capabilities Browser Tab */}
            {discoveryTab === 1 && <CapabilitiesBrowser />}

            {/* Service Explorer Tab */}
            {discoveryTab === 2 && <ServiceExplorer />}
          </Box>
        </AccordionDetails>
      </Accordion>

      {/* Template Viewer Modal */}
      <Dialog
        open={templateModalOpen}
        onClose={() => setTemplateModalOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>{selectedTemplate?.name} Template Details</DialogTitle>
        <DialogContent>
          {selectedTemplate && (
            <Stack spacing={2} sx={{ mt: 2 }}>
              <Box>
                <Typography variant="subtitle2" color="textSecondary">
                  Description
                </Typography>
                <Typography variant="body2">
                  {selectedTemplate.description}
                </Typography>
              </Box>
              <Box>
                <Typography variant="subtitle2" color="textSecondary">
                  Template ID
                </Typography>
                <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                  {selectedTemplate.id}
                </Typography>
              </Box>
              <Box>
                <Typography variant="subtitle2" color="textSecondary">
                  Phases
                </Typography>
                <Stack
                  direction="row"
                  spacing={1}
                  sx={{ flexWrap: 'wrap', gap: 1 }}
                >
                  {Array.from({ length: selectedTemplate.phase_count }).map(
                    (_, i) => (
                      <Chip
                        key={i}
                        label={`Phase ${i + 1}`}
                        size="small"
                        variant="outlined"
                      />
                    )
                  )}
                </Stack>
              </Box>
              <Box>
                <Typography variant="subtitle2" color="textSecondary">
                  Template Type
                </Typography>
                <Chip
                  label={
                    selectedTemplate.is_template
                      ? 'Built-in Template'
                      : 'Custom'
                  }
                  size="small"
                  color={selectedTemplate.is_template ? 'default' : 'primary'}
                />
              </Box>
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setTemplateModalOpen(false)}>Close</Button>
          <Button
            variant="contained"
            color="success"
            onClick={async () => {
              try {
                await workflowManagementService.executeWorkflow(selectedTemplate.id);
                console.log('Template execution started:', selectedTemplate.id);
                setTemplateModalOpen(false);
              } catch (err) {
                console.error('Failed to execute template:', err);
                setError(err.message);
              }
            }}
          >
            Execute Template
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default UnifiedServicesPanel;
