# Using the New Backend Features - Quick Start Guide

This guide shows you how to use the newly implemented backend features from the frontend.

---

## 1. Using the Analytics/KPI Endpoint

### Get Dashboard KPIs

```javascript
// Fetch KPI data for the last 7 days
async function getKPIs(range = '7d') {
  const response = await fetch(
    `http://localhost:8000/api/analytics/kpis?range=${range}`
  );
  const data = await response.json();

  return {
    // Task Statistics
    totalTasks: data.total_tasks,
    completedTasks: data.completed_tasks,
    failedTasks: data.failed_tasks,
    pendingTasks: data.pending_tasks,

    // Success Metrics
    successRate: data.success_rate, // 0-100
    failureRate: data.failure_rate, // 0-100
    completionRate: data.completion_rate, // 0-100

    // Timing Metrics (in seconds)
    avgTime: data.avg_execution_time_seconds,
    medianTime: data.median_execution_time_seconds,
    minTime: data.min_execution_time_seconds,
    maxTime: data.max_execution_time_seconds,

    // Cost Metrics (in USD)
    totalCost: data.total_cost_usd,
    avgCostPerTask: data.avg_cost_per_task,
    costByModel: data.cost_by_model, // { "gpt-4": 1.50, "llama2": 0.0, ... }
    costByPhase: data.cost_by_phase, // { "research": 0.10, "draft": 0.30, ... }

    // Model Usage
    modelsUsed: data.models_used, // { "gpt-4": 5, "llama2": 10, ... }
    primaryModel: data.primary_model,

    // Task Breakdown
    taskTypes: data.task_types, // { "blog_post": 8, "social_media": 2, ... }

    // Chart Data
    tasksPerDay: data.tasks_per_day, // [ { date: "2025-12-16", count: 5 }, ... ]
    costPerDay: data.cost_per_day, // [ { date: "2025-12-16", cost: 1.50 }, ... ]
    successTrend: data.success_trend, // [ { date: "2025-12-16", success_rate: 85, ... }, ... ]
  };
}

// Example: Update dashboard with KPIs
async function updateDashboard() {
  const kpis = await getKPIs('7d');

  document.getElementById('total-tasks').textContent = kpis.totalTasks;
  document.getElementById('success-rate').textContent =
    `${kpis.successRate.toFixed(1)}%`;
  document.getElementById('total-cost').textContent =
    `$${kpis.totalCost.toFixed(2)}`;
  document.getElementById('avg-time').textContent =
    `${kpis.avgTime.toFixed(0)}s`;

  // Chart.js example
  const ctx = document.getElementById('tasksChart').getContext('2d');
  new Chart(ctx, {
    type: 'line',
    data: {
      labels: kpis.tasksPerDay.map((d) => d.date),
      datasets: [
        {
          label: 'Tasks Created Per Day',
          data: kpis.tasksPerDay.map((d) => d.count),
          borderColor: '#2196F3',
        },
      ],
    },
  });
}
```

### Time Ranges

```javascript
// Get different time periods
getKPIs('1d'); // Last 24 hours
getKPIs('7d'); // Last 7 days (default)
getKPIs('30d'); // Last 30 days
getKPIs('90d'); // Last 90 days
getKPIs('all'); // All-time metrics
```

---

## 2. Using the Task Status Enum

### Validate Task Status

```javascript
// Import the status values (available via docs/schema)
const VALID_STATUSES = [
  'pending',
  'generating',
  'awaiting_approval',
  'approved',
  'rejected',
  'completed',
  'failed',
  'published',
];

function isValidStatus(status) {
  return VALID_STATUSES.includes(status);
}

// Use in UI
<select name="status">
  <option value="pending">Pending</option>
  <option value="generating">Generating</option>
  <option value="awaiting_approval">Awaiting Approval</option>
  <option value="approved">Approved</option>
  <option value="rejected">Rejected</option>
  <option value="completed">Completed</option>
  <option value="failed">Failed</option>
  <option value="published">Published</option>
