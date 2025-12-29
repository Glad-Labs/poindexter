# Cost Dashboard Integration Guide

**Last Updated:** December 2025  
**Project:** Glad Labs - Cost Metrics Dashboard  
**Version:** 1.0

## Overview

The Glad Labs system now features integrated cost dashboards that provide real-time visibility into AI spending, budget management, and cost optimization opportunities. This document describes the two main cost dashboards and how they work together.

---

## Dashboard Types

### 1. **Executive Dashboard** (Main Homepage)

**Path:** `/` (Home)  
**Purpose:** High-level business KPI overview with cost breakdown visualization  
**Target Users:** Business owners, executives, project managers

#### Features:

- **KPI Cards** - Business metrics including:
  - 📈 Revenue (vs. previous month)
  - 📝 Content Published (vs. previous month)
  - ✅ Tasks Completed (vs. previous month)
  - 💰 AI Savings (vs. previous month)
  - 💸 Total Cost (This period)
  - 🎯 Average Cost Per Task
  - 📊 Engagement Rate
  - ✓ Agent Uptime

- **Cost Breakdown Cards** - Visual cost distribution:
  - By Pipeline Phase (Research, Draft, Assess, Refine, Finalize)
  - By AI Model (Ollama, GPT-3.5, GPT-4, Claude)

- **Trend Charts** - 30-day trends for:
  - Publishing volume
  - Engagement metrics
  - AI cost trends

- **System Status** - Real-time system health
- **Quick Actions** - One-click navigation to key workflows

#### Cost Component:

The Executive Dashboard includes an embedded `CostBreakdownCards` component that displays:

```jsx
<CostBreakdownCards
  costByPhase={kpis.costByPhase}
  costByModel={kpis.costByModel}
/>
```

This provides immediate visibility into cost distribution without leaving the main dashboard.

---

### 2. **Cost Metrics Dashboard** (Detailed Analytics)

**Path:** `/costs`  
**Purpose:** Comprehensive cost analytics, history, and optimization insights  
**Target Users:** Finance teams, DevOps engineers, cost optimization specialists

#### Features:

- **Cost Metrics Cards** - Key cost statistics:
  - Total Cost (Period)
  - Average Cost Per Task
  - Total Tasks Processed
  - Monthly Budget Status

- **Budget Management**:
  - Monthly budget tracking with progress bars
  - Percentage used indicator (color-coded warnings)
  - Remaining balance projection
  - Daily average calculation

- **Cost Breakdown Analysis**:
  - Pie charts for visual distribution
  - Cost by Pipeline Phase with percentages
  - Cost by AI Model with percentages
  - Sortable lists for detailed inspection

- **Cost Trend Chart** - 4-month historical trend visualization
  - Helpful for identifying patterns
  - Detecting cost spikes or anomalies

- **Cost Optimization Recommendations**:
  - ✓ Increase Batch Size (potential 15% savings)
  - ⚡ Enable Caching (potential 8-10% savings)
  - 📊 Optimize Peak Hours (volume discount qualification)
  - 🎯 Model Selection (potential 20% savings)

- **Budget Alerts**:
  - High API usage warnings
  - Budget threshold alerts
  - System health notifications

---

## Data Flow Architecture

```
Frontend (React)
    ↓
    ├─ cofounderAgentClient.js (API client)
    │   ├─ getCostMetrics()
    │   ├─ getCostsByPhase(period)
    │   ├─ getCostsByModel(period)
    │   ├─ getCostHistory(period)
    │   └─ getBudgetStatus(monthlyBudget)
    ↓
Backend (FastAPI)
    ├─ metrics_routes.py
    │   ├─ GET /api/metrics/costs
    │   ├─ GET /api/metrics/costs/breakdown/phase
    │   ├─ GET /api/metrics/costs/breakdown/model
    │   ├─ GET /api/metrics/costs/history
    │   └─ GET /api/metrics/costs/budget
    ↓
    Database (PostgreSQL)
        └─ cost_tracking table
```

---

## API Endpoints

### Core Endpoints

#### 1. **GET /api/metrics/costs**

