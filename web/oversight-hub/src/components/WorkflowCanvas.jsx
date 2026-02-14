import React, { useState, useCallback, useEffect } from 'react';
import ReactFlow, {
  Controls,
  Background,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
} from 'reactflow';
import 'reactflow/dist/style.css';
import {
  Box,
  Card,
  CardContent,
  Button,
  IconButton,
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Stack,
  Chip,
  Typography,
  Alert,
  Divider,
  LinearProgress,
  CircularProgress,
} from '@mui/material';
import {
  Plus,
  Save,
  Play,
  ArrowUp,
  ArrowDown,
  Trash2,
  GripVertical,
} from 'lucide-react';
import PhaseNode from './PhaseNode';
import PhaseConfigPanel from './PhaseConfigPanel';
import * as workflowBuilderService from '../services/workflowBuilderService';
import { modelService } from '../services/modelService';

const nodeTypes = {
  phase: PhaseNode,
};

const PHASE_TO_AGENT_MAP = {
  research: 'research_agent',
  draft: 'creative_agent',
  refine: 'creative_agent',
  assess: 'qa_agent',
  image: 'image_agent',
  image_selection: 'image_agent',
  publish: 'publishing_agent',
  finalize: 'publishing_agent',
};

const KNOWN_AGENT_IDS = new Set(Object.values(PHASE_TO_AGENT_MAP));

const normalizePhaseName = (name) =>
  typeof name === 'string' ? name.trim() : '';

const isValidAgentId = (agentId = '') => {
  const normalized = normalizePhaseName(agentId);
  return (
    Boolean(normalized) &&
    (KNOWN_AGENT_IDS.has(normalized) || normalized.endsWith('_agent'))
  );
};

const getDefaultAgentForPhaseType = (phaseType = '') =>
  PHASE_TO_AGENT_MAP[normalizePhaseName(phaseType)] || 'creative_agent';

const resolvePhaseAgent = (phase = {}) => {
  const configuredAgent = normalizePhaseName(phase?.agent);
  if (isValidAgentId(configuredAgent)) {
    return configuredAgent;
  }

  const phaseType = inferBasePhaseType(phase);
  return getDefaultAgentForPhaseType(phaseType);
};

const inferBasePhaseType = (phase = {}) => {
  const explicitType = normalizePhaseName(phase?.metadata?.phase_type);
  if (explicitType) {
    return explicitType;
  }

  const explicitAgent = normalizePhaseName(phase?.agent);
  if (explicitAgent) {
    return explicitAgent;
  }

  const phaseName = normalizePhaseName(phase?.name);
  if (!phaseName) {
    return 'phase';
  }

  return phaseName.replace(/_\d+$/, '');
};

const getUniquePhaseName = (baseName, existingNames) => {
  const normalizedBase = normalizePhaseName(baseName) || 'phase';
  if (!existingNames.has(normalizedBase)) {
    return normalizedBase;
  }

  let index = 2;
  while (existingNames.has(`${normalizedBase}_${index}`)) {
    index += 1;
  }
  return `${normalizedBase}_${index}`;
};

const buildDefaultPhaseInputs = (inputFields = []) => {
  const defaults = {};
  inputFields.forEach((field) => {
    if (!field?.key) {
      return;
    }

    if (field.default_value !== undefined && field.default_value !== null) {
      defaults[field.key] = field.default_value;
      return;
    }

    defaults[field.key] = field.input_type === 'boolean' ? false : '';
  });

  return defaults;
};

const buildPhaseMetadata = (phase = {}) => {
  const existingMetadata = phase?.metadata || {};
  const inputSchema = Array.isArray(existingMetadata.input_schema)
    ? existingMetadata.input_schema
    : Array.isArray(phase?.input_fields)
      ? phase.input_fields
      : [];

  return {
    ...existingMetadata,
    input_schema: inputSchema,
    phase_inputs: {
      ...buildDefaultPhaseInputs(inputSchema),
      ...(existingMetadata.phase_inputs || {}),
    },
  };
};

const TERMINAL_EXECUTION_STATUSES = new Set([
  'completed',
  'failed',
  'cancelled',
]);

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

