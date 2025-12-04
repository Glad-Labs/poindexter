# Oversight Hub Component Migration Guide

**Purpose:** Update React components to use the refactored API client  
**Status:** Ready for implementation  
**Time Estimate:** 2-4 hours for complete migration

---

## 📋 Overview

This guide shows how to migrate existing Oversight Hub components from direct API calls to the new refactored `apiClient.js`.

### Key Benefits

✅ **Centralized API Logic**

- Single source of truth for all endpoints
- Consistent error handling across app
- Automatic retry logic
- Token management built-in

✅ **Improved Maintainability**

- Easy to update endpoints in one place
- Type-safe with JSDoc comments
- Comprehensive error handling
- Better error messages for users

✅ **Performance Improvements**

- Request caching
- Automatic retries for transient failures
- Proper timeout handling
- Rate limit awareness

---

## 🔄 Migration Pattern

### Before (Direct API Calls)

```javascript
// OLD: Scattered API calls throughout component
const handleCreateTask = async (taskData) => {
  try {
    const response = await fetch('http://localhost:8000/api/tasks', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(taskData),
    });

    if (response.status === 401) {
      localStorage.removeItem('token');
      navigate('/login');
    }

    const data = await response.json();
    setTasks([...tasks, data]);
  } catch (error) {
    setError(error.message);
  }
};
```

### After (Using API Client)

```javascript
// NEW: Clean, centralized API client usage
import { createTask, formatApiError } from '../lib/apiClient';

const handleCreateTask = async (taskData) => {
  try {
    const newTask = await createTask(taskData);
    setTasks([...tasks, newTask]);
  } catch (error) {
    setError(formatApiError(error)); // User-friendly message
  }
};
```

---

## 📁 Components to Migrate

### 1. TaskList.jsx

**Current:** Lists tasks, calls backend directly  
**Uses:** `/api/tasks` GET endpoint

**Migration:**

```javascript
// BEFORE
const fetchTasks = async () => {
  const response = await fetch(
    `http://localhost:8000/api/tasks?skip=${skip}&limit=${limit}`
  );
  const data = await response.json();
  setTasks(data.tasks);
};

// AFTER
import { listTasks } from '../lib/apiClient';

const fetchTasks = async () => {
  const response = await listTasks(skip, limit, 'all');
  setTasks(response.tasks || []);
};
```

### 2. TaskCreationModal.jsx

**Current:** Creates new tasks  
**Uses:** `/api/tasks` POST endpoint

**Migration:**

```javascript
// BEFORE
const handleSubmit = async (e) => {
  e.preventDefault();
  const response = await fetch('http://localhost:8000/api/tasks', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(formData),
  });
  const newTask = await response.json();
  onTaskCreated(newTask);
};

// AFTER
import { createTask, formatApiError } from '../lib/apiClient';

const handleSubmit = async (e) => {
  e.preventDefault();
  try {
    const newTask = await createTask(formData);
    onTaskCreated(newTask);
  } catch (error) {
    setErrorMessage(formatApiError(error));
  }
};
```

### 3. TaskDetailModal.jsx

**Current:** Shows task details, allows status updates  
**Uses:** `/api/tasks/{id}` GET/PATCH endpoints

**Migration:**

```javascript
// BEFORE
useEffect(() => {
  const fetchTask = async () => {
    const response = await fetch(`http://localhost:8000/api/tasks/${taskId}`);
    const data = await response.json();
    setTask(data);
  };
  fetchTask();
}, [taskId]);

const updateTaskStatus = async (newStatus) => {
  await fetch(`http://localhost:8000/api/tasks/${taskId}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ status: newStatus }),
  });
};

// AFTER
import { getTask, updateTask } from '../lib/apiClient';

useEffect(() => {
  const fetchTask = async () => {
    const task = await getTask(taskId);
    setTask(task);
  };
  fetchTask();
}, [taskId]);

const updateTaskStatus = async (newStatus) => {
  const updated = await updateTask(taskId, { status: newStatus });
  setTask(updated);
};
```

### 4. TaskPreviewModal.jsx

**Current:** Previews generated content before publishing  
**Uses:** `/api/tasks/{id}/result` and `/api/tasks/{id}/publish` endpoints

**Migration:**

```javascript
// BEFORE
const previewContent = async () => {
  const response = await fetch(
    `http://localhost:8000/api/tasks/${taskId}/preview`
  );
  const data = await response.json();
  setPreviewData(data);
};

const publishContent = async () => {
  const response = await fetch(
    `http://localhost:8000/api/tasks/${taskId}/publish`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ category_id: selectedCategory }),
    }
  );
  const post = await response.json();
  onPublished(post);
};

// AFTER
import {
  previewContent as apiPreviewContent,
  publishTaskAsPost,
} from '../lib/apiClient';

