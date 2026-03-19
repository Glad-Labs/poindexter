import logger from '@/lib/logger';
import React, { useState, useEffect, useRef } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Grid,
  LinearProgress,
  Chip,
  Alert,
  IconButton,
  Tooltip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
} from '@mui/material';
import {
  TrendingDown as SavingsIcon,
  Warning as WarningIcon,
  Refresh as RefreshIcon,
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
} from '@mui/icons-material';
import {
  getCostMetrics,
  getCostsByPhase,
  getCostsByModel,
  getCostHistory,
  getBudgetStatus,
} from '../services/cofounderAgentClient';

/**
 * Cost Metrics Dashboard Component
 *
 * Displays real-time cost analytics including:
 * - Monthly budget usage and remaining balance ($100/month)
 * - AI cache performance and savings
 * - Model router efficiency
 * - Intervention alerts
 * - Total cost optimization impact
 */

const CostMetricsDashboard = () => {
  const [metrics, setMetrics] = useState(null);
  const [costsByPhase, setCostsByPhase] = useState(null);
  const [costsByModel, setCostsByModel] = useState(null);
  const [costHistory, setCostHistory] = useState(null);
  const [budgetStatus, setBudgetStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);

  // Fetch all cost metrics from APIs
  const fetchMetrics = async () => {
    try {
      setLoading(true);
      setError(null);

      // Fetch all data in parallel
      const [mainMetrics, phaseData, modelData, historyData, budgetData] =
        await Promise.all([
          getCostMetrics(),
          getCostsByPhase('month'),
          getCostsByModel('month'),
          getCostHistory('week'),
          getBudgetStatus(150.0),
        ]);

      // Validate and set main metrics
      const metricsData = mainMetrics?.costs || mainMetrics;
      setMetrics(metricsData);
      setCostsByPhase(phaseData?.phases || []);
      setCostsByModel(modelData?.models || []);
      setCostHistory(historyData?.daily_data || []);
      setBudgetStatus(budgetData);
      setLastUpdated(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch metrics');
      logger.error('Error fetching cost metrics:', err);
    } finally {
      setLoading(false);
    }
  };

  // Fetch on mount and set up auto-refresh every 60 seconds
  const cancelledRef = useRef(false);
  useEffect(() => {
    cancelledRef.current = false;

    const safeFetch = async () => {
      try {
        setLoading(true);
        setError(null);

        const [mainMetrics, phaseData, modelData, historyData, budgetData] =
          await Promise.all([
            getCostMetrics(),
            getCostsByPhase('month'),
            getCostsByModel('month'),
            getCostHistory('week'),
            getBudgetStatus(150.0),
          ]);

        if (cancelledRef.current) return;

        const metricsData = mainMetrics?.costs || mainMetrics;
        setMetrics(metricsData);
        setCostsByPhase(phaseData?.phases || []);
        setCostsByModel(modelData?.models || []);
        setCostHistory(historyData?.daily_data || []);
        setBudgetStatus(budgetData);
        setLastUpdated(new Date());
      } catch (err) {
        if (cancelledRef.current) return;
        setError(
          err instanceof Error ? err.message : 'Failed to fetch metrics'
        );
        logger.error('Error fetching cost metrics:', err);
      } finally {
        if (!cancelledRef.current) setLoading(false);
      }
    };

    safeFetch();
    const interval = setInterval(safeFetch, 60000);
    return () => {
      cancelledRef.current = true;
      clearInterval(interval);
    };
  }, []);

  // Calculate budget usage percentage from budgetStatus if available
  const budgetUsagePercent = budgetStatus
    ? budgetStatus.percent_used
    : metrics?.budget?.current_spent
      ? (metrics.budget.current_spent / metrics.budget.monthly_limit) * 100
      : 0;

  // Determine budget status color
  const getBudgetStatusColor = () => {
    if (budgetUsagePercent >= 90) return 'error';
    if (budgetUsagePercent >= 75) return 'warning';
    return 'success';
  };

  // Get actual budget data from budgetStatus or metrics
  const budgetData = budgetStatus || metrics?.budget || {};
  const monthlyBudget = budgetData.monthly_budget || 150.0;
  const amountSpent = budgetData.amount_spent || budgetData.current_spent || 0;
  const amountRemaining =
    budgetData.amount_remaining || budgetData.remaining || 0;

  if (loading && !metrics) {
    return (
      <Box sx={{ p: 3 }}>
        <LinearProgress />
        <Typography sx={{ mt: 2 }}>Loading cost metrics...</Typography>
      </Box>
    );
  }

  if (error) {
    return (
      <Alert severity="error" sx={{ m: 3 }}>
        {error}
      </Alert>
    );
  }

  if (!metrics) {
    return (
      <Alert severity="info" sx={{ m: 3 }}>
        No cost metrics available
      </Alert>
    );
  }

  return (
    <Box sx={{ p: 3 }}>
      {/* Header */}
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          mb: 3,
        }}
      >
        <Typography variant="h4" component="h1">
          💰 Cost Metrics Dashboard
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          {lastUpdated && (
            <Typography variant="caption" color="text.secondary">
              Last updated: {lastUpdated.toLocaleTimeString()}
            </Typography>
          )}
          <Tooltip title="Refresh metrics">
            <IconButton onClick={fetchMetrics} disabled={loading}>
              <RefreshIcon />
            </IconButton>
          </Tooltip>
        </Box>
      </Box>

      {/* Budget Overview */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            📊 Monthly Budget Status
          </Typography>
          <Grid container spacing={2}>
            <Grid
              sx={{
                width: '100%',
                '@media (min-width: 960px)': { width: 'calc(50% - 8px)' },
              }}
            >
              <Typography variant="body2" color="text.secondary">
                Budget Used
              </Typography>
              <Typography variant="h5">
                ${amountSpent.toFixed(2)} / ${monthlyBudget.toFixed(2)}
              </Typography>
            </Grid>
            <Grid
              sx={{
                width: '100%',
                '@media (min-width: 960px)': { width: 'calc(50% - 8px)' },
              }}
            >
              <Typography variant="body2" color="text.secondary">
                Remaining
              </Typography>
              <Typography variant="h5" color={getBudgetStatusColor()}>
                ${amountRemaining.toFixed(2)}
              </Typography>
            </Grid>
            <Grid sx={{ width: '100%' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <LinearProgress
                  variant="determinate"
                  value={Math.min(budgetUsagePercent, 100)}
                  color={getBudgetStatusColor()}
                  sx={{ flexGrow: 1, height: 10, borderRadius: 5 }}
                />
                <Typography variant="body2">
                  {budgetUsagePercent.toFixed(1)}%
                </Typography>
              </Box>
            </Grid>
          </Grid>

          {/* Budget Alerts from new API */}
          {budgetStatus?.alerts && budgetStatus.alerts.length > 0 && (
            <Box sx={{ mt: 2 }}>
              {budgetStatus.alerts.map((alert, index) => (
                <Alert
                  key={index}
                  severity={
                    alert.level === 'critical'
                      ? 'error'
                      : alert.level === 'warning'
                        ? 'warning'
                        : 'info'
                  }
                  icon={<WarningIcon />}
                  sx={{ mb: 1 }}
                >
                  {alert.message}
                </Alert>
              ))}
            </Box>
          )}

          {/* Projected costs */}
          {budgetStatus?.projected_final_cost && (
            <Box sx={{ mt: 2, p: 2, bgcolor: 'info.light', borderRadius: 1 }}>
              <Typography variant="body2" color="text.secondary">
                Projected Monthly Cost
              </Typography>
              <Typography variant="h6">
                ${budgetStatus.projected_final_cost.toFixed(2)}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Based on current daily burn rate: $
                {budgetStatus.daily_burn_rate.toFixed(4)}/day
              </Typography>
            </Box>
          )}
        </CardContent>
      </Card>

      {/* Cost Breakdown by Phase */}
      {costsByPhase && costsByPhase.length > 0 && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              📈 Costs by Pipeline Phase
            </Typography>
            <TableContainer component={Paper} variant="outlined">
              <Table size="small">
                <TableHead>
                  <TableRow sx={{ bgcolor: 'action.hover' }}>
                    <TableCell>Phase</TableCell>
                    <TableCell align="right">Cost</TableCell>
                    <TableCell align="right">Tasks</TableCell>
                    <TableCell align="right">Avg Cost/Task</TableCell>
                    <TableCell align="right">% of Total</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {costsByPhase.map((phase, idx) => (
                    <TableRow key={idx}>
                      <TableCell>
                        <Chip
                          label={phase.phase}
                          size="small"
                          variant="outlined"
                        />
                      </TableCell>
                      <TableCell align="right">
                        ${phase.total_cost.toFixed(4)}
                      </TableCell>
                      <TableCell align="right">{phase.task_count}</TableCell>
                      <TableCell align="right">
                        ${phase.avg_cost.toFixed(4)}
                      </TableCell>
                      <TableCell align="right">
                        {phase.percent_of_total.toFixed(1)}%
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </CardContent>
        </Card>
      )}

      {/* Cost Breakdown by Model */}
      {costsByModel && costsByModel.length > 0 && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              🤖 Costs by AI Model
            </Typography>
            <TableContainer component={Paper} variant="outlined">
              <Table size="small">
                <TableHead>
                  <TableRow sx={{ bgcolor: 'action.hover' }}>
                    <TableCell>Model</TableCell>
                    <TableCell align="right">Provider</TableCell>
                    <TableCell align="right">Cost</TableCell>
                    <TableCell align="right">Tasks</TableCell>
                    <TableCell align="right">Avg Cost/Task</TableCell>
                    <TableCell align="right">% of Total</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {costsByModel.map((model, idx) => (
                    <TableRow key={idx}>
                      <TableCell>{model.model}</TableCell>
                      <TableCell align="right">
                        <Chip
                          label={model.provider}
                          size="small"
                          color={
                            model.provider === 'ollama' ? 'success' : 'primary'
                          }
                          variant="outlined"
                        />
                      </TableCell>
                      <TableCell align="right">
                        ${model.total_cost.toFixed(4)}
                      </TableCell>
                      <TableCell align="right">{model.task_count}</TableCell>
                      <TableCell align="right">
                        ${model.avg_cost_per_task.toFixed(4)}
                      </TableCell>
                      <TableCell align="right">
                        {model.percent_of_total.toFixed(1)}%
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </CardContent>
        </Card>
      )}

      {/* Cost Trend */}
      {costHistory && costHistory.length > 0 && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              📉 Cost History (Last 7 Days)
            </Typography>
            <TableContainer component={Paper} variant="outlined">
              <Table size="small">
                <TableHead>
                  <TableRow sx={{ bgcolor: 'action.hover' }}>
                    <TableCell>Date</TableCell>
                    <TableCell align="right">Cost</TableCell>
                    <TableCell align="right">Tasks</TableCell>
                    <TableCell align="right">Avg Cost/Task</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {costHistory.map((day, idx) => (
                    <TableRow key={idx}>
                      <TableCell>{day.date}</TableCell>
                      <TableCell align="right">
                        ${day.cost.toFixed(4)}
                      </TableCell>
                      <TableCell align="right">{day.tasks}</TableCell>
                      <TableCell align="right">
                        ${day.avg_cost.toFixed(4)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </CardContent>
        </Card>
      )}

      {/* AI Cache Performance - Optional if available */}
      {metrics?.ai_cache && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              🚀 AI Cache Performance
            </Typography>
            <Grid container spacing={2}>
              <Grid
                sx={{
                  width: '100%',
                  '@media (min-width: 960px)': { width: 'calc(25% - 6px)' },
                }}
              >
                <Typography variant="body2" color="text.secondary">
                  Hit Rate
                </Typography>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <Typography variant="h5">
                    {metrics.ai_cache.hit_rate_percentage.toFixed(1)}%
                  </Typography>
                  {metrics.ai_cache.hit_rate_percentage >= 50 ? (
                    <CheckCircleIcon color="success" />
                  ) : (
                    <ErrorIcon color="warning" />
                  )}
                </Box>
              </Grid>
              <Grid
                sx={{
                  width: '100%',
                  '@media (min-width: 960px)': { width: 'calc(25% - 6px)' },
                }}
              >
                <Typography variant="body2" color="text.secondary">
                  Cache Hits
                </Typography>
                <Typography variant="h5">
                  {metrics.ai_cache.cache_hits.toLocaleString()}
                </Typography>
              </Grid>
              <Grid
                sx={{
                  width: '100%',
                  '@media (min-width: 960px)': { width: 'calc(25% - 6px)' },
                }}
              >
                <Typography variant="body2" color="text.secondary">
                  Memory Entries
                </Typography>
                <Typography variant="h5">
                  {metrics.ai_cache.memory_entries.toLocaleString()}
                </Typography>
              </Grid>
              <Grid
                sx={{
                  width: '100%',
                  '@media (min-width: 960px)': { width: 'calc(25% - 6px)' },
                }}
              >
                <Typography variant="body2" color="text.secondary">
                  Estimated Savings
                </Typography>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <Typography variant="h5" color="success.main">
                    ${metrics.ai_cache.estimated_savings_usd.toFixed(2)}
                  </Typography>
                  <SavingsIcon color="success" />
                </Box>
              </Grid>
            </Grid>
          </CardContent>
        </Card>
      )}

      {/* Model Router Performance - Optional if available */}
      {metrics?.model_router && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              🎯 Model Router Efficiency
            </Typography>
            <Grid container spacing={2}>
              <Grid
                sx={{
                  width: '100%',
                  '@media (min-width: 960px)': { width: 'calc(33.333% - 5px)' },
                }}
              >
                <Typography variant="body2" color="text.secondary">
                  Budget Model Usage
                </Typography>
                <Typography variant="h5">
                  {metrics.model_router.budget_model_percentage.toFixed(1)}%
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {metrics.model_router.budget_model_uses.toLocaleString()} /{' '}
                  {metrics.model_router.total_requests.toLocaleString()}{' '}
                  requests
                </Typography>
              </Grid>
              <Grid
                sx={{
                  width: '100%',
                  '@media (min-width: 960px)': { width: 'calc(33.333% - 5px)' },
                }}
              >
                <Typography variant="body2" color="text.secondary">
                  Cost Savings
                </Typography>
                <Typography variant="h5" color="success.main">
                  ${metrics.model_router.estimated_savings_usd.toFixed(2)}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {metrics.model_router.savings_percentage.toFixed(1)}%
                  reduction
                </Typography>
              </Grid>
              <Grid
                sx={{
                  width: '100%',
                  '@media (min-width: 960px)': { width: 'calc(33.333% - 5px)' },
                }}
              >
                <Typography variant="body2" color="text.secondary">
                  Optimization Status
                </Typography>
                <Chip
                  label={metrics.summary.optimization_status}
                  color={
                    metrics.summary.optimization_status === 'Excellent'
                      ? 'success'
                      : metrics.summary.optimization_status === 'Good'
                        ? 'primary'
                        : 'warning'
                  }
                  icon={<CheckCircleIcon />}
                />
              </Grid>
            </Grid>
          </CardContent>
        </Card>
      )}

      {/* Intervention Alerts - Optional if available */}
      {metrics?.interventions && metrics.interventions.pending_count > 0 && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              ⚠️ Pending Interventions
            </Typography>
            <Alert severity="warning" icon={<WarningIcon />}>
              <Typography variant="body2">
                <strong>{metrics.interventions.pending_count}</strong> tasks are
                pending budget approval (threshold: $
                {metrics.interventions.budget_threshold_usd.toFixed(2)})
              </Typography>
              {metrics.interventions.pending_task_ids.length > 0 && (
                <Typography variant="caption" sx={{ display: 'block', mt: 1 }}>
                  Task IDs: {metrics.interventions.pending_task_ids.join(', ')}
                </Typography>
              )}
            </Alert>
          </CardContent>
        </Card>
      )}

      {/* Summary Card */}
      <Card
        sx={{
          background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
          color: 'white',
        }}
      >
        <CardContent>
          <Typography variant="h6" gutterBottom sx={{ color: 'white' }}>
            💡 Monthly Cost Summary
          </Typography>
          <Grid container spacing={2}>
            <Grid
              sx={{
                width: '100%',
                '@media (min-width: 960px)': { width: 'calc(33.333% - 5px)' },
              }}
            >
              <Typography
                variant="body2"
                sx={{ color: 'rgba(255,255,255,0.8)' }}
              >
                Total Spent This Month
              </Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Typography variant="h4" sx={{ color: 'white' }}>
                  ${budgetStatus?.amount_spent.toFixed(2) || '0.00'}
                </Typography>
              </Box>
            </Grid>
            <Grid
              sx={{
                width: '100%',
                '@media (min-width: 960px)': { width: 'calc(33.333% - 5px)' },
              }}
            >
              <Typography
                variant="body2"
                sx={{ color: 'rgba(255,255,255,0.8)' }}
              >
                Remaining Budget
              </Typography>
              <Typography variant="h4" sx={{ color: 'white' }}>
                $
                {budgetStatus?.amount_remaining.toFixed(2) ||
                  monthlyBudget.toFixed(2)}
              </Typography>
            </Grid>
            <Grid
              sx={{
                width: '100%',
                '@media (min-width: 960px)': { width: 'calc(33.333% - 5px)' },
              }}
            >
              <Typography
                variant="body2"
                sx={{ color: 'rgba(255,255,255,0.8)' }}
              >
                Projected Final
              </Typography>
              <Typography variant="h4" sx={{ color: 'white' }}>
                ${budgetStatus?.projected_final_cost.toFixed(2) || '0.00'}
              </Typography>
            </Grid>
          </Grid>
          <Typography
            variant="caption"
            sx={{ color: 'rgba(255,255,255,0.8)', display: 'block', mt: 2 }}
          >
            Based on current spending patterns and analysis
          </Typography>
        </CardContent>
      </Card>

      {/* Metrics Timestamp */}
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: 'block', mt: 2, textAlign: 'center' }}
      >
        Metrics generated: {lastUpdated?.toLocaleString() || 'Loading...'}
      </Typography>
    </Box>
  );
};

export default CostMetricsDashboard;
