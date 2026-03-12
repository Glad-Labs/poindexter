/**
 * workflowBuilderService.test.js
 *
 * Unit tests for services/workflowBuilderService.js.
 *
 * Tests cover:
 * - getAvailablePhases — success, missing phases warning but still returns, network error
 * - createWorkflow — success, missing name throws, empty phases throws, payload shape, network error
 * - listWorkflows — success, default params, custom skip/limit, custom page/page_size, network error
 * - getWorkflow — success, missing workflowId throws, network error
 * - updateWorkflow — success, missing workflowId throws, empty name throws, empty phases throws, removes undefined fields
 * - deleteWorkflow — success, missing workflowId throws, network error
 * - executeWorkflow — success with input data, default empty input data, missing workflowId throws
 * - getExecutionStatus — success, missing executionId throws, 404 not re-logged (still throws)
 * - getWorkflowExecutions — success, missing workflowId throws, options (limit/offset/status)
 * - exportWorkflowToJSON — returns pretty JSON string
 * - importWorkflowFromJSON — valid JSON returns object, invalid structure throws, invalid JSON throws
 *
 * makeRequest is mocked; no network calls.
 */

import { vi } from 'vitest';

const { mockMakeRequest } = vi.hoisted(() => ({
  mockMakeRequest: vi.fn(),
}));

vi.mock('@/services/cofounderAgentClient', () => ({
  makeRequest: mockMakeRequest,
}));

