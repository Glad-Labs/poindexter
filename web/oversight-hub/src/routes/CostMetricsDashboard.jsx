import logger from '@/lib/logger';
import { useState, useEffect } from 'react';
import './CostMetricsDashboard.css';
import {
  getCostMetrics,
  getCostsByPhase,
  getCostsByModel,
  getCostHistory,
  getBudgetStatus,
} from '../services/cofounderAgentClient';
import {
  validateCostMetrics,
  validateCostsByPhase,
  validateCostsByModel,
  validateCostHistory,
  validateBudgetStatus,
  safeValidate,
} from '../services/responseValidationSchemas';

function CostMetricsDashboard() {
  const [costMetrics, setCostMetrics] = useState(null);
  const [costsByPhase, setCostsByPhase] = useState(null);
  const [costsByModel, setCostsByModel] = useState(null);
  const [costHistory, setCostHistory] = useState(null);
  const [budgetStatus, setBudgetStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [timeRange, setTimeRange] = useState('month');

  // Fetch real cost data from API
  useEffect(() => {
    const fetchCostData = async () => {
      try {
        setLoading(true);
        setError(null);

        const [metrics, phaseData, modelData, historyData, budgetData] =
          await Promise.all([
            getCostMetrics(),
            getCostsByPhase(timeRange),
            getCostsByModel(timeRange),
            getCostHistory(timeRange),
            getBudgetStatus(150.0),
          ]);

        // Validate all responses before processing
        const validatedMetrics = safeValidate(
          validateCostMetrics,
          metrics,
          'Cost metrics'
        );
        const validatedPhaseData = safeValidate(
          validateCostsByPhase,
          phaseData,
          'Phase data'
        );
        const validatedModelData = safeValidate(
          validateCostsByModel,
          modelData,
          'Model data'
        );
        const validatedHistoryData = safeValidate(
          validateCostHistory,
          historyData,
          'History data'
        );
        const validatedBudgetData = safeValidate(
          validateBudgetStatus,
          budgetData,
          'Budget data'
        );

        // If any validation fails, throw error
        if (!validatedMetrics || !validatedBudgetData) {
          throw new Error(
            'Invalid API response format - check backend contract'
          );
        }

        // Process metrics data with validated values
        const totalCost = validatedMetrics.total_cost || 0;
        const avgCostPerTask = validatedMetrics.avg_cost_per_task || 0;
        const totalTasks = validatedMetrics.total_tasks || 0;

        setCostMetrics([
          {
            label: 'Total Cost (Period)',
            value: `$${totalCost.toFixed(2)}`,
            change: `${totalTasks} tasks`,
            positive: true,
          },
          {
            label: 'Avg Cost/Task',
            value: `$${avgCostPerTask.toFixed(6)}`,
            change: 'Optimization target',
            positive: true,
          },
          {
            label: 'Total Tasks',
            value: totalTasks.toLocaleString(),
            change: `${(totalTasks * avgCostPerTask).toFixed(2)}$ total`,
            positive: true,
          },
          {
            label: 'Monthly Budget',
            value: '$150.00',
            change: `${validatedBudgetData?.percent_used || 0}% used`,
            positive: (validatedBudgetData?.percent_used || 0) < 80,
          },
        ]);

        // phases/models come from backend as arrays of objects;
        // convert to {name: cost} maps for the simple chart view
        const rawPhases = validatedPhaseData?.phases || [];
        const phaseMap = Array.isArray(rawPhases)
          ? rawPhases
              .filter(
                (p) =>
                  p &&
                  typeof p.phase === 'string' &&
                  p.phase.length > 0 &&
                  typeof p.total_cost === 'number' &&
                  Number.isFinite(p.total_cost)
              )
              .reduce((acc, p) => ({ ...acc, [p.phase]: p.total_cost }), {})
          : rawPhases && typeof rawPhases === 'object'
            ? rawPhases
            : {};
        setCostsByPhase(phaseMap);

        const rawModels = validatedModelData?.models || [];
        const modelMap = Array.isArray(rawModels)
          ? rawModels
              .filter(
                (m) =>
                  m &&
                  typeof m.model === 'string' &&
                  m.model.length > 0 &&
                  typeof m.total_cost === 'number' &&
                  Number.isFinite(m.total_cost)
              )
              .reduce((acc, m) => ({ ...acc, [m.model]: m.total_cost }), {})
          : rawModels && typeof rawModels === 'object'
            ? rawModels
            : {};
        setCostsByModel(modelMap);

        setCostHistory(validatedHistoryData?.daily_data || []);
        setBudgetStatus(validatedBudgetData);
      } catch (err) {
        logger.error('Error fetching cost data:', err);
        setError(
          err instanceof Error ? err.message : 'Failed to fetch cost data'
        );
        // Do NOT fall back to mock data - show error instead
        setCostMetrics([]);
        setCostsByPhase({});
        setCostsByModel({});
        setCostHistory([]);
        setBudgetStatus(null);
      } finally {
        setLoading(false);
      }
    };

    fetchCostData();
  }, [timeRange]);

  // Mock cost breakdown by service (for visualization)
  const costBreakdown = Object.entries(costsByPhase || {})
    .map(([phase, cost]) => ({
      service: phase.charAt(0).toUpperCase() + phase.slice(1),
      cost: Math.round((cost || 0) * 100),
      percentage: Math.round(((cost || 0) * 100) / 100),
    }))
    .filter((item) => item.cost > 0)
    .sort((a, b) => b.cost - a.cost);

  // Format cost history into monthly trend data
  const costTrend = (costHistory || [])
    .map((item, idx) => ({
      month: item.date
        ? new Date(item.date).toLocaleDateString('en-US', { month: 'short' })
        : `Month ${idx + 1}`,
      cost: item.cost || 0,
    }))
    .slice(-4); // Last 4 months

  return (
    <div className="cost-metrics-container">
      <div className="dashboard-header">
        <div>
          <h1 className="dashboard-title">💰 Cost Metrics Dashboard</h1>
          <p className="dashboard-subtitle">
            Real-time AI cost tracking and optimization analysis
          </p>
        </div>
        <div className="time-range-selector">
          <select
            value={timeRange}
            onChange={(e) => setTimeRange(e.target.value)}
          >
            <option value="today">Today</option>
            <option value="week">Last 7 Days</option>
            <option value="month">This Month</option>
          </select>
        </div>
      </div>

      {loading && (
        <div className="loading">
          <p>Loading cost metrics...</p>
        </div>
      )}

      {error && (
        <div className="error">
          <p>⚠️ {error}</p>
          <small>Please check your database connection and try again.</small>
        </div>
      )}

      {!loading && costMetrics && (
        <>
          {/* Key Metrics */}
          <div className="metrics-grid">
            {costMetrics.map((metric, idx) => (
              <div key={idx} className="metric-card">
                <h3 className="metric-label">{metric.label}</h3>
                <p className="metric-value">{metric.value}</p>
                <p
                  className={`metric-change ${metric.positive ? 'positive' : 'negative'}`}
                >
                  {metric.positive ? '✓' : '⚠️'} {metric.change}
                </p>
              </div>
            ))}
          </div>

          {/* Budget Overview */}
          {budgetStatus && (
            <div className="budget-section">
              <h2 className="section-title">💳 Monthly Budget Overview</h2>
              <div className="budget-card">
                <div className="budget-header">
                  <span className="budget-label">Current Spend</span>
                  <span className="budget-value">
                    ${budgetStatus.amount_spent?.toFixed(2) || '0.00'} / $
                    {budgetStatus.monthly_budget?.toFixed(2) || '150.00'}
                  </span>
                </div>
                <div className="budget-bar">
                  <div
                    className={`budget-fill ${
                      budgetStatus.percent_used >= 90
                        ? 'critical'
                        : budgetStatus.percent_used >= 75
                          ? 'warning'
                          : 'normal'
                    }`}
                    style={{ width: `${budgetStatus.percent_used || 0}%` }}
                  >
                    <span className="budget-percent">
                      {budgetStatus.percent_used?.toFixed(1) || 0}%
                    </span>
                  </div>
                </div>
                <div className="budget-info">
                  <span className="budget-remaining">
                    💵 ${budgetStatus.amount_remaining?.toFixed(2) || '0.00'}{' '}
                    remaining
                  </span>
                  <span className="budget-days">
                    📅 Projected daily: $
                    {((budgetStatus.amount_spent || 0) / 30).toFixed(2)}
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* Cost Breakdown */}
          {costBreakdown.length > 0 && (
            <div className="breakdown-section">
              <h2 className="section-title">📊 Cost by Pipeline Phase</h2>
              <div className="breakdown-list">
                {costBreakdown.map((item, idx) => (
                  <div key={idx} className="breakdown-item">
                    <div className="item-info">
                      <span className="item-label">{item.service}</span>
                      <span className="item-cost">${item.cost.toFixed(6)}</span>
                    </div>
                    <div className="progress-bar">
                      <div
                        className="progress-fill"
                        style={{ width: `${item.percentage * 100}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Model Costs */}
          {costsByModel && Object.keys(costsByModel).length > 0 && (
            <div className="models-section">
              <h2 className="section-title">🤖 Cost by AI Model</h2>
              <div className="models-grid">
                {Object.entries(costsByModel).map(([model, cost]) => (
                  <div key={model} className="model-card">
                    <div className="model-name">{model.toUpperCase()}</div>
                    <div className="model-cost">${(cost || 0).toFixed(6)}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Cost Breakdown Grid */}
          <div className="breakdown-grid">
            <div className="breakdown-chart">
              <div className="pie-chart">
                <svg
                  viewBox="0 0 100 100"
                  style={{ width: '200px', height: '200px' }}
                >
                  <circle
                    cx="50"
                    cy="50"
                    r="45"
                    fill="none"
                    stroke="var(--accent-primary)"
                    strokeWidth="30"
                    strokeDasharray={`${42 * 2.83} 283`}
                    style={{
                      transform: 'rotate(-90deg)',
                      transformOrigin: '50px 50px',
                    }}
                  />
                  <circle
                    cx="50"
                    cy="50"
                    r="45"
                    fill="none"
                    stroke="#2196f3"
                    strokeWidth="30"
                    strokeDasharray={`${25 * 2.83} 283`}
                    strokeDashoffset={`-${42 * 2.83}`}
                    style={{
                      transform: 'rotate(-90deg)',
                      transformOrigin: '50px 50px',
                    }}
                  />
                  <text
                    x="50"
                    y="55"
                    textAnchor="middle"
                    fill="var(--text-primary)"
                    fontSize="16"
                  >
                    100%
                  </text>
                </svg>
              </div>
            </div>

            <div className="breakdown-list">
              {costBreakdown.map((item, idx) => (
                <div key={idx} className="breakdown-item">
                  <div className="item-header">
                    <span className="item-name">{item.service}</span>
                    <span className="item-percentage">{item.percentage}%</span>
                  </div>
                  <div className="item-bar">
                    <div
                      className="item-fill"
                      style={{
                        width: `${item.percentage}%`,
                        backgroundColor: [
                          'var(--accent-primary)',
                          '#2196f3',
                          '#9c27b0',
                          '#ff9800',
                          '#4caf50',
                        ][idx],
                      }}
                    ></div>
                  </div>
                  <span className="item-cost">
                    ${item.cost.toLocaleString()}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {/* Cost Trend - Last 4 Months */}
      <div className="trend-section">
        <h2 className="section-title">Cost Trend (Last 4 Months)</h2>
        <div className="trend-chart">
          <div className="trend-graph">
            {costTrend.map((data, idx) => (
              <div key={idx} className="trend-item">
                <div
                  className="trend-bar"
                  style={{ height: `${(data.cost / 13000) * 100}%` }}
                >
                  <span className="trend-tooltip">
                    ${data.cost.toLocaleString()}
                  </span>
                </div>
                <span className="trend-label">{data.month}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Cost Recommendations */}
      <div className="recommendations-section">
        <h2 className="section-title">💡 Cost Optimization Recommendations</h2>
        <div className="recommendations-list">
          <div className="recommendation">
            <span className="rec-icon">✓</span>
            <div className="rec-content">
              <h3 className="rec-title">Increase Batch Size</h3>
              <p className="rec-desc">
                Processing requests in larger batches could reduce API call
                costs by 15%
              </p>
            </div>
            <button className="rec-action">Implement</button>
          </div>

          <div className="recommendation">
            <span className="rec-icon">⚡</span>
            <div className="rec-content">
              <h3 className="rec-title">Enable Caching</h3>
              <p className="rec-desc">
                Implement response caching to avoid redundant API calls and save
                8-10% monthly
              </p>
            </div>
            <button className="rec-action">Setup</button>
          </div>

          <div className="recommendation">
            <span className="rec-icon">📊</span>
            <div className="rec-content">
              <h3 className="rec-title">Optimize Peak Hours</h3>
              <p className="rec-desc">
                Distribute workload evenly across hours to qualify for volume
                discounts
              </p>
            </div>
            <button className="rec-action">Configure</button>
          </div>

          <div className="recommendation">
            <span className="rec-icon">🎯</span>
            <div className="rec-content">
              <h3 className="rec-title">Model Selection</h3>
              <p className="rec-desc">
                Use smaller models for simpler tasks to reduce compute costs by
                20%
              </p>
            </div>
            <button className="rec-action">Review</button>
          </div>
        </div>
      </div>

      {/* Usage Alerts */}
      <div className="alerts-section">
        <h2 className="section-title">⚠️ Budget Alerts</h2>
        <div className="alerts-list">
          <div className="alert alert-warning">
            <span className="alert-icon">⚠️</span>
            <div className="alert-content">
              <h4 className="alert-title">High API Usage</h4>
              <p className="alert-message">
                API calls increased 25% this week. Consider optimizing your
                queries.
              </p>
            </div>
          </div>

          <div className="alert alert-info">
            <span className="alert-icon">ℹ️</span>
            <div className="alert-content">
              <h4 className="alert-title">Budget at 83%</h4>
              <p className="alert-message">
                You are approaching your monthly budget limit. 6 days remaining.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default CostMetricsDashboard;