Returns comprehensive cost metrics and model usage data.

```json
{
  "total_cost": 127.5,
  "avg_cost_per_task": 0.0087,
  "total_tasks": 15000,
  "costs": {
    "research": 0.0,
    "draft": 0.00525,
    "assess": 0.00275,
    "refine": 0.0035,
    "finalize": 0.00025
  }
}
```

#### 2. **GET /api/metrics/costs/breakdown/phase**

Returns cost breakdown by pipeline phase.

**Query Parameters:**

- `period` (string): "today", "week", "month" (default: "week")

```json
{
  "phases": {
    "research": { "cost": 12.5, "percentage": 10, "task_count": 150 },
    "draft": { "cost": 52.5, "percentage": 42, "task_count": 210 },
    "assess": { "cost": 27.5, "percentage": 22, "task_count": 180 },
    "refine": { "cost": 35.0, "percentage": 28, "task_count": 200 }
  },
  "total": 127.5
}
```

#### 3. **GET /api/metrics/costs/breakdown/model**

Returns cost breakdown by AI model.

**Query Parameters:**

- `period` (string): "today", "week", "month" (default: "week")

```json
{
  "models": {
    "ollama": { "cost": 0.0, "percentage": 0, "calls": 0 },
    "gpt-3.5": { "cost": 52.5, "percentage": 41, "calls": 525 },
    "gpt-4": { "cost": 7.5, "percentage": 6, "calls": 75 },
    "claude": { "cost": 67.5, "percentage": 53, "calls": 135 }
  },
  "total": 127.5
}
```

#### 4. **GET /api/metrics/costs/history**

Returns historical cost data for trend analysis.

**Query Parameters:**

- `period` (string): "week", "month" (default: "week")

```json
{
  "daily_data": [
    { "date": "2025-01-20", "cost": 4.5 },
    { "date": "2025-01-21", "cost": 5.2 },
    { "date": "2025-01-22", "cost": 4.8 }
  ],
  "weekly_average": 5.1
}
```

#### 5. **GET /api/metrics/costs/budget**

Returns budget status and projections.

**Query Parameters:**

- `monthly_budget` (float): Monthly budget limit in USD (default: 150.0)

```json
{
  "monthly_budget": 150.0,
  "amount_spent": 127.5,
  "amount_remaining": 22.5,
  "percent_used": 85,
  "daily_burn_rate": 4.25,
  "days_remaining": 5,
  "projected_overage": false,
  "alerts": [
    "Budget at 85% - approaching limit",
    "Daily spend increased 15% this week"
  ]
}
```

---

## Frontend Components

### CostBreakdownCards Component

**Location:** `web/oversight-hub/src/components/CostBreakdownCards.jsx`  
**Used In:** ExecutiveDashboard, CostMetricsDashboard

#### Props:

```jsx
<CostBreakdownCards
  costByPhase={{
    research: 0.0,
    draft: 0.00525,
    assess: 0.00275,
    refine: 0.0035,
  }}
  costByModel={{
    ollama: 0.0,
    'gpt-3.5': 0.00525,
    'gpt-4': 0.00075,
    claude: 0.00095,
  }}
/>
```

#### Features:

- Displays cost distribution by pipeline phase
- Displays cost distribution by AI model
- Color-coded visualization
- Percentage and absolute cost display
- Progress bars for visual comparison
- Empty state handling

### CostMetricsDashboard Component

**Location:** `web/oversight-hub/src/routes/CostMetricsDashboard.jsx`  
**Route:** `/costs`

Complete standalone dashboard with:

- All cost metrics and analytics
- Budget management interface
- Historical trend analysis
- Cost optimization recommendations
- Budget alerts and notifications

---

## Navigation

### Access Points:

#### From Main Navigation Menu:

1. Click "Costs" (💰) in the left sidebar navigation menu
2. Directly navigates to `/costs` route

#### From Executive Dashboard:

1. Click "View Costs" button (💰) in the Quick Actions section
2. Alternative: Click "View Reports" then look for cost sections

### Route Map:

