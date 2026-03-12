/**
 * unifiedStatusService.test.js
 *
 * Unit tests for services/unifiedStatusService.js.
 *
 * Tests cover:
 * - updateStatus — success, sends correct payload fields, propagates network error
 * - approve — success, missing taskId throws, calls updateStatus with approved status
 * - reject — success, missing taskId throws, missing reason throws, calls updateStatus with rejected status
 * - hold — success, missing taskId throws, calls updateStatus with on_hold status
 * - resume — success, missing taskId throws, calls updateStatus with pending status
 * - cancel — success, missing taskId throws, calls updateStatus with cancelled status
 * - getHistory — success, missing taskId throws, network error returns fallback object with history:[]
 * - getFailures — success, missing taskId throws, network error returns fallback with failures:[]
 * - getMetrics — success, default range used, network error returns fallback with metrics:{}
 * - retry — success, missing taskId throws, calls updateStatus with pending status
 * - batchApprove — success on multiple tasks, empty array throws
 * - batchReject — success, empty array throws, missing reason throws
 *
 * makeRequest and localStorage are mocked; no network calls.
 */

import { vi } from 'vitest';

const { mockMakeRequest } = vi.hoisted(() => ({
  mockMakeRequest: vi.fn(),
}));

vi.mock('@/services/cofounderAgentClient', () => ({
  makeRequest: mockMakeRequest,
}));

