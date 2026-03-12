/**
 * capabilityTasksService.test.js
 *
 * Unit tests for services/capabilityTasksService.js (CapabilityTasksService class).
 *
 * Tests cover:
 * - listCapabilities — success, optional tag/costTier filters, no filters, not-ok throws
 * - getCapability — success, not-ok throws
 * - createTask — success, sends correct body, not-ok throws
 * - getTask — success, not-ok throws
 * - listTasks — success, skip/limit params, not-ok throws
 * - updateTask — success, sends correct body, not-ok throws
 * - deleteTask — success (no return value), not-ok throws
 * - executeTask — success, not-ok throws
 * - getExecution — success, not-ok throws
 * - listExecutions — success, skip/limit/status params, not-ok throws
 *
 * global.fetch is mocked; no network calls.
 */

import { vi } from 'vitest';

vi.mock('../config/apiConfig', () => ({
  getApiUrl: () => 'http://localhost:8000',
}));

import CapabilityTasksService from '../capabilityTasksService';

const _mockFetch = ({ ok = true, json = {}, statusText = 'OK' } = {}) => {
  global.fetch = vi.fn().mockResolvedValue({
    ok,
    statusText,
    json: async () => json,
  });
};

const _mockFetchFail = (statusText = 'Not Found') => {
  _mockFetch({ ok: false, statusText });
};

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// listCapabilities
// ---------------------------------------------------------------------------

describe('listCapabilities', () => {
  it('returns capabilities on success', async () => {
    _mockFetch({ json: { capabilities: ['blog_writer', 'seo_optimizer'] } });
    const result = await CapabilityTasksService.listCapabilities();
    expect(result.capabilities).toHaveLength(2);
  });

  it('includes tag filter in URL when provided', async () => {
    _mockFetch({ json: {} });
    await CapabilityTasksService.listCapabilities('content');
    expect(global.fetch.mock.calls[0][0]).toContain('tag=content');
  });

  it('includes cost_tier filter in URL when provided', async () => {
    _mockFetch({ json: {} });
    await CapabilityTasksService.listCapabilities(null, 'cheap');
    expect(global.fetch.mock.calls[0][0]).toContain('cost_tier=cheap');
  });

  it('omits query string when no filters provided', async () => {
    _mockFetch({ json: {} });
    await CapabilityTasksService.listCapabilities();
    expect(global.fetch.mock.calls[0][0]).toContain('/api/capabilities');
    expect(global.fetch.mock.calls[0][0]).not.toContain('?');
  });

  it('throws when response is not ok', async () => {
    _mockFetchFail('Service Unavailable');
    await expect(CapabilityTasksService.listCapabilities()).rejects.toThrow(
      'Failed to list capabilities: Service Unavailable'
    );
  });

  it('calls GET /api/capabilities', async () => {
    _mockFetch({ json: {} });
    await CapabilityTasksService.listCapabilities();
    expect(global.fetch.mock.calls[0][0]).toContain('/api/capabilities');
  });
});

// ---------------------------------------------------------------------------
// getCapability
// ---------------------------------------------------------------------------

describe('getCapability', () => {
  it('returns capability details on success', async () => {
    _mockFetch({ json: { name: 'blog_writer', schema: {} } });
    const result = await CapabilityTasksService.getCapability('blog_writer');
    expect(result.name).toBe('blog_writer');
  });

  it('throws when response is not ok', async () => {
    _mockFetchFail('Not Found');
    await expect(
      CapabilityTasksService.getCapability('nonexistent')
    ).rejects.toThrow('Failed to get capability: Not Found');
  });

  it('calls GET /api/capabilities/:name', async () => {
    _mockFetch({ json: {} });
    await CapabilityTasksService.getCapability('seo_optimizer');
    expect(global.fetch.mock.calls[0][0]).toContain(
      '/api/capabilities/seo_optimizer'
    );
  });
});

// ---------------------------------------------------------------------------
// createTask
// ---------------------------------------------------------------------------