```
/                       → Executive Dashboard (with CostBreakdownCards)
/costs                  → Cost Metrics Dashboard (detailed)
/analytics              → Analytics Dashboard (related metrics)
/tasks                  → Task Management
/models                 → Model Management
/settings               → Settings
```

---

## Time Range Selection

Both dashboards support time range filters:

- **Today** - Current day only
- **7 Days** - Last 7 days (default for Cost Metrics Dashboard)
- **30 Days** - Last 30 days (default for Executive Dashboard)
- **90 Days** - Last 3 months
- **All Time** - Complete history

### Frontend Implementation:

```jsx
<select value={timeRange} onChange={(e) => setTimeRange(e.target.value)}>
  <option value="1d">Last 24 Hours</option>
  <option value="7d">Last 7 Days</option>
  <option value="30d">Last 30 Days</option>
  <option value="90d">Last 90 Days</option>
  <option value="all">All Time</option>
</select>
```

---

## Backend Cost Tracking

### Database Schema

The cost data is stored in PostgreSQL with the following structure:

```sql
-- Cost tracking table
CREATE TABLE cost_tracking (
  id SERIAL PRIMARY KEY,
  task_id INT REFERENCES tasks(id),
  phase VARCHAR(50),
  model VARCHAR(50),
  cost DECIMAL(10, 6),
  tokens_used INT,
  created_at TIMESTAMP DEFAULT NOW(),
  provider VARCHAR(50)
);

-- Query pattern for cost aggregation:
SELECT
  phase,
  SUM(cost) as total_cost,
  COUNT(*) as task_count,
  AVG(cost) as avg_cost
FROM cost_tracking
WHERE created_at >= DATE_TRUNC('week', NOW())
GROUP BY phase
ORDER BY total_cost DESC;
```

---

## Cost Calculation Methods

### Phase-Based Costs

Costs are calculated per pipeline phase:

- **Research Phase**: Data gathering, fact-checking
- **Draft Phase**: Initial content creation
- **Assess Phase**: Quality analysis, critique
- **Refine Phase**: Incorporating feedback, improvements
- **Finalize Phase**: Final formatting, publishing prep

### Model-Based Costs

Costs tracked by AI provider:

- **Ollama**: Local models (typically $0 cost)
- **GPT-3.5**: OpenAI's cost-effective model
- **GPT-4**: OpenAI's advanced model
- **Claude**: Anthropic's models

### Cost Formula:

```
Cost per Task = (tokens_used / 1000) * rate_per_1k_tokens
Total Cost = Sum of all task costs
Average Cost = Total Cost / Task Count
```

---

## Budget Management

### Monthly Budget Tracking

1. **Set Budget Limit** - Configure in environment or settings
2. **Track Spending** - Real-time cost accumulation
3. **Monitor Usage** - Percentage-based indicators
4. **Alert System** - Warnings at 75%, 90%, 100%

### Budget Status Indicators:

| Usage  | Status   | Color     | Action              |
| ------ | -------- | --------- | ------------------- |
| < 60%  | Normal   | 🟢 Green  | Continue operations |
| 60-75% | Warning  | 🟡 Yellow | Monitor spending    |
| 75-90% | Alert    | 🟠 Orange | Review optimization |
| > 90%  | Critical | 🔴 Red    | Immediate action    |

---

## Cost Optimization Recommendations

The system provides automatic recommendations:

### 1. Batch Processing

- **Recommendation**: Increase batch size
- **Potential Savings**: 15%
- **Implementation**: Group multiple tasks into single API calls

### 2. Response Caching

- **Recommendation**: Enable caching layer
- **Potential Savings**: 8-10%
- **Implementation**: Cache identical requests

### 3. Peak Hour Optimization

- **Recommendation**: Distribute workload evenly
- **Potential Savings**: Qualify for volume discounts
- **Implementation**: Schedule tasks outside peak hours

### 4. Model Selection

- **Recommendation**: Use cost-appropriate models
- **Potential Savings**: 20%
- **Implementation**: Route simple tasks to cheaper models

---

## Troubleshooting

### Cost Data Not Loading

**Problem**: Dashboard shows "No data available"

**Solutions**:

