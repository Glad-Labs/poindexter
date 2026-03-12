/**
 * workflowManagementService.test.js
 *
 * Unit tests for services/workflowManagementService.js.
 *
 * Tests cover:
 * - getWorkflowHistory — success, options (limit/offset/status), no options, response.error throws, network error
 * - getExecutionDetails — success, response.error throws, network error
 * - getWorkflowStatistics — success, response.error throws
 * - getPerformanceMetrics — success, default range, custom range, response.error throws
 * - executeWorkflow — success, response.error throws, network error
 * - getWorkflowExecutionHistory — success, options, no options, response.error throws
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
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

import {
  getWorkflowHistory,
  getExecutionDetails,
  getWorkflowStatistics,
  getPerformanceMetrics,
  executeWorkflow,
  getWorkflowExecutionHistory,
} from '../workflowManagementService';

const _ok = (data) => mockMakeRequest.mockResolvedValue(data);
const _error = (msg) => mockMakeRequest.mockResolvedValue({ error: msg });
const _throw = (msg) => mockMakeRequest.mockRejectedValue(new Error(msg));

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// getWorkflowHistory
// ---------------------------------------------------------------------------

describe('getWorkflowHistory', () => {
  it('returns history on success', async () => {
    _ok({ executions: [{ id: 'exec-1' }], total: 1 });
    const result = await getWorkflowHistory();
    expect(result.total).toBe(1);
  });

  it('includes limit in URL when provided', async () => {
    _ok({});
    await getWorkflowHistory({ limit: 10 });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('limit=10');
  });

  it('includes offset in URL when provided', async () => {
    _ok({});
    await getWorkflowHistory({ offset: 20 });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('offset=20');
  });

  it('includes status filter in URL when provided', async () => {
    _ok({});
    await getWorkflowHistory({ status: 'COMPLETED' });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('status=COMPLETED');
  });

  it('omits query string when no options provided', async () => {
    _ok({});
    await getWorkflowHistory();
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/workflows/history');
  });

  it('throws when response contains error field', async () => {
    _error('Access denied');
    await expect(getWorkflowHistory()).rejects.toThrow('Access denied');
  });

  it('propagates network errors', async () => {
    _throw('Connection refused');
    await expect(getWorkflowHistory()).rejects.toThrow('Connection refused');
  });

  it('calls GET /api/workflows/history', async () => {
    _ok({});
    await getWorkflowHistory();
    expect(mockMakeRequest.mock.calls[0][0]).toContain(
      '/api/workflows/history'
    );
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// getExecutionDetails
// ---------------------------------------------------------------------------

describe('getExecutionDetails', () => {
  it('returns execution details on success', async () => {
    _ok({ id: 'exec-42', status: 'COMPLETED', phases: [] });
    const result = await getExecutionDetails('exec-42');
    expect(result.id).toBe('exec-42');
  });

  it('throws when response contains error field', async () => {
    _error('Execution not found');
    await expect(getExecutionDetails('bad-id')).rejects.toThrow(
      'Execution not found'
    );
  });

  it('propagates network errors', async () => {
    _throw('Timeout');
    await expect(getExecutionDetails('exec-1')).rejects.toThrow('Timeout');
  });

  it('calls GET /api/workflow/:id/details', async () => {
    _ok({});
    await getExecutionDetails('exec-99');
    expect(mockMakeRequest.mock.calls[0][0]).toBe(
      '/api/workflow/exec-99/details'
    );
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// getWorkflowStatistics
// ---------------------------------------------------------------------------

describe('getWorkflowStatistics', () => {
  it('returns statistics on success', async () => {
    _ok({ total: 100, completed: 80, failed: 5 });
    const result = await getWorkflowStatistics();
    expect(result.total).toBe(100);
  });

  it('throws when response contains error field', async () => {
    _error('Stats unavailable');
    await expect(getWorkflowStatistics()).rejects.toThrow('Stats unavailable');
  });

  it('propagates network errors', async () => {
    _throw('Network error');
    await expect(getWorkflowStatistics()).rejects.toThrow('Network error');
  });

  it('calls GET /api/workflows/statistics', async () => {
    _ok({});
    await getWorkflowStatistics();
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/workflows/statistics');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// getPerformanceMetrics
// ---------------------------------------------------------------------------

describe('getPerformanceMetrics', () => {
  it('returns metrics on success', async () => {
    _ok({ avg_duration_ms: 4500, success_rate: 0.95 });
    const result = await getPerformanceMetrics();
    expect(result.success_rate).toBe(0.95);
  });

  it('uses default range of 30d', async () => {
    _ok({});
    await getPerformanceMetrics();
    expect(mockMakeRequest.mock.calls[0][0]).toContain('range=30d');
  });

  it('passes custom range in URL', async () => {
    _ok({});
    await getPerformanceMetrics('7d');
    expect(mockMakeRequest.mock.calls[0][0]).toContain('range=7d');
  });

  it('throws when response contains error field', async () => {
    _error('Metrics unavailable');
    await expect(getPerformanceMetrics()).rejects.toThrow(
      'Metrics unavailable'
    );
  });

  it('calls GET /api/workflows/performance-metrics', async () => {
    _ok({});
    await getPerformanceMetrics();
    expect(mockMakeRequest.mock.calls[0][0]).toContain(
      '/api/workflows/performance-metrics'
    );
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// executeWorkflow
// ---------------------------------------------------------------------------

describe('executeWorkflow', () => {
  it('returns execution result on success', async () => {
    _ok({ execution_id: 'exec-new', status: 'RUNNING' });
    const result = await executeWorkflow('workflow-1');
    expect(result.execution_id).toBe('exec-new');
  });

  it('sends task input as POST body', async () => {
    _ok({ execution_id: 'exec-1' });
    const input = { topic: 'AI trends', tone: 'professional' };
    await executeWorkflow('workflow-5', input);
    expect(mockMakeRequest.mock.calls[0][2]).toEqual(input);
  });

  it('uses empty object as default task input', async () => {
    _ok({ execution_id: 'exec-2' });
    await executeWorkflow('workflow-5');
    expect(mockMakeRequest.mock.calls[0][2]).toEqual({});
  });

  it('throws when response contains error field', async () => {
    _error('Workflow not found');
    await expect(executeWorkflow('bad-id')).rejects.toThrow(
      'Workflow not found'
    );
  });

  it('propagates network errors', async () => {
    _throw('Timeout');
    await expect(executeWorkflow('workflow-1')).rejects.toThrow('Timeout');
  });

  it('calls POST /api/workflows/execute/:id', async () => {
    _ok({ execution_id: 'x' });
    await executeWorkflow('my-workflow');
    expect(mockMakeRequest.mock.calls[0][0]).toBe(
      '/api/workflows/execute/my-workflow'
    );
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// getWorkflowExecutionHistory
// ---------------------------------------------------------------------------

describe('getWorkflowExecutionHistory', () => {
  it('returns execution history on success', async () => {
    _ok({ executions: [{ id: 'e1' }], total: 1 });
    const result = await getWorkflowExecutionHistory('workflow-1');
    expect(result.total).toBe(1);
  });

  it('includes limit in URL when provided', async () => {
    _ok({});
    await getWorkflowExecutionHistory('workflow-1', { limit: 25 });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('limit=25');
  });

  it('includes offset in URL when provided', async () => {
    _ok({});
    await getWorkflowExecutionHistory('workflow-1', { offset: 50 });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('offset=50');
  });

  it('omits query string when no options provided', async () => {
    _ok({});
    await getWorkflowExecutionHistory('workflow-5');
    expect(mockMakeRequest.mock.calls[0][0]).toBe(
      '/api/workflows/workflow-5/history'
    );
  });

  it('throws when response contains error field', async () => {
    _error('Workflow not found');
    await expect(getWorkflowExecutionHistory('bad-id')).rejects.toThrow(
      'Workflow not found'
    );
  });

  it('propagates network errors', async () => {
    _throw('Network error');
    await expect(getWorkflowExecutionHistory('workflow-1')).rejects.toThrow(
      'Network error'
    );
  });

  it('calls GET /api/workflows/:id/history', async () => {
    _ok({});
    await getWorkflowExecutionHistory('wf-42');
    expect(mockMakeRequest.mock.calls[0][0]).toContain(
      '/api/workflows/wf-42/history'
    );
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});