describe('createTask', () => {
  it('returns created task on success', async () => {
    _mockFetch({ json: { id: 'task-new', name: 'My Task' } });
    const result = await CapabilityTasksService.createTask(
      'My Task',
      'A description',
      []
    );
    expect(result.id).toBe('task-new');
  });

  it('sends name, description, steps, and tags in request body', async () => {
    _mockFetch({ json: { id: 'task-1' } });
    const steps = [{ capability: 'blog_writer', params: {} }];
    await CapabilityTasksService.createTask('Title', 'Desc', steps, [
      'content',
    ]);
    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.name).toBe('Title');
    expect(body.description).toBe('Desc');
    expect(body.steps).toEqual(steps);
    expect(body.tags).toEqual(['content']);
  });

  it('uses empty array as default tags', async () => {
    _mockFetch({ json: { id: 'task-2' } });
    await CapabilityTasksService.createTask('Title', 'Desc', []);
    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.tags).toEqual([]);
  });

  it('throws when response is not ok', async () => {
    _mockFetchFail('Bad Request');
    await expect(
      CapabilityTasksService.createTask('T', 'D', [])
    ).rejects.toThrow('Failed to create task: Bad Request');
  });

  it('calls POST /api/tasks/capability', async () => {
    _mockFetch({ json: { id: 'x' } });
    await CapabilityTasksService.createTask('T', 'D', []);
    expect(global.fetch.mock.calls[0][0]).toContain('/api/tasks/capability');
    expect(global.fetch.mock.calls[0][1].method).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// getTask
// ---------------------------------------------------------------------------

describe('getTask', () => {
  it('returns task on success', async () => {
    _mockFetch({ json: { id: 'task-5', status: 'pending' } });
    const result = await CapabilityTasksService.getTask('task-5');
    expect(result.id).toBe('task-5');
  });

  it('throws when response is not ok', async () => {
    _mockFetchFail('Not Found');
    await expect(CapabilityTasksService.getTask('bad-id')).rejects.toThrow(
      'Failed to get task: Not Found'
    );
  });

  it('calls GET /api/tasks/capability/:id', async () => {
    _mockFetch({ json: {} });
    await CapabilityTasksService.getTask('task-42');
    expect(global.fetch.mock.calls[0][0]).toContain(
      '/api/tasks/capability/task-42'
    );
  });
});

// ---------------------------------------------------------------------------
// listTasks
// ---------------------------------------------------------------------------

describe('listTasks', () => {
  it('returns tasks on success', async () => {
    _mockFetch({ json: [{ id: 't1' }, { id: 't2' }] });
    const result = await CapabilityTasksService.listTasks();
    expect(result).toHaveLength(2);
  });

  it('includes skip and limit in URL', async () => {
    _mockFetch({ json: [] });
    await CapabilityTasksService.listTasks(10, 25);
    expect(global.fetch.mock.calls[0][0]).toContain('skip=10');
    expect(global.fetch.mock.calls[0][0]).toContain('limit=25');
  });

  it('throws when response is not ok', async () => {
    _mockFetchFail('Forbidden');
    await expect(CapabilityTasksService.listTasks()).rejects.toThrow(
      'Failed to list tasks: Forbidden'
    );
  });

  it('calls GET /api/tasks/capability', async () => {
    _mockFetch({ json: [] });
    await CapabilityTasksService.listTasks();
    expect(global.fetch.mock.calls[0][0]).toContain('/api/tasks/capability');
  });
});

// ---------------------------------------------------------------------------
// updateTask
// ---------------------------------------------------------------------------

describe('updateTask', () => {
  it('returns updated task on success', async () => {
    _mockFetch({ json: { id: 'task-1', name: 'Updated Title' } });
    const result = await CapabilityTasksService.updateTask(
      'task-1',
      'Updated Title',
      'New desc',
      []
    );
    expect(result.name).toBe('Updated Title');
  });

  it('sends name, description, and steps in request body', async () => {
    _mockFetch({ json: { id: 'task-1' } });
    const steps = [{ capability: 'seo_optimizer' }];
    await CapabilityTasksService.updateTask(
      'task-1',
      'New Name',
      'New Desc',
      steps
    );
    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.name).toBe('New Name');
    expect(body.description).toBe('New Desc');
    expect(body.steps).toEqual(steps);
  });

  it('throws when response is not ok', async () => {
    _mockFetchFail('Conflict');
    await expect(
      CapabilityTasksService.updateTask('task-1', 'T', 'D', [])
    ).rejects.toThrow('Failed to update task: Conflict');
  });

  it('calls PUT /api/tasks/capability/:id', async () => {
    _mockFetch({ json: {} });
    await CapabilityTasksService.updateTask('task-7', 'N', 'D', []);
    expect(global.fetch.mock.calls[0][0]).toContain(
      '/api/tasks/capability/task-7'
    );
    expect(global.fetch.mock.calls[0][1].method).toBe('PUT');
  });
});

// ---------------------------------------------------------------------------
// deleteTask
// ---------------------------------------------------------------------------

describe('deleteTask', () => {
  it('resolves without error on success', async () => {
    _mockFetch({});
    await expect(
      CapabilityTasksService.deleteTask('task-1')
    ).resolves.toBeUndefined();
  });

  it('throws when response is not ok', async () => {
    _mockFetchFail('Not Found');
    await expect(CapabilityTasksService.deleteTask('bad-id')).rejects.toThrow(
      'Failed to delete task: Not Found'
    );
  });

  it('calls DELETE /api/tasks/capability/:id', async () => {
    _mockFetch({});
    await CapabilityTasksService.deleteTask('task-99');
    expect(global.fetch.mock.calls[0][0]).toContain(
      '/api/tasks/capability/task-99'
    );
    expect(global.fetch.mock.calls[0][1].method).toBe('DELETE');
  });
});

// ---------------------------------------------------------------------------
// executeTask
// ---------------------------------------------------------------------------

describe('executeTask', () => {
  it('returns execution result on success', async () => {
    _mockFetch({ json: { execution_id: 'exec-1', status: 'RUNNING' } });
    const result = await CapabilityTasksService.executeTask('task-1');
    expect(result.execution_id).toBe('exec-1');
  });

  it('throws when response is not ok', async () => {
    _mockFetchFail('Internal Server Error');
    await expect(CapabilityTasksService.executeTask('task-1')).rejects.toThrow(
      'Failed to execute task: Internal Server Error'
    );
  });

  it('calls POST /api/tasks/capability/:id/execute', async () => {
    _mockFetch({ json: { execution_id: 'x' } });
    await CapabilityTasksService.executeTask('task-3');
    expect(global.fetch.mock.calls[0][0]).toContain(
      '/api/tasks/capability/task-3/execute'
    );
    expect(global.fetch.mock.calls[0][1].method).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// getExecution
// ---------------------------------------------------------------------------

describe('getExecution', () => {
  it('returns execution on success', async () => {
    _mockFetch({ json: { id: 'exec-5', status: 'COMPLETED' } });
    const result = await CapabilityTasksService.getExecution(
      'task-1',
      'exec-5'
    );
    expect(result.id).toBe('exec-5');
  });

  it('throws when response is not ok', async () => {
    _mockFetchFail('Not Found');
    await expect(
      CapabilityTasksService.getExecution('task-1', 'bad-exec')
    ).rejects.toThrow('Failed to get execution: Not Found');
  });

  it('calls GET /api/tasks/capability/:taskId/executions/:execId', async () => {
    _mockFetch({ json: {} });
    await CapabilityTasksService.getExecution('task-X', 'exec-Y');
    expect(global.fetch.mock.calls[0][0]).toContain(
      '/api/tasks/capability/task-X/executions/exec-Y'
    );
  });
});

// ---------------------------------------------------------------------------
// listExecutions
// ---------------------------------------------------------------------------

describe('listExecutions', () => {
  it('returns executions on success', async () => {
    _mockFetch({ json: [{ id: 'e1' }, { id: 'e2' }] });
    const result = await CapabilityTasksService.listExecutions('task-1');
    expect(result).toHaveLength(2);
  });

  it('includes skip and limit in URL', async () => {
    _mockFetch({ json: [] });
    await CapabilityTasksService.listExecutions('task-1', 5, 20);
    expect(global.fetch.mock.calls[0][0]).toContain('skip=5');
    expect(global.fetch.mock.calls[0][0]).toContain('limit=20');
  });

  it('includes status filter when provided', async () => {
    _mockFetch({ json: [] });
    await CapabilityTasksService.listExecutions('task-1', 0, 50, 'COMPLETED');
    expect(global.fetch.mock.calls[0][0]).toContain('status=COMPLETED');
  });

  it('throws when response is not ok', async () => {
    _mockFetchFail('Forbidden');
    await expect(
      CapabilityTasksService.listExecutions('task-1')
    ).rejects.toThrow('Failed to list executions: Forbidden');
  });

  it('calls GET /api/tasks/capability/:id/executions', async () => {
    _mockFetch({ json: [] });
    await CapabilityTasksService.listExecutions('task-Z');
    expect(global.fetch.mock.calls[0][0]).toContain(
      '/api/tasks/capability/task-Z/executions'
    );
  });
});
