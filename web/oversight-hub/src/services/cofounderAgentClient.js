/**
 * Cofounder Agent API Client - JWT Auth
 *
 * Environment Variables (required):
 * - REACT_APP_API_URL: Backend API base URL (e.g., https://api.example.com or http://localhost:8000)
 *
 * NOTE: This service does NOT directly update Zustand store.
 * Auth state updates are handled by AuthContext only.
 * Use getAuthToken() to read current token from localStorage.
 */
import { getAuthToken } from './authService';
import { clearPersistedAuthState } from './authService';
import { logErrorToSentry } from './sentryUtils';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

/**
 * Capitalize each word in a string
 * @param {string} str - The string to capitalize
 * @returns {string} - The capitalized string
 */
function capitalizeWords(str) {
  if (!str) {
    return '';
  }
  return str
    .split(' ')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');
}

// API configuration validation - REACT_APP_API_URL should be set in environment

function getAuthHeaders() {
  const accessToken = getAuthToken();
  const headers = { 'Content-Type': 'application/json' };
  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`;
  }
  return headers;
}

export async function makeRequest(
  endpoint,
  method = 'GET',
  data = null,
  retry = false,
  onUnauthorized = null,
  timeout = 30000 // 30 seconds - allows for long-running operations like Ollama generation
) {
  try {
    const url = `${API_BASE_URL}${endpoint}`;
    const config = { method, headers: getAuthHeaders() };

    // Handle FormData (file uploads) - must NOT set Content-Type header
    if (data instanceof FormData) {
      delete config.headers['Content-Type']; // Let browser set it automatically
      config.body = data;
    } else if (data) {
      config.body = JSON.stringify(data);
    }

    // Use AbortController to implement timeout
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);
    config.signal = controller.signal;

    try {
      const response = await fetch(url, config);
      clearTimeout(timeoutId);

      if (response.status === 401 && !retry) {
        // Try to refresh token in development — do NOT clear existing state
        // (other requests may be in-flight with a valid token)
        if (process.env.NODE_ENV === 'development') {
          try {
            const { initializeDevToken } = await import('./authService');
            await initializeDevToken({
              forceRefresh: true,
              validateWithBackend: false,
            });
            // Retry the request with new token
            return makeRequest(
              endpoint,
              method,
              data,
              true,
              onUnauthorized,
              timeout
            );
          } catch (refreshError) {
            logErrorToSentry(refreshError, {
              customContext: { action: 'refreshToken' },
            });
          }
        }

        clearPersistedAuthState();

        // Call the onUnauthorized callback if provided
        if (onUnauthorized) {
          onUnauthorized();
        }
        throw new Error('Unauthorized - token expired or invalid');
      }

      // Handle 204 No Content response (no body to parse)
      if (response.status === 204) {
        return { success: true };
      }

      const result = await response.json().catch(() => response.text());
      if (!response.ok) {
        // Extract error message from response
        let errorMessage = `HTTP ${response.status}`;
        if (typeof result === 'string') {
          errorMessage = result || errorMessage;
        } else if (typeof result === 'object' && result !== null) {
          // Try different error message keys used by FastAPI
          if (result.detail) {
            errorMessage =
              typeof result.detail === 'string'
                ? result.detail
                : JSON.stringify(result.detail);
          } else if (result.message) {
            errorMessage =
              typeof result.message === 'string'
                ? result.message
                : JSON.stringify(result.message);
          } else {
            errorMessage = JSON.stringify(result);
          }
        }
        const error = new Error(errorMessage);
        error.status = response.status;
        error.response = result; // Include full response for debugging
        logErrorToSentry(error, {
          customContext: {
            action: 'apiErrorResponse',
            status: response.status,
          },
        });
        throw error;
      }
      return result;
    } catch (fetchError) {
      clearTimeout(timeoutId);
      // Check if it's an abort error (timeout)
      if (fetchError.name === 'AbortError') {
        throw new Error(
          `Request timeout after ${timeout}ms - operation took too long`
        );
      }
      throw fetchError;
    }
  } catch (error) {
    logErrorToSentry(error, {
      customContext: { action: 'makeRequest', endpoint },
    });
    throw error;
  }
}

/**
 * NOTE: login() function removed - use AuthCallback + exchangeCodeForToken from authService instead
 * This service should NOT handle authentication state updates.
 * Authentication is managed exclusively by AuthContext.
 */

export async function logout() {
  try {
    // Attempt to notify backend of logout
    await makeRequest('/api/auth/logout', 'POST');
  } catch (_error) {
    // Continue with local logout even if API call fails
  }
  // Note: Actual state clearing happens in AuthContext.logout()
}

export async function getTasks(
  limit = 50,
  offset = 0,
  { status, search } = {}
) {
  const params = new URLSearchParams({ limit, offset });
  if (status) params.set('status', status);
  if (search) params.set('search', search);
  return makeRequest(`/api/tasks?${params}`, 'GET', null, false, null, 120000); // 120 second timeout
}

export async function getTaskStatus(taskId) {
  // Use the correct backend endpoint: /api/tasks/{taskId}
  // Use 180 second timeout for task status (allows for long-running operations)
  try {
    return await makeRequest(
      `/api/tasks/${taskId}`,
      'GET',
      null,
      false,
      null,
      180000
    );
  } catch (error) {
    // If 404, the task ID doesn't exist
    if (error.status === 404) {
      return null;
    }
    throw error;
  }
}

export async function getMetrics() {
  return makeRequest('/api/metrics', 'GET');
}

// ============================================================================
// OAuth Provider Functions
// ============================================================================

/**
 * Get available OAuth providers
 * @returns {Promise} List of available OAuth providers
 */
export async function getOAuthProviders() {
  return makeRequest('/api/auth/providers', 'GET');
}

/**
 * Get OAuth login URL for a specific provider
 * @param {string} provider - Provider name (e.g., 'github')
 * @returns {Promise} Login URL redirect
 */
export async function getOAuthLoginURL(provider) {
  const data = await makeRequest(`/api/auth/${provider}/login`, 'GET');
  return data.login_url;
}

/**
 * Handle OAuth callback after user authorization
 * @param {string} provider - OAuth provider (e.g., 'github')
 * @param {string} code - Authorization code from provider
 * @param {string} state - State parameter for CSRF protection
 * @returns {Promise} User data and tokens
 */
export async function handleOAuthCallback(provider, code, state) {
  if (!code) {
    throw new Error('Authorization code missing from OAuth callback');
  }

  return makeRequest(
    `/api/auth/${provider}/callback`,
    'POST',
    {
      code,
      state,
    },
    true,
    null,
    15000
  );
}

/**
 * Get current user data
 * @returns {Promise} Current user data
 */
export async function getCurrentUser() {
  return makeRequest('/api/auth/me', 'GET');
}

// ============================================================================
// ============================================================================
// Task Management
// ============================================================================

/**
 * Create new task
 * @param {object} taskData - Task data {title, description, type, parameters}
 * @returns {Promise} Created task with ID
 */
export async function createTask(taskData) {
  return makeRequest('/api/tasks', 'POST', taskData, false, null, 60000); // 60s for task creation
}

/**
 * List tasks with optional filtering
 * @param {number} limit - Number of tasks to return
 * @param {number} offset - Offset for pagination
 * @param {string} status - Filter by status (pending, in_progress, completed, failed)
 * @returns {Promise} List of tasks
 */
export async function listTasks(limit = 20, offset = 0, status = null) {
  const query = new URLSearchParams({ limit, offset });
  if (status) {
    query.append('status', status);
  }
  return makeRequest(`/api/tasks?${query.toString()}`, 'GET');
}

/**
 * Generate an image for a task using AI
 * @param {string} taskId - Task ID
 * @param {Object} options - Image generation options
 * @param {string} options.source - Image source/model
 * @param {string} options.topic - Topic for image generation
 * @param {string} options.content_summary - Brief summary of content
 * @returns {Promise<Object>} Generated image data with image_url
 */
export async function generateTaskImage(taskId, options = {}) {
  return makeRequest(`/api/tasks/${taskId}/generate-image`, 'POST', options);
}

// ============================================================================
// Intelligent Orchestrator
// ============================================================================

export async function getOrchestratorApproval(taskId) {
  return makeRequest(`/api/orchestrator/approval/${taskId}`, 'GET');
}

export async function approveOrchestratorResult(taskId, action) {
  return makeRequest(`/api/orchestrator/approve/${taskId}`, 'POST', action);
}

export async function getOrchestratorTools() {
  return makeRequest('/api/orchestrator/tools', 'GET');
}

/**
 * Chat API Methods
 * Endpoints for conversation-based AI interactions
 */

export async function sendChatMessage(
  message,
  model = 'openai-gpt4',
  conversationId = 'default'
) {
  /**
   * Send a chat message and get AI response
   *
   * @param {string} message - The user's message/prompt
   * @param {string} model - The model to use (openai-gpt4, claude-opus, gemini-pro, ollama-mistral, etc.)
   * @param {string} conversationId - Conversation ID to maintain context (default: 'default')
   * @returns {Promise<object>} - Response with message and conversation_id
   */
  const payload = {
    message,
    model,
    conversation_id: conversationId,
  };
  return makeRequest('/api/chat', 'POST', payload, false, null, 60000); // 60s timeout for chat
}

export async function getChatHistory(conversationId = 'default') {
  /**
   * Get conversation history
   *
   * @param {string} conversationId - The conversation ID to retrieve
   * @returns {Promise<object>} - Conversation history with messages
   */
  return makeRequest(
    `/api/chat/history/${conversationId}`,
    'GET',
    null,
    false,
    null,
    30000
  );
}

export async function clearChatHistory(conversationId = 'default') {
  /**
   * Clear a conversation's history
   *
   * @param {string} conversationId - The conversation to clear
   * @returns {Promise<object>} - Confirmation response
   */
  return makeRequest(
    `/api/chat/history/${conversationId}`,
    'DELETE',
    null,
    false,
    null,
    10000
  );
}

export async function getAvailableModels() {
  /**
   * Get list of available AI models
   *
   * @returns {Promise<object>} - List of available models with info
   */
  return makeRequest('/api/chat/models', 'GET', null, false, null, 10000);
}

/**
 * Agent API Methods
 * Endpoints for multi-agent orchestration and management
 */

export async function getAgentStatus(agentId) {
  /**
   * Get real-time status of a specific agent
   *
   * @param {string} agentId - The agent ID
   * @returns {Promise<object>} - Agent status info (status, tasks_completed, current_task, etc.)
   */
  return makeRequest(
    `/api/agents/${agentId}/status`,
    'GET',
    null,
    false,
    null,
    10000
  );
}

export async function getAgentLogs(agentId, limit = 100) {
  /**
   * Get logs for a specific agent
   *
   * @param {string} agentId - The agent ID
   * @param {number} limit - Maximum number of log entries to retrieve
   * @returns {Promise<Array>} - Array of log entries
   */
  return makeRequest(
    `/api/agents/${agentId}/logs?limit=${limit}`,
    'GET',
    null,
    false,
    null,
    10000
  );
}

export async function sendAgentCommand(agentId, command) {
  /**
   * Send a command/task to a specific agent
   *
   * @param {string} agentId - The agent ID
   * @param {string} command - The command or task description
   * @returns {Promise<object>} - Command execution result
   */
  const payload = { command };
  return makeRequest(
    `/api/agents/${agentId}/command`,
    'POST',
    payload,
    false,
    null,
    30000
  );
}

export async function getAgentMetrics(agentId) {
  /**
   * Get performance metrics for a specific agent
   *
   * @param {string} agentId - The agent ID
   * @returns {Promise<object>} - Agent metrics (success rate, avg response time, etc.)
   */
  return makeRequest(
    `/api/agents/${agentId}/metrics`,
    'GET',
    null,
    false,
    null,
    10000
  );
}

/**
 * Workflow API Methods
 * Endpoints for workflow execution history and management
 */

export async function getWorkflowHistory(limit = 50, offset = 0) {
  /**
   * Get workflow execution history
   *
   * @param {number} limit - Maximum number of executions to retrieve
   * @param {number} offset - Pagination offset
   * @returns {Promise<Array|object>} - List of workflow executions
   */
  return makeRequest(
    `/api/workflow/history?limit=${limit}&offset=${offset}`,
    'GET',
    null,
    false,
    null,
    15000
  );
}

export async function getExecutionDetails(executionId) {
  /**
   * Get detailed information about a specific execution
   *
   * @param {string} executionId - The execution ID
   * @returns {Promise<object>} - Detailed execution information
   */
  return makeRequest(
    `/api/workflow/execution/${executionId}`,
    'GET',
    null,
    false,
    null,
    10000
  );
}

export async function retryExecution(executionId) {
  /**
   * Retry a failed execution
   *
   * @param {string} executionId - The execution ID to retry
   * @returns {Promise<object>} - New execution result
   */
  return makeRequest(
    `/api/workflow/execution/${executionId}/retry`,
    'POST',
    null,
    false,
    null,
    30000
  );
}

export async function getDetailedMetrics(timeRange = '24h') {
  /**
   * Get detailed performance metrics across all workflows and agents
   *
   * @param {string} timeRange - Time range for metrics ('1h', '24h', '7d', '30d')
   * @returns {Promise<object>} - Detailed metrics data
   */
  return makeRequest(
    `/api/metrics/detailed?range=${timeRange}`,
    'GET',
    null,
    false,
    null,
    15000
  );
}

export async function exportMetrics(format = 'csv', timeRange = '24h') {
  /**
   * Export metrics in specified format
   *
   * @param {string} format - Export format ('csv', 'json', 'pdf')
   * @param {string} timeRange - Time range for export
   * @returns {Promise<Blob>} - Exported data as file
   */
  return makeRequest(
    `/api/metrics/export?format=${format}&range=${timeRange}`,
    'GET',
    null,
    false,
    null,
    30000
  );
}

/**
 * Cost Metrics - Get cost breakdown and usage statistics
 */

export async function getCostMetrics() {
  /**
   * Get AI model usage and cost metrics
   *
   * @returns {Promise<object>} - Cost breakdown by model and provider, token usage
   */
  return makeRequest('/api/metrics/costs', 'GET', null, true, null, 15000);
}

export async function getUsageMetrics(period = 'last_24h') {
  /**
   * Get comprehensive usage metrics
   *
   * @param {string} period - Time period: last_1h, last_24h, last_7d, all
   * @returns {Promise<object>} - Usage stats, token counts, cost analysis
   */
  return makeRequest(
    `/api/metrics/usage?period=${period}`,
    'GET',
    null,
    true,
    null,
    15000
  );
}

// ============================================================================
// NEW Week 2 Cost Analytics Methods (Database-Backed)
// ============================================================================

export async function getCostsByPhase(period = 'week') {
  /**
   * Get cost breakdown by pipeline phase
   *
   * @param {string} period - Time period: today, week, month
   * @returns {Promise<object>} - Costs per phase with task counts and percentages
   */
  return makeRequest(
    `/api/metrics/costs/breakdown/phase?period=${period}`,
    'GET',
    null,
    true,
    null,
    10000
  );
}

export async function getCostsByModel(period = 'week') {
  /**
   * Get cost breakdown by AI model
   *
   * @param {string} period - Time period: today, week, month
   * @returns {Promise<object>} - Costs per model with provider and percentages
   */
  return makeRequest(
    `/api/metrics/costs/breakdown/model?period=${period}`,
    'GET',
    null,
    true,
    null,
    10000
  );
}

export async function getCostHistory(period = 'week') {
  /**
   * Get cost history and trends
   *
   * @param {string} period - Time period: week, month
   * @returns {Promise<object>} - Daily costs, trend direction, weekly average
   */
  return makeRequest(
    `/api/metrics/costs/history?period=${period}`,
    'GET',
    null,
    true,
    null,
    10000
  );
}

export async function getBudgetStatus(monthlyBudget = 150.0) {
  /**
   * Get budget status and alerts
   *
   * @param {number} monthlyBudget - Monthly budget limit in USD (default $150)
   * @returns {Promise<object>} - Budget metrics, burn rate, projections, alerts
   */
  return makeRequest(
    `/api/metrics/costs/budget?monthly_budget=${monthlyBudget}`,
    'GET',
    null,
    true,
    null,
    10000
  );
}

/**
 * Bulk Task Operations - Perform actions on multiple tasks at once
 */

export async function bulkUpdateTasks(taskIds, action) {
  /**
   * Perform bulk operations on multiple tasks
   *
   * @param {Array<string>} taskIds - List of task IDs to update
   * @param {string} action - Action to perform: pause, resume, cancel, delete
   * @returns {Promise<object>} - Result with updated count, failed count, errors
   */
  const payload = {
    task_ids: taskIds,
    action,
  };
  return makeRequest('/api/tasks/bulk', 'POST', payload, true, null, 30000);
}

/**
 * Orchestrator Routes - Get orchestration status and analytics
 */

export async function getOrchestratorOverallStatus() {
  /**
   * Get overall orchestrator status
   *
   * @returns {Promise<object>} - Status of orchestrator, active agents, pending tasks
   */
  return makeRequest(
    '/api/orchestrator/status',
    'GET',
    null,
    true,
    null,
    15000
  );
}

export async function getActiveAgents() {
  /**
   * Get list of currently active agents
   *
   * @returns {Promise<Array>} - List of active agents with status
   */
  return makeRequest(
    '/api/orchestrator/active-agents',
    'GET',
    null,
    true,
    null,
    10000
  );
}

export async function getTaskQueue() {
  /**
   * Get current task queue pending execution
   *
   * @returns {Promise<Array>} - List of pending tasks
   */
  return makeRequest(
    '/api/orchestrator/task-queue',
    'GET',
    null,
    true,
    null,
    10000
  );
}

export async function getLearningPatterns() {
  /**
   * Get patterns learned from execution history
   *
   * @returns {Promise<object>} - Learning patterns and insights
   */
  return makeRequest(
    '/api/orchestrator/learning-patterns',
    'GET',
    null,
    true,
    null,
    15000
  );
}

export async function getBusinessMetricsAnalysis() {
  /**
   * Get business metrics analysis and trends
   *
   * @returns {Promise<object>} - Business metrics analysis
   */
  return makeRequest(
    '/api/orchestrator/business-metrics-analysis',
    'GET',
    null,
    true,
    null,
    15000
  );
}

export const cofounderAgentClient = {
  logout,
  getTasks,
  getTaskStatus,
  getMetrics,
  // OAuth functions
  getOAuthProviders,
  getOAuthLoginURL,
  handleOAuthCallback,
  getCurrentUser,
  // Task management
  createTask,
  listTasks,
  generateTaskImage,
  // Bulk operations
  bulkUpdateTasks,
  // Metrics & Analytics
  getCostMetrics,
  getUsageMetrics,
  // Week 2 Cost Analytics
  getCostsByPhase,
  getCostsByModel,
  getCostHistory,
  getBudgetStatus,
  // Orchestrator
  getOrchestratorOverallStatus,
  getActiveAgents,
  getTaskQueue,
  getLearningPatterns,
  getBusinessMetricsAnalysis,
  // Intelligent Orchestrator
  getOrchestratorApproval,
  approveOrchestratorResult,
  getOrchestratorTools,
  // Chat
  sendChatMessage,
  getChatHistory,
  clearChatHistory,
  getAvailableModels,
  // Agents
  getAgentStatus,
  getAgentLogs,
  sendAgentCommand,
  getAgentMetrics,
  // Workflow
  getWorkflowHistory,
  getExecutionDetails,
  retryExecution,
  getDetailedMetrics,
  exportMetrics,
};
