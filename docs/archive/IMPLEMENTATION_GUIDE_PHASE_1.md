# Glad Labs - Complete Integration Implementation Guide

**Objective:** Enable the full workflow: Login → Delegate Task → Agent Execute → Display Metrics

---

## 🎯 Phase 0: Critical Fixes (Do This First - 1 Week)

### Fix 1: Update Frontend Data Fetching Hook (BLOCKING)

**File:** `web/oversight-hub/src/hooks/useTasks.js`

**Current Problem:**

```javascript
// ❌ BROKEN - Still uses Firebase
import { collection, onSnapshot } from 'firebase/firestore';
```

**Solution - Replace entire file:**

```javascript
import { useEffect, useState } from 'react';
import useStore from '../store/useStore';

/**
 * Fetch tasks from PostgreSQL backend API (replacing Firebase Firestore)
 * Polls every 5 seconds for updates
 */
const useTasks = () => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const setTasks = useStore((state) => state.setTasks);
  const accessToken = useStore((state) => state.accessToken);

  useEffect(() => {
    // Don't fetch if not authenticated
    if (!accessToken) {
      setLoading(false);
      return;
    }

    const fetchTasks = async () => {
      try {
        const response = await fetch(
          `${process.env.REACT_APP_API_URL}/api/tasks`,
          {
            headers: {
              Authorization: `Bearer ${accessToken}`,
              'Content-Type': 'application/json',
            },
          }
        );

        if (!response.ok) {
          throw new Error(`Failed to fetch tasks: ${response.status}`);
        }

        const data = await response.json();
        setTasks(data.tasks || []);
        setError(null);
      } catch (err) {
        console.error('Error fetching tasks:', err);
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    // Fetch immediately
    fetchTasks();

    // Poll every 5 seconds for updates
    const interval = setInterval(fetchTasks, 5000);
    return () => clearInterval(interval);
  }, [setTasks, accessToken]);

  return { loading, error };
};

export default useTasks;
```

---

### Fix 2: Add Authentication State to Zustand Store

**File:** `web/oversight-hub/src/store/useStore.js`

**Replace with:**

```javascript
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

const useStore = create(
  persist(
    (set) => ({
      // ===== AUTHENTICATION =====
      user: null,
      accessToken: null,
      refreshToken: null,
      isAuthenticated: false,

      setUser: (user) => set({ user }),
      setAccessToken: (token) => set({ accessToken: token }),
      setRefreshToken: (token) => set({ refreshToken: token }),
      setIsAuthenticated: (isAuth) => set({ isAuthenticated: isAuth }),

      logout: () =>
        set({
          user: null,
          accessToken: null,
          refreshToken: null,
          isAuthenticated: false,
          tasks: [],
          selectedTask: null,
        }),

      // ===== TASKS & DATA =====
      tasks: [],
      selectedTask: null,
      isModalOpen: false,
      metrics: {
        totalTasks: 0,
        completedTasks: 0,
        failedTasks: 0,
        successRate: 0,
        avgExecutionTime: 0,
        totalCost: 0,
      },

      setTasks: (tasks) => set({ tasks }),
      setSelectedTask: (task) =>
        set({ selectedTask: task, isModalOpen: !!task }),
      setIsModalOpen: (isOpen) => set({ isModalOpen: isOpen }),
      setMetrics: (metrics) => set({ metrics }),

      // ===== SETTINGS =====
      theme: 'dark',
      autoRefresh: false,
      notifications: {
        desktop: true,
      },
      apiKeys: {
        mercury: '',
        gcp: '',
      },

      setTheme: (theme) => set({ theme }),
      toggleTheme: () =>
        set((state) => ({ theme: state.theme === 'light' ? 'dark' : 'light' })),
      toggleAutoRefresh: () =>
        set((state) => ({ autoRefresh: !state.autoRefresh })),
      toggleDesktopNotifications: () =>
        set((state) => ({
          notifications: {
            ...state.notifications,
            desktop: !state.notifications.desktop,
          },
        })),
      setApiKey: (key, value) =>
        set((state) => ({
          apiKeys: {
            ...state.apiKeys,
            [key]: value,
          },
        })),
    }),
    {
      name: 'oversight-hub-storage',
      partialize: (state) => ({
        // Persist these fields
        theme: state.theme,
        autoRefresh: state.autoRefresh,
        notifications: state.notifications,
        apiKeys: state.apiKeys,
        accessToken: state.accessToken, // ✅ NEW
        refreshToken: state.refreshToken, // ✅ NEW
        user: state.user, // ✅ NEW
      }),
    }
  )
);

export default useStore;
```