const parseExecutionStatusCode = (error) => {
  const statusCode =
    error?.status ||
    error?.statusCode ||
    error?.response?.status ||
    error?.response?.statusCode;

  if (Number.isFinite(statusCode)) {
    return Number(statusCode);
  }

  const message =
    typeof error?.message === 'string' ? error.message.toLowerCase() : '';
  if (message.includes('404') || message.includes('not found')) {
    return 404;
  }

  return null;
};

const ensureUniqueWorkflowPhases = (phases = []) => {
  const usedNames = new Set();

  return phases.map((phase) => {
    const baseType = inferBasePhaseType(phase);
    const uniqueName = getUniquePhaseName(
      normalizePhaseName(phase?.name) || baseType,
      usedNames
    );

    usedNames.add(uniqueName);

    return {
      ...phase,
      name: uniqueName,
      agent: resolvePhaseAgent(phase),
      metadata: {
        ...buildPhaseMetadata(phase),
        phase_type: baseType,
      },
    };
  });
};

const WorkflowCanvas = ({ onSave, availablePhases, workflow = null }) => {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedNode, setSelectedNode] = useState(null);
  const [workflowName, setWorkflowName] = useState(workflow?.name || '');
  const [workflowDescription, setWorkflowDescription] = useState(
    workflow?.description || ''
  );
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [error, setError] = useState(null);
  const [successMessage, setSuccessMessage] = useState('');
  const [availableModels, setAvailableModels] = useState([]);
  const [draggedNodeId, setDraggedNodeId] = useState(null);
  const [dragOverNodeId, setDragOverNodeId] = useState(null);
  const [executionId, setExecutionId] = useState(null);
  const [executionStatus, setExecutionStatus] = useState(null);
  const [executionProgress, setExecutionProgress] = useState(0);
  const [executionResults, setExecutionResults] = useState({});
  const [executionFinalOutput, setExecutionFinalOutput] = useState(null);
  const [executionErrorMessage, setExecutionErrorMessage] = useState('');
  const [executionPollingError, setExecutionPollingError] = useState('');
  const [executionHistory, setExecutionHistory] = useState([]);
  const [executionHistoryLoading, setExecutionHistoryLoading] = useState(false);
  const [executionHistoryError, setExecutionHistoryError] = useState('');

  const isPersistedWorkflow = Boolean(workflow?.isPersisted && workflow?.id);
  const isTemplateWorkflow = Boolean(workflow?.is_template);
  const effectiveWorkflowId = workflow?.id || null;

  const rebuildGraphFromPhases = useCallback(
    (phaseConfigs, selectedPhaseName = null) => {
      const newNodes = phaseConfigs.map((phase, index) => ({
        id: `phase-${index}`,
        data: { label: phase.name, phase },
        position: { x: index * 250, y: 0 },
        type: 'phase',
      }));

      const newEdges = phaseConfigs.slice(0, -1).map((_, index) => ({
        id: `edge-${index}`,
        source: `phase-${index}`,
        target: `phase-${index + 1}`,
      }));

      setNodes(newNodes);
      setEdges(newEdges);

      if (!selectedPhaseName) {
        setSelectedNode(null);
        return;
      }

      const nextSelected = newNodes.find(
        (node) => node.data?.phase?.name === selectedPhaseName
      );
      setSelectedNode(nextSelected || null);
    },
    [setNodes, setEdges]
  );

  useEffect(() => {
    setWorkflowName(workflow?.name || '');
    setWorkflowDescription(workflow?.description || '');

    if (
      workflow &&
      Array.isArray(workflow.phases) &&
      workflow.phases.length > 0
    ) {
      const normalizedPhases = ensureUniqueWorkflowPhases(workflow.phases);

      rebuildGraphFromPhases(normalizedPhases);
    } else {
      setNodes([]);
      setEdges([]);
    }

    setSelectedNode(null);
  }, [workflow, setNodes, setEdges, rebuildGraphFromPhases]);

  useEffect(() => {
    let mounted = true;

    const loadModels = async () => {
      try {
        const models = await modelService.getAvailableModels();
        if (mounted) {
          setAvailableModels(Array.isArray(models) ? models : []);
        }
      } catch {
        if (mounted) {
          setAvailableModels(modelService.getDefaultModels());
        }
      }
    };

    loadModels();

    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!executionId) {
      return undefined;
    }

    let active = true;
    let intervalId;

    const pollExecutionStatus = async () => {
      try {
        const execution =
          await workflowBuilderService.getExecutionStatus(executionId);

        if (!active) {
          return;
        }

        const nextStatus = normalizeExecutionStatus(
          execution?.execution_status || execution?.status
        );
        const nextProgress = Number.isFinite(execution?.progress_percent)
          ? execution.progress_percent
          : nextStatus === 'completed'
            ? 100
            : 0;

        setExecutionStatus(nextStatus);
        setExecutionProgress(nextProgress);
        setExecutionResults(execution?.phase_results || {});
        setExecutionFinalOutput(execution?.final_output ?? null);
        setExecutionErrorMessage(execution?.error_message || '');
        setExecutionPollingError('');

        if (TERMINAL_EXECUTION_STATUSES.has(nextStatus) && intervalId) {
          clearInterval(intervalId);
        }
      } catch (pollError) {
        if (!active) {
          return;
        }

        const statusCode = parseExecutionStatusCode(pollError);
        if (statusCode === 404) {
          setExecutionStatus((currentStatus) => currentStatus || 'pending');
          setExecutionPollingError('');
          return;
        }

        const message = pollError?.message || '';

        setExecutionPollingError(
          message || 'Failed to refresh execution status'
        );
      }
    };

    pollExecutionStatus();
    intervalId = setInterval(pollExecutionStatus, 2000);

    return () => {
      active = false;
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  }, [executionId]);

  const loadExecutionHistory = useCallback(
    async (workflowIdOverride = null) => {
      const targetWorkflowId = workflowIdOverride || effectiveWorkflowId;

      if (!targetWorkflowId) {
        setExecutionHistory([]);
        setExecutionHistoryError('');
        return;
      }

      try {
        setExecutionHistoryLoading(true);
        const result = await workflowBuilderService.getWorkflowExecutions(
          targetWorkflowId,
          { limit: 10, offset: 0 }
        );
        setExecutionHistory(result?.executions || []);
        setExecutionHistoryError('');
      } catch (historyError) {
        setExecutionHistoryError(
          historyError?.message || 'Failed to load execution history'
        );
      } finally {
        setExecutionHistoryLoading(false);
      }
    },
    [effectiveWorkflowId]
  );

  useEffect(() => {
    loadExecutionHistory();
  }, [loadExecutionHistory, executionId]);

  const onConnect = useCallback(
    (connection) => {
      setEdges((eds) => addEdge(connection, eds));
    },
    [setEdges]
  );

  const addPhase = (phase) => {
    const existingNames = new Set(
      nodes
        .map((node) => normalizePhaseName(node?.data?.phase?.name))
        .filter(Boolean)
    );
    const baseType = inferBasePhaseType(phase);
    const uniqueName = getUniquePhaseName(
      phase?.name || baseType,
      existingNames
    );

    const phaseConfig = {
      ...phase,
      name: uniqueName,
      agent: resolvePhaseAgent(phase),
      metadata: {
        ...buildPhaseMetadata(phase),
        phase_type: baseType,
      },
    };

    const phaseConfigs = nodes.map((node) => node.data.phase);
    rebuildGraphFromPhases([...phaseConfigs, phaseConfig], uniqueName);
  };

  const updatePhaseConfig = (nodeId, config) => {
    setNodes((nds) =>
      nds.map((node) =>
        node.id === nodeId
          ? {
              ...node,
              data: { ...node.data, label: config?.name, phase: config },
            }
          : node
      )
    );
  };

  const removePhase = (nodeId) => {
    const phaseConfigs = nodes
      .filter((node) => node.id !== nodeId)
      .map((node) => node.data.phase);
    rebuildGraphFromPhases(phaseConfigs);
  };

  const movePhase = (nodeId, direction) => {
    const currentIndex = nodes.findIndex((node) => node.id === nodeId);
    if (currentIndex < 0) {
      return;
    }

    const targetIndex =
      direction === 'up' ? currentIndex - 1 : currentIndex + 1;

    if (targetIndex < 0 || targetIndex >= nodes.length) {
      return;
    }

    const phaseConfigs = nodes.map((node) => node.data.phase);
    const reordered = [...phaseConfigs];
    const [moved] = reordered.splice(currentIndex, 1);
    reordered.splice(targetIndex, 0, moved);

    rebuildGraphFromPhases(reordered, moved?.name || null);
  };

  const clearDragState = () => {
    setDraggedNodeId(null);
    setDragOverNodeId(null);
  };

  const handlePhaseDragStart = (event, nodeId) => {
    setDraggedNodeId(nodeId);
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', nodeId);
  };

  const handlePhaseDragOver = (event, nodeId) => {
    event.preventDefault();
    if (nodeId !== draggedNodeId) {
      setDragOverNodeId(nodeId);
    }
    event.dataTransfer.dropEffect = 'move';
  };

  const handlePhaseDrop = (event, targetNodeId) => {
    event.preventDefault();

    const sourceNodeId =
      draggedNodeId || event.dataTransfer.getData('text/plain');

    if (!sourceNodeId || sourceNodeId === targetNodeId) {
      clearDragState();
      return;
    }

    const sourceIndex = nodes.findIndex((node) => node.id === sourceNodeId);
    const targetIndex = nodes.findIndex((node) => node.id === targetNodeId);

    if (sourceIndex < 0 || targetIndex < 0) {
      clearDragState();
      return;
    }

    const phaseConfigs = nodes.map((node) => node.data.phase);
    const reordered = [...phaseConfigs];
    const [moved] = reordered.splice(sourceIndex, 1);
    reordered.splice(targetIndex, 0, moved);

    rebuildGraphFromPhases(reordered, moved?.name || null);
    clearDragState();
  };

  const buildWorkflowDefinition = () => {
    if (nodes.length === 0) {
      setError('Workflow must have at least one phase');
      return null;
    }

    if (!workflowName.trim()) {
      setError('Workflow name is required');
      return null;
    }

    if (!workflowDescription.trim()) {
      setError('Workflow description is required');
      return null;
    }

    const phases = nodes.map((node) => ({
      name: normalizePhaseName(node?.data?.phase?.name),
      agent: resolvePhaseAgent(node?.data?.phase),
      description: node?.data?.phase?.description,
      timeout_seconds: node?.data?.phase?.timeout_seconds || 300,
      max_retries: node?.data?.phase?.max_retries || 3,
      skip_on_error: node?.data?.phase?.skip_on_error || false,
      required: node?.data?.phase?.required !== false,
      quality_threshold: node?.data?.phase?.quality_threshold,
      metadata: {
        ...buildPhaseMetadata(node?.data?.phase),
        phase_type: inferBasePhaseType(node?.data?.phase),
      },
    }));

    const hasEmptyPhaseName = phases.some((phase) => !phase.name);
    if (hasEmptyPhaseName) {
      setError('Every workflow phase must have a name');
      return null;
    }

    const phaseNameCounts = phases.reduce((counts, phase) => {
      counts.set(phase.name, (counts.get(phase.name) || 0) + 1);
      return counts;
    }, new Map());

    const duplicatePhaseNames = Array.from(phaseNameCounts.entries())
      .filter(([, count]) => count > 1)
      .map(([name]) => name);

    if (duplicatePhaseNames.length > 0) {
      setError(
        `Duplicate phase names are not allowed: ${duplicatePhaseNames.join(', ')}`
      );
      return null;
    }

    const missingRequiredInputs = [];

    phases.forEach((phase) => {
      const inputSchema = Array.isArray(phase?.metadata?.input_schema)
        ? phase.metadata.input_schema
        : [];
      const phaseInputs = phase?.metadata?.phase_inputs || {};

      inputSchema.forEach((field) => {
        if (!field?.required || !field?.key) {
          return;
        }

        const value = phaseInputs[field.key];
        const isMissing =
          value === undefined ||
          value === null ||
          (typeof value === 'string' && !value.trim());

        if (isMissing) {
          missingRequiredInputs.push(
            `${phase.name}: ${field.label || field.key}`
          );
        }
      });
    });

    if (missingRequiredInputs.length > 0) {
      setError(
        `Missing required phase inputs: ${missingRequiredInputs.join(', ')}`
      );
      return null;
    }

    return {
      name: workflowName,
      description: workflowDescription,
      is_template: isTemplateWorkflow,
      tags: workflow?.tags || [],
      phases,
    };
  };

  const handleSave = async () => {
    const definition = buildWorkflowDefinition();
    if (!definition) {
      return;
    }

    try {
      const response = workflow?.id
        ? isPersistedWorkflow
          ? await workflowBuilderService.updateWorkflow(workflow.id, definition)
          : await workflowBuilderService.createWorkflow(definition)
        : await workflowBuilderService.createWorkflow(definition);

      onSave(response);
      setSuccessMessage('Workflow saved successfully');
      setError(null);
      setSaveDialogOpen(false);
    } catch (err) {
      setError(err.message || 'Failed to save workflow');
    }
  };

  const handleExecute = async () => {
    const definition = buildWorkflowDefinition();
    if (!definition) {
      return;
    }

    try {
      const persistedWorkflow = workflow?.id
        ? isPersistedWorkflow
          ? await workflowBuilderService.updateWorkflow(workflow.id, definition)
          : await workflowBuilderService.createWorkflow(definition)
        : await workflowBuilderService.createWorkflow(definition);

      const execution = await workflowBuilderService.executeWorkflow(
        persistedWorkflow.id,
        {
          topic: definition.name,
          source: 'workflow_canvas',
        }
      );

      setSuccessMessage(
        `Workflow execution started (${execution.execution_id || 'queued'})`
      );
      setExecutionId(execution.execution_id || null);
      setExecutionStatus(normalizeExecutionStatus(execution.status));
      setExecutionProgress(
        Number.isFinite(execution?.progress_percent)
          ? execution.progress_percent
          : 0
      );
      setExecutionResults({});
      setExecutionFinalOutput(null);
      setExecutionErrorMessage('');
      setExecutionPollingError('');
      setError(null);

      await loadExecutionHistory(persistedWorkflow?.id || null);
    } catch (err) {
      setError(err.message || 'Failed to execute workflow');
    }
  };

  return (
    <Box
      sx={{
        display: 'flex',
        overflowX: 'auto',
        height: 'calc(100vh - 220px)',
        minHeight: 640,
        gap: 2,
        p: 2,
      }}
    >
      <Card sx={{ width: 280, minWidth: 280, flexShrink: 0, overflow: 'auto' }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Available Phases
          </Typography>
          <Stack spacing={1}>
            {availablePhases.map((phase) => (
              <Button
                key={phase.name}
                variant="outlined"
                fullWidth
                size="small"
                onClick={() => addPhase(phase)}
                startIcon={<Plus size={16} />}
              >
                {phase.name}
              </Button>
            ))}
          </Stack>
        </CardContent>
      </Card>

      <Box
        sx={{
          flex: 1,
          minWidth: 420,
          height: '100%',
          minHeight: 600,
          position: 'relative',
          borderRadius: 1,
          overflow: 'hidden',
          border: '1px solid #ddd',
        }}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={(_, node) => setSelectedNode(node)}
          onPaneClick={() => setSelectedNode(null)}
          nodeTypes={nodeTypes}
        >
          <Background />
          <Controls />
          <MiniMap />
        </ReactFlow>

        {error && (
          <Alert
            severity="error"
            sx={{ position: 'absolute', top: 16, left: 16 }}
          >
            {error}
          </Alert>
        )}

        {successMessage && (
          <Alert
            severity="success"
            onClose={() => setSuccessMessage('')}
            sx={{ position: 'absolute', top: error ? 76 : 16, left: 16 }}
          >
            {successMessage}
          </Alert>
        )}
      </Box>

      <Card sx={{ width: 350, minWidth: 350, flexShrink: 0, overflow: 'auto' }}>
        <CardContent>
          {selectedNode ? (
            <Stack spacing={2}>
              <Button
                variant="text"
                size="small"
                onClick={() => setSelectedNode(null)}
                sx={{ alignSelf: 'flex-start', textTransform: 'none' }}
              >
                Back to workflow details
              </Button>

              <PhaseConfigPanel
                nodeId={selectedNode.id}
                phase={selectedNode.data.phase}
                availableModels={availableModels}
                onUpdate={updatePhaseConfig}
                onRemove={removePhase}
              />

              <Divider />

              <Box>
                <Typography variant="subtitle2" gutterBottom>
                  Phase Order
                </Typography>
                <Stack spacing={0.5}>
                  {nodes.map((node, index) => (
                    <Box
                      key={node.id}
                      draggable
                      onDragStart={(event) =>
                        handlePhaseDragStart(event, node.id)
                      }
                      onDragOver={(event) =>
                        handlePhaseDragOver(event, node.id)
                      }
                      onDrop={(event) => handlePhaseDrop(event, node.id)}
                      onDragEnd={clearDragState}
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        border: '1px solid #e0e0e0',
                        borderRadius: 1,
                        px: 1,
                        py: 0.5,
                        cursor: 'grab',
                        backgroundColor:
                          node.id === dragOverNodeId
                            ? '#e8f4ff'
                            : node.id === selectedNode.id
                              ? '#f3f8ff'
                              : 'transparent',
                      }}
                    >
                      <Button
                        size="small"
                        onClick={() => setSelectedNode(node)}
                        sx={{
                          textTransform: 'none',
                          justifyContent: 'flex-start',
                          gap: 0.5,
                          flex: 1,
                        }}
                      >
                        <GripVertical size={14} color="#9e9e9e" />
                        {index + 1}. {node.data.phase.name}
                      </Button>
                      <Box>
                        <IconButton
                          size="small"
                          onClick={() => movePhase(node.id, 'up')}
                          disabled={index === 0}
                        >
                          <ArrowUp size={14} />
                        </IconButton>
                        <IconButton
                          size="small"
                          onClick={() => movePhase(node.id, 'down')}
                          disabled={index === nodes.length - 1}
                        >
                          <ArrowDown size={14} />
                        </IconButton>
                        <IconButton
                          size="small"
                          color="error"
                          onClick={() => removePhase(node.id)}
                        >
                          <Trash2 size={14} />
                        </IconButton>
                      </Box>
                    </Box>
                  ))}
                </Stack>
              </Box>
            </Stack>
          ) : (
            <>
              <Typography variant="h6" gutterBottom>
                Workflow Details
              </Typography>
              <Stack spacing={2}>
                <TextField
                  label="Workflow Name"
                  value={workflowName}
                  onChange={(e) => setWorkflowName(e.target.value)}
                  fullWidth
                  size="small"
                />
                <TextField
                  label="Description"
                  value={workflowDescription}
                  onChange={(e) => setWorkflowDescription(e.target.value)}
                  fullWidth
                  multiline
                  rows={3}
                  size="small"
                />
                <Box>
                  <Typography variant="subtitle2" gutterBottom>
                    Phases: {nodes.length}
                  </Typography>
                  <Stack spacing={0.5}>
                    {nodes.map((node, index) => (
                      <Box
                        key={node.id}
                        draggable
                        onDragStart={(event) =>
                          handlePhaseDragStart(event, node.id)
                        }
                        onDragOver={(event) =>
                          handlePhaseDragOver(event, node.id)
                        }
                        onDrop={(event) => handlePhaseDrop(event, node.id)}
                        onDragEnd={clearDragState}
                        sx={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          border: '1px solid #e0e0e0',
                          borderRadius: 1,
                          px: 1,
                          py: 0.5,
                          cursor: 'grab',
                          backgroundColor:
                            node.id === dragOverNodeId
                              ? '#e8f4ff'
                              : 'transparent',
                        }}
                      >
                        <Button
                          size="small"
                          onClick={() => setSelectedNode(node)}
                          sx={{
                            textTransform: 'none',
                            justifyContent: 'flex-start',
                            gap: 0.5,
                            flex: 1,
                          }}
                        >
                          <GripVertical size={14} color="#9e9e9e" />
                          {index + 1}. {node.data.phase.name}
                        </Button>
                        <Box>
                          <IconButton
                            size="small"
                            onClick={() => movePhase(node.id, 'up')}
                            disabled={index === 0}
                          >
                            <ArrowUp size={14} />
                          </IconButton>
                          <IconButton
                            size="small"
                            onClick={() => movePhase(node.id, 'down')}
                            disabled={index === nodes.length - 1}
                          >
                            <ArrowDown size={14} />
                          </IconButton>
                          <IconButton
                            size="small"
                            color="error"
                            onClick={() => removePhase(node.id)}
                          >
                            <Trash2 size={14} />
                          </IconButton>
                        </Box>
                      </Box>
                    ))}
                  </Stack>
                </Box>
                <Stack direction="row" spacing={1}>
                  <Button
                    variant="contained"
                    startIcon={<Save size={18} />}
                    onClick={() => setSaveDialogOpen(true)}
                    fullWidth
                  >
                    Save
                  </Button>
                  <Button
                    variant="contained"
                    color="success"
                    startIcon={<Play size={18} />}
                    onClick={handleExecute}
                    fullWidth
                  >
                    Execute
                  </Button>
                </Stack>

                {executionId && (
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
                            color={
                              executionStatus === 'completed'
                                ? 'success'
                                : executionStatus === 'failed'
                                  ? 'error'
                                  : executionStatus === 'cancelled'
                                    ? 'warning'
                                    : 'default'
                            }
                          />
                          <Typography variant="caption" color="text.secondary">
                            {executionProgress}%
                          </Typography>
                        </Stack>

                        <LinearProgress
                          variant="determinate"
                          value={Math.max(
                            0,
                            Math.min(100, executionProgress || 0)
                          )}
                        />

                        {executionPollingError && (
                          <Alert severity="warning">
                            {executionPollingError}
                          </Alert>
                        )}

                        {executionErrorMessage && (
                          <Alert severity="error">
                            {executionErrorMessage}
                          </Alert>
                        )}

                        {Object.entries(executionResults || {}).map(
                          ([phaseName, phaseResult]) => {
                            const phaseStatus = normalizeExecutionStatus(
                              phaseResult?.status
                            );
                            const preview = getPhaseOutputPreview(phaseResult);
                            const executionMode =
                              getPhaseExecutionMode(phaseResult);

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
                                  <Typography
                                    variant="caption"
                                    fontWeight={600}
                                  >
                                    {phaseName}
                                  </Typography>
                                  <Chip
                                    size="small"
                                    label={phaseStatus || 'unknown'}
                                    color={
                                      phaseStatus === 'completed'
                                        ? 'success'
                                        : phaseStatus === 'failed'
                                          ? 'error'
                                          : 'default'
                                    }
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

                        <Box>
                          <Stack
                            direction="row"
                            alignItems="center"
                            justifyContent="space-between"
                            spacing={1}
                          >
                            <Typography variant="subtitle2">
                              Recent Executions
                            </Typography>
                            <Button
                              size="small"
                              onClick={loadExecutionHistory}
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
                              <Typography
                                variant="caption"
                                color="text.secondary"
                              >
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
                                      executionId === itemId
                                        ? 'primary.main'
                                        : 'divider',
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
                                      color={
                                        itemStatus === 'completed'
                                          ? 'success'
                                          : itemStatus === 'failed'
                                            ? 'error'
                                            : 'default'
                                      }
                                    />
                                  </Stack>
                                  <Button
                                    size="small"
                                    sx={{
                                      mt: 0.5,
                                      textTransform: 'none',
                                      p: 0,
                                    }}
                                    onClick={() => setExecutionId(itemId)}
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
                )}
              </Stack>
            </>
          )}
        </CardContent>
      </Card>

      <Dialog
        open={saveDialogOpen}
        onClose={() => setSaveDialogOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Save Workflow</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="textSecondary" gutterBottom>
            {isTemplateWorkflow
              ? 'Saving this template as a reusable workflow template'
              : 'Saving this workflow definition'}
          </Typography>
          <TextField
            label="Workflow Name"
            value={workflowName}
            onChange={(e) => setWorkflowName(e.target.value)}
            fullWidth
            margin="normal"
          />
          <TextField
            label="Description"
            value={workflowDescription}
            onChange={(e) => setWorkflowDescription(e.target.value)}
            fullWidth
            multiline
            rows={3}
            margin="normal"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSaveDialogOpen(false)}>Cancel</Button>
          <Button onClick={handleSave} variant="contained">
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default WorkflowCanvas;
