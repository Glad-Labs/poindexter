import logger from '@/lib/logger';
import { useState, useEffect, useRef } from 'react';
import './PerformanceDashboard.css';
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from 'recharts';
import { getApiUrl } from '../config/apiConfig';

function PerformanceDashboard() {
  const [performanceData, setPerformanceData] = useState(null);
  const [clientMetrics, setClientMetrics] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  // Track whether this is the initial load so we only show the spinner once.
  const initialLoadDone = useRef(false);

  // Fetch performance metrics from backend.
  // Uses AbortController so that any in-flight request is cancelled when the
  // component unmounts or when autoRefresh changes — prevents stale responses
  // from overwriting state after unmount (issue #513).
  // Polling interval extended to 15 s: metrics queries hit 5 DB tables and the
  // data changes at most once per task completion, so 5 s was excessive.
  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;

    const fetchPerformanceData = async () => {
      // Only show the loading spinner on the first fetch; subsequent polls are
      // silent so the UI does not flicker every 15 seconds.
      if (!initialLoadDone.current) {
        setLoading(true);
      }
      setError(null);

      try {
        const API_BASE_URL = getApiUrl();
        const response = await fetch(
          `${API_BASE_URL}/api/metrics/performance`,
          {
            headers: { Accept: 'application/json' },
            signal,
          }
        );

        if (!response.ok) {
          throw new Error(
            `Failed to fetch performance metrics: ${response.status}`
          );
        }

        const data = await response.json();

        if (!data || typeof data !== 'object') {
          throw new Error('Invalid performance data format');
        }

        setPerformanceData(data);
        initialLoadDone.current = true;
      } catch (err) {
        if (err.name === 'AbortError') {
          // Request was cancelled on unmount — not an error.
          return;
        }
        logger.error('Error fetching performance data:', err);
        setError(
          err instanceof Error
            ? err.message
            : 'Failed to fetch performance data'
        );
        setPerformanceData(null);
      } finally {
        setLoading(false);
      }
    };

    fetchPerformanceData();
    const interval = autoRefresh
      ? setInterval(fetchPerformanceData, 15000)
      : null;

    return () => {
      controller.abort();
      if (interval) clearInterval(interval);
    };
  }, [autoRefresh]);

  // Update client metrics from window.apiMetrics
  useEffect(() => {
    const updateClientMetrics = () => {
      if (window.apiMetrics && Array.isArray(window.apiMetrics)) {
        // Get last 20 metrics and reverse to show newest first
        const recent = window.apiMetrics.slice(-20).reverse();
        setClientMetrics(recent);
      }
    };

    updateClientMetrics();
    const interval = setInterval(updateClientMetrics, 2000);

    return () => clearInterval(interval);
  }, []);

  // Cache hit rate cards
  const cacheCards = performanceData?.cache_stats
    ? Object.entries(performanceData.cache_stats).map(([name, stats]) => ({
        label: name
          .replace(/_/g, ' ')
          .split(' ')
          .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
          .join(' '),
        hitRate: `${((stats.hit_rate || 0) * 100).toFixed(1)}%`,
        ttl: `${stats.ttl}s`,
        positive: (stats.hit_rate || 0) > 0.5,
      }))
    : [];

  // Overall stats
  const overallStats = performanceData?.overall_stats || {};

  // Route latencies for chart
  const routeLatencies = performanceData?.route_latencies || {};
  const latencyChartData = Object.entries(routeLatencies)
    .map(([endpoint, latencies]) => ({
      endpoint: endpoint.replace('/api/', '').toUpperCase(),
      p50: latencies.p50 || 0,
      p95: latencies.p95 || 0,
      p99: latencies.p99 || 0,
      hitRate: `${((latencies.cache_hit_rate || 0) * 100).toFixed(0)}%`,
    }))
    .slice(0, 10);

  // Model router decisions for chart
  const modelDecisions = performanceData?.model_router_decisions || {};
  const modelChartData = Object.entries(modelDecisions)
    .sort((a, b) => b[1] - a[1])
    .map(([provider, count]) => ({
      provider:
        provider.charAt(0).toUpperCase() + provider.slice(1).toLowerCase(),
      count,
      percentage: (
        (count / Object.values(modelDecisions).reduce((a, b) => a + b, 1)) *
        100
      ).toFixed(1),
    }));

  return (
    <div className="performance-metrics-container">
      <div className="dashboard-header">
        <div>
          <h1 className="dashboard-title">⚡ Performance Dashboard</h1>
          <p className="dashboard-subtitle">
            Real-time caching, latency, and routing analytics
          </p>
        </div>
        <div className="header-controls">
          <label className="auto-refresh-toggle">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto-refresh
          </label>
        </div>
      </div>

      {loading && (
        <div className="loading">
          <p>Loading performance metrics...</p>
        </div>
      )}

      {error && (
        <div className="error">
          <p>⚠️ {error}</p>
          <small>Please check your connection and try again.</small>
        </div>
      )}

      {!loading && performanceData && (
        <>
          {/* Overall Statistics */}
          <div className="overall-stats-section">
            <h2 className="section-title">📊 Overall Performance</h2>
            <div className="stats-grid">
              <div className="stat-card">
                <span className="stat-label">Total Requests</span>
                <span className="stat-value">
                  {(overallStats.total_requests || 0).toLocaleString()}
                </span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Cached Requests</span>
                <span className="stat-value">
                  {(overallStats.cached_requests || 0).toLocaleString()}
                </span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Overall Hit Rate</span>
                <span className="stat-value">
                  {((overallStats.overall_hit_rate || 0) * 100).toFixed(1)}%
                </span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Avg Latency</span>
                <span className="stat-value">
                  {((overallStats.avg_latency_ms || 0) / 1000).toFixed(3)}s
                </span>
              </div>
            </div>
          </div>

          {/* Cache Hit Rates */}
          <div className="cache-section">
            <h2 className="section-title">💾 Cache Hit Rates</h2>
            <div className="cache-cards-grid">
              {cacheCards.map((card, idx) => (
                <div key={idx} className="cache-card">
                  <h3 className="cache-label">{card.label}</h3>
                  <p className="cache-hit-rate">{card.hitRate}</p>
                  <p className="cache-ttl">TTL: {card.ttl}</p>
                  <div
                    className={`cache-indicator ${card.positive ? 'good' : 'fair'}`}
                  >
                    {card.positive ? '✅' : '⏱️'}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Route Latencies Chart */}
          {latencyChartData.length > 0 && (
            <div className="latency-section">
              <h2 className="section-title">
                ⏱️ Route Latencies (P50/P95/P99)
              </h2>
              <ResponsiveContainer width="100%" height={350}>
                <BarChart data={latencyChartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="endpoint" />
                  <YAxis
                    label={{
                      value: 'Latency (ms)',
                      angle: -90,
                      position: 'insideLeft',
                    }}
                  />
                  <Tooltip />
                  <Legend />
                  <Bar
                    dataKey="p50"
                    fill="#82ca9d"
                    name="P50 (50th percentile)"
                  />
                  <Bar
                    dataKey="p95"
                    fill="#ffc658"
                    name="P95 (95th percentile)"
                  />
                  <Bar
                    dataKey="p99"
                    fill="#ff7c7c"
                    name="P99 (99th percentile)"
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Model Router Decisions Pie Chart */}
          {modelChartData.length > 0 && (
            <div className="model-section">
              <h2 className="section-title">🤖 Model Router Decisions</h2>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'center',
                  marginBottom: '40px',
                }}
              >
                <ResponsiveContainer width="100%" height={300}>
                  <PieChart>
                    <Pie
                      data={modelChartData}
                      dataKey="count"
                      nameKey="provider"
                      cx="50%"
                      cy="50%"
                      outerRadius={80}
                      label={({ provider, percentage }) =>
                        `${provider} (${percentage}%)`
                      }
                    >
                      {modelChartData.map((entry, index) => (
                        <Cell
                          key={`cell-${index}`}
                          fill={
                            [
                              '#8884d8',
                              '#82ca9d',
                              '#ffc658',
                              '#ff7c7c',
                              '#8dd1e1',
                            ][index % 5]
                          }
                        />
                      ))}
                    </Pie>
                    <Tooltip formatter={(value) => value.toLocaleString()} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="model-table">
                <table style={{ width: '100%', textAlign: 'left' }}>
                  <thead>
                    <tr>
                      <th>Provider</th>
                      <th>Requests</th>
                      <th>Percentage</th>
                    </tr>
                  </thead>
                  <tbody>
                    {modelChartData.map((model, idx) => (
                      <tr key={idx}>
                        <td>{model.provider}</td>
                        <td>{model.count.toLocaleString()}</td>
                        <td>{model.percentage}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Live Client Metrics */}
          {clientMetrics.length > 0 && (
            <div className="client-metrics-section">
              <h2 className="section-title">
                📱 Live Client Metrics (Last 20 Requests)
              </h2>
              <div className="metrics-table">
                <div className="table-header">
                  <span className="col-time">Timestamp</span>
                  <span className="col-endpoint">Endpoint</span>
                  <span className="col-method">Method</span>
                  <span className="col-status">Status</span>
                  <span className="col-duration">Duration</span>
                  <span className="col-cached">Cached</span>
                </div>
                {clientMetrics.map((metric, idx) => (
                  <div key={idx} className="table-row">
                    <span className="col-time">
                      {new Date(metric.timestamp).toLocaleTimeString()}
                    </span>
                    <span className="col-endpoint">{metric.endpoint}</span>
                    <span className="col-method">{metric.method}</span>
                    <span className={`col-status status-${metric.status}`}>
                      {metric.status}
                    </span>
                    <span className="col-duration">{metric.duration_ms}ms</span>
                    <span className="col-cached">
                      {metric.cached ? '✅' : '—'}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default PerformanceDashboard;
