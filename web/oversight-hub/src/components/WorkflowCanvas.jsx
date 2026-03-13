import React, { useState, useCallback } from 'react';
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
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Stack,
  Chip,
  Typography,
  Alert,
} from '@mui/material';
import { Plus, Save, Play } from 'lucide-react';
import PhaseNode from './PhaseNode';
import PhaseConfigPanel from './PhaseConfigPanel';
import * as workflowBuilderService from '../services/workflowBuilderService';

const nodeTypes = {
  phase: PhaseNode,
};

const normalizePhaseName = (name) =>
  typeof name === 'string' ? name.trim() : '';

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
      agent: baseType,
      metadata: {
        ...(phase?.metadata || {}),
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
  const isPersistedWorkflow = Boolean(workflow?.isPersisted && workflow?.id);
  const isTemplateWorkflow = Boolean(workflow?.is_template);

  // Initialize with workflow if provided
  React.useEffect(() => {
    setWorkflowName(workflow?.name || '');
    setWorkflowDescription(workflow?.description || '');

    if (
      workflow &&
      Array.isArray(workflow.phases) &&
      workflow.phases.length > 0
    ) {
      const normalizedPhases = ensureUniqueWorkflowPhases(workflow.phases);

      const newNodes = normalizedPhases.map((phase, index) => ({
        id: `phase-${index}`,
        data: { label: phase.name, phase },
        position: { x: index * 250, y: 0 },
        type: 'phase',
      }));

      const newEdges = workflow.phases.slice(0, -1).map((_, index) => ({
        id: `edge-${index}`,
        source: `phase-${index}`,
        target: `phase-${index + 1}`,
      }));

      setNodes(newNodes);
      setEdges(newEdges);
    } else {
      setNodes([]);
      setEdges([]);
    }

    setSelectedNode(null);
  }, [workflow, setNodes, setEdges]);

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
      agent: baseType,
      metadata: {
        ...(phase?.metadata || {}),
        phase_type: baseType,
      },
    };

    const newNodeId = `phase-${nodes.length}`;
    const newNode = {
      id: newNodeId,
      data: { label: uniqueName, phase: phaseConfig },
      position: { x: nodes.length * 250, y: 0 },
      type: 'phase',
    };

    setNodes((nds) => [...nds, newNode]);

    // Auto-connect to last node
    if (nodes.length > 0) {
      const lastNode = nodes[nodes.length - 1];
      setEdges((eds) => [
        ...eds,
        {
          id: `edge-${nodes.length - 1}`,
          source: lastNode.id,
          target: newNodeId,
        },
      ]);
    }
  };

  const updatePhaseConfig = (nodeId, config) => {
    setNodes((nds) =>
      nds.map((node) =>
        node.id === nodeId
          ? { ...node, data: { ...node.data, phase: config } }
          : node
      )
    );
  };

  const removePhase = (nodeId) => {
    setNodes((nds) => nds.filter((n) => n.id !== nodeId));
    setEdges((eds) =>
      eds.filter((e) => e.source !== nodeId && e.target !== nodeId)
    );
  };

  const buildWorkflowDefinition = () => {
    // Verify workflow has phases
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
      agent: inferBasePhaseType(node?.data?.phase),
      description: node?.data?.phase?.description,
      timeout_seconds: node?.data?.phase?.timeout_seconds || 300,
      max_retries: node?.data?.phase?.max_retries || 3,
      skip_on_error: node?.data?.phase?.skip_on_error || false,
      required: node?.data?.phase?.required !== false,
      quality_threshold: node?.data?.phase?.quality_threshold,
      metadata: {
        ...(node?.data?.phase?.metadata || {}),
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
    if (definition) {
      try {
        const response = workflow?.id
          ? isPersistedWorkflow
            ? await workflowBuilderService.updateWorkflow(
                workflow.id,
                definition
              )
            : await workflowBuilderService.createWorkflow(definition)
          : await workflowBuilderService.createWorkflow(definition);
        onSave(response);
        setSuccessMessage('Workflow saved successfully');
        setSaveDialogOpen(false);
      } catch (err) {
        setError(err.message);
      }
    }
  };

  const handleExecute = async () => {
    const definition = buildWorkflowDefinition();
    if (definition) {
      try {
        const persistedWorkflow = workflow?.id
          ? isPersistedWorkflow
            ? await workflowBuilderService.updateWorkflow(
                workflow.id,
                definition
              )
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
        setError(null);
      } catch (err) {
        setError(err.message || 'Failed to execute workflow');
      }
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
      {/* Sidebar: Available Phases */}
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

      {/* Canvas: React Flow */}
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

      {/* Right Panel: Phase Config or Workflow Info */}
      <Card sx={{ width: 350, minWidth: 350, flexShrink: 0, overflow: 'auto' }}>
        <CardContent>
          {selectedNode ? (
            <PhaseConfigPanel
              nodeId={selectedNode.id}
              phase={selectedNode.data.phase}
              onUpdate={updatePhaseConfig}
              onRemove={removePhase}
            />
          ) : (
            <>
              <Typography variant="h6" gutterBottom>
                Workflow Details
              </Typography>
              {isTemplateWorkflow && (
                <Alert severity="info" sx={{ mb: 2 }}>
                  Editing template mode (persists as reusable template)
                </Alert>
              )}
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
                  <Stack direction="row" spacing={1} flexWrap="wrap">
                    {nodes.map((node) => (
                      <Chip
                        key={node.id}
                        label={node.data.phase.name}
                        size="small"
                        variant="outlined"
                      />
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
              </Stack>
            </>
          )}
        </CardContent>
      </Card>

      {/* Save Dialog */}
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
