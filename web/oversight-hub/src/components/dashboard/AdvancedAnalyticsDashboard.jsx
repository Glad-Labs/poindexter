import logger from '@/lib/logger';
import React, { useState, useEffect } from 'react';
import {
  Box,
  Card,
  CardContent,
  CardHeader,
  CircularProgress,
  Alert,
  Stack,
  Button,
  ToggleButton,
  ToggleButtonGroup,
  Grid,
  Typography,
  LinearProgress,
} from '@mui/material';
import {
  getKPIs,
  getTaskMetrics,
  getCostBreakdown,
  getContentMetrics,
} from '../../services/analyticsService';

/**
 * AdvancedAnalyticsDashboard Component (Phase 2.1)
 *
 * Provides detailed analytics view with:
 * - KPI trends
 * - Task execution metrics
 * - Cost breakdown by provider
 * - Content publishing metrics
 * - System performance data
 */
export const AdvancedAnalyticsDashboard = () => {
  const [timeRange, setTimeRange] = useState('30d');
  const [kpiData, setKpiData] = useState(null);
  const [taskMetrics, setTaskMetrics] = useState(null);
  const [costBreakdown, setCostBreakdown] = useState(null);
  const [contentMetrics, setContentMetrics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  useEffect(() => {
    const loadAnalyticsData = async () => {
      setLoading(true);
      setError(null);

      try {
        const [kpis, tasks, costs, content] = await Promise.all([
          getKPIs(timeRange),
          getTaskMetrics(timeRange),
          getCostBreakdown(timeRange),
          getContentMetrics(timeRange),
        ]);

        setKpiData(kpis);
        setTaskMetrics(tasks);
        setCostBreakdown(costs);
        setContentMetrics(content);
      } catch (err) {
        logger.error('Failed to load analytics:', err);
        setError(`Failed to load analytics: ${err.message}`);
      } finally {
        setLoading(false);
      }
    };

    loadAnalyticsData();
  }, [timeRange, refreshTrigger]);

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ width: '100%' }}>
      {/* Header with Time Range Controls */}
      <Box
        sx={{
          mb: 3,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <Typography variant="h5" sx={{ fontWeight: 'bold' }}>
          Advanced Analytics
        </Typography>
        <ToggleButtonGroup
          value={timeRange}
          exclusive
          onChange={(e, newRange) => newRange && setTimeRange(newRange)}
          size="small"
        >
          <ToggleButton value="7d">7 Days</ToggleButton>
          <ToggleButton value="30d">30 Days</ToggleButton>
          <ToggleButton value="90d">90 Days</ToggleButton>
          <ToggleButton value="all">All Time</ToggleButton>
        </ToggleButtonGroup>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {/* KPI Metrics Row */}
      {kpiData && (
        <Grid container spacing={2} sx={{ mb: 3 }}>
          <Grid item xs={12} sm={6} md={3}>
            <Card>
              <CardHeader
                title="Total Revenue"
                subheader={kpiData?.kpis?.revenue?.currency}
              />
              <CardContent>
                <Typography variant="h4" sx={{ color: '#4caf50' }}>
                  ${kpiData?.kpis?.revenue?.current?.toLocaleString()}
                </Typography>
                <Typography variant="caption" sx={{ color: '#4caf50' }}>
                  ↑ {kpiData?.kpis?.revenue?.change}% from previous
                </Typography>
              </CardContent>
            </Card>
          </Grid>

          <Grid item xs={12} sm={6} md={3}>
            <Card>
              <CardHeader title="Content Published" subheader="Posts" />
              <CardContent>
                <Typography variant="h4" sx={{ color: '#2196f3' }}>
                  {kpiData?.kpis?.contentPublished?.current}
                </Typography>
                <Typography variant="caption" sx={{ color: '#2196f3' }}>
                  ↑ {kpiData?.kpis?.contentPublished?.change}% from previous
                </Typography>
              </CardContent>
            </Card>
          </Grid>

          <Grid item xs={12} sm={6} md={3}>
            <Card>
              <CardHeader title="Tasks Completed" subheader="Tasks" />
              <CardContent>
                <Typography variant="h4" sx={{ color: '#ff9800' }}>
                  {kpiData?.kpis?.tasksCompleted?.current}
                </Typography>
                <Typography variant="caption" sx={{ color: '#ff9800' }}>
                  ↑ {kpiData?.kpis?.tasksCompleted?.change}% from previous
                </Typography>
              </CardContent>
            </Card>
          </Grid>

          <Grid item xs={12} sm={6} md={3}>
            <Card>
              <CardHeader title="AI Cost Savings" subheader="USD" />
              <CardContent>
                <Typography variant="h4" sx={{ color: '#9c27b0' }}>
                  ${kpiData?.kpis?.aiSavings?.current?.toLocaleString()}
                </Typography>
                <Typography variant="caption" sx={{ color: '#9c27b0' }}>
                  ↑ {kpiData?.kpis?.aiSavings?.change}% from previous
                </Typography>
              </CardContent>
            </Card>
          </Grid>
        </Grid>
      )}

      {/* Task Metrics Card */}
      {taskMetrics && (
        <Card sx={{ mb: 3 }}>
          <CardHeader title="Task Execution Metrics" />
          <CardContent>
            <Stack spacing={2}>
              <Box>
                <Typography variant="subtitle2">Completion Rate</Typography>
                <LinearProgress
                  variant="determinate"
                  value={taskMetrics?.completion_rate || 0}
                />
                <Typography variant="caption">
                  {taskMetrics?.completion_rate || 0}%
                </Typography>
              </Box>

              <Box>
                <Typography variant="subtitle2">Success Rate</Typography>
                <LinearProgress
                  variant="determinate"
                  value={taskMetrics?.success_rate || 0}
                  sx={{ backgroundColor: '#e8f5e9' }}
                />
                <Typography variant="caption">
                  {taskMetrics?.success_rate || 0}%
                </Typography>
              </Box>

              <Grid container spacing={2}>
                <Grid item xs={12} sm={6}>
                  <Typography variant="subtitle2">
                    Avg Execution Time
                  </Typography>
                  <Typography variant="h6">
                    {taskMetrics?.avg_execution_time_seconds?.toFixed(2) || '0'}
                    s
                  </Typography>
                </Grid>
                <Grid item xs={12} sm={6}>
                  <Typography variant="subtitle2">
                    Median Execution Time
                  </Typography>
                  <Typography variant="h6">
                    {taskMetrics?.median_execution_time_seconds?.toFixed(2) ||
                      '0'}
                    s
                  </Typography>
                </Grid>
              </Grid>
            </Stack>
          </CardContent>
        </Card>
      )}

      {/* Cost Breakdown Card */}
      {costBreakdown && (
        <Card sx={{ mb: 3 }}>
          <CardHeader title="Cost Breakdown by Provider" />
          <CardContent>
            <Stack spacing={2}>
              {costBreakdown?.providers?.map((provider) => (
                <Box key={provider.name}>
                  <Box
                    sx={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      mb: 1,
                    }}
                  >
                    <Typography variant="subtitle2">{provider.name}</Typography>
                    <Typography variant="subtitle2" sx={{ fontWeight: 'bold' }}>
                      ${provider.cost?.toFixed(2) || '0.00'}
                    </Typography>
                  </Box>
                  <LinearProgress
                    variant="determinate"
                    value={provider.percentage || 0}
                  />
                  <Typography variant="caption">
                    {provider.percentage || 0}% of total
                  </Typography>
                </Box>
              ))}

              <Box sx={{ borderTop: '1px solid #e0e0e0', pt: 2, mt: 2 }}>
                <Typography variant="subtitle1" sx={{ fontWeight: 'bold' }}>
                  Total Cost: $
                  {costBreakdown?.total_cost?.toLocaleString() || '0.00'}
                </Typography>
              </Box>
            </Stack>
          </CardContent>
        </Card>
      )}

      {/* Content Metrics Card */}
      {contentMetrics && (
        <Card>
          <CardHeader title="Content Publishing Metrics" />
          <CardContent>
            <Grid container spacing={2}>
              <Grid item xs={12} sm={6} md={3}>
                <Box>
                  <Typography variant="subtitle2">Total Posts</Typography>
                  <Typography variant="h5">
                    {contentMetrics?.total_posts || 0}
                  </Typography>
                </Box>
              </Grid>

              <Grid item xs={12} sm={6} md={3}>
                <Box>
                  <Typography variant="subtitle2">Avg Quality Score</Typography>
                  <Typography variant="h5">
                    {contentMetrics?.avg_quality_score?.toFixed(1) || '0.0'}/10
                  </Typography>
                </Box>
              </Grid>

              <Grid item xs={12} sm={6} md={3}>
                <Box>
                  <Typography variant="subtitle2">Total Engagement</Typography>
                  <Typography variant="h5">
                    {contentMetrics?.total_engagement?.toLocaleString() || 0}
                  </Typography>
                </Box>
              </Grid>

              <Grid item xs={12} sm={6} md={3}>
                <Box>
                  <Typography variant="subtitle2">
                    Avg Engagement/Post
                  </Typography>
                  <Typography variant="h5">
                    {contentMetrics?.avg_engagement_per_post?.toFixed(0) || 0}
                  </Typography>
                </Box>
              </Grid>
            </Grid>
          </CardContent>
        </Card>
      )}

      {/* Refresh Button */}
      <Box sx={{ mt: 3, display: 'flex', justifyContent: 'center' }}>
        <Button
          variant="outlined"
          onClick={() => setRefreshTrigger((prev) => prev + 1)}
        >
          Refresh Data
        </Button>
      </Box>
    </Box>
  );
};

export default AdvancedAnalyticsDashboard;