vi.mock('@/lib/logger', () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

import { unifiedStatusService } from '../unifiedStatusService';

const _ok = (data) => mockMakeRequest.mockResolvedValue(data);
const _throw = (msg) => mockMakeRequest.mockRejectedValue(new Error(msg));

// Setup localStorage mock
const localStorageMock = (() => {
  let store = {};
  return {
    getItem: (key) => store[key] ?? null,
    setItem: (key, val) => {
      store[key] = String(val);
    },
    removeItem: (key) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();

Object.defineProperty(global, 'localStorage', { value: localStorageMock });

beforeEach(() => {
  vi.clearAllMocks();
  localStorageMock.clear();
});

// ---------------------------------------------------------------------------
// updateStatus
// ---------------------------------------------------------------------------

describe('updateStatus', () => {
  it('returns response on success', async () => {
    _ok({ task_id: 'task-1', status: 'approved' });
    const result = await unifiedStatusService.updateStatus(
      'task-1',
      'approved'
    );
    expect(result.status).toBe('approved');
  });

  it('sends status, reason, and metadata fields', async () => {
    _ok({ task_id: 'task-1' });
    await unifiedStatusService.updateStatus('task-1', 'approved', {
      reason: 'LGTM',
      feedback: 'Good job',
    });
    const payload = mockMakeRequest.mock.calls[0][2];
    expect(payload.status).toBe('approved');
    expect(payload.reason).toBe('LGTM');
    expect(payload.metadata.feedback).toBe('Good job');
    expect(payload.metadata.updated_from_ui).toBe(true);
  });

  it('uses provided userId in payload', async () => {
    _ok({});
    await unifiedStatusService.updateStatus('task-1', 'approved', {
      userId: 'user-42',
    });
    expect(mockMakeRequest.mock.calls[0][2].updated_by).toBe('user-42');
  });

  it('falls back to anonymous when no user in localStorage', async () => {
    _ok({});
    await unifiedStatusService.updateStatus('task-1', 'approved');
    expect(mockMakeRequest.mock.calls[0][2].updated_by).toBe('anonymous');
  });

  it('reads userId from localStorage currentUser', async () => {
    localStorageMock.setItem(
      'currentUser',
      JSON.stringify({ id: 'user-from-storage' })
    );
    _ok({});
    await unifiedStatusService.updateStatus('task-1', 'approved');
    expect(mockMakeRequest.mock.calls[0][2].updated_by).toBe(
      'user-from-storage'
    );
  });

  it('propagates network error wrapped in new Error', async () => {
    _throw('Server error');
    await expect(
      unifiedStatusService.updateStatus('task-1', 'approved')
    ).rejects.toThrow('Server error');
  });

  it('calls PUT /api/tasks/:id/status/validated', async () => {
    _ok({});
    await unifiedStatusService.updateStatus('task-7', 'pending');
    expect(mockMakeRequest.mock.calls[0][0]).toBe(
      '/api/tasks/task-7/status/validated'
    );
    expect(mockMakeRequest.mock.calls[0][1]).toBe('PUT');
  });
});

// ---------------------------------------------------------------------------
// approve
// ---------------------------------------------------------------------------

describe('approve', () => {
  it('returns result on success', async () => {
    _ok({ task_id: 'task-1', status: 'approved' });
    const result = await unifiedStatusService.approve('task-1', 'Looks great');
    expect(result.status).toBe('approved');
  });

  it('throws when taskId is missing', async () => {
    await expect(unifiedStatusService.approve('')).rejects.toThrow(
      'Task ID is required'
    );
    await expect(unifiedStatusService.approve(null)).rejects.toThrow(
      'Task ID is required'
    );
  });

  it('calls updateStatus with approved status', async () => {
    _ok({});
    await unifiedStatusService.approve('task-1', 'LGTM');
    const payload = mockMakeRequest.mock.calls[0][2];
    expect(payload.status).toBe('approved');
    expect(payload.metadata.action).toBe('approve');
  });

  it('includes feedback in metadata', async () => {
    _ok({});
    await unifiedStatusService.approve('task-1', 'Great work');
    const payload = mockMakeRequest.mock.calls[0][2];
    expect(payload.metadata.approval_feedback).toBe('Great work');
  });
});

// ---------------------------------------------------------------------------
// reject
// ---------------------------------------------------------------------------

describe('reject', () => {
  it('returns result on success', async () => {
    _ok({ task_id: 'task-1', status: 'rejected' });
    const result = await unifiedStatusService.reject('task-1', 'Off topic');
    expect(result.status).toBe('rejected');
  });

  it('throws when taskId is missing', async () => {
    await expect(unifiedStatusService.reject('', 'reason')).rejects.toThrow(
      'Task ID is required'
    );
  });

  it('throws when reason is empty', async () => {
    await expect(unifiedStatusService.reject('task-1', '')).rejects.toThrow(
      'Rejection reason is required'
    );
  });

  it('throws when reason is whitespace-only', async () => {
    await expect(unifiedStatusService.reject('task-1', '   ')).rejects.toThrow(
      'Rejection reason is required'
    );
  });

  it('calls updateStatus with rejected status', async () => {
    _ok({});
    await unifiedStatusService.reject('task-1', 'Poor quality');
    const payload = mockMakeRequest.mock.calls[0][2];
    expect(payload.status).toBe('rejected');
    expect(payload.metadata.action).toBe('reject');
    expect(payload.metadata.rejection_reason).toBe('Poor quality');
  });
});

// ---------------------------------------------------------------------------
// hold
// ---------------------------------------------------------------------------

describe('hold', () => {
  it('returns result on success', async () => {
    _ok({ task_id: 'task-1', status: 'on_hold' });
    const result = await unifiedStatusService.hold(
      'task-1',
      'Waiting for assets'
    );
    expect(result.status).toBe('on_hold');
  });

  it('throws when taskId is missing', async () => {
    await expect(unifiedStatusService.hold('')).rejects.toThrow(
      'Task ID is required'
    );
  });

  it('calls updateStatus with on_hold status', async () => {
    _ok({});
    await unifiedStatusService.hold('task-1', 'Blocked');
    const payload = mockMakeRequest.mock.calls[0][2];
    expect(payload.status).toBe('on_hold');
    expect(payload.metadata.action).toBe('hold');
  });
});

// ---------------------------------------------------------------------------
// resume
// ---------------------------------------------------------------------------

describe('resume', () => {
  it('returns result on success', async () => {
    _ok({ task_id: 'task-1', status: 'pending' });
    const result = await unifiedStatusService.resume('task-1');
    expect(result.status).toBe('pending');
  });

  it('throws when taskId is missing', async () => {
    await expect(unifiedStatusService.resume('')).rejects.toThrow(
      'Task ID is required'
    );
  });

  it('uses default reason when reason is empty', async () => {
    _ok({});
    await unifiedStatusService.resume('task-1');
    expect(mockMakeRequest.mock.calls[0][2].reason).toBe(
      'Resumed from on-hold'
    );
  });

  it('calls updateStatus with pending status', async () => {
    _ok({});
    await unifiedStatusService.resume('task-1', 'Assets ready');
    const payload = mockMakeRequest.mock.calls[0][2];
    expect(payload.status).toBe('pending');
    expect(payload.metadata.action).toBe('resume');
  });
});

// ---------------------------------------------------------------------------
// cancel
// ---------------------------------------------------------------------------

describe('cancel', () => {
  it('returns result on success', async () => {
    _ok({ task_id: 'task-1', status: 'cancelled' });
    const result = await unifiedStatusService.cancel(
      'task-1',
      'No longer needed'
    );
    expect(result.status).toBe('cancelled');
  });

  it('throws when taskId is missing', async () => {
    await expect(unifiedStatusService.cancel('')).rejects.toThrow(
      'Task ID is required'
    );
  });

  it('calls updateStatus with cancelled status', async () => {
    _ok({});
    await unifiedStatusService.cancel('task-1', 'Abandoned');
    const payload = mockMakeRequest.mock.calls[0][2];
    expect(payload.status).toBe('cancelled');
    expect(payload.metadata.action).toBe('cancel');
  });
});

// ---------------------------------------------------------------------------
// getHistory
// ---------------------------------------------------------------------------

describe('getHistory', () => {
  it('returns history on success', async () => {
    _ok({ task_id: 'task-1', history: [{ status: 'approved' }], total: 1 });
    const result = await unifiedStatusService.getHistory('task-1');
    expect(result.total).toBe(1);
  });

  it('throws when taskId is missing', async () => {
    await expect(unifiedStatusService.getHistory('')).rejects.toThrow(
      'Task ID is required'
    );
  });

  it('returns fallback object on network error', async () => {
    _throw('Connection refused');
    const result = await unifiedStatusService.getHistory('task-1');
    expect(result.task_id).toBe('task-1');
    expect(result.history).toEqual([]);
    expect(result.total).toBe(0);
    expect(result.error).toBe('Connection refused');
  });

  it('calls GET /api/tasks/:id/status-history with default limit', async () => {
    _ok({ history: [] });
    await unifiedStatusService.getHistory('task-5');
    expect(mockMakeRequest.mock.calls[0][0]).toBe(
      '/api/tasks/task-5/status-history?limit=50'
    );
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });

  it('uses custom limit parameter', async () => {
    _ok({ history: [] });
    await unifiedStatusService.getHistory('task-5', 10);
    expect(mockMakeRequest.mock.calls[0][0]).toContain('limit=10');
  });
});

// ---------------------------------------------------------------------------
// getFailures
// ---------------------------------------------------------------------------

describe('getFailures', () => {
  it('returns failures on success', async () => {
    _ok({ task_id: 'task-1', failures: [{ reason: 'timeout' }], total: 1 });
    const result = await unifiedStatusService.getFailures('task-1');
    expect(result.total).toBe(1);
  });

  it('throws when taskId is missing', async () => {
    await expect(unifiedStatusService.getFailures('')).rejects.toThrow(
      'Task ID is required'
    );
  });

  it('returns fallback object on network error', async () => {
    _throw('Timeout');
    const result = await unifiedStatusService.getFailures('task-1');
    expect(result.failures).toEqual([]);
    expect(result.total).toBe(0);
    expect(result.error).toBe('Timeout');
  });

  it('calls GET /api/tasks/:id/status-history/failures', async () => {
    _ok({ failures: [] });
    await unifiedStatusService.getFailures('task-3');
    expect(mockMakeRequest.mock.calls[0][0]).toContain(
      '/api/tasks/task-3/status-history/failures'
    );
  });
});

// ---------------------------------------------------------------------------
// getMetrics
// ---------------------------------------------------------------------------

describe('getMetrics', () => {
  it('returns metrics on success', async () => {
    _ok({ metrics: { total: 50, approved: 40 } });
    const result = await unifiedStatusService.getMetrics();
    expect(result.metrics.total).toBe(50);
  });

  it('uses default timeRange of 7d', async () => {
    _ok({});
    await unifiedStatusService.getMetrics();
    expect(mockMakeRequest.mock.calls[0][0]).toContain('time_range=7d');
  });

  it('passes custom timeRange', async () => {
    _ok({});
    await unifiedStatusService.getMetrics({ timeRange: '30d' });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('time_range=30d');
  });

  it('includes status filter when provided', async () => {
    _ok({});
    await unifiedStatusService.getMetrics({ status: 'approved' });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('status=approved');
  });

  it('omits status filter when not provided', async () => {
    _ok({});
    await unifiedStatusService.getMetrics();
    expect(mockMakeRequest.mock.calls[0][0]).not.toContain('status=');
  });

  it('returns fallback object on network error', async () => {
    _throw('Server down');
    const result = await unifiedStatusService.getMetrics();
    expect(result.metrics).toEqual({});
    expect(result.error).toBe('Server down');
  });
});

// ---------------------------------------------------------------------------
// retry
// ---------------------------------------------------------------------------

describe('retry', () => {
  it('returns result on success', async () => {
    _ok({ task_id: 'task-1', status: 'pending' });
    const result = await unifiedStatusService.retry('task-1');
    expect(result.status).toBe('pending');
  });

  it('throws when taskId is missing', async () => {
    await expect(unifiedStatusService.retry('')).rejects.toThrow(
      'Task ID is required'
    );
  });

  it('calls updateStatus with pending status and retry metadata', async () => {
    _ok({});
    await unifiedStatusService.retry('task-1', 'Retry after fix');
    const payload = mockMakeRequest.mock.calls[0][2];
    expect(payload.status).toBe('pending');
    expect(payload.metadata.action).toBe('retry');
    expect(payload.reason).toBe('Retry after fix');
  });

  it('uses default reason "Manual retry"', async () => {
    _ok({});
    await unifiedStatusService.retry('task-1');
    expect(mockMakeRequest.mock.calls[0][2].reason).toBe('Manual retry');
  });
});

// ---------------------------------------------------------------------------
// batchApprove
// ---------------------------------------------------------------------------

describe('batchApprove', () => {
  it('returns array of results for multiple tasks', async () => {
    mockMakeRequest.mockResolvedValue({ status: 'approved' });
    const results = await unifiedStatusService.batchApprove(
      ['task-1', 'task-2'],
      'LGTM'
    );
    expect(results).toHaveLength(2);
    expect(results[0].status).toBe('approved');
  });

  it('throws when taskIds array is empty', async () => {
    await expect(unifiedStatusService.batchApprove([])).rejects.toThrow(
      'At least one task ID is required'
    );
  });

  it('throws when taskIds is not an array', async () => {
    await expect(unifiedStatusService.batchApprove('task-1')).rejects.toThrow(
      'At least one task ID is required'
    );
  });

  it('calls approve for each task ID', async () => {
    mockMakeRequest.mockResolvedValue({ status: 'approved' });
    await unifiedStatusService.batchApprove(['task-A', 'task-B']);
    // Each approve calls makeRequest once
    expect(mockMakeRequest).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// batchReject
// ---------------------------------------------------------------------------

describe('batchReject', () => {
  it('returns array of results for multiple tasks', async () => {
    mockMakeRequest.mockResolvedValue({ status: 'rejected' });
    const results = await unifiedStatusService.batchReject(
      ['task-1', 'task-2'],
      'Off topic'
    );
    expect(results).toHaveLength(2);
  });

  it('throws when taskIds array is empty', async () => {
    await expect(
      unifiedStatusService.batchReject([], 'reason')
    ).rejects.toThrow('At least one task ID is required');
  });

  it('throws when reason is empty', async () => {
    await expect(
      unifiedStatusService.batchReject(['task-1'], '')
    ).rejects.toThrow('Rejection reason is required');
  });

  it('calls reject for each task ID', async () => {
    mockMakeRequest.mockResolvedValue({ status: 'rejected' });
    await unifiedStatusService.batchReject(['task-X', 'task-Y'], 'Bad content');
    expect(mockMakeRequest).toHaveBeenCalledTimes(2);
  });
});