---

### Fix 3: Update API Client with Auth Headers

**File:** `web/oversight-hub/src/services/cofounderAgentClient.js`

**Add login function and update headers:**

```javascript
import useStore from '../store/useStore';

const API_BASE_URL =
  process.env.REACT_APP_API_URL || 'http://localhost:8000/api';

/**
 * Get auth headers with JWT token
 */
function getAuthHeaders() {
  const accessToken = useStore.getState().accessToken;

  const headers = {
    'Content-Type': 'application/json',
  };

  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`;
  }

  return headers;
}

/**
 * Make HTTP request with auth headers
 */
async function makeRequest(endpoint, method = 'GET', data = null) {
  try {
    const url = `${API_BASE_URL}${endpoint}`;
    const config = {
      method,
      headers: getAuthHeaders(),
    };

    if (data) {
      config.body = JSON.stringify(data);
    }

    const response = await fetch(url, config);

    const contentType = response.headers.get('content-type');
    let result;
    if (contentType && contentType.includes('application/json')) {
      result = await response.json();
    } else {
      result = await response.text();
    }

    if (!response.ok) {
      // Handle 401 Unauthorized - try to refresh token
      if (response.status === 401) {
        const refreshed = await refreshAccessToken();
        if (refreshed) {
          // Retry the original request
          return makeRequest(endpoint, method, data);
        }
      }

      const error = new Error(
        result?.message || `API Error: ${response.status}`
      );
      error.status = response.status;
      error.data = result;
      throw error;
    }

    return result;
  } catch (error) {
    console.error(`API request failed: ${endpoint}`, error);
    throw error;
  }
}

/**
 * Login user
 */
export async function login(email, password) {
  const response = await makeRequest('/auth/login', 'POST', {
    email,
    password,
  });

  if (response.success) {
    // Store tokens and user
    useStore.setState({
      accessToken: response.access_token,
      refreshToken: response.refresh_token,
      user: response.user,
      isAuthenticated: true,
    });
  }

  return response;
}

/**
 * Register user
 */
export async function register(email, username, password) {
  return makeRequest('/auth/register', 'POST', {
    email,
    username,
    password,
    password_confirm: password,
  });
}

/**
 * Logout user
 */
export async function logout() {
  try {
    await makeRequest('/auth/logout', 'POST');
  } finally {
    useStore.setState({
      accessToken: null,
      refreshToken: null,
      user: null,
      isAuthenticated: false,
      tasks: [],
    });
  }
}

/**
 * Refresh access token
 */
export async function refreshAccessToken() {
  try {
    const refreshToken = useStore.getState().refreshToken;
    if (!refreshToken) return false;

    const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    if (response.ok) {
      const data = await response.json();
      useStore.setState({ accessToken: data.access_token });
      return true;
    }

    return false;
  } catch (error) {
    console.error('Token refresh failed:', error);
    return false;
  }
}

/**
 * Get current user profile
 */
export async function getCurrentUser() {
  return makeRequest('/auth/me', 'GET');
}

/**
 * Create blog post (async - returns task_id)
 */
export async function createBlogPost(params) {
  return makeRequest('/content/create-blog-post', 'POST', {
    topic: params.topic,
    style: params.style || 'technical',
    tone: params.tone || 'professional',
    target_length: params.targetLength || 1500,
    tags: params.tags || [],
    categories: params.categories || [],
  });
}

/**
 * Get task status
 */
export async function getTaskStatus(taskId) {
  return makeRequest(`/tasks/${taskId}`, 'GET');
}

/**
 * Get all tasks for current user
 */
export async function getTasks(limit = 50, offset = 0) {
  return makeRequest(`/tasks?limit=${limit}&offset=${offset}`, 'GET');
}

/**
 * Get metrics for all tasks
 */
export async function getMetrics() {
  return makeRequest('/metrics', 'GET');
}

/**
 * Poll task until completion
 */
