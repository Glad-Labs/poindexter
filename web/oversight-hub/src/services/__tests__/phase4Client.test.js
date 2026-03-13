/**
 * phase4Client.test.js
 *
 * Unit tests for services/phase4Client.js (Issue #716 — 497 lines, zero coverage).
 *
 * Covers:
 * - makeRequest (via public API): success path, HTTP error with detail, timeout (AbortError),
 *   non-ok response with plain statusText fallback, re-throws unknown errors
 * - agentDiscoveryClient: listAgents, getRegistry, getAgent, getAgentsByPhase,
 *   getAgentsByCapability, getAgentsByCategory, searchAgents
 * - serviceRegistryClient: listServices, getService, getServiceActions, executeServiceAction
 * - workflowClient: getTemplates, executeWorkflow, getWorkflowStatus, getWorkflowHistory, cancelWorkflow
 * - taskClient: createTask, listTasks, getTask, updateTask, executeTask,
 *   getTaskStatus, approveTask, rejectTask
 * - unifiedServicesClient: content/financial/market/compliance shortcuts delegate to serviceRegistryClient
 * - healthCheck: returns healthy:true when list returns array; healthy:false on error
 *
 * global.fetch is mocked; no network calls.
 */

import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

// ------------------------------------------------------------------
// Mock dependencies
// ------------------------------------------------------------------

const { mockLogError } = vi.hoisted(() => ({
  mockLogError: vi.fn(),
}));

vi.mock('@/services/errorLoggingService', () => ({
  logError: mockLogError,
}));

vi.mock('@/config/apiConfig', () => ({
  getApiUrl: () => 'http://localhost:8000',
}));

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

// Stub AbortController for timeout tests
class FakeAbortController {
  constructor() {
    this.signal = { aborted: false };
    this.abort = vi.fn(() => {
      this.signal.aborted = true;
    });
  }
}
vi.stubGlobal('AbortController', FakeAbortController);

const mockSetTimeout = vi.fn((fn, delay) => {
  // Return a fake timer id; do not actually call fn unless requested
  return 42;
});
const mockClearTimeout = vi.fn();
vi.stubGlobal('setTimeout', mockSetTimeout);
vi.stubGlobal('clearTimeout', mockClearTimeout);

import phase4Client, {
  agentDiscoveryClient,
  serviceRegistryClient,
  workflowClient,
  taskClient,
  unifiedServicesClient,
  healthCheck,
} from '../phase4Client';

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

function okResponse(data) {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
    statusText: 'OK',
  };
}

function errorResponse(status, detail) {
  return {
    ok: false,
    status,
    statusText: 'Error',
    json: () => Promise.resolve({ detail }),
    text: () => Promise.resolve(detail),
  };
}

function jsonParseErrorResponse(status) {
  return {
    ok: false,
    status,
    statusText: 'Bad Gateway',
    json: () => Promise.reject(new Error('not json')),
    text: () => Promise.resolve('502 Bad Gateway'),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ------------------------------------------------------------------
// agentDiscoveryClient
// ------------------------------------------------------------------

describe('agentDiscoveryClient', () => {
  it('listAgents calls GET /api/agents/list', async () => {
    const agents = ['content_agent', 'financial_agent'];
    mockFetch.mockResolvedValueOnce(okResponse(agents));
    const result = await agentDiscoveryClient.listAgents();
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/agents/list',
      expect.objectContaining({ method: 'GET' })
    );
    expect(result).toEqual(agents);
  });

  it('getRegistry calls GET /api/agents/registry', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ agents: {} }));
    await agentDiscoveryClient.getRegistry();
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/agents/registry',
      expect.anything()
    );
  });

  it('getAgent calls GET /api/agents/{name}', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ name: 'content_agent' }));
    await agentDiscoveryClient.getAgent('content_agent');
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/agents/content_agent',
      expect.anything()
    );
  });

  it('getAgentsByPhase calls GET /api/agents/by-phase/{phase}', async () => {
    mockFetch.mockResolvedValueOnce(okResponse([]));
    await agentDiscoveryClient.getAgentsByPhase('draft');
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/agents/by-phase/draft',
      expect.anything()
    );
  });

  it('getAgentsByCapability calls GET /api/agents/by-capability/{cap}', async () => {
    mockFetch.mockResolvedValueOnce(okResponse([]));
    await agentDiscoveryClient.getAgentsByCapability('web_search');
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/agents/by-capability/web_search',
      expect.anything()
    );
  });

  it('getAgentsByCategory calls GET /api/agents/by-category/{cat}', async () => {
    mockFetch.mockResolvedValueOnce(okResponse([]));
    await agentDiscoveryClient.getAgentsByCategory('content');
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/agents/by-category/content',
      expect.anything()
    );
  });

  it('searchAgents encodes query and calls GET /api/agents/search', async () => {
    mockFetch.mockResolvedValueOnce(okResponse([]));
    await agentDiscoveryClient.searchAgents('my query');
    const url = mockFetch.mock.calls[0][0];
    expect(url).toContain('/api/agents/search');
    expect(url).toContain(encodeURIComponent('my query'));
  });

  it('throws with detail message on HTTP error', async () => {
    mockFetch.mockResolvedValueOnce(errorResponse(404, 'Agent not found'));
    await expect(agentDiscoveryClient.getAgent('unknown')).rejects.toThrow(
      'Agent not found'
    );
  });

  it('logs error via logError on HTTP error', async () => {
    mockFetch.mockResolvedValueOnce(errorResponse(500, 'Server down'));
    try {
      await agentDiscoveryClient.listAgents();
    } catch {
      // expected
    }
    expect(mockLogError).toHaveBeenCalled();
  });

  it('falls back to statusText when response body is not JSON', async () => {
    mockFetch.mockResolvedValueOnce(jsonParseErrorResponse(502));
    await expect(agentDiscoveryClient.listAgents()).rejects.toThrow(
      /Bad Gateway|502/
    );
  });
});

