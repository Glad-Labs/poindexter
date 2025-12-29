# Frontend KPI Parameter Fix

**Date:** December 27, 2025  
**Issue:** ExecutiveDashboard sending old parameter format to backend  
**Status:** ✅ FIXED

## Error Fixed

```
:8000/api/analytics/kpis?range=30days:1  Failed to load resource: the server responded with a status of 400 (Bad Request)
```

## Root Cause

ExecutiveDashboard.jsx was using the old time range format (`30days`, `7days`, etc.) while the backend now expects the new format (`30d`, `7d`, etc.).

## Changes Made

### File: web/oversight-hub/src/components/pages/ExecutiveDashboard.jsx

**1. Updated default state (line 26)**

```diff
- const [timeRange, setTimeRange] = useState('30days');
+ const [timeRange, setTimeRange] = useState('30d');
```

**2. Updated select options (lines 211-214)**

```diff
  <select value={timeRange} onChange={(e) => setTimeRange(e.target.value)}>
-   <option value="7days">Last 7 Days</option>
-   <option value="30days">Last 30 Days</option>
-   <option value="90days">Last 90 Days</option>
-   <option value="1year">Last Year</option>
+   <option value="1d">Last 24 Hours</option>
+   <option value="7d">Last 7 Days</option>
+   <option value="30d">Last 30 Days</option>
+   <option value="90d">Last 90 Days</option>
+   <option value="all">All Time</option>
</select>
```

## Result

✅ ExecutiveDashboard now sends correct parameter format to backend  
✅ KPI dashboard will load successfully  
✅ Time range selector shows all available options  
✅ Consistent with backend API requirements (1d, 7d, 30d, 90d, all)

## Verification

The frontend will now make requests like:

```
GET http://localhost:8000/api/analytics/kpis?range=30d
```

Instead of the failing:

```
GET http://localhost:8000/api/analytics/kpis?range=30days  ❌
```

No changes needed to the backend - it was already correctly updated!
