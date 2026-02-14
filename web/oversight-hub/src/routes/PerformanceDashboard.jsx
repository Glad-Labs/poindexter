import { useState, useEffect } from 'react';
import './PerformanceDashboard.css';

function PerformanceDashboard() {
  const [performanceData, setPerformanceData] = useState(null);
  const [clientMetrics, setClientMetrics] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  // Fetch performance metrics from backend
  useEffect(() => {
    const fetchPerformanceData = async () => {
      try {
        setLoading(true);
        setError(null);

        const API_BASE_URL =
          process.env.REACT_APP_API_URL || 'http://localhost:8000';
        const response = await fetch(
          `${API_BASE_URL}/api/metrics/performance`,
          {
            headers: {
              Accept: 'application/json',
            },
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
      } catch (err) {
        console.error('Error fetching performance data:', err);
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
      ? setInterval(fetchPerformanceData, 5000)
      : null;

    return () => {
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

          {/* Route Latencies */}
          {latencyChartData.length > 0 && (
            <div className="latency-section">
              <h2 className="section-title">
                ⏱️ Route Latencies (P50/P95/P99)
              </h2>
              <div className="latency-chart">
                {latencyChartData.map((route, idx) => (
                  <div key={idx} className="latency-row">
                    <div className="route-info">
                      <span className="route-name">{route.endpoint}</span>
                      <span className="cache-hit-badge">{route.hitRate}</span>
                    </div>
                    <div className="latency-bars">
                      <div className="latency-bar-group">
                        <div className="latency-label">p50</div>
                        <div
                          className="latency-bar p50"
                          style={{
                            width: `${Math.min((route.p50 / 200) * 100, 100)}%`,
                          }}
                        >
                          {route.p50 > 10 ? `${route.p50.toFixed(0)}ms` : ''}
                        </div>
                      </div>
                      <div className="latency-bar-group">
                        <div className="latency-label">p95</div>
                        <div
                          className="latency-bar p95"
                          style={{
                            width: `${Math.min((route.p95 / 500) * 100, 100)}%`,
                          }}
                        >
                          {route.p95 > 20 ? `${route.p95.toFixed(0)}ms` : ''}
                        </div>
                      </div>
                      <div className="latency-bar-group">
                        <div className="latency-label">p99</div>
                        <div
                          className="latency-bar p99"
                          style={{
                            width: `${Math.min((route.p99 / 1000) * 100, 100)}%`,
                          }}
                        >
                          {route.p99 > 30 ? `${route.p99.toFixed(0)}ms` : ''}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Model Router Decisions */}
          {modelChartData.length > 0 && (
            <div className="model-section">
              <h2 className="section-title">🤖 Model Router Decisions</h2>
              <div className="model-chart">
                {modelChartData.map((model, idx) => (
                  <div key={idx} className="model-row">
                    <div className="model-info">
                      <span className="model-name">{model.provider}</span>
                      <span className="model-count">
                        {model.count.toLocaleString()} requests
                      </span>
                    </div>
                    <div className="model-bar-container">
                      <div
                        className="model-bar"
                        style={{
                          width: `${parseFloat(model.percentage)}%`,
                        }}
                      >
                        <span className="model-percentage">
                          {model.percentage}%
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
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
