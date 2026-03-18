import logger from '@/lib/logger';
import React, { useState, useEffect, useCallback } from 'react';
import {
  Box,
  Card,
  CardContent,
  CardHeader,
  Grid,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Typography,
  Alert,
  Divider,
  Chip,
  Button,
  Tooltip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Tabs,
  Tab,
} from '@mui/material';
import {
  AttachMoney as CostIcon,
  TrendingDown as SaveIcon,
  Speed as FastIcon,
  SyncAlt as BalanceIcon,
  Star as QualityIcon,
  Info as InfoIcon,
} from '@mui/icons-material';
import { modelService } from '../services/modelService';

const PHASES = ['research', 'outline', 'draft', 'assess', 'refine', 'finalize'];
const PHASE_NAMES = {
  research: 'Research',
  outline: 'Outline',
  draft: 'Draft',
  assess: 'Assess',
  refine: 'Refine',
  finalize: 'Finalize',
};

// Electricity cost tracking for Ollama models
// Power consumption estimates (watts) for different model sizes
// Based on typical GPU/CPU usage patterns for local LLM inference
const MODEL_POWER_CONSUMPTION = {
  // Small models (8B parameters) - ~30W average
  'qwen3:8b': 30,

  // Large models (27-35B parameters) - ~80W+ average
  'gemma3:27b': 80,
  'qwen3.5:35b': 85,

  // Default for unknown models
  default: 50,
};

// Average US electricity price: $0.12 per kWh
// Calculated per token: typical inference processes ~5 tokens/second
const ELECTRICITY_COST_CONFIG = {
  pricePerKwh: 0.12, // dollars per kilowatt-hour
  avgTokensPerSecond: 5, // typical inference throughput
  secondsPerPost: 600, // ~10 minutes for full blog post processing (6 phases)
};

// Default model definitions - will be updated with actual Ollama models
const AVAILABLE_MODELS = {
  ollama: {
    name: 'Ollama (Local)',
    models: [], // Will be populated from Ollama API
  },
  gpt: {
    name: 'OpenAI',
    models: [
      { id: 'gpt-3.5-turbo', name: 'GPT-3.5 Turbo', cost: 0.0005 },
      { id: 'gpt-4', name: 'GPT-4', cost: 0.003 },
      { id: 'gpt-4-turbo', name: 'GPT-4 Turbo', cost: 0.001 },
      { id: 'gpt-4o', name: 'GPT-4o', cost: 0.0015 },
    ],
  },
  claude: {
    name: 'Anthropic',
    models: [
      { id: 'claude-3-haiku', name: 'Claude 3 Haiku', cost: 0.00025 },
      { id: 'claude-3-sonnet', name: 'Claude 3 Sonnet', cost: 0.003 },
      { id: 'claude-3-opus', name: 'Claude 3 Opus', cost: 0.015 },
    ],
  },
};

// Recommended Ollama models for content generation (for reference/documentation)
const QUALITY_PRESETS = {
  fast: {
    label: 'Fast (Cheapest)',
    icon: <FastIcon />,
    color: 'success',
    description: 'Qwen 3 8B for all phases — fastest local inference',
    avgCost: 'Free (local)',
  },
  balanced: {
    label: 'Balanced',
    icon: <BalanceIcon />,
    color: 'warning',
    description:
      'Qwen 3 8B for research/outline/finalize, Qwen 3.5 35B for draft/refine, Gemma 3 27B for assess',
    avgCost: 'Free (local)',
  },
  quality: {
    label: 'Quality (Best)',
    icon: <QualityIcon />,
    color: 'info',
    description:
      'Qwen 3.5 35B for outline/draft/refine, Gemma 3 27B for assess, Qwen 3 8B for research/finalize',
    avgCost: 'Free (local)',
  },
};

/**
 * ModelSelectionPanel Component
 *
 * Allows users to:
 * 1. Quickly select quality preference (Fast/Balanced/Quality)
 * 2. Manually override individual phase selections
 * 3. See cost estimates in real-time
 * 4. Save preferences for future tasks
 *
 * Integration: Use in TaskCreationModal or as standalone dashboard
 *
 * Props:
 * - onSelectionChange: Callback function when model selections change
 * - initialQuality: Initial quality preference ('fast', 'balanced', 'quality')
 */