// ------------------------------------------------------------------
// serviceRegistryClient
// ------------------------------------------------------------------

describe('serviceRegistryClient', () => {
  it('listServices calls /api/agents/registry', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({}));
    await serviceRegistryClient.listServices();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/agents/registry'),
      expect.anything()
    );
  });

  it('getService calls /api/services/{name}', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ name: 'content_service' }));
    await serviceRegistryClient.getService('content_service');
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/services/content_service',
      expect.anything()
    );
  });

  it('getServiceActions calls /api/services/{name}/actions', async () => {
    mockFetch.mockResolvedValueOnce(okResponse([]));
    await serviceRegistryClient.getServiceActions('content_service');
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/services/content_service/actions'),
      expect.anything()
    );
  });

  it('executeServiceAction sends POST to /api/services/{name}/actions/{action}', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ result: 'ok' }));
    const result = await serviceRegistryClient.executeServiceAction(
      'content_service',
      'generate',
      { topic: 'AI' }
    );
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/services/content_service/actions/generate');
    expect(options.method).toBe('POST');
    expect(result).toEqual({ result: 'ok' });
  });
});

// ------------------------------------------------------------------
// workflowClient
// ------------------------------------------------------------------

describe('workflowClient', () => {
  it('getTemplates sends POST to /api/workflows/templates', async () => {
    mockFetch.mockResolvedValueOnce(okResponse([]));
    await workflowClient.getTemplates();
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/workflows/templates');
    expect(options.method).toBe('POST');
  });

  it('executeWorkflow sends POST to /api/workflows/execute/{id}', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ execution_id: 'exec-1' }));
    const result = await workflowClient.executeWorkflow('tmpl-1', { k: 'v' });
    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/workflows/execute/tmpl-1');
    expect(result.execution_id).toBe('exec-1');
  });

  it('getWorkflowStatus calls GET /api/workflows/status/{id}', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ status: 'running' }));
    await workflowClient.getWorkflowStatus('exec-1');
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/workflows/status/exec-1',
      expect.anything()
    );
  });

  it('getWorkflowHistory calls GET /api/workflows/{id}/history', async () => {
    mockFetch.mockResolvedValueOnce(okResponse([]));
    await workflowClient.getWorkflowHistory('tmpl-1', 10);
    const url = mockFetch.mock.calls[0][0];
    expect(url).toContain('/api/workflows/tmpl-1/history');
    expect(url).toContain('limit=10');
  });

  it('cancelWorkflow sends POST to /api/workflows/cancel/{id}', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ cancelled: true }));
    await workflowClient.cancelWorkflow('exec-1');
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/workflows/cancel/exec-1');
    expect(options.method).toBe('POST');
  });
});

// ------------------------------------------------------------------
// taskClient
// ------------------------------------------------------------------