export async function pollTaskStatus(taskId, onProgress = null) {
  let attempts = 0;
  const MAX_ATTEMPTS = 120; // 10 minutes with 5-second intervals
  const POLL_INTERVAL = 5000;

  return new Promise((resolve, reject) => {
    const poll = async () => {
      try {
        if (attempts >= MAX_ATTEMPTS) {
          reject(new Error('Task polling timeout'));
          return;
        }

        const task = await getTaskStatus(taskId);

        if (onProgress) onProgress(task);

        if (task.status === 'completed' || task.status === 'failed') {
          resolve(task);
          return;
        }

        attempts++;
        setTimeout(poll, POLL_INTERVAL);
      } catch (error) {
        reject(error);
      }
    };

    poll();
  });
}

// Export all functions
export default {
  login,
  register,
  logout,
  getCurrentUser,
  createBlogPost,
  getTaskStatus,
  getTasks,
  getMetrics,
  pollTaskStatus,
};
```

---

### Fix 4: Update LoginForm Component

**File:** `web/oversight-hub/src/components/LoginForm.jsx`

**Replace with:**

```javascript
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import useStore from '../store/useStore';
import cofounderAgentClient from '../services/cofounderAgentClient';
import './LoginForm.css';

function LoginForm() {
  const navigate = useNavigate();
  const setUser = useStore((state) => state.setUser);
  const setIsAuthenticated = useStore((state) => state.setIsAuthenticated);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const response = await cofounderAgentClient.login(email, password);

      if (response.success) {
        setIsAuthenticated(true);
        navigate('/dashboard');
      } else {
        setError(response.message || 'Login failed');
      }
    } catch (err) {
      console.error('Login error:', err);
      setError(err.message || 'An error occurred during login');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-container">
      <div className="login-form">
        <h1>Glad Labs - Oversight Hub</h1>
        <p>Login to manage your AI agents and tasks</p>

        {error && <div className="error-message">{error}</div>}

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              placeholder="your@email.com"
            />
          </div>

          <div className="form-group">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              placeholder="••••••••"
            />
          </div>

          <button type="submit" disabled={loading} className="login-button">
            {loading ? 'Logging in...' : 'Login'}
          </button>
        </form>

        <p className="register-link">
          Don't have an account? <a href="/register">Register here</a>
        </p>
      </div>
    </div>
  );
}

export default LoginForm;
```

---

### Fix 5: Fix SQLAlchemy Metadata Issue

**File:** `src/cofounder_agent/models.py` (line 448)

**Before:**

```python
class Task(Base):
    __tablename__ = "tasks"
    # ...
    metadata = Column(JSONB, default={})  # ❌ Reserved name
```

**After:**

```python
class Task(Base):
    __tablename__ = "tasks"
    # ...
    task_metadata = Column('metadata', JSONB, default={})  # ✅ Alias to 'metadata' in DB
```

**Also update everywhere it's referenced:**

```python
# Old: task.metadata['key'] = value
# New: task.task_metadata['key'] = value
```

---

## 🎯 Phase 1: Core Features (Next 2 Weeks)

### Feature 1: Create Task Modal

**New File:** `web/oversight-hub/src/components/TaskCreationModal.jsx`

```javascript
import React, { useState } from 'react';
import useStore from '../store/useStore';
import cofounderAgentClient from '../services/cofounderAgentClient';
import './TaskCreationModal.css';

function TaskCreationModal() {
  const [topic, setTopic] = useState('');
  const [style, setStyle] = useState('technical');
  const [audience, setAudience] = useState('developers');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const setTasks = useStore((state) => state.setTasks);
  const tasks = useStore((state) => state.tasks);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      // Create task
      const response = await cofounderAgentClient.createBlogPost({
        topic,
        style,
        targetAudience: audience,
      });

      if (response.task_id) {
        // Add to tasks list with pending status
        const newTask = {
          id: response.task_id,
          topic,
          status: 'queued',
          createdAt: new Date().toISOString(),
          progress: 0,
        };

        setTasks([newTask, ...tasks]);

        // Start polling
        await cofounderAgentClient.pollTaskStatus(response.task_id, (task) => {
          // Update task in store
          setTasks(tasks.map((t) => (t.id === task.id ? task : t)));
        });
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay">
      <div className="modal-content">
        <h2>Create Blog Post Task</h2>

        {error && <div className="error">{error}</div>}

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Topic</label>
            <input
              type="text"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="e.g., AI Safety Best Practices"
              required
            />
          </div>

          <div className="form-group">
            <label>Style</label>
            <select value={style} onChange={(e) => setStyle(e.target.value)}>
              <option value="technical">Technical</option>
              <option value="casual">Casual</option>
              <option value="professional">Professional</option>
            </select>
          </div>

          <div className="form-group">
            <label>Target Audience</label>
            <select
              value={audience}
              onChange={(e) => setAudience(e.target.value)}
            >
              <option value="developers">Developers</option>
              <option value="business">Business</option>
              <option value="general">General</option>
            </select>
          </div>

          <button type="submit" disabled={loading}>
            {loading ? 'Creating...' : 'Create Task'}
          </button>
        </form>
      </div>
    </div>
  );
}