1. Verify backend is running: `http://localhost:8000/health`
2. Check database connection: `DATABASE_URL` in `.env.local`
3. Ensure cost_tracking table exists
4. Check browser console for API errors
5. Verify authentication token is valid

### Budget Status Incorrect

**Problem**: Budget percentage doesn't match actual spend

**Solutions**:

1. Verify `monthly_budget` parameter is correct
2. Check database for duplicate cost records
3. Verify cost calculations in backend
4. Check timezone settings

### Missing Cost Breakdowns

**Problem**: Phase or model costs not showing

**Solutions**:

1. Ensure tasks are being tracked with phase information
2. Verify model names match expected values
3. Check cost_tracking records exist for period
4. Review backend logs for calculation errors

---

## Performance Considerations

### Query Optimization

The metrics endpoints are optimized for performance:

```python
# Database query with indexes
SELECT
  phase,
  SUM(cost) as cost,
  COUNT(*) as count
FROM cost_tracking
WHERE created_at >= NOW() - INTERVAL '30 days'
  AND created_at < NOW()
GROUP BY phase;

# With indexes on:
# - created_at (for date filtering)
# - phase (for grouping)
# - model (for grouping)
```

### Caching Strategy

- Cost data cached for **5 minutes** on backend
- Browser caches dashboard for **2 minutes**
- Budget status cached for **1 minute** (updated frequently)

### Load Times

- Executive Dashboard KPIs: **< 500ms**
- Cost Metrics Dashboard: **< 1s**
- Trend charts: **< 2s**
- Historical data: **< 3s**

---

## Integration Checklist

Use this checklist to verify cost dashboard integration:

- ✅ Route `/costs` added to AppRoutes.jsx
- ✅ CostMetricsDashboard imported and registered
- ✅ Navigation menu includes "Costs" link
- ✅ ExecutiveDashboard includes "View Costs" button
- ✅ CostBreakdownCards component working
- ✅ Backend API endpoints responding
- ✅ Database cost_tracking table populated
- ✅ CSS styling for cost buttons applied
- ✅ Time range filters functional
- ✅ Budget alerts configured

---

## Files Modified/Created

| File                                                            | Change                        | Purpose            |
| --------------------------------------------------------------- | ----------------------------- | ------------------ |
| `web/oversight-hub/src/routes/AppRoutes.jsx`                    | Added `/costs` route          | Route registration |
| `web/oversight-hub/src/components/LayoutWrapper.jsx`            | Added navigation item         | Menu integration   |
| `web/oversight-hub/src/components/pages/ExecutiveDashboard.jsx` | Added "View Costs" button     | Quick access link  |
| `web/oversight-hub/src/components/pages/ExecutiveDashboard.css` | Added `.costs-button` styling | Button styling     |
| `web/oversight-hub/src/routes/CostMetricsDashboard.jsx`         | Already exists                | Detailed dashboard |
| `src/cofounder_agent/routes/metrics_routes.py`                  | Already exists                | Backend endpoints  |

---

## Next Steps

### Recommended Enhancements:

1. **Email Alerts** - Send budget alerts via email
2. **Export Reports** - Download cost reports as CSV/PDF
3. **Cost Forecasting** - Predict future costs based on trends
4. **Budget Alerts** - Configure custom alert thresholds
5. **Model Comparison** - Side-by-side cost analysis
6. **Cost Allocation** - Assign costs to projects/teams
7. **Monthly Reports** - Auto-generate monthly summaries
8. **Cost Anomaly Detection** - Alert on unusual spikes

---

## Support

For issues or questions:

1. Check the troubleshooting section above
2. Review backend logs: `src/cofounder_agent/server.log`
3. Check browser console for API errors
4. Verify `.env.local` configuration
5. Ensure database connectivity

---

## Related Documentation

- [Architecture and Design](02-ARCHITECTURE_AND_DESIGN.md) - System architecture overview
- [Analytics Dashboard](FRONTEND_CONSTRAINT_INTEGRATION_COMPLETE.md) - Related analytics features
- [Operations and Maintenance](06-OPERATIONS_AND_MAINTENANCE.md) - System operations