</select>;
```

---

## 3. Using Model Selection with Validation

### Submit Task with Model Selection

```javascript
async function createTaskWithModels() {
  const taskData = {
    task_type: 'blog_post',
    topic: 'The Future of AI',
    style: 'technical',
    tone: 'professional',
    target_length: 2000,

    // Option 1: Specify models for each phase
    models_by_phase: {
      research: 'mistral', // Mistral for research
      outline: 'mistral', // Mistral for outline
      draft: 'gpt-4', // GPT-4 for draft (better quality)
      assess: 'claude-3-sonnet', // Claude for assessment
      refine: 'gpt-4', // GPT-4 for refinement
      finalize: 'mistral', // Mistral for final formatting
    },

    // Option 2: Or let system auto-select based on quality
    // quality_preference: 'balanced'  // 'budget' | 'balanced' | 'quality' | 'premium'
  };

  try {
    const response = await fetch('http://localhost:8000/api/content/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(taskData),
    });

    if (response.ok) {
      const result = await response.json();
      console.log('✅ Task created:', result.task_id);
      console.log('📊 Estimated cost:', result.estimated_cost);
      console.log('💰 Cost breakdown:', result.cost_breakdown);
      return result;
    } else {
      const error = await response.json();
      console.error('❌ Error:', error.detail);
      // Error might be: "Invalid model selection: research: Model 'invalid_model' is not available"
    }
  } catch (err) {
    console.error('Request failed:', err);
  }
}
```

### Handle Model Validation Errors

```javascript
async function createTaskSafely() {
  const taskData = {
    /* ... */
  };

  try {
    const response = await fetch('http://localhost:8000/api/content/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(taskData),
    });

    if (!response.ok) {
      const error = await response.json();

      // Check for model validation errors
      if (error.detail && error.detail.includes('Invalid model selection')) {
        const message = error.detail;
        // Show user: "Please select a valid model. Available models: llama2, mistral, ..."
        showUserError(message);
      } else {
        showUserError('Failed to create task');
      }
    }
  } catch (err) {
    showUserError('Network error: ' + err.message);
  }
}
```

---

## 4. Quality-Based Model Auto-Selection

### Let the System Choose Models

```javascript
// Instead of specifying models manually:
const taskDataWithAutoSelect = {
  task_type: 'blog_post',
  topic: 'My Article Topic',
  quality_preference: 'balanced', // System will choose models

  // Supported values:
  // 'budget'    - Use cheapest models (Ollama mostly)
  // 'balanced'  - Mix local and cloud models, good quality
  // 'quality'   - Use high-quality paid models (GPT-4, Claude)
  // 'premium'   - Use best models for each phase (Claude Opus + GPT-4)
};

const response = await fetch('http://localhost:8000/api/content/tasks', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(taskDataWithAutoSelect),
});

const result = await response.json();
console.log('Selected models:', result.models_used);
// Output: { research: 'mistral', outline: 'mistral', draft: 'neural-chat', ... }
```

---

## 5. Workflow History - Both Paths Work

### Use the Correct Endpoint

```javascript
// Primary path (what frontend should use)
async function getWorkflowHistory() {
  const response = await fetch('http://localhost:8000/api/workflow/history', {
    headers: { Authorization: `Bearer ${token}` },
  });
  return await response.json();
}

// Alternative path (also works for backward compatibility)
// http://localhost:8000/api/workflows/history

// Both return:
// {
//   executions: [ { id, status, created_at, ... }, ... ],
//   total: 42,
//   limit: 50,
//   offset: 0
// }
```

---

## 6. Understanding Cost Estimates

### Cost Breakdown by Phase

```javascript
// When you create a task, you get:
{
  task_id: "uuid",
  estimated_cost: 0.005234,  // Total cost in USD
  cost_breakdown: {
    research: 0.001000,   // Mistral research
    outline: 0.000800,    // Mistral outline
    draft: 0.002000,      // GPT-4 draft (expensive)
    assess: 0.001000,     // Claude assessment
    refine: 0.000200,     // GPT-4 refinement
    finalize: 0.000234    // Mistral finalization
  },
  models_used: {
    research: 'mistral',
    outline: 'mistral',
    draft: 'gpt-4',
    assess: 'claude-3-sonnet',
    refine: 'gpt-4',
    finalize: 'mistral'
  }
}