export default TaskCreationModal;
```

---

### Feature 2: Create Metrics Display Component

**New File:** `web/oversight-hub/src/components/MetricsDisplay.jsx`

```javascript
import React, { useEffect, useState } from 'react';
import useStore from '../store/useStore';
import cofounderAgentClient from '../services/cofounderAgentClient';
import './MetricsDisplay.css';

function MetricsDisplay() {
  const [loading, setLoading] = useState(true);
  const metrics = useStore((state) => state.metrics);
  const setMetrics = useStore((state) => state.setMetrics);

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const data = await cofounderAgentClient.getMetrics();
        setMetrics(data);
      } catch (err) {
        console.error('Failed to fetch metrics:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchMetrics();

    // Refresh every 30 seconds
    const interval = setInterval(fetchMetrics, 30000);
    return () => clearInterval(interval);
  }, [setMetrics]);

  if (loading) return <div>Loading metrics...</div>;

  return (
    <div className="metrics-container">
      <h2>Performance Metrics</h2>

      <div className="metrics-grid">
        <MetricCard title="Total Tasks" value={metrics.totalTasks} icon="📊" />

        <MetricCard
          title="Completed"
          value={metrics.completedTasks}
          icon="✅"
          color="green"
        />

        <MetricCard
          title="Failed"
          value={metrics.failedTasks}
          icon="❌"
          color="red"
        />

        <MetricCard
          title="Success Rate"
          value={`${metrics.successRate}%`}
          icon="📈"
          color="blue"
        />

        <MetricCard
          title="Avg Execution Time"
          value={`${Math.round(metrics.avgExecutionTime)}s`}
          icon="⏱️"
        />

        <MetricCard
          title="Total Cost"
          value={`$${metrics.totalCost.toFixed(2)}`}
          icon="💰"
        />
      </div>
    </div>
  );
}

function MetricCard({ title, value, icon, color = 'default' }) {
  return (
    <div className={`metric-card metric-${color}`}>
      <div className="metric-icon">{icon}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-title">{title}</div>
    </div>
  );
}

