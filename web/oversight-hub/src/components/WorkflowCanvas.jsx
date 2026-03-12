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
  Typography,
  Alert,
  Divider,
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
import ExecutionStatusPanel from './ExecutionStatusPanel';
import useWorkflowExecution from '../hooks/useWorkflowExecution';
import useExecutionHistory from '../hooks/useExecutionHistory';
import useNodeDragDrop from '../hooks/useNodeDragDrop';
import * as workflowBuilderService from '../services/workflowBuilderService';
import { modelService } from '../services/modelService';

// ============================================================================
// Module-level constants & pure utility functions
// (tightly coupled to workflow graph — not extracted to hooks)
// ============================================================================

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

const resolvePhaseAgent = (phase = {}) => {
  const configuredAgent = normalizePhaseName(phase?.agent);
  if (isValidAgentId(configuredAgent)) {
    return configuredAgent;
  }

  const phaseType = inferBasePhaseType(phase);
  return getDefaultAgentForPhaseType(phaseType);
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

// ============================================================================
// WorkflowCanvas component — thin compositor
// ============================================================================

const WorkflowCanvas = ({ onSave, availablePhases, workflow = null }) => {
  // ---- Workflow graph state (ReactFlow) ------------------------------------
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedNode, setSelectedNode] = useState(null);

  // ---- Workflow metadata ---------------------------------------------------
  const [workflowName, setWorkflowName] = useState(workflow?.name || '');
  const [workflowDescription, setWorkflowDescription] = useState(
    workflow?.description || ''
  );
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [error, setError] = useState(null);
  const [successMessage, setSuccessMessage] = useState('');
  const [availableModels, setAvailableModels] = useState([]);

  const isPersistedWorkflow = Boolean(workflow?.isPersisted && workflow?.id);
  const isTemplateWorkflow = Boolean(workflow?.is_template);
  const effectiveWorkflowId = workflow?.id || null;

  // ---- Execution state (extracted hook) ------------------------------------
  const {
    executionId,
    setExecutionId,
    executionStatus,
    executionProgress,
    executionResults,
    executionFinalOutput,
    executionErrorMessage,
    executionPollingError,
    startExecution,
  } = useWorkflowExecution({
    onHistoryRefresh: (persistedId) => loadExecutionHistory(persistedId),
  });

  // ---- Execution history (extracted hook) ----------------------------------
  const {
    executionHistory,
    executionHistoryLoading,
    executionHistoryError,
    loadExecutionHistory,
  } = useExecutionHistory({
    workflowId: effectiveWorkflowId,
    executionId,
  });

  // ---- Drag-and-drop (extracted hook) --------------------------------------
  const {
    draggedNodeId,
    dragOverNodeId,
    handlePhaseDragStart,
    handlePhaseDragOver,
    handlePhaseDrop,
    clearDragState,
  } = useNodeDragDrop({
    onReorder: (sourceIndex, targetIndex) => {
      const phaseConfigs = nodes.map((node) => node.data.phase);
      const reordered = [...phaseConfigs];
      const [moved] = reordered.splice(sourceIndex, 1);
      reordered.splice(targetIndex, 0, moved);
      rebuildGraphFromPhases(reordered, moved?.name || null);
    },
  });

  // ---- Graph builders ------------------------------------------------------

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

  // ---- Lifecycle effects ---------------------------------------------------

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

  // ---- Phase mutation handlers ---------------------------------------------

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

  // ---- Workflow save / execute ---------------------------------------------

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
      await startExecution({ workflow, isPersistedWorkflow, definition });
      setSuccessMessage('Workflow execution started');
      setError(null);
    } catch (err) {
      setError(err.message || 'Failed to execute workflow');
    }
  };

  // ---- Phase order list (shared between selected/unselected views) ---------

  const renderPhaseOrderList = (highlightSelected) => (
    <Stack spacing={0.5}>
      {nodes.map((node, index) => (
        <Box
          key={node.id}
          draggable
          onDragStart={(event) => handlePhaseDragStart(event, node.id)}
          onDragOver={(event) => handlePhaseDragOver(event, node.id)}
          onDrop={(event) => handlePhaseDrop(event, node.id, nodes)}
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
                : highlightSelected && node.id === selectedNode?.id
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
  );

  // ---- Render --------------------------------------------------------------

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
      {/* Available Phases sidebar */}
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

      {/* ReactFlow canvas */}
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

      {/* Right panel — Phase config (node selected) or Workflow details */}
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
                {renderPhaseOrderList(true)}
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
                  {renderPhaseOrderList(false)}
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

                {/* Execution status + history (extracted sub-component) */}
                <ExecutionStatusPanel
                  executionId={executionId}
                  executionStatus={executionStatus}
                  executionProgress={executionProgress}
                  executionResults={executionResults}
                  executionFinalOutput={executionFinalOutput}
                  executionErrorMessage={executionErrorMessage}
                  executionPollingError={executionPollingError}
                  executionHistory={executionHistory}
                  executionHistoryLoading={executionHistoryLoading}
                  executionHistoryError={executionHistoryError}
                  onSelectExecution={setExecutionId}
                  onRefreshHistory={loadExecutionHistory}
                />
              </Stack>
            </>
          )}
        </CardContent>
      </Card>

      {/* Save dialog */}
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