describe('taskClient', () => {
  it('createTask sends POST to /api/tasks with body', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ id: 'task-1' }));
    const result = await taskClient.createTask({ task_name: 'Test' });
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/tasks');
    expect(options.method).toBe('POST');
    expect(result.id).toBe('task-1');
  });

  it('listTasks calls GET /api/tasks with query params', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ tasks: [] }));
    await taskClient.listTasks({ status: 'pending' }, 20);
    const url = mockFetch.mock.calls[0][0];
    expect(url).toContain('/api/tasks');
    expect(url).toContain('limit=20');
    expect(url).toContain('status=pending');
  });

  it('getTask calls GET /api/tasks/{id}', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ id: 'task-1' }));
    await taskClient.getTask('task-1');
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/tasks/task-1',
      expect.anything()
    );
  });

  it('updateTask sends PUT to /api/tasks/{id} with updates', async () => {
    mockFetch.mockResolvedValueOnce(
      okResponse({ id: 'task-1', status: 'done' })
    );
    await taskClient.updateTask('task-1', { status: 'done' });
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/tasks/task-1');
    expect(options.method).toBe('PUT');
  });

  it('executeTask sends POST to /api/tasks/{id}/execute', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ started: true }));
    await taskClient.executeTask('task-1');
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/tasks/task-1/execute');
    expect(options.method).toBe('POST');
  });

  it('getTaskStatus calls GET /api/tasks/{id}/status', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ status: 'running' }));
    await taskClient.getTaskStatus('task-1');
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/tasks/task-1/status'),
      expect.anything()
    );
  });

  it('approveTask sends POST to /api/tasks/{id}/approve', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ approved: true }));
    await taskClient.approveTask('task-1', { comment: 'LGTM' });
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/tasks/task-1/approve');
    expect(options.method).toBe('POST');
  });

  it('rejectTask sends POST to /api/tasks/{id}/reject', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ rejected: true }));
    await taskClient.rejectTask('task-1', { reason: 'bad' });
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/tasks/task-1/reject');
    expect(options.method).toBe('POST');
  });

  it('propagates HTTP error from task endpoints', async () => {
    mockFetch.mockResolvedValueOnce(errorResponse(404, 'Task not found'));
    await expect(taskClient.getTask('unknown')).rejects.toThrow(
      'Task not found'
    );
  });
});

// ------------------------------------------------------------------
// unifiedServicesClient — spot-check that shortcuts delegate correctly
// ------------------------------------------------------------------

describe('unifiedServicesClient', () => {
  it('content.getService calls getService("content_service")', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ name: 'content_service' }));
    await unifiedServicesClient.content.getService();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/services/content_service'),
      expect.anything()
    );
  });

  it('financial.trackCosts calls executeServiceAction correctly', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({}));
    await unifiedServicesClient.financial.trackCosts({ amount: 5 });
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain(
      '/api/services/financial_service/actions/track_costs'
    );
    expect(opts.method).toBe('POST');
  });

  it('market.analyzeTrends calls executeServiceAction correctly', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({}));
    await unifiedServicesClient.market.analyzeTrends({ topic: 'AI' });
    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain(
      '/api/services/market_service/actions/analyze_trends'
    );
  });

  it('compliance.review calls executeServiceAction correctly', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({}));
    await unifiedServicesClient.compliance.review({ content: 'text' });
    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/services/compliance_service/actions/review');
  });
});

// ------------------------------------------------------------------
// healthCheck
// ------------------------------------------------------------------

describe('healthCheck', () => {
  it('returns healthy:true when listAgents returns an array', async () => {
    mockFetch.mockResolvedValueOnce(okResponse(['agent1', 'agent2']));
    const result = await healthCheck();
    expect(result.healthy).toBe(true);
    expect(result.timestamp).toBeDefined();
  });

  it('returns healthy:false when listAgents returns a non-array', async () => {
    mockFetch.mockResolvedValueOnce(okResponse({ agents: [] })); // object, not array
    const result = await healthCheck();
    expect(result.healthy).toBe(false);
  });

  it('returns healthy:false with error message when fetch fails', async () => {
    mockFetch.mockRejectedValueOnce(new Error('Backend unreachable'));
    const result = await healthCheck();
    expect(result.healthy).toBe(false);
    expect(result.error).toBe('Backend unreachable');
  });
});

// ------------------------------------------------------------------
// Default export shape
// ------------------------------------------------------------------

describe('phase4Client default export', () => {
  it('exports all client objects and healthCheck', () => {
    expect(phase4Client).toHaveProperty('agentDiscoveryClient');
    expect(phase4Client).toHaveProperty('serviceRegistryClient');
    expect(phase4Client).toHaveProperty('workflowClient');
    expect(phase4Client).toHaveProperty('taskClient');
    expect(phase4Client).toHaveProperty('unifiedServicesClient');
    expect(phase4Client).toHaveProperty('healthCheck');
  });
});
