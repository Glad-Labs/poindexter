# Oversight Hub - Implementation Guide for Top Issues

## Issue #1: Fix 17 Hardcoded Fetch Calls

### Current State (Examples)

**Location 1: `ModelManagement.jsx` line 66**

```javascript
// ❌ BROKEN - No auth, no timeout, hardcoded localhost
const response = await fetch('http://localhost:11434/api/tags');
```

**Location 2: `TaskManagement.jsx` line 91**

```javascript
// ❌ BROKEN - Inconsistent error handling
const response = await fetch(
  `http://localhost:8000/api/tasks?limit=${limit}&offset=${offset}`
);
```

**Location 3: `CommandPane.jsx` line 228**

```javascript
// ❌ BROKEN - Hardcoded URL, no token management
const response = await fetch(COFOUNDER_API_URL, {
  method: 'POST',
  body: JSON.stringify(commandData),
});
```

### Solution: Use Existing API Client

**File**: `src/services/cofounderAgentClient.js` (already exists!)

The app already has a well-designed API client:

```javascript
export async function makeRequest(
  endpoint,           // '/api/tasks'
  method = 'GET',
  data = null,
  retry = false,
  onUnauthorized = null,
  timeout = 30000    // 30 second default
)
```

✅ Features included:

- JWT token management
- Timeout handling (configurable)
- Retry logic on 401 (token refresh)
- Error logging
- Environment-based URLs

### Refactoring Steps

**Step 1: Create service layer functions**

Create `src/services/modelService.js`:

```javascript
import { makeRequest } from './cofounderAgentClient';

/**
 * Fetch available Ollama models
 * @returns {Promise<Array>} Array of model objects
 */
export async function getAvailableOllamaModels() {
  const result = await makeRequest(
    '/api/models/ollama/available',
    'GET',
    null,
    false,
    null,
    10000 // 10 second timeout for model list
  );

  if (!result || result.error) {
    console.error('Failed to fetch models:', result?.error);
    return []; // Return empty array as fallback
  }

  return result.models || [];
}

/**
 * Test Ollama model generation
 * @param {string} modelId - Model ID to test
 * @param {string} prompt - Test prompt
 * @returns {Promise<string>} Generated text
 */
export async function testModelGeneration(modelId, prompt) {
  const result = await makeRequest(
    '/api/models/test-generation',
    'POST',
    {
      model_id: modelId,
      prompt: prompt,
      timeout: 60000, // 1 minute for generation
    },
    false,
    null,
    120000 // 2 minute overall timeout
  );

  if (!result || result.error) {
    throw new Error(result?.error || 'Generation failed');
  }

  return result.output;
}
```

**Step 2: Update components to use services**

Replace in `ModelManagement.jsx`:

```javascript
// ❌ BEFORE (line 66)
const response = await fetch('http://localhost:11434/api/tags');
if (!response.ok) {
  console.error('Failed to fetch models');
  return;
}
const data = await response.json();
setAvailableModels(data.models || []);

// ✅ AFTER
import { getAvailableOllamaModels } from '../services/modelService';