export default MetricsDisplay;
```

---

### Feature 3: Backend Task Management Endpoint

**File:** `src/cofounder_agent/routes/tasks.py` (Create new file)

```python
"""
Task Management Routes

Handles task creation, retrieval, and status updates
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session

from database import get_session
from models import User, Task
from services.database_service import DatabaseService
from routes.auth_routes import get_current_user

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

database_service = DatabaseService()


class TaskCreateRequest(BaseModel):
    """Create task request"""
    topic: str
    style: str = "technical"
    tone: str = "professional"
    target_audience: str = "general"
    category: str = "blog_post"


class TaskResponse(BaseModel):
    """Task response"""
    id: str
    topic: str
    status: str
    progress: int = 0
    created_at: str
    updated_at: str
    metadata: Optional[Dict[str, Any]] = None


@router.post("/", response_model=TaskResponse)
async def create_task(
    request: TaskCreateRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Create a new task for agent execution

    1. Creates task in database
    2. Sends command to agent
    3. Returns task_id for polling
    """
    try:
        task_id = await database_service.add_task({
            "topic": request.topic,
            "style": request.style,
            "tone": request.tone,
            "target_audience": request.target_audience,
            "category": request.category,
            "user_id": str(current_user.id),
            "status": "queued",
            "metadata": {},
        })

        # Get created task
        task = await database_service.get_task(task_id)

        return TaskResponse(
            id=task_id,
            topic=request.topic,
            status="queued",
            created_at=task.get("created_at", datetime.utcnow().isoformat()),
            updated_at=task.get("updated_at", datetime.utcnow().isoformat()),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get task status and details"""
    try:
        task = await database_service.get_task(task_id)

        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        return TaskResponse(
            id=task["id"],
            topic=task["topic"],
            status=task["status"],
            progress=task.get("progress", 0),
            created_at=task["created_at"],
            updated_at=task["updated_at"],
            metadata=task.get("metadata"),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/")
async def list_tasks(
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
):
    """List all tasks for current user"""
    try:
        tasks = await database_service.get_tasks_for_user(
            str(current_user.id),
            limit=limit,
            offset=offset
        )

        return {
            "tasks": [
                TaskResponse(
                    id=t["id"],
                    topic=t["topic"],
                    status=t["status"],
                    progress=t.get("progress", 0),
                    created_at=t["created_at"],
                    updated_at=t["updated_at"],
                )
                for t in tasks
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
```

---

### Feature 4: Backend Metrics Endpoint

**File:** `src/cofounder_agent/routes/metrics.py` (Create new file)

```python
"""
Metrics Endpoint

Returns aggregated metrics for all tasks
"""

from fastapi import APIRouter, Depends
from typing import Dict, Any
from sqlalchemy.orm import Session

from database import get_session
from models import User, Task
from routes.auth_routes import get_current_user

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/")
async def get_metrics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """
    Get aggregated metrics for all tasks of current user
    """
    try:
        # Get all tasks for user
        tasks = db.query(Task).filter(Task.user_id == current_user.id).all()

        total_tasks = len(tasks)
        completed_tasks = len([t for t in tasks if t.status == "completed"])
        failed_tasks = len([t for t in tasks if t.status == "failed"])
        success_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

        # Calculate average execution time (placeholder)
        total_time = sum([
            (t.completed_at - t.created_at).total_seconds()
            for t in tasks
            if t.status == "completed" and t.completed_at
        ]) or 0

        avg_time = (total_time / completed_tasks) if completed_tasks > 0 else 0

        # Get total cost from task metadata (placeholder)
        total_cost = sum([
            t.task_metadata.get("cost", 0)
            for t in tasks
            if t.task_metadata
        ]) or 0

        return {
            "totalTasks": total_tasks,
            "completedTasks": completed_tasks,
            "failedTasks": failed_tasks,
            "successRate": round(success_rate, 2),
            "avgExecutionTime": round(avg_time, 2),
            "totalCost": round(total_cost, 4),
        }
    except Exception as e:
        return {
            "totalTasks": 0,
            "completedTasks": 0,
            "failedTasks": 0,
            "successRate": 0,
            "avgExecutionTime": 0,
            "totalCost": 0,
            "error": str(e),
        }
```

---

### Feature 5: Register New Routes in main.py

**File:** `src/cofounder_agent/main.py` (Update to add new routes)

**Add to imports (around line 30):**

```python
from routes.tasks import router as tasks_router
from routes.metrics import router as metrics_router
```

**Add to app setup (around line 150):**

```python
# Register new routers
app.include_router(tasks_router, tags=["tasks"])
app.include_router(metrics_router, tags=["metrics"])
```

---

## 📋 Testing Checklist

### After Implementing Phase 0 & 1

- [ ] User can login via frontend
- [ ] JWT tokens stored in localStorage
- [ ] User can create a task via modal
- [ ] Task appears in dashboard
- [ ] Task status updates as "queued"
- [ ] Metrics endpoint returns valid data
- [ ] Metrics display shows correct numbers
- [ ] User can logout
- [ ] Session persists after page refresh

### API Testing (with curl/Postman)

```bash
# 1. Login
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"password"}'

# 2. Create task
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"topic":"AI Safety","style":"technical"}'

# 3. Get task
curl -X GET http://localhost:8000/api/tasks/TASK_ID \
  -H "Authorization: Bearer YOUR_TOKEN"

# 4. Get metrics
curl -X GET http://localhost:8000/api/metrics \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## 🚀 Implementation Order

**Week 1 (Days 1-2):**

1. Fix useTasks.js
2. Update useStore.js
3. Update cofounderAgentClient.js
4. Fix SQLAlchemy metadata

**Week 1 (Days 3-5):** 5. Update LoginForm.jsx 6. Test authentication end-to-end 7. Create TaskCreationModal.jsx 8. Create MetricsDisplay.jsx

**Week 2 (Days 1-3):** 9. Create tasks.py routes 10. Create metrics.py routes 11. Register routes in main.py 12. Test task creation

**Week 2 (Days 4-5):** 13. Connect frontend to backend endpoints 14. End-to-end testing 15. Bug fixes and refinement

---

This guide provides everything needed to implement the full workflow. Each piece builds on the previous one.
