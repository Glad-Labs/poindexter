import React, { useState, useEffect, useCallback } from 'react';
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
  Tab,
  Tabs,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  LinearProgress,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Paper,
} from '@mui/material';
import { Edit as EditIcon, Refresh as RefreshIcon } from '@mui/icons-material';
import {
  getWorkflowHistory,
  getExecutionDetails,
  getWorkflowStatistics,
  getPerformanceMetrics,
} from '../../services/workflowManagementService';

/**
 * WorkflowBuilder Component (Phase 3.3)
 *
 * Manage and monitor workflow execution:
 * - View workflow execution history
 * - Monitor workflow performance
 * - View execution details and results
 * - Track workflow statistics
 */
export const WorkflowBuilder = () => {
  const [activeTab, setActiveTab] = useState(0);
  const [workflows, setWorkflows] = useState([]);
  const [statistics, setStatistics] = useState(null);
  const [performanceMetrics, setPerformanceMetrics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [timeRange, setTimeRange] = useState('30d');
  const [selectedExecution, setSelectedExecution] = useState(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [detailsLoading, setDetailsLoading] = useState(false);

  const loadWorkflowData = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const [historyResult, statsResult, metricsResult] = await Promise.all([
        getWorkflowHistory({ limit: 100 }),
        getWorkflowStatistics(),
        getPerformanceMetrics(timeRange),
      ]);

      setWorkflows(historyResult.executions || []);
      setStatistics(statsResult);
      setPerformanceMetrics(metricsResult);
    } catch (err) {
      setError(`Failed to load workflow data: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [timeRange]);

  useEffect(() => {
    loadWorkflowData();
  }, [loadWorkflowData]);

  const handleViewDetails = async (executionId) => {
    setDetailsLoading(true);

    try {
      const details = await getExecutionDetails(executionId);
      setSelectedExecution(details);
      setDetailsOpen(true);
    } catch (err) {
      setError(`Failed to load execution details: ${err.message}`);
    } finally {
      setDetailsLoading(false);
    }
  };

  const getStatusColor = (status) => {
    switch (status?.toUpperCase()) {
      case 'COMPLETED':
        return 'success';
      case 'RUNNING':
        return 'info';
      case 'PENDING':
        return 'warning';
      case 'FAILED':
        return 'error';
      case 'PAUSED':
        return 'default';
      default:
        return 'default';
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ width: '100%' }}>
      {/* Header */}
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" sx={{ fontWeight: 'bold', mb: 1 }}>
          Workflow Builder & Monitor
        </Typography>
        <Typography variant="body2" color="textSecondary">
          Create, manage, and monitor workflow executions
        </Typography>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {/* Tab Navigation */}
      <Paper sx={{ mb: 3 }}>
        <Tabs value={activeTab} onChange={(e, newTab) => setActiveTab(newTab)}>
          <Tab label="Workflow History" />
          <Tab label="Statistics" />
          <Tab label="Performance" />
        </Tabs>
      </Paper>

      {/* Tab 0: Workflow History */}
      {activeTab === 0 && (
        <Stack spacing={3}>
          <Box
            sx={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <Typography variant="h6">
              Workflow Executions ({workflows.length})
            </Typography>
            <Button
              startIcon={<RefreshIcon />}
              onClick={loadWorkflowData}
              disabled={loading}
            >
              Refresh
            </Button>
          </Box>

          {workflows.length === 0 ? (
            <Alert severity="info">No workflow executions found</Alert>
          ) : (
            <TableContainer component={Card}>
              <Table>
                <TableHead>
                  <TableRow sx={{ backgroundColor: '#f5f5f5' }}>
                    <TableCell>Workflow ID</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell>Started</TableCell>
                    <TableCell>Duration</TableCell>
                    <TableCell>Tasks</TableCell>
                    <TableCell align="right">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {workflows.map((workflow) => (
                    <TableRow key={workflow.id} hover>
                      <TableCell
                        sx={{ fontFamily: 'monospace', fontSize: '0.85em' }}
                      >
                        {workflow.id?.substring(0, 8)}...
                      </TableCell>
                      <TableCell>
                        <Chip
                          label={workflow.status || 'UNKNOWN'}
                          size="small"
                          color={getStatusColor(workflow.status)}
                        />
                      </TableCell>
                      <TableCell>
                        {new Date(
                          workflow.created_at || Date.now()
                        ).toLocaleDateString()}
                      </TableCell>
                      <TableCell>
                        {workflow.duration_seconds
                          ? `${workflow.duration_seconds.toFixed(1)}s`
                          : '-'}
                      </TableCell>
                      <TableCell>{workflow.task_count || 0}</TableCell>
                      <TableCell align="right">
                        <IconButton
                          size="small"
                          onClick={() => handleViewDetails(workflow.id)}
                        >
                          <EditIcon fontSize="small" />
                        </IconButton>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Stack>
      )}

      {/* Tab 1: Statistics */}
      {activeTab === 1 && (
        <Grid container spacing={3}>
          <Grid item xs={12} sm={6} md={3}>
            <Card>
              <CardHeader title="Total Workflows" />
              <CardContent>
                <Typography variant="h4" sx={{ color: '#2196f3' }}>
                  {statistics?.total_workflows || 0}
                </Typography>
              </CardContent>
            </Card>
          </Grid>

          <Grid item xs={12} sm={6} md={3}>
            <Card>
              <CardHeader title="Completed" />
              <CardContent>
                <Typography variant="h4" sx={{ color: '#4caf50' }}>
                  {statistics?.completed_workflows || 0}
                </Typography>
                <Typography variant="caption" color="textSecondary">
                  {statistics?.total_workflows
                    ? (
                        ((statistics.completed_workflows || 0) * 100) /
                        statistics.total_workflows
                      ).toFixed(1)
                    : 0}
                  %
                </Typography>
              </CardContent>
            </Card>
          </Grid>

          <Grid item xs={12} sm={6} md={3}>
            <Card>
              <CardHeader title="Failed" />
              <CardContent>
                <Typography variant="h4" sx={{ color: '#f44336' }}>
                  {statistics?.failed_workflows || 0}
                </Typography>
              </CardContent>
            </Card>
          </Grid>

          <Grid item xs={12} sm={6} md={3}>
            <Card>
              <CardHeader title="Running" />
              <CardContent>
                <Typography variant="h4" sx={{ color: '#ff9800' }}>
                  {statistics?.running_workflows || 0}
                </Typography>
              </CardContent>
            </Card>
          </Grid>

          {/* Success Rate */}
          <Grid item xs={12}>
            <Card>
              <CardHeader title="Overall Success Rate" />
              <CardContent>
                <Box sx={{ mb: 1 }}>
                  <LinearProgress
                    variant="determinate"
                    value={statistics?.success_rate || 0}
                  />
                </Box>
                <Typography variant="body2">
                  {(statistics?.success_rate || 0).toFixed(1)}% of workflows
                  completed successfully
                </Typography>
              </CardContent>
            </Card>
          </Grid>

          {/* Average Metrics */}
          <Grid item xs={12} sm={6}>
            <Card>
              <CardHeader title="Avg Execution Time" />
              <CardContent>
                <Typography variant="h5">
                  {statistics?.avg_execution_time_seconds
                    ? `${statistics.avg_execution_time_seconds.toFixed(1)}s`
                    : '-'}
                </Typography>
              </CardContent>
            </Card>
          </Grid>

          <Grid item xs={12} sm={6}>
            <Card>
              <CardHeader title="Avg Tasks per Workflow" />
              <CardContent>
                <Typography variant="h5">
                  {statistics?.avg_tasks_per_workflow
                    ? statistics.avg_tasks_per_workflow.toFixed(1)
                    : '-'}
                </Typography>
              </CardContent>
            </Card>
          </Grid>
        </Grid>
      )}

      {/* Tab 2: Performance */}
      {activeTab === 2 && (
        <Stack spacing={3}>
          <Box sx={{ display: 'flex', gap: 2 }}>
            {['7d', '30d', '90d', 'all'].map((range) => (
              <Button
                key={range}
                variant={timeRange === range ? 'contained' : 'outlined'}
                onClick={() => setTimeRange(range)}
              >
                {range}
              </Button>
            ))}
          </Box>

          <Grid container spacing={3}>
            {performanceMetrics && (
              <>
                <Grid item xs={12} sm={6}>
                  <Card>
                    <CardHeader title="Peak Execution Time" />
                    <CardContent>
                      <Typography variant="h5">
                        {performanceMetrics.max_execution_time_seconds
                          ? `${performanceMetrics.max_execution_time_seconds.toFixed(1)}s`
                          : '-'}
                      </Typography>
                    </CardContent>
                  </Card>
                </Grid>

                <Grid item xs={12} sm={6}>
                  <Card>
                    <CardHeader title="Min Execution Time" />
                    <CardContent>
                      <Typography variant="h5">
                        {performanceMetrics.min_execution_time_seconds
                          ? `${performanceMetrics.min_execution_time_seconds.toFixed(1)}s`
                          : '-'}
                      </Typography>
                    </CardContent>
                  </Card>
                </Grid>

                <Grid item xs={12}>
                  <Card>
                    <CardHeader title="Performance Trend" />
                    <CardContent>
                      <LinearProgress
                        variant="determinate"
                        value={Math.min(
                          100,
                          (performanceMetrics.throughput || 0) * 10
                        )}
                      />
                      <Typography
                        variant="caption"
                        sx={{ mt: 1, display: 'block' }}
                      >
                        Throughput: {performanceMetrics.throughput || 0}{' '}
                        workflows/day
                      </Typography>
                    </CardContent>
                  </Card>
                </Grid>
              </>
            )}
          </Grid>
        </Stack>
      )}

      {/* Execution Details Dialog */}
      <Dialog
        open={detailsOpen}
        onClose={() => setDetailsOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>Execution Details</DialogTitle>
        <DialogContent>
          {detailsLoading && <CircularProgress />}

          {!detailsLoading && selectedExecution && (
            <Stack spacing={2} sx={{ pt: 2 }}>
              <Box>
                <Typography variant="subtitle2">Execution ID</Typography>
                <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                  {selectedExecution.id}
                </Typography>
              </Box>

              <Box>
                <Typography variant="subtitle2">Status</Typography>
                <Chip
                  label={selectedExecution.status}
                  color={getStatusColor(selectedExecution.status)}
                />
              </Box>

              <Box>
                <Typography variant="subtitle2">Execution Time</Typography>
                <Typography variant="body2">
                  {selectedExecution.duration_seconds
                    ? `${selectedExecution.duration_seconds.toFixed(2)}s`
                    : 'N/A'}
                </Typography>
              </Box>

              {selectedExecution.task_results && (
                <Box>
                  <Typography variant="subtitle2">Task Results</Typography>
                  <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                    {typeof selectedExecution.task_results === 'string'
                      ? selectedExecution.task_results
                      : JSON.stringify(selectedExecution.task_results, null, 2)}
                  </Typography>
                </Box>
              )}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDetailsOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default WorkflowBuilder;