try {
  const models = await getAvailableOllamaModels();
  setAvailableModels(models);
} catch (error) {
  setError(`Failed to load models: ${error.message}`);
}
```

### All 17 Locations to Fix

| Component               | Line | Current Endpoint                      | Fix                                 |
| ----------------------- | ---- | ------------------------------------- | ----------------------------------- |
| ModelManagement.jsx     | 66   | localhost:11434/api/tags              | Create getAvailableOllamaModels()   |
| ModelManagement.jsx     | 97   | localhost:11434/api/generate          | Create testModelGeneration()        |
| ModelSelectionPanel.jsx | 196  | localhost:11434/api/tags              | Use getAvailableOllamaModels()      |
| LayoutWrapper.jsx       | 103  | localhost:11434/api/tags              | Use getAvailableOllamaModels()      |
| ExecutiveDashboard.jsx  | 38   | localhost:8000/api/analytics/kpis     | Create getDashboardMetrics()        |
| ExecutionHub.jsx        | 43   | localhost:8000/api/workflow/history   | Create getWorkflowHistory()         |
| ResultPreviewPanel.jsx  | 204  | localhost:8000/api/content/preview    | Use makeRequest() directly          |
| ResultPreviewPanel.jsx  | 480  | localhost:8000/api/tasks/{id}         | Create getTaskDetails()             |
| TaskManagement.jsx      | 91   | localhost:8000/api/tasks              | Create getTasks() (already exists!) |
| TaskManagement.jsx      | 169  | localhost:8000/api/tasks/{id}/approve | Create approveTask()                |
| TaskManagement.jsx      | 235  | localhost:8000/api/tasks/{id}/reject  | Create rejectTask()                 |
| TaskManagement.jsx      | 1069 | localhost:8000/api/tasks (nested)     | Use existing service                |
| TaskManagement.jsx      | 1380 | localhost:8000/api/tasks (nested)     | Use existing service                |
| TaskManagement.jsx      | 1442 | localhost:8000/api/tasks (nested)     | Use existing service                |
| CommandPane.jsx         | 228  | COFOUNDER_API_URL                     | Use makeRequest() directly          |
| LangGraphTest.jsx       | 27   | localhost:8000/api/langgraph          | Create getLangGraphTest()           |

### Effort Estimate: 2-3 hours

- 15 min: Review existing `taskService.js` and `modelService.js`
- 30 min: Create missing service functions
- 90 min: Update 9 components
- 30 min: Test and verify
- 15 min: ESLint check

---

## Issue #2: Break Down TaskManagement Mega-Component

### Current Problem (1,499 lines in one file)

**File**: `src/components/tasks/TaskManagement.jsx`

**Lines 1-60**: Imports and state declarations  
**Lines 61-150**: useEffect for data fetching  
**Lines 151-250**: Sorting and filtering logic  
**Lines 251-400**: Task approval logic  
**Lines 401-600**: Task rejection logic  
**Lines 601-800**: JSX rendering (tasks table)  
**Lines 801-1000**: Modal dialogs  
**Lines 1001-1200**: Action handlers  
**Lines 1201-1499**: Nested fetch calls and error handling

### Solution: Extract into 5 Files

**1. Create `useTaskData.js` hook (100 lines)**

```javascript
// src/hooks/useTaskData.js
import { useState, useEffect } from 'react';
import { getTasks } from '../services/taskService';

export function useTaskData(page, limit, sortBy, sortDirection) {
  const [tasks, setTasks] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchTasks = async () => {
      try {
        setLoading(true);
        const offset = (page - 1) * limit;
        const response = await getTasks(limit, offset, sortBy, sortDirection);

        setTasks(response.tasks);
        setTotal(response.total);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    fetchTasks();
    const interval = setInterval(fetchTasks, 30000); // Auto-refresh

    return () => clearInterval(interval);
  }, [page, limit, sortBy, sortDirection]);

  return { tasks, total, loading, error, setTasks };
}
```

**2. Create `TaskFilters.jsx` component (120 lines)**

```javascript
// src/components/tasks/TaskFilters.jsx
import React from 'react';
import { Box, TextField, Select, MenuItem, Button } from '@mui/material';

const TaskFilters = ({
  sortBy,
  sortDirection,
  statusFilter,
  onSortChange,
  onDirectionChange,
  onStatusChange,
  onResetFilters,
}) => {
  return (
    <Box sx={{ display: 'flex', gap: 2, mb: 2 }}>
      <Select
        value={sortBy}
        onChange={(e) => onSortChange(e.target.value)}
        label="Sort By"
      >
        <MenuItem value="created_at">Created Date</MenuItem>
        <MenuItem value="status">Status</MenuItem>
        <MenuItem value="name">Name</MenuItem>
      </Select>

      <Select
        value={sortDirection}
        onChange={(e) => onDirectionChange(e.target.value)}
        label="Direction"
      >
        <MenuItem value="asc">Ascending</MenuItem>
        <MenuItem value="desc">Descending</MenuItem>
      </Select>

      <Select
        value={statusFilter}
        onChange={(e) => onStatusChange(e.target.value)}
        label="Status"
      >
        <MenuItem value="">All</MenuItem>
        <MenuItem value="pending">Pending</MenuItem>
        <MenuItem value="in_progress">In Progress</MenuItem>
        <MenuItem value="completed">Completed</MenuItem>
        <MenuItem value="failed">Failed</MenuItem>
      </Select>

      <Button variant="outlined" onClick={onResetFilters}>
        Reset
      </Button>
    </Box>
  );
};

export default TaskFilters;
```

**3. Create `TaskTable.jsx` component (200 lines)**

```javascript
// src/components/tasks/TaskTable.jsx
import React from 'react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TablePagination,
  Box,
  CircularProgress,
} from '@mui/material';

