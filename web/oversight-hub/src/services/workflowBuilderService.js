/**
 * Workflow Builder Service
 *
 * Frontend API client for custom workflow management and execution.
 * Provides methods for CRUD operations on custom workflows and workflow templates.
 *
 * Endpoints:
 * - GET /api/workflows/available-phases - Get available phases for building
 * - POST /api/workflows/custom - Create custom workflow
 * - GET /api/workflows/custom - List user's custom workflows (paginated)
 * - GET /api/workflows/custom/{id} - Get workflow details
 * - PUT /api/workflows/custom/{id} - Update workflow
 * - DELETE /api/workflows/custom/{id} - Delete workflow
 * - POST /api/workflows/custom/{id}/execute - Execute workflow
 */

import { makeRequest } from './cofounderAgentClient';

/**
 * Get available phases for workflow building
 * @returns {Promise<Object>} Response with phases array
 */
export const getAvailablePhases = async () => {
  try {
    const response = await makeRequest(
      '/api/workflows/available-phases',
      'GET'
    );
    return response;
  } catch (error) {
    console.error('Error fetching available phases:', error);
    throw new Error(`Failed to load available phases: ${error.message}`);
  }
};

/**
 * Create a new custom workflow
 * @param {Object} workflowDefinition - Workflow definition with name, description, phases
 * @returns {Promise<Object>} Created workflow object with ID
 */
export const createWorkflow = async (workflowDefinition) => {
  try {
    // Validate required fields
    if (!workflowDefinition.name || !workflowDefinition.name.trim()) {
      throw new Error('Workflow name is required');
    }

    if (!workflowDefinition.phases || workflowDefinition.phases.length === 0) {
      throw new Error('At least one phase is required');
    }

    const payload = {
      name: workflowDefinition.name.trim(),
      description: workflowDefinition.description || '',
      phases: workflowDefinition.phases,
      tags: workflowDefinition.tags || [],
      is_template: workflowDefinition.is_template || false,
    };

    const response = await makeRequest(
      '/api/workflows/custom',
      'POST',
      payload
    );
    return response;
  } catch (error) {
    console.error('Error creating workflow:', error);
    throw new Error(`Failed to create workflow: ${error.message}`);
  }
};

/**
 * List user's custom workflows
 * @param {Object} options - Query options
 * @param {number} options.skip - Number of results to skip (default: 0)
 * @param {number} options.limit - Number of results to return (default: 50)
 * @param {boolean} options.include_templates - Include templates (default: false)
 * @returns {Promise<Object>} Response with workflows array and total count
 */
export const listWorkflows = async (options = {}) => {
  try {
    const {
      skip = 0,
      limit = 50,
      page,
      page_size,
      include_templates = false,
    } = options;

    const resolvedPageSize =
      Number(page_size) > 0 ? Number(page_size) : Number(limit) || 50;
    const resolvedPage =
      Number(page) > 0
        ? Number(page)
        : Math.floor(Number(skip) / resolvedPageSize) + 1;

    const queryParams = new URLSearchParams({
      page: String(resolvedPage),
      page_size: String(resolvedPageSize),
      include_templates,
    });

    const response = await makeRequest(
      `/api/workflows/custom?${queryParams.toString()}`,
      'GET'
    );
    return response;
  } catch (error) {
    console.error('Error listing workflows:', error);
    throw new Error(`Failed to load workflows: ${error.message}`);
  }
};

/**
 * Get workflow details
 * @param {string} workflowId - Workflow ID
 * @returns {Promise<Object>} Workflow object with all details
 */
export const getWorkflow = async (workflowId) => {
  try {
    if (!workflowId) {
      throw new Error('Workflow ID is required');
    }

    const response = await makeRequest(
      `/api/workflows/custom/${workflowId}`,
      'GET'
    );
    return response;
  } catch (error) {
    console.error('Error fetching workflow:', error);
    throw new Error(`Failed to load workflow: ${error.message}`);
  }
};

/**
 * Update an existing workflow
 * @param {string} workflowId - Workflow ID to update
 * @param {Object} updates - Updated workflow data
 * @returns {Promise<Object>} Updated workflow object
 */
