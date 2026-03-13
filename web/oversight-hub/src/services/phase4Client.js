/**
 * Phase 4 Client - Clean wrapper for unified services architecture
 *
 * Provides access to Phase 4 REST endpoints:
 * - Agent Discovery API (/api/agents/*)
 * - Service Registry API (/api/services/*)
 * - Workflow Execution API (/api/workflows/*)
 * - Task Management API (/api/tasks/*)
 *
 * @module phase4Client
 */

import { logError } from './errorLoggingService';
import { getApiUrl } from '../config/apiConfig';

const API_BASE_URL = getApiUrl();
const REQUEST_TIMEOUT = 30000; // 30 seconds

/**
 * Core makeRequest wrapper - mirrors cofounderAgentClient pattern
 * Handles cookie-based auth, timeouts, and error formatting
 */
async function makeRequest(endpoint, options = {}) {
  const {
    method = 'GET',
    body = null,
    headers = {},
    timeout = REQUEST_TIMEOUT,
  } = options;

  try {
    const finalHeaders = {
      'Content-Type': 'application/json',
      ...headers,
    };

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      method,
      headers: finalHeaders,
      credentials: 'include',
      body: body ? JSON.stringify(body) : null,
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      let errorData;
      try {
        errorData = await response.json();
      } catch {
        errorData = { detail: response.statusText };
      }

      const errorMessage = errorData.detail || `HTTP ${response.status}`;
      logError(`Phase4 API Error (${endpoint})`, {
        status: response.status,
        message: errorMessage,
        endpoint,
      });

      const error = new Error(errorMessage);
      error.status = response.status;
      error.endpoint = endpoint;
      throw error;
    }

    return await response.json();
  } catch (error) {
    if (error.name === 'AbortError') {
      const timeoutError = new Error(`Request timeout after ${timeout}ms`);
      timeoutError.endpoint = endpoint;
      logError('Phase4 API Timeout', {
        message: timeoutError.message,
        endpoint,
      });
      throw timeoutError;
    }

    if (error.message) throw error;

    const unexpectedError = new Error(String(error));
    unexpectedError.endpoint = endpoint;
    logError('Phase4 API Error', {
      message: unexpectedError.message,
      endpoint,
    });
    throw unexpectedError;
  }
}

/**
 * Agent Discovery Client
 * Provides methods for discovering and querying agents by phase, capability, or category
 */
const agentDiscoveryClient = {
  /**
   * List all available agents
   */
  listAgents: async () => {
    return makeRequest('/api/agents/list');
  },

  /**
   * Get complete agent registry with full metadata
   */
  getRegistry: async () => {
    return makeRequest('/api/agents/registry');
  },

  /**
   * Get specific agent metadata by name
   */
  getAgent: async (agentName) => {
    return makeRequest(`/api/agents/${agentName}`);
  },

  /**
   * Get all agents handling a specific phase
   * @param {string} phase - Phase name (e.g., 'draft', 'review', 'publish')
   */
  getAgentsByPhase: async (phase) => {
    return makeRequest(`/api/agents/by-phase/${phase}`);
  },

  /**
   * Get all agents with specific capability
   * @param {string} capability - Capability name (e.g., 'content_generation', 'cost_tracking')
   */
  getAgentsByCapability: async (capability) => {
    return makeRequest(`/api/agents/by-capability/${capability}`);
  },

  /**
   * Get all agents in a specific category
   * @param {string} category - Category name (e.g., 'content', 'financial')
   */
  getAgentsByCategory: async (category) => {
    return makeRequest(`/api/agents/by-category/${category}`);
  },

  /**
   * Search agents by query
   * @param {string} query - Search query
   */
  searchAgents: async (query) => {
    return makeRequest(`/api/agents/search?q=${encodeURIComponent(query)}`);
  },
};

/**
 * Service Registry Client
 * Provides methods for discovering and executing service actions
 */
