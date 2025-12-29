# Cost Dashboard Quick Reference

**Last Updated:** December 2025

## Quick Access

### URLs

- **Executive Dashboard**: `http://localhost:3001/` (Home page)
- **Cost Metrics Dashboard**: `http://localhost:3001/costs`

### Navigation

- Click **Costs** (💰) in the left sidebar menu
- Or click **View Costs** button in Executive Dashboard Quick Actions

---

## What's Available

### Executive Dashboard (Home Page)

- KPI cards (Revenue, Content, Tasks, Savings, Costs, etc.)
- Cost breakdown by phase and model
- 30-day trend charts
- System status
- Quick action buttons

### Cost Metrics Dashboard (/costs)

- Total cost metrics
- Budget tracking and alerts
- Detailed cost breakdowns (phase & model)
- 4-month cost trends
- Cost optimization recommendations
- Budget alert notifications

---

## API Endpoints

| Endpoint                                 | Purpose           | Default Period |
| ---------------------------------------- | ----------------- | -------------- |
| `GET /api/metrics/costs`                 | Main cost metrics | All time       |
| `GET /api/metrics/costs/breakdown/phase` | Costs by phase    | week           |
| `GET /api/metrics/costs/breakdown/model` | Costs by model    | week           |
| `GET /api/metrics/costs/history`         | Cost trends       | week           |
| `GET /api/metrics/costs/budget`          | Budget status     | month          |

### Example Usage

```bash
# Get cost metrics
curl http://localhost:8000/api/metrics/costs

# Get cost breakdown by phase (this month)
curl "http://localhost:8000/api/metrics/costs/breakdown/phase?period=month"

# Get budget status with $200 limit
curl "http://localhost:8000/api/metrics/costs/budget?monthly_budget=200"
```

---

## Components

### CostBreakdownCards

- **File**: `web/oversight-hub/src/components/CostBreakdownCards.jsx`
- **Used In**: Both Executive Dashboard and Cost Metrics Dashboard
- **Displays**: Phase costs + Model costs with percentages

### CostMetricsDashboard

- **File**: `web/oversight-hub/src/routes/CostMetricsDashboard.jsx`
- **Route**: `/costs`
- **Full**: Comprehensive cost analytics dashboard

---

## Configuration

### Monthly Budget

Set in backend or frontend as needed:

```python
# Backend default
monthly_budget = 150.0  # USD

# Frontend override
getBudgetStatus(200.0)  # Check with $200 budget
```

### Time Ranges

- `today` or `1d` - Current day
- `week` or `7d` - Last 7 days
- `month` or `30d` - Last 30 days
- `all` - All time

---

## Troubleshooting

### Dashboard Blank

1. Check backend running: `http://localhost:8000/health`
2. Verify database: `DATABASE_URL` in `.env.local`
3. Check browser console for errors

### No Cost Data

1. Verify tasks are being tracked
2. Check `cost_tracking` table populated
3. Review backend logs

### Budget Numbers Wrong

1. Verify `monthly_budget` parameter
2. Check for duplicate records in database
3. Confirm cost calculations

---

## Environment Setup

In `.env.local`:

```env
# Backend cost calculation
DATABASE_URL=postgresql://user:pass@localhost:5432/glad_labs

# Optional: Override default budget
MONTHLY_BUDGET=150.0

# Optional: Cost tracking enabled
ENABLE_COST_TRACKING=true
```

---

## Integration Points

### Frontend Routes

```jsx
// AppRoutes.jsx
<Route path="/costs" element={<CostMetricsDashboard />} />

// LayoutWrapper.jsx navigation
{ label: 'Costs', icon: '💰', path: 'costs' }

// ExecutiveDashboard.jsx quick action
<button onClick={() => navigate('/costs')}>View Costs</button>
```

### Backend Routes

```python
# metrics_routes.py
@metrics_router.get("/costs")
@metrics_router.get("/costs/breakdown/phase")
@metrics_router.get("/costs/breakdown/model")
@metrics_router.get("/costs/history")
@metrics_router.get("/costs/budget")
```

---

## Common Tasks

### View Costs for Last 30 Days

1. Go to `/costs`
2. Select "Last 30 Days" from time range selector
3. View all metrics update for 30-day period

### Check Budget Status

1. Go to `/costs`
2. Look at budget section with progress bar
3. See percentage used and remaining amount

### Review Cost Optimization Tips

1. Go to `/costs`
2. Scroll to "Cost Optimization Recommendations"
3. Review suggested actions and implementation tips

### Export Cost Data

Currently: Manual copy/paste from dashboard  
Future: Download as CSV/PDF coming soon

---

## Budget Alert Thresholds

| Threshold | Alert Level | Action        |
| --------- | ----------- | ------------- |
| < 60%     | Normal      | Continue      |
| 60-75%    | Yellow      | Monitor       |
| 75-90%    | Orange      | Review        |
| > 90%     | Red         | Action needed |

---

## File Locations

```
Frontend:
  web/oversight-hub/
  ├── src/routes/
  │   ├── AppRoutes.jsx (routes config)
  │   └── CostMetricsDashboard.jsx (dashboard)
  ├── src/components/
  │   ├── LayoutWrapper.jsx (navigation)
  │   ├── CostBreakdownCards.jsx (visualization)
  │   └── pages/ExecutiveDashboard.jsx (KPIs)
  └── src/services/cofounderAgentClient.js (API calls)

Backend:
  src/cofounder_agent/
  ├── routes/metrics_routes.py (endpoints)
  ├── services/database_service.py (queries)
  └── models/cost_models.py (schemas)
```

---

## Performance Tips

- **Dashboard loads in < 1 second** (with caching)
- **Real-time updates every 2 minutes**
- **Trend charts query last 30 days** (optimized)
- **Budget alerts update every 1 minute**

---

## Support Commands

```bash
# Check backend health
curl http://localhost:8000/health

# Check database connection
psql $DATABASE_URL -c "SELECT 1"

# View cost tracking records
psql $DATABASE_URL -c "SELECT * FROM cost_tracking LIMIT 10"

# View cost by phase (last 7 days)
psql $DATABASE_URL -c "
  SELECT phase, SUM(cost) FROM cost_tracking
  WHERE created_at > NOW() - INTERVAL '7 days'
  GROUP BY phase;"
```

---

**For detailed documentation**, see [COST_DASHBOARD_INTEGRATION.md](COST_DASHBOARD_INTEGRATION.md)