const previewContent = async () => {
  const preview = await apiPreviewContent(taskId);
  setPreviewData(preview);
};

const publishContent = async () => {
  const post = await publishTaskAsPost(taskId, {
    category_id: selectedCategory,
  });
  onPublished(post);
};
```

### 5. TaskResultModal.jsx

**Current:** Shows task results/generated content  
**Uses:** `/api/tasks/{id}/result` endpoint

**Migration:**

```javascript
// BEFORE
useEffect(() => {
  const fetchResult = async () => {
    const response = await fetch(
      `http://localhost:8000/api/tasks/${taskId}/result`
    );
    const data = await response.json();
    setResult(data);
  };
  fetchResult();
}, [taskId]);

// AFTER
import { getTaskResult } from '../lib/apiClient';

useEffect(() => {
  const fetchResult = async () => {
    const result = await getTaskResult(taskId);
    setResult(result);
  };
  fetchResult();
}, [taskId]);
```

### 6. StrapiPosts.jsx

**Current:** Lists and manages blog posts  
**Uses:** `/api/posts` endpoints

**Migration:**

```javascript
// BEFORE
const fetchPosts = async () => {
  const response = await fetch(
    `http://localhost:8000/api/posts?skip=${skip}&limit=${limit}&status=published`
  );
  const data = await response.json();
  setPosts(data.posts);
};

