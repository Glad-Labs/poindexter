/**
 * taskService.test.js
 *
 * Unit tests for services/taskService.js.
 *
 * Tests cover:
 * - getTasks — success, pagination params, status/category filters, response.error throws, propagates network error
 * - getTask — success, response.error throws
 * - createTask — success (data.id path, id path, raw result path), response.error throws
 * - updateTask — success, response.error throws
 * - approveTask — success with feedback, defaults, response.error throws
 * - publishTask — success, triggers non-blocking revalidation, response.error throws
 * - rejectTask — success, defaults, response.error throws
 * - deleteTask — success, response.error throws
 * - getContentTask — success, response.error throws
 * - deleteContentTask — success, response.error throws
 * - pauseTask — success, response.error throws
 * - resumeTask — success, response.error throws
 * - cancelTask — success, response.error throws
 * - revalidatePublicSite — success, non-ok status returns failure object, fetch throws returns failure object
 *
 * makeRequest and global.fetch are mocked; no network calls.
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

vi.mock('../config/apiConfig', () => ({
  getApiUrl: () => 'http://localhost:8000',
}));

import {
  getTasks,
  getTask,
  createTask,
  updateTask,
  approveTask,
  publishTask,
  rejectTask,
  deleteTask,
  getContentTask,
  deleteContentTask,
  pauseTask,
  resumeTask,
  cancelTask,
  revalidatePublicSite,
} from '../taskService';

const _ok = (data) => mockMakeRequest.mockResolvedValue(data);
const _error = (msg) => mockMakeRequest.mockResolvedValue({ error: msg });
const _throw = (msg) => mockMakeRequest.mockRejectedValue(new Error(msg));

beforeEach(() => {
  vi.clearAllMocks();
  // Default fetch mock for revalidatePublicSite
  global.fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ revalidated: true }),
  });
});

// ---------------------------------------------------------------------------
// getTasks
// ---------------------------------------------------------------------------

describe('getTasks', () => {
  it('returns tasks array on success', async () => {
    _ok({ tasks: [{ id: '1' }, { id: '2' }] });
    const result = await getTasks();
    expect(result).toEqual([{ id: '1' }, { id: '2' }]);
  });

  it('returns empty array when tasks key absent', async () => {
    _ok({});
    const result = await getTasks();
    expect(result).toEqual([]);
  });

  it('includes offset and limit in request URL', async () => {
    _ok({ tasks: [] });
    await getTasks(10, 50);
    const url = mockMakeRequest.mock.calls[0][0];
    expect(url).toContain('offset=10');
    expect(url).toContain('limit=50');
  });

  it('includes status filter when provided', async () => {
    _ok({ tasks: [] });
    await getTasks(0, 20, { status: 'pending' });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('status=pending');
  });

  it('includes category filter when provided', async () => {
    _ok({ tasks: [] });
    await getTasks(0, 20, { category: 'content' });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('category=content');
  });

  it('does not include undefined filter keys', async () => {
    _ok({ tasks: [] });
    await getTasks(0, 20, {});
    const url = mockMakeRequest.mock.calls[0][0];
    expect(url).not.toContain('status=');
    expect(url).not.toContain('category=');
  });

  it('throws when response contains error field', async () => {
    _error('Unauthorized');
    await expect(getTasks()).rejects.toThrow(
      'Could not fetch tasks: Unauthorized'
    );
  });

  it('propagates network errors', async () => {
    _throw('Network failure');
    await expect(getTasks()).rejects.toThrow('Network failure');
  });

  it('calls GET /api/tasks', async () => {
    _ok({ tasks: [] });
    await getTasks();
    expect(mockMakeRequest.mock.calls[0][0]).toContain('/api/tasks');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// getTask
// ---------------------------------------------------------------------------

describe('getTask', () => {
  it('returns task object on success', async () => {
    _ok({ id: 'task-1', status: 'pending' });
    const result = await getTask('task-1');
    expect(result.id).toBe('task-1');
  });

  it('throws when response contains error field', async () => {
    _error('Task not found');
    await expect(getTask('bad-id')).rejects.toThrow(
      'Could not fetch task: Task not found'
    );
  });

  it('calls GET /api/tasks/:id', async () => {
    _ok({ id: 'task-42' });
    await getTask('task-42');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/tasks/task-42');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// createTask
// ---------------------------------------------------------------------------

describe('createTask', () => {
  it('returns full result object on success', async () => {
    _ok({ id: 'new-task-id', status: 'pending' });
    const result = await createTask({ task_name: 'Test', topic: 'AI' });
    expect(result).toEqual({ id: 'new-task-id', status: 'pending' });
  });

  it('returns result with nested data if present', async () => {
    _ok({ data: { id: 'new-task-id' } });
    const result = await createTask({ task_name: 'Test', topic: 'AI' });
    expect(result).toEqual({ data: { id: 'new-task-id' } });
  });

  it('returns result even without id fields', async () => {
    const raw = { status: 'queued' };
    _ok(raw);
    const result = await createTask({ task_name: 'Test', topic: 'AI' });
    expect(result).toEqual(raw);
  });

  it('passes task data directly as request body', async () => {
    _ok({ id: 'x' });
    await createTask({ task_name: 'Blog Post', topic: 'Tech' });
    const body = mockMakeRequest.mock.calls[0][2];
    expect(body).toEqual({ task_name: 'Blog Post', topic: 'Tech' });
  });

  it('throws when response contains error field', async () => {
    _error('Validation failed');
    await expect(createTask({})).rejects.toThrow(
      'Could not create task: Validation failed'
    );
  });

  it('calls POST /api/tasks', async () => {
    _ok({ id: 'z' });
    await createTask({});
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/tasks');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// updateTask
// ---------------------------------------------------------------------------

describe('updateTask', () => {
  it('returns updated task on success', async () => {
    _ok({ id: 'task-1', status: 'approved' });
    const result = await updateTask('task-1', { status: 'approved' });
    expect(result.status).toBe('approved');
  });

  it('throws when response contains error field', async () => {
    _error('Not found');
    await expect(updateTask('bad-id', {})).rejects.toThrow(
      'Could not update task: Not found'
    );
  });

  it('calls PATCH /api/tasks/:id with updates', async () => {
    _ok({ id: 'task-5' });
    await updateTask('task-5', { status: 'done' });
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/tasks/task-5');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('PATCH');
    expect(mockMakeRequest.mock.calls[0][2]).toEqual({ status: 'done' });
  });
});

// ---------------------------------------------------------------------------
// approveTask
// ---------------------------------------------------------------------------

describe('approveTask', () => {
  it('returns approved task on success', async () => {
    _ok({ id: 'task-1', status: 'approved' });
    const result = await approveTask('task-1', 'Looks great');
    expect(result.status).toBe('approved');
  });

  it('sends auto_publish: false', async () => {
    _ok({});
    await approveTask('task-1', 'LGTM');
    const body = mockMakeRequest.mock.calls[0][2];
    expect(body.auto_publish).toBe(false);
  });

  it('sends feedback in request body', async () => {
    _ok({});
    await approveTask('task-1', 'Great work');
    expect(mockMakeRequest.mock.calls[0][2].feedback).toBe('Great work');
  });

  it('sends empty string as default feedback', async () => {
    _ok({});
    await approveTask('task-1');
    expect(mockMakeRequest.mock.calls[0][2].feedback).toBe('');
  });

  it('throws when response contains error field', async () => {
    _error('Permission denied');
    await expect(approveTask('task-1')).rejects.toThrow(
      'Could not approve task: Permission denied'
    );
  });

  it('calls POST /api/tasks/:id/approve', async () => {
    _ok({});
    await approveTask('task-7');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/tasks/task-7/approve');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// publishTask
// ---------------------------------------------------------------------------

describe('publishTask', () => {
  it('returns published task on success', async () => {
    _ok({ id: 'task-1', status: 'published' });
    const result = await publishTask('task-1');
    expect(result.status).toBe('published');
  });

  it('triggers non-blocking revalidatePublicSite after success', async () => {
    // First call: publishTask itself; second call: revalidatePublicSite
    mockMakeRequest
      .mockResolvedValueOnce({
        id: 'task-1',
        status: 'published',
        post_slug: 'my-post',
      })
      .mockResolvedValueOnce({ success: true });
    await publishTask('task-1');
    // Flush any pending promises from the non-blocking revalidation call
    await new Promise((r) => setTimeout(r, 0));
    // revalidatePublicSite calls makeRequest a second time
    expect(mockMakeRequest).toHaveBeenCalledTimes(2);
    expect(mockMakeRequest.mock.calls[1][0]).toBe('/api/revalidate-cache');
  });

  it('throws when response contains error field', async () => {
    _error('Publish failed');
    await expect(publishTask('task-1')).rejects.toThrow(
      'Could not publish task: Publish failed'
    );
  });

  it('calls POST /api/tasks/:id/publish', async () => {
    _ok({ id: 'task-3', status: 'published' });
    await publishTask('task-3');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/tasks/task-3/publish');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// rejectTask
// ---------------------------------------------------------------------------

describe('rejectTask', () => {
  it('returns rejected task on success', async () => {
    _ok({ id: 'task-1', status: 'rejected' });
    const result = await rejectTask('task-1', 'Off-topic', 'Too short');
    expect(result.status).toBe('rejected');
  });

  it('sends reason and feedback in request body', async () => {
    _ok({});
    await rejectTask('task-1', 'Bad quality', 'Needs more detail');
    const body = mockMakeRequest.mock.calls[0][2];
    expect(body.reason).toBe('Bad quality');
    expect(body.feedback).toBe('Needs more detail');
  });

  it('uses reason as feedback when feedback is null', async () => {
    _ok({});
    await rejectTask('task-1', 'Too short');
    const body = mockMakeRequest.mock.calls[0][2];
    expect(body.feedback).toBe('Too short');
  });

  it('uses default reason when reason is empty', async () => {
    _ok({});
    await rejectTask('task-1');
    const body = mockMakeRequest.mock.calls[0][2];
    expect(body.reason).toBe('Rejected');
  });

  it('sends allow_revisions: true by default', async () => {
    _ok({});
    await rejectTask('task-1', 'Reason');
    expect(mockMakeRequest.mock.calls[0][2].allow_revisions).toBe(true);
  });

  it('sends allow_revisions: false when specified', async () => {
    _ok({});
    await rejectTask('task-1', 'Reason', null, false);
    expect(mockMakeRequest.mock.calls[0][2].allow_revisions).toBe(false);
  });

  it('throws when response contains error field', async () => {
    _error('Not allowed');
    await expect(rejectTask('task-1', 'Reason')).rejects.toThrow(
      'Could not reject task: Not allowed'
    );
  });

  it('calls POST /api/tasks/:id/reject', async () => {
    _ok({});
    await rejectTask('task-9', 'Reason');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/tasks/task-9/reject');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// deleteTask
// ---------------------------------------------------------------------------

describe('deleteTask', () => {
  it('returns result on success', async () => {
    _ok({ deleted: true });
    const result = await deleteTask('task-1');
    expect(result.deleted).toBe(true);
  });

  it('throws when response contains error field', async () => {
    _error('Cannot delete running task');
    await expect(deleteTask('task-1')).rejects.toThrow(
      'Could not delete task: Cannot delete running task'
    );
  });

  it('calls DELETE /api/tasks/:id', async () => {
    _ok({});
    await deleteTask('task-5');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/tasks/task-5');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('DELETE');
  });
});

// ---------------------------------------------------------------------------
// getContentTask
// ---------------------------------------------------------------------------

describe('getContentTask', () => {
  it('returns task with content on success', async () => {
    _ok({ id: 'task-1', content: 'Article body here' });
    const result = await getContentTask('task-1');
    expect(result.content).toBe('Article body here');
  });

  it('throws when response contains error field', async () => {
    _error('Task not found');
    await expect(getContentTask('bad-id')).rejects.toThrow(
      'Could not fetch task: Task not found'
    );
  });

  it('calls GET /api/tasks/:id', async () => {
    _ok({ id: 'task-11' });
    await getContentTask('task-11');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/tasks/task-11');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// deleteContentTask
// ---------------------------------------------------------------------------

describe('deleteContentTask', () => {
  it('returns result on success', async () => {
    _ok({ deleted: true });
    const result = await deleteContentTask('task-1');
    expect(result.deleted).toBe(true);
  });

  it('throws when response contains error field', async () => {
    _error('Not found');
    await expect(deleteContentTask('bad-id')).rejects.toThrow(
      'Could not delete task: Not found'
    );
  });

  it('calls DELETE /api/tasks/:id', async () => {
    _ok({});
    await deleteContentTask('task-22');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/tasks/task-22');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('DELETE');
  });
});

// ---------------------------------------------------------------------------
// pauseTask
// ---------------------------------------------------------------------------

describe('pauseTask', () => {
  it('returns paused task on success', async () => {
    _ok({ id: 'task-1', status: 'paused' });
    const result = await pauseTask('task-1');
    expect(result.status).toBe('paused');
  });

  it('throws when response contains error field', async () => {
    _error('Cannot pause');
    await expect(pauseTask('task-1')).rejects.toThrow(
      'Could not pause task: Cannot pause'
    );
  });

  it('calls POST /api/tasks/:id/pause', async () => {
    _ok({});
    await pauseTask('task-3');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/tasks/task-3/pause');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// resumeTask
// ---------------------------------------------------------------------------

describe('resumeTask', () => {
  it('returns resumed task on success', async () => {
    _ok({ id: 'task-1', status: 'running' });
    const result = await resumeTask('task-1');
    expect(result.status).toBe('running');
  });

  it('throws when response contains error field', async () => {
    _error('Cannot resume');
    await expect(resumeTask('task-1')).rejects.toThrow(
      'Could not resume task: Cannot resume'
    );
  });

  it('calls POST /api/tasks/:id/resume', async () => {
    _ok({});
    await resumeTask('task-4');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/tasks/task-4/resume');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// cancelTask
// ---------------------------------------------------------------------------

describe('cancelTask', () => {
  it('returns cancelled task on success', async () => {
    _ok({ id: 'task-1', status: 'cancelled' });
    const result = await cancelTask('task-1');
    expect(result.status).toBe('cancelled');
  });

  it('throws when response contains error field', async () => {
    _error('Already completed');
    await expect(cancelTask('task-1')).rejects.toThrow(
      'Could not cancel task: Already completed'
    );
  });

  it('calls POST /api/tasks/:id/cancel', async () => {
    _ok({});
    await cancelTask('task-6');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/tasks/task-6/cancel');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// revalidatePublicSite
// ---------------------------------------------------------------------------

describe('revalidatePublicSite', () => {
  it('returns revalidation result on success', async () => {
    _ok({ revalidated: true, count: 3 });
    const result = await revalidatePublicSite(['/archive']);
    expect(result.revalidated).toBe(true);
  });

  it('returns failure object when makeRequest returns error', async () => {
    _error('Server error');
    const result = await revalidatePublicSite();
    expect(result.success).toBe(false);
    expect(result.error).toBe('Server error');
  });

  it('returns failure object when makeRequest throws', async () => {
    _throw('ECONNREFUSED');
    const result = await revalidatePublicSite();
    expect(result.success).toBe(false);
    expect(result.error).toBe('ECONNREFUSED');
  });

  it('calls /api/revalidate-cache via POST using makeRequest', async () => {
    _ok({});
    await revalidatePublicSite(['/']);
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/revalidate-cache');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
  });

  it('sends paths in request body', async () => {
    _ok({});
    await revalidatePublicSite(['/archive', '/']);
    const body = mockMakeRequest.mock.calls[0][2];
    expect(body.paths).toEqual(['/archive', '/']);
  });

  it('uses empty paths array by default', async () => {
    _ok({});
    await revalidatePublicSite();
    const body = mockMakeRequest.mock.calls[0][2];
    expect(body.paths).toEqual([]);
  });
});