const serviceRegistryClient = {
  /**
   * List all registered services with full metadata
   * Note: Currently uses agents registry since services are indexed as agents
   */
  listServices: async () => {
    return makeRequest('/api/agents/registry');
  },

  /**
   * Get specific service metadata and available actions
   * @param {string} serviceName - Service name (e.g., 'content_service')
   */
  getService: async (serviceName) => {
    return makeRequest(`/api/services/${serviceName}`);
  },

  /**
   * Get available actions for a specific service
   * @param {string} serviceName - Service name
   */
  getServiceActions: async (serviceName) => {
    return makeRequest(`/api/services/${serviceName}/actions`);
  },

  /**
   * Execute a service action
   * @param {string} serviceName - Service name
   * @param {string} actionName - Action name
   * @param {object} params - Action parameters
   */
  executeServiceAction: async (serviceName, actionName, params = {}) => {
    return makeRequest(`/api/services/${serviceName}/actions/${actionName}`, {
      method: 'POST',
      body: params,
    });
  },
};

/**
 * Workflow Client
 * Provides methods for managing and executing workflows
 */
const workflowClient = {
  /**
   * Get available workflow templates
   */
  getTemplates: async () => {
    return makeRequest('/api/workflows/templates', {
      method: 'POST',
    });
  },

  /**
   * Execute a workflow from template
   * @param {string} templateId - Template ID
   * @param {object} params - Workflow parameters
   */
  executeWorkflow: async (templateId, params = {}) => {
    return makeRequest(`/api/workflows/execute/${templateId}`, {
      method: 'POST',
      body: params,
    });
  },

  /**
   * Get workflow execution status
   * @param {string} executionId - Execution ID
   */
  getWorkflowStatus: async (executionId) => {
    return makeRequest(`/api/workflows/status/${executionId}`);
  },

  /**
   * Get workflow execution history
   * @param {string} templateId - Template ID
   * @param {number} limit - Max results to return
   */
  getWorkflowHistory: async (templateId, limit = 50) => {
    return makeRequest(`/api/workflows/${templateId}/history?limit=${limit}`);
  },

  /**
   * Cancel a running workflow
   * @param {string} executionId - Execution ID
   */
  cancelWorkflow: async (executionId) => {
    return makeRequest(`/api/workflows/cancel/${executionId}`, {
      method: 'POST',
    });
  },
};

/**
 * Task Client
 * Provides methods for task management
 */
const taskClient = {
  /**
   * Create a new task
   * @param {object} taskData - Task configuration
   */
  createTask: async (taskData) => {
    return makeRequest('/api/tasks', {
      method: 'POST',
      body: taskData,
    });
  },

  /**
   * List tasks with optional filtering
   * @param {object} filters - Filter options (phase, status, agent, etc.)
   * @param {number} limit - Max results
   */
  listTasks: async (filters = {}, limit = 100) => {
    const queryString = new URLSearchParams({
      limit,
      ...filters,
    }).toString();
    return makeRequest(`/api/tasks?${queryString}`);
  },

  /**
   * Get specific task by ID
   * @param {string} taskId - Task ID
   */
  getTask: async (taskId) => {
    return makeRequest(`/api/tasks/${taskId}`);
  },

  /**
   * Update task
   * @param {string} taskId - Task ID
   * @param {object} updates - Fields to update
   */
  updateTask: async (taskId, updates) => {
    return makeRequest(`/api/tasks/${taskId}`, {
      method: 'PUT',
      body: updates,
    });
  },

  /**
   * Execute task (trigger execution)
   * @param {string} taskId - Task ID
   */
  executeTask: async (taskId) => {
    return makeRequest(`/api/tasks/${taskId}/execute`, {
      method: 'POST',
    });
  },

  /**
   * Get task execution status
   * @param {string} taskId - Task ID
   */
  getTaskStatus: async (taskId) => {
    return makeRequest(`/api/tasks/${taskId}/status`);
  },

  /**
   * Approve task result
   * @param {string} taskId - Task ID
   * @param {object} approval - Approval details
   */
  approveTask: async (taskId, approval = {}) => {
    return makeRequest(`/api/tasks/${taskId}/approve`, {
      method: 'POST',
      body: approval,
    });
  },

  /**
   * Reject task result
   * @param {string} taskId - Task ID
   * @param {object} rejection - Rejection details
   */
  rejectTask: async (taskId, rejection = {}) => {
    return makeRequest(`/api/tasks/${taskId}/reject`, {
      method: 'POST',
      body: rejection,
    });
  },
};