vi.mock('@/lib/logger', () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

import {
  getAvailablePhases,
  createWorkflow,
  listWorkflows,
  getWorkflow,
  updateWorkflow,
  deleteWorkflow,
  executeWorkflow,
  getExecutionStatus,
  getWorkflowExecutions,
  exportWorkflowToJSON,
  importWorkflowFromJSON,
} from '../workflowBuilderService';

const _ok = (data) => mockMakeRequest.mockResolvedValue(data);
const _throw = (msg) => mockMakeRequest.mockRejectedValue(new Error(msg));

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// getAvailablePhases
// ---------------------------------------------------------------------------

describe('getAvailablePhases', () => {
  it('returns response on success', async () => {
    _ok({ phases: ['research', 'creative', 'qa'] });
    const result = await getAvailablePhases();
    expect(result.phases).toHaveLength(3);
  });

  it('returns response even when phases is missing', async () => {
    _ok({});
    const result = await getAvailablePhases();
    expect(result).toBeDefined();
  });

  it('throws wrapped error on network failure', async () => {
    _throw('Connection refused');
    await expect(getAvailablePhases()).rejects.toThrow(
      'Failed to load available phases: Connection refused'
    );
  });

  it('calls GET /api/workflows/available-phases', async () => {
    _ok({ phases: [] });
    await getAvailablePhases();
    expect(mockMakeRequest.mock.calls[0][0]).toBe(
      '/api/workflows/available-phases'
    );
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// createWorkflow
// ---------------------------------------------------------------------------

describe('createWorkflow', () => {
  it('returns created workflow on success', async () => {
    _ok({ id: 'wf-1', name: 'My Workflow' });
    const result = await createWorkflow({
      name: 'My Workflow',
      phases: [{ name: 'research' }],
    });
    expect(result.id).toBe('wf-1');
  });

  it('throws when name is missing', async () => {
    await expect(createWorkflow({ name: '', phases: [{}] })).rejects.toThrow(
      'Workflow name is required'
    );
  });

  it('throws when name is whitespace-only', async () => {
    await expect(createWorkflow({ name: '   ', phases: [{}] })).rejects.toThrow(
      'Workflow name is required'
    );
  });

  it('throws when phases is empty', async () => {
    await expect(createWorkflow({ name: 'Valid', phases: [] })).rejects.toThrow(
      'At least one phase is required'
    );
  });

  it('sends correct payload shape', async () => {
    _ok({ id: 'wf-new' });
    await createWorkflow({
      name: '  Blog Factory  ',
      description: 'Produces blog posts',
      phases: [{ name: 'research' }],
      tags: ['content'],
      is_template: true,
    });
    const payload = mockMakeRequest.mock.calls[0][2];
    expect(payload.name).toBe('Blog Factory'); // trimmed
    expect(payload.description).toBe('Produces blog posts');
    expect(payload.is_template).toBe(true);
    expect(payload.tags).toEqual(['content']);
  });

  it('uses defaults for tags and is_template', async () => {
    _ok({ id: 'wf-2' });
    await createWorkflow({ name: 'Simple', phases: [{ name: 'research' }] });
    const payload = mockMakeRequest.mock.calls[0][2];
    expect(payload.tags).toEqual([]);
    expect(payload.is_template).toBe(false);
    expect(payload.description).toBe('');
  });

  it('throws wrapped error on network failure', async () => {
    _throw('Server error');
    await expect(
      createWorkflow({ name: 'Valid', phases: [{}] })
    ).rejects.toThrow('Failed to create workflow: Server error');
  });

  it('calls POST /api/workflows/custom', async () => {
    _ok({ id: 'x' });
    await createWorkflow({ name: 'WF', phases: [{}] });
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/workflows/custom');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// listWorkflows
// ---------------------------------------------------------------------------

describe('listWorkflows', () => {
  it('returns workflow list on success', async () => {
    _ok({ workflows: [{ id: 'w1' }], total: 1 });
    const result = await listWorkflows();
    expect(result.total).toBe(1);
  });

  it('uses defaults for page=1, page_size=50', async () => {
    _ok({});
    await listWorkflows();
    const url = mockMakeRequest.mock.calls[0][0];
    expect(url).toContain('page=1');
    expect(url).toContain('page_size=50');
  });

  it('derives page from skip/limit when page not provided', async () => {
    _ok({});
    await listWorkflows({ skip: 50, limit: 25 });
    const url = mockMakeRequest.mock.calls[0][0];
    expect(url).toContain('page=3'); // 50 / 25 + 1 = 3
    expect(url).toContain('page_size=25');
  });

  it('uses explicit page/page_size when provided', async () => {
    _ok({});
    await listWorkflows({ page: 2, page_size: 10 });
    const url = mockMakeRequest.mock.calls[0][0];
    expect(url).toContain('page=2');
    expect(url).toContain('page_size=10');
  });

  it('throws wrapped error on network failure', async () => {
    _throw('Timeout');
    await expect(listWorkflows()).rejects.toThrow(
      'Failed to load workflows: Timeout'
    );
  });

  it('calls GET /api/workflows/custom', async () => {
    _ok({});
    await listWorkflows();
    expect(mockMakeRequest.mock.calls[0][0]).toContain('/api/workflows/custom');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// getWorkflow
// ---------------------------------------------------------------------------

describe('getWorkflow', () => {
  it('returns workflow details on success', async () => {
    _ok({ id: 'wf-42', name: 'Blog Pipeline' });
    const result = await getWorkflow('wf-42');
    expect(result.name).toBe('Blog Pipeline');
  });

  it('throws when workflowId is missing', async () => {
    await expect(getWorkflow('')).rejects.toThrow('Workflow ID is required');
    await expect(getWorkflow(null)).rejects.toThrow('Workflow ID is required');
  });

  it('throws wrapped error on network failure', async () => {
    _throw('Not found');
    await expect(getWorkflow('wf-1')).rejects.toThrow(
      'Failed to load workflow: Not found'
    );
  });

  it('calls GET /api/workflows/custom/:id', async () => {
    _ok({});
    await getWorkflow('wf-99');
    expect(mockMakeRequest.mock.calls[0][0]).toBe(
      '/api/workflows/custom/wf-99'
    );
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// updateWorkflow
// ---------------------------------------------------------------------------

describe('updateWorkflow', () => {
  it('returns updated workflow on success', async () => {
    _ok({ id: 'wf-1', name: 'Updated' });
    const result = await updateWorkflow('wf-1', {
      name: 'Updated',
      phases: [{ name: 'research' }],
    });
    expect(result.name).toBe('Updated');
  });

  it('throws when workflowId is missing', async () => {
    await expect(
      updateWorkflow('', { name: 'x', phases: [{}] })
    ).rejects.toThrow('Workflow ID is required');
  });

  it('throws when name is provided but empty', async () => {
    await expect(
      updateWorkflow('wf-1', { name: '  ', phases: [{}] })
    ).rejects.toThrow('Workflow name cannot be empty');
  });

  it('throws when phases is provided but empty', async () => {
    await expect(
      updateWorkflow('wf-1', { name: 'Valid', phases: [] })
    ).rejects.toThrow('At least one phase is required');
  });

  it('removes undefined fields from payload', async () => {
    _ok({ id: 'wf-1' });
    await updateWorkflow('wf-1', { name: 'Updated' }); // no description/phases/tags
    const payload = mockMakeRequest.mock.calls[0][2];
    expect(Object.keys(payload)).not.toContain('phases');
    expect(Object.keys(payload)).not.toContain('tags');
  });

  it('trims name in payload', async () => {
    _ok({ id: 'wf-1' });
    await updateWorkflow('wf-1', {
      name: '  Trimmed Name  ',
      phases: [{ name: 'research' }],
    });
    expect(mockMakeRequest.mock.calls[0][2].name).toBe('Trimmed Name');
  });

  it('calls PUT /api/workflows/custom/:id', async () => {
    _ok({});
    await updateWorkflow('wf-5', { name: 'New Name', phases: [{}] });
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/workflows/custom/wf-5');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('PUT');
  });
});

// ---------------------------------------------------------------------------
// deleteWorkflow
// ---------------------------------------------------------------------------

describe('deleteWorkflow', () => {
  it('returns deletion result on success', async () => {
    _ok({ deleted: true });
    const result = await deleteWorkflow('wf-1');
    expect(result.deleted).toBe(true);
  });

  it('throws when workflowId is missing', async () => {
    await expect(deleteWorkflow('')).rejects.toThrow('Workflow ID is required');
  });

  it('throws wrapped error on network failure', async () => {
    _throw('Permission denied');
    await expect(deleteWorkflow('wf-1')).rejects.toThrow(
      'Failed to delete workflow: Permission denied'
    );
  });

  it('calls DELETE /api/workflows/custom/:id', async () => {
    _ok({});
    await deleteWorkflow('wf-77');
    expect(mockMakeRequest.mock.calls[0][0]).toBe(
      '/api/workflows/custom/wf-77'
    );
    expect(mockMakeRequest.mock.calls[0][1]).toBe('DELETE');
  });
});

// ---------------------------------------------------------------------------
// executeWorkflow
// ---------------------------------------------------------------------------

describe('executeWorkflow', () => {
  it('returns execution result on success', async () => {
    _ok({ execution_id: 'exec-1', status: 'RUNNING' });
    const result = await executeWorkflow('wf-1');
    expect(result.execution_id).toBe('exec-1');
  });

  it('sends input data wrapped in input_data key', async () => {
    _ok({ execution_id: 'exec-2' });
    const input = { topic: 'AI future' };
    await executeWorkflow('wf-1', input);
    expect(mockMakeRequest.mock.calls[0][2]).toEqual({ input_data: input });
  });

  it('sends empty input_data by default', async () => {
    _ok({ execution_id: 'exec-3' });
    await executeWorkflow('wf-1');
    expect(mockMakeRequest.mock.calls[0][2]).toEqual({ input_data: {} });
  });

  it('throws when workflowId is missing', async () => {
    await expect(executeWorkflow('')).rejects.toThrow(
      'Workflow ID is required'
    );
  });

  it('calls POST /api/workflows/custom/:id/execute', async () => {
    _ok({ execution_id: 'x' });
    await executeWorkflow('wf-9');
    expect(mockMakeRequest.mock.calls[0][0]).toBe(
      '/api/workflows/custom/wf-9/execute'
    );
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// getExecutionStatus
// ---------------------------------------------------------------------------

describe('getExecutionStatus', () => {
  it('returns status on success', async () => {
    _ok({ execution_id: 'exec-5', status: 'COMPLETED' });
    const result = await getExecutionStatus('exec-5');
    expect(result.status).toBe('COMPLETED');
  });

  it('throws when executionId is missing', async () => {
    await expect(getExecutionStatus('')).rejects.toThrow(
      'Execution ID is required'
    );
  });

  it('throws wrapped error on network failure (non-404)', async () => {
    _throw('Server error');
    await expect(getExecutionStatus('exec-1')).rejects.toThrow(
      'Failed to load execution status: Server error'
    );
  });

  it('still throws on 404-like errors', async () => {
    _throw('not found');
    await expect(getExecutionStatus('exec-1')).rejects.toThrow(
      'Failed to load execution status: not found'
    );
  });

  it('calls GET /api/workflows/executions/:id', async () => {
    _ok({ status: 'RUNNING' });
    await getExecutionStatus('exec-42');
    expect(mockMakeRequest.mock.calls[0][0]).toBe(
      '/api/workflows/executions/exec-42'
    );
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// getWorkflowExecutions
// ---------------------------------------------------------------------------

describe('getWorkflowExecutions', () => {
  it('returns executions on success', async () => {
    _ok({ executions: [{ id: 'e1' }], total: 1 });
    const result = await getWorkflowExecutions('wf-1');
    expect(result.total).toBe(1);
  });

  it('throws when workflowId is missing', async () => {
    await expect(getWorkflowExecutions('')).rejects.toThrow(
      'Workflow ID is required'
    );
  });

  it('uses default limit=10 and offset=0', async () => {
    _ok({});
    await getWorkflowExecutions('wf-1');
    const url = mockMakeRequest.mock.calls[0][0];
    expect(url).toContain('limit=10');
    expect(url).toContain('offset=0');
  });

  it('uses custom limit and offset', async () => {
    _ok({});
    await getWorkflowExecutions('wf-1', { limit: 25, offset: 50 });
    const url = mockMakeRequest.mock.calls[0][0];
    expect(url).toContain('limit=25');
    expect(url).toContain('offset=50');
  });

  it('includes status filter when provided', async () => {
    _ok({});
    await getWorkflowExecutions('wf-1', { status: 'COMPLETED' });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('status=COMPLETED');
  });

  it('calls GET /api/workflows/custom/:id/executions', async () => {
    _ok({});
    await getWorkflowExecutions('wf-7');
    expect(mockMakeRequest.mock.calls[0][0]).toContain(
      '/api/workflows/custom/wf-7/executions'
    );
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// exportWorkflowToJSON
// ---------------------------------------------------------------------------

describe('exportWorkflowToJSON', () => {
  it('returns pretty-printed JSON string', () => {
    const workflow = { name: 'My Workflow', phases: [{ name: 'research' }] };
    const result = exportWorkflowToJSON(workflow);
    expect(typeof result).toBe('string');
    expect(JSON.parse(result)).toEqual(workflow);
    // Check it is pretty-printed (contains newlines and spaces)
    expect(result).toContain('\n');
  });
});

// ---------------------------------------------------------------------------
// importWorkflowFromJSON
// ---------------------------------------------------------------------------

describe('importWorkflowFromJSON', () => {
  it('returns parsed workflow object for valid JSON', () => {
    const workflow = { name: 'Imported', phases: [{ name: 'research' }] };
    const result = importWorkflowFromJSON(JSON.stringify(workflow));
    expect(result).toEqual(workflow);
  });

  it('throws when name is missing from parsed workflow', () => {
    const invalid = JSON.stringify({ phases: [{ name: 'research' }] });
    expect(() => importWorkflowFromJSON(invalid)).toThrow(
      'Failed to import workflow'
    );
  });

  it('throws when phases is missing from parsed workflow', () => {
    const invalid = JSON.stringify({ name: 'No Phases' });
    expect(() => importWorkflowFromJSON(invalid)).toThrow(
      'Failed to import workflow'
    );
  });

  it('throws when phases is not an array', () => {
    const invalid = JSON.stringify({ name: 'Bad', phases: 'not-an-array' });
    expect(() => importWorkflowFromJSON(invalid)).toThrow(
      'Failed to import workflow'
    );
  });

  it('throws on invalid JSON string', () => {
    expect(() => importWorkflowFromJSON('not-valid-json')).toThrow(
      'Failed to import workflow'
    );
  });
});