const TaskTable = ({
  tasks,
  loading,
  page,
  limit,
  total,
  onPageChange,
  onRowsPerPageChange,
  onSelectTask,
}) => {
  if (loading) {
    return <CircularProgress />;
  }

  return (
    <Box>
      <Table>
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>Created</TableCell>
            <TableCell>Actions</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {tasks.map((task) => (
            <TableRow key={task.id} onClick={() => onSelectTask(task)}>
              <TableCell>{task.name}</TableCell>
              <TableCell>{task.status}</TableCell>
              <TableCell>
                {new Date(task.created_at).toLocaleDateString()}
              </TableCell>
              <TableCell>{/* Action buttons */}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <TablePagination
        component="div"
        count={total}
        page={page - 1}
        onPageChange={(e, newPage) => onPageChange(newPage + 1)}
        rowsPerPage={limit}
        onRowsPerPageChange={(e) => onRowsPerPageChange(e.target.value)}
      />
    </Box>
  );
};

export default TaskTable;
```

**4. Create `TaskActions.jsx` component (150 lines)**

```javascript
// src/components/tasks/TaskActions.jsx
import React, { useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  TextField,
  Button,
  Box,
} from '@mui/material';
import { approveTask, rejectTask } from '../services/taskService';

const TaskActions = ({ selectedTask, onTaskUpdated, onClose }) => {
  const [feedback, setFeedback] = useState('');
  const [approving, setApproving] = useState(false);

  const handleApprove = async () => {
    setApproving(true);
    try {
      const result = await approveTask(selectedTask.id, feedback);
      onTaskUpdated(result);
      onClose();
    } catch (error) {
      console.error('Approval failed:', error);
    } finally {
      setApproving(false);
    }
  };

  const handleReject = async () => {
    setApproving(true);
    try {
      const result = await rejectTask(selectedTask.id, feedback);
      onTaskUpdated(result);
      onClose();
    } catch (error) {
      console.error('Rejection failed:', error);
    } finally {
      setApproving(false);
    }
  };

  return (
    <Dialog open={!!selectedTask} onClose={onClose}>
      <DialogTitle>Task: {selectedTask?.name}</DialogTitle>
      <DialogContent>
        <TextField
          label="Feedback"
          multiline
          rows={4}
          fullWidth
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          sx={{ my: 2 }}
        />

        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            variant="contained"
            color="success"
            onClick={handleApprove}
            disabled={approving}
          >
            Approve
          </Button>
          <Button
            variant="contained"
            color="error"
            onClick={handleReject}
            disabled={approving}
          >
            Reject
          </Button>
        </Box>
      </DialogContent>
    </Dialog>
  );
};

export default TaskActions;
```

**5. Refactor `TaskManagement.jsx` to 150 lines (Orchestrator)**

```javascript
// src/components/tasks/TaskManagement.jsx (REFACTORED)
import React, { useState } from 'react';
import { Box } from '@mui/material';
import TaskFilters from './TaskFilters';
import TaskTable from './TaskTable';
import TaskActions from './TaskActions';
import { useTaskData } from '../../hooks/useTaskData';

function TaskManagement() {
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(10);
  const [sortBy, setSortBy] = useState('created_at');
  const [sortDirection, setSortDirection] = useState('desc');
  const [statusFilter, setStatusFilter] = useState('');
  const [selectedTask, setSelectedTask] = useState(null);

  const { tasks, total, loading, error, setTasks } = useTaskData(
    page,
    limit,
    sortBy,
    sortDirection
  );

  const handleTaskUpdated = (updatedTask) => {
    setTasks(tasks.map((t) => (t.id === updatedTask.id ? updatedTask : t)));
    setSelectedTask(null);
  };

  if (error) {
    return <div>Error: {error}</div>;
  }

  return (
    <Box sx={{ p: 2 }}>
      <TaskFilters
        sortBy={sortBy}
        sortDirection={sortDirection}
        statusFilter={statusFilter}
        onSortChange={setSortBy}
        onDirectionChange={setSortDirection}
        onStatusChange={setStatusFilter}
        onResetFilters={() => {
          setSortBy('created_at');
          setSortDirection('desc');
          setStatusFilter('');
        }}
      />

      <TaskTable
        tasks={tasks}
        loading={loading}
        page={page}
        limit={limit}
        total={total}
        onPageChange={setPage}
        onRowsPerPageChange={setLimit}
        onSelectTask={setSelectedTask}
      />

      {selectedTask && (
        <TaskActions
          selectedTask={selectedTask}
          onTaskUpdated={handleTaskUpdated}
          onClose={() => setSelectedTask(null)}
        />
      )}
    </Box>
  );
}

export default TaskManagement;
```

### Benefits of This Refactoring

✅ **Testability**: Each component can be tested independently  
✅ **Reusability**: TaskTable can be used elsewhere  
✅ **Maintainability**: Changes to one concern don't affect others  
✅ **Performance**: Components re-render independently  
✅ **Readability**: Much clearer intent and flow

### Effort Estimate: 4-6 hours

- 30 min: Plan component boundaries
- 2 hours: Extract hook and components
- 1 hour: Update imports and types
- 1 hour: Test all functionality
- 30 min: ESLint and formatting

---

## Issue #3: Consolidate Auth State Management

### Current Problem (State in 2 Places)

**Zustand Store** (`src/store/useStore.js`):

```javascript
user: null,
accessToken: null,
refreshToken: null,
isAuthenticated: false,
```

**AuthContext** (`src/context/AuthContext.jsx`):

```javascript
const [user, setUser] = useState(null);
const [accessToken, setAccessToken] = useState(null);
const [refreshToken, setRefreshToken] = useState(null);
const [loading, setLoading] = useState(true);
const [isAuthenticated, setIsAuthenticated] = useState(false);
```

❌ **Problem**: Auth state can drift between stores!

### Solution: Single Source of Truth (Zustand Approach)

**Step 1: Enhance `useStore.js`**

```javascript
// src/store/useStore.js
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

const useStore = create(
  persist(
    (set) => ({
      // ===== AUTHENTICATION STATE =====
      user: null,
      accessToken: null,
      refreshToken: null,
      isAuthenticated: false,
      authLoading: true,
      authError: null,

      // Auth actions
      setUser: (user) => set({ user }),
      setAccessToken: (token) => set({ accessToken: token }),
      setRefreshToken: (token) => set({ refreshToken: token }),
      setIsAuthenticated: (isAuth) => set({ isAuthenticated: isAuth }),
      setAuthLoading: (loading) => set({ authLoading: loading }),
      setAuthError: (error) => set({ authError: error }),

      // Combined action for login
      login: (user, accessToken, refreshToken) =>
        set({
          user,
          accessToken,
          refreshToken,
          isAuthenticated: true,
          authLoading: false,
          authError: null,
        }),

      // Clear auth state on logout
      logout: () =>
        set({
          user: null,
          accessToken: null,
          refreshToken: null,
          isAuthenticated: false,
          authLoading: false,
          tasks: [],
          selectedTask: null,
        }),

      // Other state (tasks, metrics, etc.) continues...
    }),
    {
      name: 'glad-labs-store',
      partialize: (state) => ({
        // Persist only specific parts
        user: state.user,
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        theme: state.theme,
        // Don't persist: authLoading, tasks, etc.
      }),
    }
  )
);

export default useStore;
```

**Step 2: Remove Auth from AuthContext**

```javascript
// src/context/AuthContext.jsx (SIMPLIFIED)
import React, { createContext } from 'react';
import useStore from '../store/useStore';

export const AuthContext = createContext();

export function AuthProvider({ children }) {
  // Just provide store access, don't duplicate state
  const user = useStore((state) => state.user);
  const isAuthenticated = useStore((state) => state.isAuthenticated);

  return (
    <AuthContext.Provider value={{ user, isAuthenticated }}>
      {children}
    </AuthContext.Provider>
  );
}
```

**Step 3: Update `useAuth.js` hook**

```javascript
// src/hooks/useAuth.js
import { useCallback } from 'react';
import useStore from '../store/useStore';
import { initializeDevToken, getAuthToken } from '../services/authService';

export function useAuth() {
  const user = useStore((state) => state.user);
  const isAuthenticated = useStore((state) => state.isAuthenticated);
  const authLoading = useStore((state) => state.authLoading);
  const login = useStore((state) => state.login);
  const logout = useStore((state) => state.logout);

  const handleLogout = useCallback(async () => {
    logout();
    // Clear localStorage, auth service state, etc.
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
  }, [logout]);

  return {
    user,
    isAuthenticated,
    loading: authLoading,
    login,
    logout: handleLogout,
  };
}
```

### Benefits

✅ Single source of truth  
✅ No state drift issues  
✅ Simpler debugging  
✅ Easier testing

### Effort Estimate: 2-3 hours

- 30 min: Update Zustand store
- 30 min: Simplify AuthContext
- 30 min: Update useAuth hook
- 1 hour: Update 8 components that read auth
- 30 min: Test and verify

---

## Summary of All 3 Issues

| Issue              | Effort    | Impact        | Priority  |
| ------------------ | --------- | ------------- | --------- |
| #1: Fetch calls    | 2-3h      | High          | 🔴 First  |
| #2: Mega-component | 4-6h      | High          | 🔴 Second |
| #3: Auth state     | 2-3h      | Medium        | 🟠 Third  |
| **TOTAL**          | **8-12h** | **Very High** |           |

Completing these three issues would **dramatically improve code quality** and set foundation for future improvements.

---

_For full analysis, see: OVERSIGHT_HUB_CODE_ANALYSIS.md_
