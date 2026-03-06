/**
 * Capability Tasks Service - Client-side service for capability API
 *
 * Handles all capability-related API calls:
 * - List capabilities
 * - Get capability details
 * - Create, read, update, delete tasks
 * - Execute tasks and get results
 */

const API_BASE = 'http://localhost:8000/api';

class CapabilityTasksService {
  /**
   * Get all available capabilities
   */
  static async listCapabilities(tag = null, costTier = null) {
    const params = new URLSearchParams();
    if (tag) params.append('tag', tag);
    if (costTier) params.append('cost_tier', costTier);

    const queryStr = params.toString() ? `?${params.toString()}` : '';
    const response = await fetch(`${API_BASE}/capabilities${queryStr}`);

    if (!response.ok) {
      throw new Error(`Failed to list capabilities: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * Get details for a specific capability (includes input/output schema)
   */
  static async getCapability(name) {
    const response = await fetch(`${API_BASE}/capabilities/${name}`);

    if (!response.ok) {
      throw new Error(`Failed to get capability: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * Create a new capability-based task
   */
  static async createTask(name, description, steps, tags = []) {
    const response = await fetch(`${API_BASE}/tasks/capability`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('authToken')}`,
      },
      body: JSON.stringify({
        name,
        description,
        steps,
        tags,
      }),
    });

    if (!response.ok) {
      throw new Error(`Failed to create task: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * Get a specific task
   */
  static async getTask(taskId) {
    const response = await fetch(`${API_BASE}/tasks/capability/${taskId}`, {
      headers: {
        Authorization: `Bearer ${localStorage.getItem('authToken')}`,
      },
    });

    if (!response.ok) {
      throw new Error(`Failed to get task: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * List user's tasks
   */
  static async listTasks(skip = 0, limit = 50) {
    const response = await fetch(
      `${API_BASE}/tasks/capability?skip=${skip}&limit=${limit}`,
      {
        headers: {
          Authorization: `Bearer ${localStorage.getItem('authToken')}`,
        },
      }
    );

    if (!response.ok) {
      throw new Error(`Failed to list tasks: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * Update a task
   */
  static async updateTask(taskId, name, description, steps) {
    const response = await fetch(`${API_BASE}/tasks/capability/${taskId}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('authToken')}`,
      },
      body: JSON.stringify({
        name,
        description,
        steps,
      }),
    });

    if (!response.ok) {
      throw new Error(`Failed to update task: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * Delete a task
   */
  static async deleteTask(taskId) {
    const response = await fetch(`${API_BASE}/tasks/capability/${taskId}`, {
      method: 'DELETE',
      headers: {
        Authorization: `Bearer ${localStorage.getItem('authToken')}`,
      },
    });

    if (!response.ok) {
      throw new Error(`Failed to delete task: ${response.statusText}`);
    }
  }

  /**
   * Execute a task (start execution)
   */
  static async executeTask(taskId) {
    const response = await fetch(
      `${API_BASE}/tasks/capability/${taskId}/execute`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${localStorage.getItem('authToken')}`,
        },
      }
    );

    if (!response.ok) {
      throw new Error(`Failed to execute task: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * Get execution result
   */
  static async getExecution(taskId, executionId) {
    const response = await fetch(
      `${API_BASE}/tasks/capability/${taskId}/executions/${executionId}`,
      {
        headers: {
          Authorization: `Bearer ${localStorage.getItem('authToken')}`,
        },
      }
    );

    if (!response.ok) {
      throw new Error(`Failed to get execution: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * List executions for a task
   */
  static async listExecutions(taskId, skip = 0, limit = 50, status = null) {
    const params = new URLSearchParams({ skip, limit });
    if (status) params.append('status', status);

    const response = await fetch(
      `${API_BASE}/tasks/capability/${taskId}/executions?${params.toString()}`,
      {
        headers: {
          Authorization: `Bearer ${localStorage.getItem('authToken')}`,
        },
      }
    );

    if (!response.ok) {
      throw new Error(`Failed to list executions: ${response.statusText}`);
    }

    return response.json();
  }
}

export default CapabilityTasksService;