const updatePost = async (postId, updates) => {
  await fetch(`http://localhost:8000/api/posts/${postId}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(updates),
  });
};

// AFTER
import { listPosts, updatePost } from '../lib/apiClient';

const fetchPosts = async () => {
  const response = await listPosts(skip, limit, true); // true = published_only
  setPosts(response.posts || []);
};

const handlePostUpdate = async (postId, updates) => {
  const updated = await updatePost(postId, updates);
  // Update local state with new post
};
```

### 7. ContentMetricsDashboard.jsx

**Current:** Shows content generation metrics  
**Uses:** `/api/metrics` and `/api/content/metrics` endpoints

**Migration:**

```javascript
// BEFORE
useEffect(() => {
  const fetchMetrics = async () => {
    const response = await fetch('http://localhost:8000/api/content/metrics');
    const metrics = await response.json();
    setMetrics(metrics);
  };
  fetchMetrics();
  const interval = setInterval(fetchMetrics, 30000); // Every 30 seconds
  return () => clearInterval(interval);
}, []);

// AFTER
import { getContentMetrics } from '../lib/apiClient';

useEffect(() => {
  const fetchMetrics = async () => {
    const metrics = await getContentMetrics();
    setMetrics(metrics);
  };
  fetchMetrics();
  const interval = setInterval(fetchMetrics, 30000);
  return () => clearInterval(interval);
}, []);
```

### 8. SystemHealthDashboard.jsx

**Current:** Shows system health and status  
**Uses:** `/api/health` and `/api/metrics` endpoints

**Migration:**

```javascript
// BEFORE
useEffect(() => {
  const checkHealth = async () => {
    const response = await fetch('http://localhost:8000/api/health');
    const health = await response.json();
    setHealthStatus(health);
  };
  checkHealth();
  const interval = setInterval(checkHealth, 10000);
  return () => clearInterval(interval);
}, []);

// AFTER
import { getHealth, getMetrics } from '../lib/apiClient';

useEffect(() => {
  const checkHealth = async () => {
    try {
      const health = await getHealth();
      const metrics = await getMetrics();
      setHealthStatus({ ...health, ...metrics });
    } catch (error) {
      console.error('Health check failed:', error);
    }
  };
  checkHealth();
  const interval = setInterval(checkHealth, 10000);
  return () => clearInterval(interval);
}, []);
```

### 9. ModelConfigurationPanel.jsx

**Current:** Configures AI models  
**Uses:** `/api/models` endpoints

**Migration:**

```javascript
// BEFORE
useEffect(() => {
  const fetchModels = async () => {
    const response = await fetch('http://localhost:8000/api/models');
    const models = await response.json();
    setModels(models);
  };
  fetchModels();
}, []);

const testModel = async (provider, model) => {
  const response = await fetch('http://localhost:8000/api/models/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, model }),
  });
  return response.ok;
};

// AFTER
import { listModels, testModel } from '../lib/apiClient';

useEffect(() => {
  const fetchModels = async () => {
    const models = await listModels();
    setModels(models);
  };
  fetchModels();
}, []);

const handleTestModel = async (provider, model) => {
  try {
    const result = await testModel(provider, model);
    setTestResult(result);
  } catch (error) {
    setError(formatApiError(error));
  }
};
```

### 10. TaskErrorHandling.jsx

**Current:** Displays and handles errors from failed tasks  
**Uses:** Error utilities from API client

**Migration:**

```javascript
// BEFORE
const getErrorMessage = (error) => {
  if (error.response?.status === 404) {
    return 'Task not found';
  } else if (error.response?.status === 422) {
    return 'Invalid input data';
  } else {
    return 'An error occurred';
  }
};

// AFTER
import { formatApiError, isRecoverableError } from '../lib/apiClient';

const handleError = (error) => {
  if (isRecoverableError(error)) {
    return 'Service temporarily unavailable. Please try again.';
  }
  return formatApiError(error);
};
```

---

## 🔐 Error Handling Patterns

### Pattern 1: Basic Error Display

```javascript
import { formatApiError } from '../lib/apiClient';

try {
  const data = await getTask(taskId);
  setTask(data);
} catch (error) {
  setErrorMessage(formatApiError(error));
  // User sees: "Task not found (404)" or "Invalid authentication (401)"
}
```

### Pattern 2: Recoverable Error Retry

```javascript
import { isRecoverableError, retryWithBackoff } from '../lib/apiClient';

try {
  const data = await retryWithBackoff(
    () => listTasks(skip, limit),
    3 // max 3 retries
  );
  setTasks(data);
} catch (error) {
  if (isRecoverableError(error)) {
    setMessage('Service temporarily unavailable. Please try again.');
  } else {
    setErrorMessage(formatApiError(error));
  }
}
```

### Pattern 3: User-Friendly Error Messages

```javascript
const handleTaskCreation = async (formData) => {
  try {
    const newTask = await createTask(formData);
    showSuccessMessage('Task created successfully');
    onTaskCreated(newTask);
  } catch (error) {
    const message = formatApiError(error);
    // Shows: "Task name is required (422)" or "Server error (500)"
    showErrorMessage(message);
  }
};
```

---

## ✅ Migration Checklist

For each component:

- [ ] Import API functions from `../lib/apiClient`
- [ ] Import error utilities (`formatApiError`, `isRecoverableError`)
- [ ] Replace fetch() calls with API client functions
- [ ] Update error handling to use `formatApiError()`
- [ ] Test component in browser
- [ ] Verify API calls work correctly
- [ ] Check error handling for edge cases
- [ ] Verify token/auth handling works

---

## 🧪 Testing Component Changes

### Before Component Migration

```bash
# Run existing tests
npm test -- web/oversight-hub --watch

# If no tests exist yet, create them:
# web/oversight-hub/src/components/__tests__/TaskList.test.jsx
```

### After Component Migration

```bash
# Test component with new API client
npm test -- TaskList.test.jsx

# Run integration test
npm test -- __tests__/integration/

# Manual browser test
npm start
# Visit http://localhost:3001
# Click "Create Task"
# Verify it works and no console errors
```

### Test Template for Component

```javascript
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TaskList from './TaskList';
import * as apiClient from '../lib/apiClient';

jest.mock('../lib/apiClient');

describe('TaskList Component', () => {
  it('renders tasks from API', async () => {
    apiClient.listTasks.mockResolvedValue({
      tasks: [{ id: '1', task_name: 'Test' }],
    });

    render(<TaskList />);

    await waitFor(() => {
      expect(screen.getByText('Test')).toBeInTheDocument();
    });
  });

  it('handles API errors gracefully', async () => {
    apiClient.listTasks.mockRejectedValue(new Error('Network error'));

    render(<TaskList />);

    await waitFor(() => {
      expect(screen.getByText(/error/i)).toBeInTheDocument();
    });
  });
});
```

---

## 📊 Migration Progress Tracking

Create a `MIGRATION_PROGRESS.md` file to track:

```markdown
# Oversight Hub API Client Migration Progress

## Component Status

- [ ] TaskList.jsx (Not started)
- [ ] TaskCreationModal.jsx (Not started)
- [ ] TaskDetailModal.jsx (Not started)
- [ ] TaskPreviewModal.jsx (Not started)
- [ ] TaskResultModal.jsx (Not started)
- [ ] StrapiPosts.jsx (Not started)
- [ ] ContentMetricsDashboard.jsx (Not started)
- [ ] SystemHealthDashboard.jsx (Not started)
- [ ] ModelConfigurationPanel.jsx (Not started)
- [ ] TaskErrorHandling.jsx (Not started)

## Overall Progress: 0/10 (0%)
```

---

## 🚀 Next Steps

1. **Start with simplest component** - TaskList.jsx or SystemHealthDashboard.jsx
2. **Migrate one component** - Complete full workflow:
   - Update imports
   - Replace fetch calls
   - Update error handling
   - Test in browser
3. **Move to next component** - Follow same pattern
4. **Test full integration** - All components working together
5. **Deploy to staging** - Run full test suite
6. **Gather feedback** - Verify UI/UX improvements

---

**Estimated Time per Component:** 15-30 minutes  
**Total Migration Time:** 2-4 hours  
**Complexity:** Low - mostly search and replace operations
