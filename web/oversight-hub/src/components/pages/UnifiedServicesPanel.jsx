/**
 * Unified Services Panel
 *
 * Modern React dashboard with multiple tabs:
 * 1. Services: Displays Phase 4 unified services, metadata, capabilities, phases
 * 2. Create Workflow: Visual workflow builder with drag-drop canvas
 * 3. My Workflows: List of user-created custom workflows
 * 4. Templates: Pre-built workflow templates
 *
 * Services include:
 * - Content Service: Content generation, critique, refinement
 * - Financial Service: Cost tracking, budget optimization, analysis
 * - Market Service: Trend analysis, opportunity identification, competitive analysis
 * - Compliance Service: Legal review, auditing, risk assessment
 *
 * @component
 */

import React, { useState, useEffect } from 'react';
import phase4Client from '../../services/phase4Client';
import WorkflowCanvas from '../WorkflowCanvas';
import CapabilityComposer from '../CapabilityComposer';
import * as workflowBuilderService from '../../services/workflowBuilderService';
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
  Container,
  Paper,
} from '@mui/material';
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
 * Main Unified Services Panel Component
 */
const UnifiedServicesPanel = () => {
  // Tabs state
  const [currentTab, setCurrentTab] = useState(0);

  // Services tab state
  const [services, setServices] = useState([]);
  const [loadingServices, setLoadingServices] = useState(true);
  const [error, setError] = useState(null);
  const [selectedCapabilities, setSelectedCapabilities] = useState([]);
  const [selectedPhases, setSelectedPhases] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [healthStatus, setHealthStatus] = useState(null);

  // Workflow builder state
  const [availablePhases, setAvailablePhases] = useState([]);
  const [workflows, setWorkflows] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loadingWorkflows, setLoadingWorkflows] = useState(false);
  const [selectedWorkflow, setSelectedWorkflow] = useState(null);

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

  // Load workflow data when tab changes to workflow tabs
  useEffect(() => {
    if (currentTab >= 1) {
      loadWorkflowData();
    }
  }, [currentTab]);

  const loadWorkflowData = async () => {
    setLoadingWorkflows(true);
    try {
      console.log('[UnifiedServicesPanel] Loading workflow data...');

      // Load available phases
      console.log('[UnifiedServicesPanel] Fetching available phases...');
      const phasesRes = await workflowBuilderService.getAvailablePhases();
      console.log(
        '[UnifiedServicesPanel] Available phases response:',
        phasesRes
      );
      const phases = phasesRes.phases || [];
      console.log(
        `[UnifiedServicesPanel] Loaded ${phases.length} phases:`,
        phases.map((p) => p.name)
      );
      setAvailablePhases(phases);

      // Load user workflows
      console.log('[UnifiedServicesPanel] Fetching user workflows...');
      const workflowsRes = await workflowBuilderService.listWorkflows({
        limit: 100,
      });
      console.log(
        '[UnifiedServicesPanel] User workflows response:',
        workflowsRes
      );
      const userWorkflows = workflowsRes.workflows || [];
      console.log(
        `[UnifiedServicesPanel] Loaded ${userWorkflows.length} user workflows`
      );
      setWorkflows(userWorkflows);

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
      console.log('[UnifiedServicesPanel] Workflow data loading completed successfully');

      setError(null);
    } catch (err) {
      const errorMsg = err?.message || String(err) || 'Unknown error loading workflow data';
      console.error('[UnifiedServicesPanel] Error loading workflow data:', err);
      console.error('[UnifiedServicesPanel] Error message:', errorMsg);
      console.error('[UnifiedServicesPanel] Error stack:', err?.stack);
      setError(`Workflow Error: ${errorMsg}`);
    } finally {
      setLoadingWorkflows(false);
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
    // This would open a modal or panel for selecting and executing specific actions
    // For now, just log it
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
    setCurrentTab(2); // Switch to My Workflows tab
  };

  const handleTabChange = (event, newValue) => {
    setCurrentTab(newValue);
  };

  if (loadingServices && currentTab === 0) {
    return (
      <div className="unified-services-panel">
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading unified services...</p>
        </div>
      </div>
    );
  }

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
          <Tab label="Phase 4 Services" id="tab-0" />
          <Tab label="Create Custom Workflow" id="tab-1" />
          <Tab label="My Workflows" id="tab-2" />
          <Tab label="Templates" id="tab-3" />
          <Tab label="Capability Composer" id="tab-4" />
        </Tabs>
      </div>

      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ m: 2 }}>
          {error}
        </Alert>
      )}

      {/* Tab 0: Phase 4 Services */}
      {currentTab === 0 && (
        <>
          {/* Header */}
          <div className="panel-header">
            <h1>Unified Services</h1>
            <p className="panel-subtitle">
              Phase 4 Architecture - Integrated service discovery and execution
            </p>

            {healthStatus && (
              <div
                className={`health-status ${healthStatus.healthy ? 'healthy' : 'unhealthy'}`}
              >
                <span className="health-indicator"></span>
                <span>
                  {healthStatus.healthy
                    ? 'All systems operational'
                    : 'Service issues detected'}
                </span>
              </div>
            )}
          </div>

          {/* Error Display */}
          {error && (
            <div className="error-banner">
              <span className="error-icon">⚠️</span>
              <span>{error}</span>
            </div>
          )}

          {/* Controls Section */}
          <div className="controls-section">
            {/* Search */}
            <div className="search-box">
              <input
                type="text"
                placeholder="Search services by name or description..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="search-input"
              />
              <span className="search-icon">🔍</span>
            </div>

            {/* Filters */}
            <div className="filters-container">
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
            </div>
          </div>

          {/* Services Display */}
          <div className="services-section">
            {filteredServices.length > 0 ? (
              <>
                <div className="services-count">
                  Showing {filteredServices.length} of {services.length}{' '}
                  services
                </div>
                <div className="services-grid">
                  {filteredServices.map((service) => (
                    <ServiceCard
                      key={service.id}
                      service={service}
                      onExecuteAction={handleExecuteAction}
                    />
                  ))}
                </div>
              </>
            ) : (
              <div className="no-results">
                <span className="no-results-icon">🔭</span>
                <h3>No services found</h3>
                <p>Try adjusting your search or filters</p>
              </div>
            )}
          </div>

          {/* Footer Info */}
          <div className="panel-footer">
            <div className="footer-info">
              <div className="info-item">
                <strong>{services.length}</strong> Total Services
              </div>
              <div className="info-item">
                <strong>{allCapabilities.length}</strong> Capabilities
              </div>
              <div className="info-item">
                <strong>{allPhases.length}</strong> Processing Phases
              </div>
            </div>
            <p className="footer-text">
              Phase 4 unified architecture | Real-time service discovery |
              Dynamic capability matching
            </p>
          </div>
        </>
      )}

      {/* Tab 1: Create Custom Workflow */}
      {currentTab === 1 && (
        <Box sx={{ p: 3 }}>
          {loadingWorkflows ? (
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

      {/* Tab 2: My Workflows */}
      {currentTab === 2 && (
        <Box sx={{ p: 3 }}>
          {workflows.length === 0 ? (
            <Typography color="textSecondary" align="center" sx={{ py: 4 }}>
              No custom workflows yet. Create one in the "Create Custom
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
                            onClick={() => {
                              setSelectedWorkflow(workflow);
                              setCurrentTab(1);
                            }}
                          >
                            <FileText size={18} />
                          </IconButton>
                          <IconButton size="small" title="Execute">
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

      {/* Tab 3: Templates */}
      {currentTab === 3 && (
        <Box sx={{ p: 3 }}>
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
                    onClick={() => {
                      // TODO: Load and view template
                    }}
                  >
                    View
                  </Button>
                  <Button
                    variant="contained"
                    color="success"
                    size="small"
                    onClick={() => {
                      // TODO: Implement template execution
                      console.log('Execute template:', template);
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

      {/* Tab 4: Capability Composer */}
      {currentTab === 4 && <CapabilityComposer />}
    </div>
  );
};

export default UnifiedServicesPanel;