export const updateWorkflow = async (workflowId, updates) => {
  try {
    if (!workflowId) {
      throw new Error('Workflow ID is required');
    }

    if (updates.name !== undefined && !updates.name.trim()) {
      throw new Error('Workflow name cannot be empty');
    }

    if (updates.phases !== undefined && updates.phases.length === 0) {
      throw new Error('At least one phase is required');
    }

    const payload = {
      name: updates.name ? updates.name.trim() : undefined,
      description:
        updates.description !== undefined ? updates.description : undefined,
      phases: updates.phases,
      tags: updates.tags,
      is_template: updates.is_template,
    };

    // Remove undefined fields
    Object.keys(payload).forEach(
      (key) => payload[key] === undefined && delete payload[key]
    );

    const response = await makeRequest(
      `/api/workflows/custom/${workflowId}`,
      'PUT',
      payload
    );
    return response;
  } catch (error) {
    console.error('Error updating workflow:', error);
    throw new Error(`Failed to update workflow: ${error.message}`);
  }
};

/**
 * Delete a workflow
 * @param {string} workflowId - Workflow ID to delete
 * @returns {Promise<Object>} Deletion response
 */
export const deleteWorkflow = async (workflowId) => {
  try {
    if (!workflowId) {
      throw new Error('Workflow ID is required');
    }

    const response = await makeRequest(
      `/api/workflows/custom/${workflowId}`,
      'DELETE'
    );
    return response;
  } catch (error) {
    console.error('Error deleting workflow:', error);
    throw new Error(`Failed to delete workflow: ${error.message}`);
  }
};

/**
 * Execute a workflow
 * @param {string} workflowId - Workflow ID to execute
 * @param {Object} inputData - Input data for workflow execution
 * @returns {Promise<Object>} Execution response with execution ID
 */
export const executeWorkflow = async (workflowId, inputData = {}) => {
  try {
    if (!workflowId) {
      throw new Error('Workflow ID is required');
    }

    const payload = {
      input_data: inputData,
    };

    const response = await makeRequest(
      `/api/workflows/custom/${workflowId}/execute`,
      'POST',
      payload
    );
    return response;
  } catch (error) {
    console.error('Error executing workflow:', error);
    throw new Error(`Failed to execute workflow: ${error.message}`);
  }
};

/**
 * Get workflow execution status
 * @param {string} executionId - Execution ID
 * @returns {Promise<Object>} Execution status and results
 */
export const getExecutionStatus = async (executionId) => {
  try {
    if (!executionId) {
      throw new Error('Execution ID is required');
    }

    // This endpoint may need to be added to the backend
    const response = await makeRequest(
      `/api/workflows/executions/${executionId}`,
      'GET'
    );
    return response;
  } catch (error) {
    console.error('Error fetching execution status:', error);
    throw new Error(`Failed to load execution status: ${error.message}`);
  }
};

/**
 * Export workflow to JSON
 * @param {Object} workflow - Workflow object
 * @returns {string} JSON string representation
 */
export const exportWorkflowToJSON = (workflow) => {
  try {
    return JSON.stringify(workflow, null, 2);
  } catch (error) {
    console.error('Error exporting workflow:', error);
    throw new Error('Failed to export workflow');
  }
};

/**
 * Import workflow from JSON
 * @param {string} jsonString - JSON string representation of workflow
 * @returns {Object} Workflow object
 */
export const importWorkflowFromJSON = (jsonString) => {
  try {
    const workflow = JSON.parse(jsonString);

    // Validate structure
    if (!workflow.name || !workflow.phases || !Array.isArray(workflow.phases)) {
      throw new Error('Invalid workflow structure');
    }

    return workflow;
  } catch (error) {
    console.error('Error importing workflow:', error);
    throw new Error(`Failed to import workflow: ${error.message}`);
  }
};

export default {
  getAvailablePhases,
  createWorkflow,
  listWorkflows,
  getWorkflow,
  updateWorkflow,
  deleteWorkflow,
  executeWorkflow,
  getExecutionStatus,
  exportWorkflowToJSON,
  importWorkflowFromJSON,
};