export function ModelSelectionPanel({
  onSelectionChange,
  initialQuality = 'balanced',
}) {
  // State
  const [qualityPreference, setQualityPreference] = useState(initialQuality);
  const [modelSelections, setModelSelections] = useState({
    research: 'auto',
    outline: 'auto',
    draft: 'auto',
    assess: 'auto',
    refine: 'auto',
    finalize: 'auto',
  });
  const [costEstimates, setCostEstimates] = useState({});
  const [electricityCosts, setElectricityCosts] = useState({});
  const [totalCost, setTotalCost] = useState(0);
  const [totalElectricityCost, setTotalElectricityCost] = useState(0);
  const [error, setError] = useState(null);
  const [phaseModels, setPhaseModels] = useState({});
  const [activeTab, setActiveTab] = useState(0); // Tab state

  // ============================================================================
  // FUNCTION DEFINITIONS (MUST BE BEFORE useEffect HOOKS THAT CALL THEM)
  // ============================================================================

  const getModelPowerConsumption = (modelId) => {
    // Return power consumption in watts for a model
    if (modelId === 'auto') {
      return MODEL_POWER_CONSUMPTION.default;
    }

    // Get exact match or find closest match
    if (MODEL_POWER_CONSUMPTION[modelId]) {
      return MODEL_POWER_CONSUMPTION[modelId];
    }

    // Try to match by base model name
    const baseModel = modelId.split(':')[0];
    for (const [key, power] of Object.entries(MODEL_POWER_CONSUMPTION)) {
      if (key.includes(baseModel)) {
        return power;
      }
    }

    // Return default for unknown models
    return MODEL_POWER_CONSUMPTION.default;
  };

  const calculateElectricityCost = useCallback((modelId, phaseIndex) => {
    // Only calculate electricity for Ollama models
    const isOllamaModel =
      !modelId.includes('gpt') && !modelId.includes('claude');
    if (!isOllamaModel) {
      return 0; // Cloud API models don't have local electricity costs
    }

    const powerWatts = getModelPowerConsumption(modelId);

    // Estimate processing time per phase (in seconds)
    // Research: 100s, Outline: 80s, Draft: 150s, Assess: 60s, Refine: 100s, Finalize: 50s
    const phaseProcessingTimes = {
      research: 100,
      outline: 80,
      draft: 150,
      assess: 60,
      refine: 100,
      finalize: 50,
    };

    const phases = [
      'research',
      'outline',
      'draft',
      'assess',
      'refine',
      'finalize',
    ];
    const processingSeconds = phaseProcessingTimes[phases[phaseIndex]] || 100;

    // Calculate energy consumption: (watts / 1000) * (seconds / 3600) = kWh
    const energyKwh = (powerWatts / 1000) * (processingSeconds / 3600);

    // Calculate cost: kWh * price per kWh
    const cost = energyKwh * ELECTRICITY_COST_CONFIG.pricePerKwh;

    return cost;
  }, []);

  const estimateCosts = useCallback(async () => {
    try {
      // Calculate costs based on model selections
      const getModelCost = (modelId) => {
        if (modelId === 'auto') return 0.002; // Default estimate for auto-select

        // Search through AVAILABLE_MODELS for the matching model
        for (const provider of Object.values(AVAILABLE_MODELS)) {
          const model = provider.models.find((m) => m.id === modelId);
          if (model) return model.cost;
        }
        return 0; // Default if not found
      };

      const phases = [
        'research',
        'outline',
        'draft',
        'assess',
        'refine',
        'finalize',
      ];
      const mockCosts = {};
      const mockElectricityCosts = {};
      let total = 0;
      let totalElectricity = 0;

      phases.forEach((phase, index) => {
        const modelId = modelSelections[phase];
        mockCosts[phase] = getModelCost(modelId);
        mockElectricityCosts[phase] = calculateElectricityCost(modelId, index);
        total += mockCosts[phase];
        totalElectricity += mockElectricityCosts[phase];
      });

      setCostEstimates(mockCosts);
      setElectricityCosts(mockElectricityCosts);
      setTotalCost(parseFloat(total.toFixed(4)));
      setTotalElectricityCost(parseFloat(totalElectricity.toFixed(4)));
    } catch (err) {
      logger.error('Error estimating costs:', err);
      setError('Failed to estimate costs');
    }
  }, [modelSelections, calculateElectricityCost]);

  const getDefaultPhaseModels = () => {
    // Fallback models when Ollama API is not available
    const defaultOllamaModels = [
      { id: 'qwen3:8b', name: 'Qwen 3 8B', cost: 0 },
      { id: 'qwen3.5:35b', name: 'Qwen 3.5 35B', cost: 0 },
      { id: 'gemma3:27b', name: 'Gemma 3 27B', cost: 0 },
    ];

    const defaultModels = { ...AVAILABLE_MODELS };
    defaultModels.ollama.models = defaultOllamaModels;

    return {
      research: defaultModels,
      outline: defaultModels,
      draft: defaultModels,
      assess: defaultModels,
      refine: defaultModels,
      finalize: defaultModels,
    };
  };

  const formatOllamaModelName = (modelId) => {
    // Convert "mistral:latest" to "Mistral" or "qwen2:7b" to "Qwen 2 7B"
    const name = modelId.split(':')[0]; // Get base name without tag

    // Create human-readable names
    const nameMap = {
      mistral: 'Mistral 7B',
      'neural-chat': 'Neural Chat 7B',
      llama2: 'Llama 2 7B',
      llama3: 'Llama 3',
      qwen2: 'Qwen 2 7B',
      'qwen2.5': 'Qwen 2.5',
      qwen3: 'Qwen 3',
      'qwen3.5': 'Qwen 3.5',
      'qwen3-coder': 'Qwen 3 Coder 30B',
      'qwen3-vl': 'Qwen 3 Vision 30B',
      mixtral: 'Mixtral 8x7B',
      gemma3: 'Gemma 3',
      'deepseek-coder': 'DeepSeek Coder 33B',
      'deepseek-r1': 'DeepSeek R1',
      llava: 'LLaVA (Vision)',
      'gpt-oss': 'GPT-OSS',
      qwq: 'QwQ',
    };

    // Look up the display name
    let displayName = nameMap[name] || name;

    // Add parameter size info if available
    if (modelId.includes('70b')) displayName += ' 70B';
    else if (modelId.includes('32b')) displayName += ' 32B';
    else if (modelId.includes('30b')) displayName += ' 30B';
    else if (modelId.includes('27b')) displayName += ' 27B';
    else if (modelId.includes('14b')) displayName += ' 14B';
    else if (modelId.includes('13b')) displayName += ' 13B';
    else if (modelId.includes('12b')) displayName += ' 12B';

    // Add quantization info for clarity
    if (modelId.includes('fp16')) displayName += ' (FP16)';
    else if (modelId.includes('q5')) displayName += ' (Q5)';

    return displayName;
  };

  const fetchAvailableModels = useCallback(async () => {
    try {
      // First, try to fetch from unified API (includes all providers)
      const models = await modelService.getAvailableModels(true); // Force refresh

      if (!models || models.length === 0) {
        // Fall back to Ollama-only if API returns nothing
        const { getOllamaModels } = await import('../services/ollamaService');
        const ollamaData = await getOllamaModels();

        if (!ollamaData || ollamaData.length === 0) {
          logger.warn('No models available, using defaults');
          setPhaseModels(getDefaultPhaseModels());
          setError('No models available - using defaults');
          return;
        }

        const ollamaModels = (ollamaData || []).map((model) => ({
          id: model.name,
          name: formatOllamaModelName(model.name),
          cost: 0,
        }));

        const updatedModels = { ...AVAILABLE_MODELS };
        updatedModels.ollama.models = ollamaModels;

        const modelsForAllPhases = {
          research: updatedModels,
          outline: updatedModels,
          draft: updatedModels,
          assess: updatedModels,
          refine: updatedModels,
          finalize: updatedModels,
        };

        setPhaseModels(modelsForAllPhases);
        setError(null);
        return;
      }

      // Group models by provider
      const grouped = modelService.groupModelsByProvider(models);

      // Convert grouped models to phase models format
      const formattedModels = {
        ollama: { name: 'Ollama (Local)', models: [] },
        gpt: { name: 'OpenAI', models: [] },
        claude: { name: 'Anthropic', models: [] },
        google: { name: 'Google', models: [] },
      };

      // Map grouped models to formatted structure
      Object.entries(grouped).forEach(([provider, providerModels]) => {
        if (!Array.isArray(providerModels) || providerModels.length === 0) {
          return; // Skip if no models for this provider
        }

        let targetKey = provider;
        if (provider === 'openai') targetKey = 'gpt';
        if (provider === 'anthropic') targetKey = 'claude';

        if (formattedModels[targetKey]) {
          formattedModels[targetKey].models = providerModels.map((model) => ({
            id: modelService.getModelValue(model),
            name:
              model.displayName ||
              modelService.formatModelDisplayName(model.name),
            cost: model.isFree ? 0 : 0.01, // Rough estimate for paid models
          }));
        }
      });

      // Build phase models with all providers available for all phases
      const modelsForAllPhases = {
        research: formattedModels,
        outline: formattedModels,
        draft: formattedModels,
        assess: formattedModels,
        refine: formattedModels,
        finalize: formattedModels,
      };

      setPhaseModels(modelsForAllPhases);
      setError(null);

      logger.log('✅ Loaded models from unified API:', {
        total: models.length,
        grouped,
      });
    } catch (err) {
      logger.error('Error fetching models:', err);
      logger.warn('Falling back to default models');

      // Fall back to default models
      setPhaseModels(getDefaultPhaseModels());
      setError('Using default models - API unavailable');
    }
  }, []);

  // ============================================================================
  // EFFECTS
  // ============================================================================

  // Load available models on mount
  useEffect(() => {
    fetchAvailableModels();
  }, [fetchAvailableModels]);

  // Update cost estimates when selections or quality preference changes
  useEffect(() => {
    estimateCosts();
  }, [modelSelections, qualityPreference, estimateCosts]);

  // Notify parent of changes - only include actual data dependencies
  useEffect(() => {
    if (onSelectionChange) {
      onSelectionChange({
        modelSelections,
        qualityPreference,
        estimatedCost: totalCost,
        electricityCost: totalElectricityCost,
        combinedCost: totalCost + totalElectricityCost,
      });
    }
  }, [
    modelSelections,
    qualityPreference,
    totalCost,
    totalElectricityCost,
    onSelectionChange,
  ]);

  const applyQualityPreset = async (preset) => {
    setQualityPreference(preset);

    // Auto-select models based on preset
    let newSelections;
    switch (preset) {
      case 'fast':
        // Fast: Use lightweight Ollama models for all phases
        newSelections = {
          research: 'qwen3:8b',
          outline: 'qwen3:8b',
          draft: 'qwen3:8b',
          assess: 'qwen3:8b',
          refine: 'qwen3:8b',
          finalize: 'qwen3:8b',
        };
        break;
      case 'balanced':
        // Balanced: Mix of capable models
        newSelections = {
          research: 'qwen3:8b',
          outline: 'qwen3:8b',
          draft: 'qwen3.5:35b',
          assess: 'gemma3:27b',
          refine: 'qwen3.5:35b',
          finalize: 'qwen3:8b',
        };
        break;
      case 'quality':
      default:
        // Quality: Use the best available models
        newSelections = {
          research: 'qwen3:8b',
          outline: 'qwen3.5:35b',
          draft: 'qwen3.5:35b',
          assess: 'gemma3:27b',
          refine: 'qwen3.5:35b',
          finalize: 'qwen3:8b',
        };
    }

    setModelSelections(newSelections);
  };

  const handlePhaseChange = (phase, model) => {
    setModelSelections({
      ...modelSelections,
      [phase]: model,
    });
  };

  const resetToAuto = () => {
    setModelSelections({
      research: 'auto',
      outline: 'auto',
      draft: 'auto',
      assess: 'auto',
      refine: 'auto',
      finalize: 'auto',
    });
  };

  const getPhaseIcon = (phase) => {
    const icons = {
      research: '🔍',
      outline: '📋',
      draft: '✍️',
      assess: '⭐',
      refine: '✨',
      finalize: '🎯',
    };
    return icons[phase] || '•';
  };

  return (
    <Box sx={{ width: '100%' }}>
      {/* Header */}
      <Card sx={{ mb: 3, backgroundColor: '#f5f5f5' }}>
        <CardHeader
          title="Model Selection & Cost Control"
          subheader="Configure AI models and review costs per pipeline step"
          avatar={<CostIcon sx={{ fontSize: 40, color: '#1976d2' }} />}
          sx={{ pb: 2 }}
        />
      </Card>

      {/* Error Alert */}
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* Tab Navigation */}
      <Card sx={{ mb: 3 }}>
        <Tabs
          value={activeTab}
          onChange={(e, newValue) => setActiveTab(newValue)}
          sx={{
            borderBottom: '1px solid #e0e0e0',
            backgroundColor: '#fafafa',
          }}
        >
          <Tab label="Quick Presets" />
          <Tab label="Fine-Tune Per Phase" />
          <Tab label="Cost Details" />
          <Tab label="Model Info" />
        </Tabs>

        {/* TAB 0: Quick Presets */}
        {activeTab === 0 && (
          <CardContent sx={{ pt: 3 }}>
            <Grid container spacing={2}>
              {Object.entries(QUALITY_PRESETS).map(([key, preset]) => (
                <Grid size={{ xs: 12, sm: 6, md: 4 }} key={key}>
                  <Button
                    fullWidth
                    variant={
                      qualityPreference === key ? 'contained' : 'outlined'
                    }
                    color={preset.color}
                    startIcon={preset.icon}
                    onClick={() => applyQualityPreset(key)}
                    sx={{
                      py: 3,
                      flexDirection: 'column',
                      alignItems: 'flex-start',
                      justifyContent: 'flex-start',
                      textAlign: 'left',
                      height: '100%',
                    }}
                  >
                    <Typography
                      variant="subtitle2"
                      sx={{ fontWeight: 'bold', mb: 1 }}
                    >
                      {preset.label}
                    </Typography>
                    <Typography variant="caption" sx={{ mb: 1 }}>
                      {preset.description}
                    </Typography>
                    <Typography
                      variant="caption"
                      sx={{ fontWeight: 'bold', mt: 1 }}
                    >
                      {preset.avgCost}
                    </Typography>
                  </Button>
                </Grid>
              ))}
            </Grid>
          </CardContent>
        )}

        {/* TAB 1: Fine-Tune Per Phase */}
        {activeTab === 1 && (
          <CardContent sx={{ pt: 3 }}>
            <Box sx={{ mb: 3, display: 'flex', justifyContent: 'flex-end' }}>
              <Tooltip title="Reset all phases to Auto-Select">
                <Button size="small" onClick={resetToAuto} variant="outlined">
                  Reset to Auto
                </Button>
              </Tooltip>
            </Box>

            <TableContainer component={Paper} variant="outlined">
              <Table size="small">
                <TableHead>
                  <TableRow sx={{ backgroundColor: '#f5f5f5' }}>
                    <TableCell sx={{ fontWeight: 'bold', width: '15%' }}>
                      Phase
                    </TableCell>
                    <TableCell sx={{ fontWeight: 'bold', width: '35%' }}>
                      Model
                    </TableCell>
                    <TableCell
                      sx={{ fontWeight: 'bold', align: 'right', width: '15%' }}
                    >
                      API Cost
                    </TableCell>
                    <TableCell
                      sx={{ fontWeight: 'bold', align: 'right', width: '15%' }}
                    >
                      ⚡ Elec.
                    </TableCell>
                    <TableCell
                      sx={{ fontWeight: 'bold', align: 'right', width: '20%' }}
                    >
                      Total
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {PHASES.map((phase, _idx) => (
                    <TableRow
                      key={phase}
                      sx={{
                        '&:nth-of-type(even)': { backgroundColor: '#fafafa' },
                      }}
                    >
                      <TableCell>
                        <Box
                          sx={{ display: 'flex', alignItems: 'center', gap: 1 }}
                        >
                          <Typography sx={{ fontSize: 18 }}>
                            {getPhaseIcon(phase)}
                          </Typography>
                          <Typography
                            sx={{ fontWeight: 500, fontSize: '0.9rem' }}
                          >
                            {PHASE_NAMES[phase]}
                          </Typography>
                        </Box>
                      </TableCell>
                      <TableCell>
                        {phaseModels[phase] && (
                          <FormControl size="small" sx={{ width: '100%' }}>
                            <InputLabel id={`phase-model-label-${phase}`}>
                              {PHASE_NAMES[phase]} model
                            </InputLabel>
                            <Select
                              labelId={`phase-model-label-${phase}`}
                              label={`${PHASE_NAMES[phase]} model`}
                              value={modelSelections[phase]}
                              onChange={(e) =>
                                handlePhaseChange(phase, e.target.value)
                              }
                            >
                              <MenuItem value="auto">Auto-Select</MenuItem>
                              <Divider />
                              {Object.entries(phaseModels[phase]).map(
                                ([providerKey, providerData]) => [
                                  <MenuItem
                                    key={`header-${providerKey}`}
                                    disabled
                                    sx={{
                                      fontWeight: 'bold',
                                      fontSize: '0.85rem',
                                      backgroundColor: '#f5f5f5',
                                    }}
                                  >
                                    {providerData.name}
                                  </MenuItem>,
                                  ...providerData.models.map((model) => (
                                    <MenuItem
                                      key={model.id}
                                      value={model.id}
                                      sx={{ pl: 4, fontSize: '0.85rem' }}
                                    >
                                      {model.name}
                                      {model.cost === 0 && (
                                        <Chip
                                          label="Free"
                                          size="small"
                                          color="success"
                                          variant="outlined"
                                          sx={{
                                            ml: 1,
                                            height: 18,
                                            fontSize: '0.65rem',
                                          }}
                                        />
                                      )}
                                    </MenuItem>
                                  )),
                                ]
                              )}
                            </Select>
                          </FormControl>
                        )}
                      </TableCell>
                      <TableCell align="right">
                        <Chip
                          label={`$${(costEstimates[phase] || 0).toFixed(4)}`}
                          size="small"
                          variant="outlined"
                          color={
                            (costEstimates[phase] || 0) === 0
                              ? 'success'
                              : 'warning'
                          }
                        />
                      </TableCell>
                      <TableCell align="right">
                        <Tooltip
                          title={`Power: ~${getModelPowerConsumption(modelSelections[phase])}W`}
                        >
                          <Chip
                            label={`$${(electricityCosts[phase] || 0).toFixed(4)}`}
                            size="small"
                            variant="outlined"
                            color={
                              (electricityCosts[phase] || 0) === 0
                                ? 'default'
                                : 'info'
                            }
                          />
                        </Tooltip>
                      </TableCell>
                      <TableCell align="right">
                        <Chip
                          label={`$${((costEstimates[phase] || 0) + (electricityCosts[phase] || 0)).toFixed(4)}`}
                          size="small"
                          variant="filled"
                          color={
                            (costEstimates[phase] || 0) +
                              (electricityCosts[phase] || 0) ===
                            0
                              ? 'success'
                              : (costEstimates[phase] || 0) +
                                    (electricityCosts[phase] || 0) >
                                  0.005
                                ? 'warning'
                                : 'default'
                          }
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </CardContent>
        )}

        {/* TAB 2: Cost Details */}
        {activeTab === 2 && (
          <CardContent sx={{ pt: 3 }}>
            <Grid container spacing={3} sx={{ mb: 3 }}>
              <Grid size={{ xs: 12, sm: 6, md: 4 }}>
                <Box
                  sx={{
                    p: 2.5,
                    backgroundColor: totalCost === 0 ? '#e8f5e9' : '#fff3e0',
                    borderRadius: 1.5,
                    borderLeft: '4px solid #1976d2',
                  }}
                >
                  <Typography
                    variant="subtitle2"
                    sx={{ fontWeight: 'bold', mb: 1 }}
                  >
                    API Cost Per Post
                  </Typography>
                  <Typography variant="caption" color="textSecondary">
                    Service provider fees
                  </Typography>
                  <Typography
                    variant="h5"
                    sx={{
                      fontWeight: 'bold',
                      mt: 1,
                      color:
                        totalCost === 0
                          ? '#4caf50'
                          : totalCost < 0.02
                            ? '#ff9800'
                            : '#f44336',
                    }}
                  >
                    ${totalCost.toFixed(4)}
                  </Typography>
                </Box>
              </Grid>

              <Grid size={{ xs: 12, sm: 6, md: 4 }}>
                <Box
                  sx={{
                    p: 2.5,
                    backgroundColor:
                      totalElectricityCost === 0 ? '#f5f5f5' : '#e3f2fd',
                    borderRadius: 1.5,
                    borderLeft: '4px solid #1976d2',
                  }}
                >
                  <Typography
                    variant="subtitle2"
                    sx={{ fontWeight: 'bold', mb: 1 }}
                  >
                    ⚡ Electricity Per Post
                  </Typography>
                  <Typography variant="caption" color="textSecondary">
                    Local power consumption
                  </Typography>
                  <Typography
                    variant="h5"
                    sx={{
                      fontWeight: 'bold',
                      mt: 1,
                      color: totalElectricityCost === 0 ? '#999' : '#1976d2',
                    }}
                  >
                    ${totalElectricityCost.toFixed(4)}
                  </Typography>
                </Box>
              </Grid>

              <Grid size={{ xs: 12, sm: 6, md: 4 }}>
                <Box
                  sx={{
                    p: 2.5,
                    backgroundColor:
                      totalCost + totalElectricityCost === 0
                        ? '#e8f5e9'
                        : totalCost + totalElectricityCost < 0.02
                          ? '#fff3e0'
                          : '#ffebee',
                    borderRadius: 1.5,
                    borderLeft: '4px solid #1976d2',
                  }}
                >
                  <Typography
                    variant="subtitle2"
                    sx={{ fontWeight: 'bold', mb: 1 }}
                  >
                    Total Combined
                  </Typography>
                  <Typography variant="caption" color="textSecondary">
                    API + Electricity
                  </Typography>
                  <Typography
                    variant="h5"
                    sx={{
                      fontWeight: 'bold',
                      mt: 1,
                      color:
                        totalCost + totalElectricityCost === 0
                          ? '#4caf50'
                          : totalCost + totalElectricityCost < 0.02
                            ? '#ff9800'
                            : '#f44336',
                    }}
                  >
                    ${(totalCost + totalElectricityCost).toFixed(4)}
                  </Typography>
                </Box>
              </Grid>
            </Grid>

            <Divider sx={{ my: 3 }} />

            <Box>
              <Typography
                variant="subtitle2"
                sx={{ fontWeight: 'bold', mb: 2 }}
              >
                Monthly Impact (30 posts)
              </Typography>
              <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
                <Box>
                  <Typography variant="caption" color="textSecondary">
                    API Cost
                  </Typography>
                  <Typography variant="h6" sx={{ fontWeight: 'bold' }}>
                    ${(totalCost * 30).toFixed(2)}
                  </Typography>
                </Box>
                <Box>
                  <Typography variant="caption" color="textSecondary">
                    Electricity Cost
                  </Typography>
                  <Typography variant="h6" sx={{ fontWeight: 'bold' }}>
                    ${(totalElectricityCost * 30).toFixed(2)}
                  </Typography>
                </Box>
                <Box>
                  <Typography variant="caption" color="textSecondary">
                    Combined Total
                  </Typography>
                  <Typography
                    variant="h6"
                    sx={{
                      fontWeight: 'bold',
                      color:
                        (totalCost + totalElectricityCost) * 30 === 0
                          ? '#4caf50'
                          : '#1976d2',
                    }}
                  >
                    ${((totalCost + totalElectricityCost) * 30).toFixed(2)}
                  </Typography>
                </Box>
              </Box>
            </Box>

            {(totalCost > 0 || totalElectricityCost > 0) && (
              <Alert severity="info" sx={{ mt: 3 }}>
                <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
                  <SaveIcon sx={{ fontSize: 18, mt: 0.5, flexShrink: 0 }} />
                  <Box>
                    <Typography
                      variant="body2"
                      sx={{ fontWeight: 'bold', mb: 1 }}
                    >
                      Cost Breakdown
                    </Typography>
                    {totalCost > 0 && (
                      <Typography
                        variant="caption"
                        sx={{ display: 'block', mb: 0.5 }}
                      >
                        • API costs: ${totalCost.toFixed(4)} per post
                      </Typography>
                    )}
                    {totalElectricityCost > 0 && (
                      <Typography
                        variant="caption"
                        sx={{ display: 'block', mb: 0.5 }}
                      >
                        • Electricity: ${totalElectricityCost.toFixed(4)} per
                        post
                      </Typography>
                    )}
                  </Box>
                </Box>
              </Alert>
            )}
          </CardContent>
        )}

        {/* TAB 3: Model Info */}
        {activeTab === 3 && (
          <CardContent sx={{ pt: 3 }}>
            <Grid container spacing={2} sx={{ mb: 3 }}>
              {Object.entries(AVAILABLE_MODELS).map(([key, provider]) => (
                <Grid size={{ xs: 12, sm: 6, md: 4 }} key={key}>
                  <Box
                    sx={{
                      p: 2,
                      backgroundColor: '#f5f5f5',
                      borderRadius: 1.5,
                      borderLeft: '3px solid #1976d2',
                    }}
                  >
                    <Typography
                      variant="subtitle2"
                      sx={{ fontWeight: 'bold', mb: 1.5 }}
                    >
                      {provider.name}
                    </Typography>
                    <Box
                      sx={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 0.75,
                      }}
                    >
                      {provider.models.slice(0, 4).map((model) => (
                        <Box key={model.id}>
                          <Typography
                            variant="caption"
                            sx={{ display: 'block' }}
                          >
                            {model.name}
                          </Typography>
                          {model.cost === 0 ? (
                            <Chip
                              label="Free (Local)"
                              size="small"
                              color="success"
                              variant="outlined"
                              sx={{ height: 18, fontSize: '0.65rem' }}
                            />
                          ) : (
                            <Typography
                              variant="caption"
                              color="textSecondary"
                              sx={{ fontSize: '0.75rem' }}
                            >
                              ${(model.cost * 1000).toFixed(2)}/1K tokens
                            </Typography>
                          )}
                        </Box>
                      ))}
                      {provider.models.length > 4 && (
                        <Typography
                          variant="caption"
                          color="textSecondary"
                          sx={{ fontStyle: 'italic', mt: 1 }}
                        >
                          + {provider.models.length - 4} more models
                        </Typography>
                      )}
                    </Box>
                  </Box>
                </Grid>
              ))}
            </Grid>

            <Divider sx={{ my: 3 }} />

            <Box sx={{ p: 2, backgroundColor: '#f0f4f8', borderRadius: 1.5 }}>
              <Box
                sx={{
                  display: 'flex',
                  gap: 1,
                  alignItems: 'flex-start',
                  mb: 2,
                }}
              >
                <InfoIcon
                  sx={{
                    fontSize: 20,
                    color: '#1976d2',
                    mt: 0.5,
                    flexShrink: 0,
                  }}
                />
                <Box>
                  <Typography
                    variant="subtitle2"
                    sx={{ fontWeight: 'bold', mb: 1 }}
                  >
                    ⚡ About Electricity Costs
                  </Typography>
                  <Typography
                    variant="caption"
                    sx={{ display: 'block', mb: 0.5 }}
                  >
                    <strong>Only applies to local Ollama models</strong>
                  </Typography>
                  <Typography
                    variant="caption"
                    sx={{ display: 'block', mb: 1 }}
                  >
                    Cloud models (GPT, Claude) don&apos;t have local electricity
                    costs.
                  </Typography>
                  <Typography
                    variant="caption"
                    sx={{ display: 'block', mb: 0.5 }}
                  >
                    Small models (7B): ~30W | Medium (14B): ~50W | Large (30B+):
                    ~80-150W
                  </Typography>
                  <Typography variant="caption" color="textSecondary">
                    Based on ${ELECTRICITY_COST_CONFIG.pricePerKwh}/kWh US
                    average rate
                  </Typography>
                </Box>
              </Box>
            </Box>
          </CardContent>
        )}
      </Card>
    </Box>
  );
}

export default ModelSelectionPanel;