// Tips for cost reduction:
// 1. Use local models (Ollama) - $0 per token
// 2. Use 'budget' quality preference
// 3. Use shorter target_length
// 4. Limit use of expensive models to critical phases
```

---

## 7. Monitoring & Debugging

### Check Analytics Data Quality

```javascript
async function validateAnalyticsData() {
  const kpis = await getKPIs('7d');

  // Verify data integrity
  const issues = [];

  // Check success rate
  if (kpis.successRate < 0 || kpis.successRate > 100) {
    issues.push('❌ Success rate out of bounds');
  }

  // Check rates sum to 100
  const total = kpis.successRate + kpis.failureRate;
  if (Math.abs(total - 100) > 1) {
    // Allow 1% rounding error
    issues.push(`❌ Success + Failure rates don't sum to 100: ${total}%`);
  }

  // Check task counts
  const accountedTasks =
    kpis.completedTasks + kpis.failedTasks + kpis.pendingTasks;
  if (accountedTasks !== kpis.totalTasks) {
    issues.push(
      `❌ Task counts don't add up: ${accountedTasks} vs ${kpis.totalTasks}`
    );
  }

  // Check cost is non-negative
  if (kpis.totalCost < 0) {
    issues.push('❌ Total cost is negative');
  }

  if (issues.length === 0) {
    console.log('✅ All analytics data looks valid');
  } else {
    console.error('⚠️ Data quality issues:', issues);
  }

  return issues;
}
```

---

## 8. Example: Update ExecutiveDashboard Component

```javascript
// In ExecutiveDashboard.jsx
import { useState, useEffect } from 'react';

export function ExecutiveDashboard() {
  const [kpis, setKpis] = useState(null);
  const [timeRange, setTimeRange] = useState('7d');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchKPIs();
  }, [timeRange]);

  const fetchKPIs = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(
        `http://localhost:8000/api/analytics/kpis?range=${timeRange}`
      );
      if (!response.ok) throw new Error('Failed to fetch KPIs');
      const data = await response.json();
      setKpis(data);
    } catch (err) {
      setError(err.message);
      console.error('Error fetching KPIs:', err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <div>Loading KPIs...</div>;
  if (error) return <div>Error: {error}</div>;
  if (!kpis) return <div>No data</div>;

  return (
    <div className="dashboard">
      <div className="time-range-selector">
        {['1d', '7d', '30d', '90d', 'all'].map((range) => (
          <button
            key={range}
            onClick={() => setTimeRange(range)}
            className={timeRange === range ? 'active' : ''}
          >
            {range === '1d'
              ? '24h'
              : range === '7d'
                ? '7d'
                : range === '30d'
                  ? '30d'
                  : range === '90d'
                    ? '90d'
                    : 'All'}
          </button>
        ))}
      </div>

      <div className="kpi-cards">
        <KPICard title="Total Tasks" value={kpis.total_tasks} />
        <KPICard title="Completed" value={kpis.completed_tasks} />
        <KPICard
          title="Success Rate"
          value={`${kpis.success_rate.toFixed(1)}%`}
        />
        <KPICard
          title="Total Cost"
          value={`$${kpis.total_cost_usd.toFixed(2)}`}
        />
        <KPICard
          title="Avg Time"
          value={`${kpis.avg_execution_time_seconds.toFixed(0)}s`}
        />
      </div>

      <div className="charts">
        <LineChart data={kpis.tasks_per_day} title="Tasks per Day" />
        <BarChart data={kpis.cost_by_model} title="Cost by Model" />
        <LineChart data={kpis.success_trend} title="Success Rate Trend" />
      </div>
    </div>
  );
}
```

---

## Troubleshooting

### KPI Endpoint Returns Zero Values

**Cause:** No tasks in database or tasks created before implementing analytics  
**Solution:** Create some test tasks using `/api/content/tasks`

### Model Validation Rejects Valid Models

**Cause:** Model name case sensitivity or tags (e.g., "llama2:13b" vs "llama2")  
**Solution:** Use exact model names from available models list

### Workflow History Returns 404

**Cause:** Frontend was calling `/api/workflow-history/history` (old path)  
**Solution:** Now use `/api/workflow/history` (fixed endpoint)

---

## API Reference

### GET /api/analytics/kpis

**Query Parameters:**

- `range`: 1d | 7d | 30d | 90d | all (default: 7d)

**Response:** KPIMetrics object with 20+ fields

### GET /api/analytics/distributions

**Query Parameters:**

- `range`: 1d | 7d | 30d | 90d | all (default: 7d)

**Response:** DistributionResponse with task breakdown

### POST /api/content/tasks

**Body:**

- `models_by_phase`: { phase: model_name }
- `quality_preference`: "budget|balanced|quality|premium"

**Response:** CreateBlogPostResponse with estimated_cost

### GET /api/workflow/history

**Headers:** Authorization: Bearer <token>

**Response:** WorkflowHistoryResponse with execution list

---

**Documentation is current as of: December 22, 2025**