/**
 * Unified Services Convenience Client
 * Provides shortcuts for common operations across services
 */
const unifiedServicesClient = {
  /**
   * Content Service shortcuts
   */
  content: {
    getService: () => serviceRegistryClient.getService('content_service'),
    listActions: () =>
      serviceRegistryClient.getServiceActions('content_service'),
    generate: (params) =>
      serviceRegistryClient.executeServiceAction(
        'content_service',
        'generate',
        params
      ),
    critique: (params) =>
      serviceRegistryClient.executeServiceAction(
        'content_service',
        'critique',
        params
      ),
    refine: (params) =>
      serviceRegistryClient.executeServiceAction(
        'content_service',
        'refine',
        params
      ),
  },

  /**
   * Financial Service shortcuts
   */
  financial: {
    getService: () => serviceRegistryClient.getService('financial_service'),
    listActions: () =>
      serviceRegistryClient.getServiceActions('financial_service'),
    trackCosts: (params) =>
      serviceRegistryClient.executeServiceAction(
        'financial_service',
        'track_costs',
        params
      ),
    optimizeBudget: (params) =>
      serviceRegistryClient.executeServiceAction(
        'financial_service',
        'optimize_budget',
        params
      ),
    analyzeCosts: (params) =>
      serviceRegistryClient.executeServiceAction(
        'financial_service',
        'analyze_costs',
        params
      ),
  },

  /**
   * Market Service shortcuts
   */
  market: {
    getService: () => serviceRegistryClient.getService('market_service'),
    listActions: () =>
      serviceRegistryClient.getServiceActions('market_service'),
    analyzeTrends: (params) =>
      serviceRegistryClient.executeServiceAction(
        'market_service',
        'analyze_trends',
        params
      ),
    identifyOpportunities: (params) =>
      serviceRegistryClient.executeServiceAction(
        'market_service',
        'identify_opportunities',
        params
      ),
    competitive: (params) =>
      serviceRegistryClient.executeServiceAction(
        'market_service',
        'competitive_analysis',
        params
      ),
  },

  /**
   * Compliance Service shortcuts
   */
  compliance: {
    getService: () => serviceRegistryClient.getService('compliance_service'),
    listActions: () =>
      serviceRegistryClient.getServiceActions('compliance_service'),
    review: (params) =>
      serviceRegistryClient.executeServiceAction(
        'compliance_service',
        'review',
        params
      ),
    audit: (params) =>
      serviceRegistryClient.executeServiceAction(
        'compliance_service',
        'audit',
        params
      ),
    riskAssess: (params) =>
      serviceRegistryClient.executeServiceAction(
        'compliance_service',
        'risk_assessment',
        params
      ),
  },
};

/**
 * Health Check
 * Verify Phase 4 endpoints are accessible
 */
const healthCheck = async () => {
  try {
    const response = await makeRequest('/api/agents/list');
    return {
      healthy: Array.isArray(response),
      timestamp: new Date().toISOString(),
    };
  } catch (error) {
    return {
      healthy: false,
      error: error.message,
      timestamp: new Date().toISOString(),
    };
  }
};

// Export all clients
const phase4Client = {
  agentDiscoveryClient,
  serviceRegistryClient,
  workflowClient,
  taskClient,
  unifiedServicesClient,
  healthCheck,
};

export default phase4Client;

// Named exports for convenience
export {
  agentDiscoveryClient,
  serviceRegistryClient,
  workflowClient,
  taskClient,
  unifiedServicesClient,
  healthCheck,
};
