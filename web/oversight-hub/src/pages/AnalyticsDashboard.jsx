import { useState, useEffect } from 'react';
import { Box, Card, CardContent, CardHeader, Grid, Tab, Tabs, CircularProgress, Alert } from '@mui/material';
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import { getKPIs, getTaskMetrics, getCostBreakdown } from '../services/analyticsService';
import './AnalyticsDashboard.css';

function AnalyticsDashboard() {
  const [activeTab, setActiveTab] = useState(0);
  const [kpiData, setKpiData] = useState(null);
  const [taskMetrics, setTaskMetrics] = useState(null);
  const [costBreakdown, setCostBreakdown] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [timeRange, setTimeRange] = useState('30d');

  useEffect(() => {
    const fetchAnalytics = async () => {
      try {
        setLoading(true);
        setError(null);

        const [kpi, tasks, costs] = await Promise.all([
          getKPIs(timeRange),
          getTaskMetrics(timeRange),
          getCostBreakdown(timeRange),
        ]);

        setKpiData(kpi);
        setTaskMetrics(tasks);
        setCostBreakdown(costs);
      } catch (err) {
        console.error('Failed to fetch analytics:', err);
        setError(err instanceof Error ? err.message : 'Failed to fetch analytics data');
      } finally {
        setLoading(false);
      }
    };

    fetchAnalytics();
    const interval = setInterval(fetchAnalytics, 60000); // Refresh every 60 seconds

    return () => clearInterval(interval);
  }, [timeRange]);

  const COLORS = ['#8884d8', '#82ca9d', '#ffc658', '#ff7c7c', '#8dd1e1'];

  const renderKPIDashboard = () => (
    <Grid container spacing={2}>
      {kpiData?.kpis?.map((kpi, idx) => (
        <Grid item xs={12} sm={6} md={3} key={idx}>
          <Card>
            <CardContent>
              <div className="kpi-card">
                <p className="kpi-label">{kpi.label}</p>
                <h2 className="kpi-value">{kpi.value}</h2>
                <p className="kpi-change">{kpi.change}</p>
              </div>
            </CardContent>
          </Card>
        </Grid>
      ))}
    </Grid>
  );

  const renderTaskMetrics = () => {
    if (!taskMetrics) return null;

    return (
      <Grid container spacing={2}>
        {taskMetrics.time_series && (
          <Grid item xs={12}>
            <Card>
              <CardHeader title="Task Execution Trend" />
              <CardContent>
                <ResponsiveContainer width="100%" height={300}>
                  <LineChart data={taskMetrics.time_series}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" />
                    <YAxis />
                    <Tooltip />
                    <Legend />
                    <Line type="monotone" dataKey="completed" stroke="#82ca9d" />
                    <Line type="monotone" dataKey="failed" stroke="#ff7c7c" />
                    <Line type="monotone" dataKey="pending" stroke="#ffc658" />
                  </LineChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          </Grid>
        )}

        {taskMetrics.by_status && (
          <Grid item xs={12} sm={6}>
            <Card>
              <CardHeader title="Tasks by Status" />
              <CardContent>
                <ResponsiveContainer width="100%" height={250}>
                  <PieChart>
                    <Pie
                      data={Object.entries(taskMetrics.by_status).map(([name, value]) => ({
                        name: name.charAt(0).toUpperCase() + name.slice(1),
                        value,
                      }))}
                      cx="50%"
                      cy="50%"
                      outerRadius={80}
                      label
                    >
                      {Object.entries(taskMetrics.by_status).map((_, index) => (
                        <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          </Grid>
        )}

        {taskMetrics.by_category && (
          <Grid item xs={12} sm={6}>
            <Card>
              <CardHeader title="Tasks by Category" />
              <CardContent>
                <ResponsiveContainer width="100%" height={250}>
                  <BarChart
                    data={Object.entries(taskMetrics.by_category).map(([category, count]) => ({
                      name: category,
                      count,
                    }))}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="name" />
                    <YAxis />
                    <Tooltip />
                    <Bar dataKey="count" fill="#8884d8" />
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          </Grid>
        )}
      </Grid>
    );
  };

  const renderCostAnalytics = () => {
    if (!costBreakdown) return null;

    return (
      <Grid container spacing={2}>
        {costBreakdown.by_provider && (
          <Grid item xs={12} sm={6}>
            <Card>
              <CardHeader title="Cost by Provider" />
              <CardContent>
                <ResponsiveContainer width="100%" height={300}>
                  <PieChart>
                    <Pie
                      data={Object.entries(costBreakdown.by_provider).map(([provider, cost]) => ({
                        name: provider,
                        value: parseFloat(cost),
                      }))}
                      cx="50%"
                      cy="50%"
                      outerRadius={80}
                      label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                    >
                      {Object.entries(costBreakdown.by_provider).map((_, index) => (
                        <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(value) => `$${value.toFixed(2)}`} />
                  </PieChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          </Grid>
        )}

        {costBreakdown.time_series && (
          <Grid item xs={12} sm={6}>
            <Card>
              <CardHeader title="Cost Trends" />
              <CardContent>
                <ResponsiveContainer width="100%" height={300}>
                  <BarChart data={costBreakdown.time_series}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" />
                    <YAxis />
                    <Tooltip formatter={(value) => `$${value.toFixed(2)}`} />
                    <Legend />
                    <Bar dataKey="total_cost" fill="#82ca9d" name="Total Cost ($)" />
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          </Grid>
        )}

        {costBreakdown.by_model && (
          <Grid item xs={12}>
            <Card>
              <CardHeader title="Cost by Model" />
              <CardContent>
                <div className="model-cost-table">
                  <table>
                    <thead>
                      <tr>
                        <th>Model</th>
                        <th>Total Cost</th>
                        <th>Requests</th>
                        <th>Avg Cost/Request</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(costBreakdown.by_model || {}).map(([model, data]) => (
                        <tr key={model}>
                          <td>{model}</td>
                          <td>${typeof data === 'object' ? data.total?.toFixed(2) : data.toFixed(2)}</td>
                          <td>{typeof data === 'object' ? data.requests?.toLocaleString() : '-'}</td>
                          <td>${typeof data === 'object' ? (data.total / data.requests).toFixed(4) : '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </Grid>
        )}
      </Grid>
    );
  };

  return (
    <Box className="analytics-dashboard">
      <div className="analytics-header">
        <h1>📊 Analytics Dashboard</h1>
        <select
          value={timeRange}
          onChange={(e) => setTimeRange(e.target.value)}
          className="time-range-select"
        >
          <option value="7d">Last 7 Days</option>
          <option value="30d">Last 30 Days</option>
          <option value="90d">Last 90 Days</option>
          <option value="all">All Time</option>
        </select>
      </div>

      {error && <Alert severity="error">{error}</Alert>}

      {loading ? (
        <div className="loading-container">
          <CircularProgress />
          <p>Loading analytics...</p>
        </div>
      ) : (
        <Box>
          <Tabs value={activeTab} onChange={(e, newValue) => setActiveTab(newValue)}>
            <Tab label="KPIs" />
            <Tab label="Tasks" />
            <Tab label="Costs" />
          </Tabs>

          <Box sx={{ mt: 2 }}>
            {activeTab === 0 && renderKPIDashboard()}
            {activeTab === 1 && renderTaskMetrics()}
            {activeTab === 2 && renderCostAnalytics()}
          </Box>
        </Box>
      )}
    </Box>
  );
}

export default AnalyticsDashboard;
