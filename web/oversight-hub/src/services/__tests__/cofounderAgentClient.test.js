/**
 * cofounderAgentClient.test.js
 *
 * Unit tests for services/cofounderAgentClient.js (Issue #672 — 995 lines, zero coverage).
 *
 * Tests cover:
 * - makeRequest: success path, 204 No Content, HTTP error with detail/message/string bodies,
 *   401 throws Unauthorized (non-dev mode), timeout (AbortError), FormData omits Content-Type
 * - logout: calls /api/auth/logout; swallows errors
 * - refreshAccessToken: success path, no refresh_token returns false, error returns false
 * - getTasks: calls correct endpoint with default and custom params
 * - getTaskStatus: success, 404 returns null, other errors re-throw
 * - createBlogPost (string form): builds payload correctly, throws on empty topic
 * - createBlogPost (options form): builds payload from option aliases, throws on empty topic
 * - createTask: POSTs to /api/tasks
 * - listTasks: appends limit/offset/status
 * - getTaskById: GETs /api/tasks/{id}
 * - getTaskMetrics: GETs /api/tasks/metrics/summary
 * - sendChatMessage: POSTs with message/model/conversation_id
 * - getChatHistory: GETs history endpoint
 * - clearChatHistory: DELETEs history endpoint
 * - getAvailableModels: GETs /api/chat/models
 * - getOAuthProviders, getOAuthLoginURL, handleOAuthCallback
 * - getCurrentUser: GETs /api/auth/me
 * - getMetrics: GETs /api/metrics
 * - publishBlogDraft: PATCHes /api/tasks/{id}/publish
 * - getAgentStatus, getAgentLogs
 * - processOrchestratorRequest, getOrchestratorStatus
 * - approveOrchestratorResult, getOrchestratorTools
 *
 * global.fetch and authService are mocked; no network calls.
 */

import { vi, describe, it, expect, beforeEach } from 'vitest';

// ------------------------------------------------------------------
// Mock authService before importing the module under test
// ------------------------------------------------------------------

const { mockGetAuthToken, mockClearPersistedAuthState } = vi.hoisted(() => ({
  mockGetAuthToken: vi.fn(),
  mockClearPersistedAuthState: vi.fn(),
}));

vi.mock('../authService', async (importOriginal) => {
  const original = await importOriginal();
  return {
    ...original,
    getAuthToken: mockGetAuthToken,
    clearPersistedAuthState: mockClearPersistedAuthState,
    initializeDevToken: vi.fn().mockResolvedValue('refreshed-token'),
  };
});

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

import {
  makeRequest,
  logout,
  refreshAccessToken,
  getTasks,
  getTaskStatus,
  createBlogPost,
  createTask,
  listTasks,
  getTaskById,
  getTaskMetrics,
  sendChatMessage,
  getChatHistory,
  clearChatHistory,
  getAvailableModels,
  getOAuthProviders,
  getOAuthLoginURL,
  handleOAuthCallback,
  getCurrentUser,
  getMetrics,
  publishBlogDraft,
  getAgentStatus,
  getAgentLogs,
  processOrchestratorRequest,
  getOrchestratorStatus,
  approveOrchestratorResult,
  getOrchestratorTools,
  generateTaskImage,
} from '../cofounderAgentClient';

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

function okJson(data, status = 200) {
  return {
    ok: true,
    status,
    statusText: 'OK',
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  };
}

function errJson(status, body) {
  return {
    ok: false,
    status,
    statusText: 'Error',
    json: () => Promise.resolve(body),
    text: () =>
      Promise.resolve(typeof body === 'string' ? body : JSON.stringify(body)),
  };
}

function noContent() {
  return {
    ok: true,
    status: 204,
    statusText: 'No Content',
    json: () => Promise.reject(new Error('no body')),
    text: () => Promise.resolve(''),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // Default: no auth token
  mockGetAuthToken.mockReturnValue(null);
  localStorage.clear();
});

// ------------------------------------------------------------------
// makeRequest — core behaviour
// ------------------------------------------------------------------

describe('makeRequest', () => {
  it('returns parsed JSON on successful response', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ id: '1' }));
    const result = await makeRequest('/api/test', 'GET');
    expect(result).toEqual({ id: '1' });
  });

  it('attaches Authorization header when token available', async () => {
    mockGetAuthToken.mockReturnValue('my-token');
    mockFetch.mockResolvedValueOnce(okJson({}));
    await makeRequest('/api/test');
    const headers = mockFetch.mock.calls[0][1].headers;
    expect(headers['Authorization']).toBe('Bearer my-token');
  });

  it('omits Authorization header when no token', async () => {
    mockFetch.mockResolvedValueOnce(okJson({}));
    await makeRequest('/api/test');
    const headers = mockFetch.mock.calls[0][1].headers;
    expect(headers['Authorization']).toBeUndefined();
  });

  it('returns {success: true} for 204 No Content response', async () => {
    mockFetch.mockResolvedValueOnce(noContent());
    const result = await makeRequest('/api/test', 'DELETE');
    expect(result).toEqual({ success: true });
  });

  it('throws with detail message from error JSON body', async () => {
    mockFetch.mockResolvedValueOnce(errJson(400, { detail: 'Bad input' }));
    await expect(makeRequest('/api/test', 'POST', {})).rejects.toThrow(
      'Bad input'
    );
  });

  it('throws with message field from error JSON body', async () => {
    mockFetch.mockResolvedValueOnce(
      errJson(422, { message: 'Validation failed' })
    );
    await expect(makeRequest('/api/test', 'POST', {})).rejects.toThrow(
      'Validation failed'
    );
  });

  it('throws with HTTP status code when error body is empty string', async () => {
    mockFetch.mockResolvedValueOnce(errJson(500, ''));
    await expect(makeRequest('/api/test')).rejects.toThrow(/HTTP 500/);
  });

  it('throws Unauthorized on 401 in non-retry non-dev mode', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      json: () => Promise.resolve({ detail: 'invalid token' }),
      text: () => Promise.resolve(''),
    });
    // Ensure NODE_ENV != development so retry is skipped
    const originalEnv = process.env.NODE_ENV;
    process.env.NODE_ENV = 'test';
    await expect(makeRequest('/api/tasks', 'GET', null, false)).rejects.toThrow(
      /Unauthorized/
    );
    process.env.NODE_ENV = originalEnv;
  });

  it('throws timeout error when AbortController fires', async () => {
    // Simulate an AbortError by rejecting with DOMException name=AbortError
    const abortError = new Error('The user aborted a request.');
    abortError.name = 'AbortError';
    mockFetch.mockRejectedValueOnce(abortError);
    await expect(
      makeRequest('/api/slow', 'GET', null, false, null, 100)
    ).rejects.toThrow(/timeout/i);
  });

  it('serializes JSON body for non-FormData payloads', async () => {
    mockFetch.mockResolvedValueOnce(okJson({}));
    await makeRequest('/api/test', 'POST', { foo: 'bar' });
    const body = mockFetch.mock.calls[0][1].body;
    expect(JSON.parse(body)).toEqual({ foo: 'bar' });
  });

  it('does not set Content-Type header for FormData body', async () => {
    mockFetch.mockResolvedValueOnce(okJson({}));
    const form = new FormData();
    form.append('file', new Blob(['data']), 'test.txt');
    await makeRequest('/api/upload', 'POST', form);
    const headers = mockFetch.mock.calls[0][1].headers;
    expect(headers['Content-Type']).toBeUndefined();
  });

  it('calls onUnauthorized callback on 401', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    });
    const onUnauth = vi.fn();
    process.env.NODE_ENV = 'test';
    try {
      await makeRequest('/api/tasks', 'GET', null, false, onUnauth);
    } catch {
      // expected
    }
    expect(onUnauth).toHaveBeenCalled();
  });
});

// ------------------------------------------------------------------
// logout
// ------------------------------------------------------------------

describe('logout', () => {
  it('calls /api/auth/logout via POST', async () => {
    mockFetch.mockResolvedValueOnce(okJson({}));
    await logout();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/auth/logout'),
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('does not throw even when API call fails', async () => {
    mockFetch.mockRejectedValueOnce(new Error('Network error'));
    await expect(logout()).resolves.toBeUndefined();
  });
});

// ------------------------------------------------------------------
// refreshAccessToken
// ------------------------------------------------------------------

describe('refreshAccessToken', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('returns false when no refresh_token stored', async () => {
    const result = await refreshAccessToken();
    expect(result).toBe(false);
  });

  it('returns true and stores new token on success', async () => {
    localStorage.setItem('refresh_token', 'ref-tok');
    mockFetch.mockResolvedValueOnce(okJson({ access_token: 'new-tok' }));
    const result = await refreshAccessToken();
    expect(result).toBe(true);
    expect(localStorage.getItem('auth_token')).toBe('new-tok');
  });

  it('returns false when response has no access_token', async () => {
    localStorage.setItem('refresh_token', 'ref-tok');
    mockFetch.mockResolvedValueOnce(okJson({}));
    const result = await refreshAccessToken();
    expect(result).toBe(false);
  });

  it('returns false on fetch error', async () => {
    localStorage.setItem('refresh_token', 'ref-tok');
    mockFetch.mockRejectedValueOnce(new Error('Server down'));
    const result = await refreshAccessToken();
    expect(result).toBe(false);
  });
});

// ------------------------------------------------------------------
// getTasks
// ------------------------------------------------------------------

describe('getTasks', () => {
  it('calls /api/tasks with default limit and offset', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ tasks: [] }));
    await getTasks();
    const url = mockFetch.mock.calls[0][0];
    expect(url).toContain('/api/tasks');
    expect(url).toContain('limit=50');
    expect(url).toContain('offset=0');
  });

  it('uses provided limit and offset', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ tasks: [] }));
    await getTasks(20, 10);
    const url = mockFetch.mock.calls[0][0];
    expect(url).toContain('limit=20');
    expect(url).toContain('offset=10');
  });
});

// ------------------------------------------------------------------
// getTaskStatus
// ------------------------------------------------------------------

describe('getTaskStatus', () => {
  it('returns task data on success', async () => {
    mockFetch.mockResolvedValueOnce(
      okJson({ id: 'task-1', status: 'completed' })
    );
    const result = await getTaskStatus('task-1');
    expect(result.id).toBe('task-1');
  });

  it('returns null when task is not found (404)', async () => {
    mockFetch.mockResolvedValueOnce(errJson(404, { detail: 'Not found' }));
    const result = await getTaskStatus('unknown');
    expect(result).toBeNull();
  });

  it('re-throws non-404 errors', async () => {
    mockFetch.mockResolvedValueOnce(errJson(500, { detail: 'Server error' }));
    await expect(getTaskStatus('task-1')).rejects.toThrow();
  });
});

// ------------------------------------------------------------------
// createBlogPost — string form
// ------------------------------------------------------------------

describe('createBlogPost (string topic)', () => {
  it('builds payload with capitalized task_name', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ id: 'task-1' }));
    await createBlogPost(
      'ai trends',
      'AI',
      'developers',
      'tech',
      {},
      'balanced',
      0.5
    );
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.task_name).toBe('Blog Post: Ai Trends');
    expect(body.topic).toBe('ai trends');
    expect(body.primary_keyword).toBe('AI');
    expect(body.target_audience).toBe('developers');
    expect(body.category).toBe('tech');
  });

  it('throws when topic is empty string', async () => {
    await expect(createBlogPost('  ')).rejects.toThrow('Topic is required');
  });
});

// ------------------------------------------------------------------
// createBlogPost — options form
// ------------------------------------------------------------------

describe('createBlogPost (options object)', () => {
  it('builds payload from options with camelCase aliases', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ id: 'task-2' }));
    await createBlogPost({
      topic: 'machine learning',
      primaryKeyword: 'ML',
      targetAudience: 'students',
      category: 'education',
    });
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.topic).toBe('machine learning');
    expect(body.primary_keyword).toBe('ML');
    expect(body.target_audience).toBe('students');
  });

  it('throws when options.topic is empty', async () => {
    await expect(createBlogPost({ topic: '' })).rejects.toThrow(
      'Topic is required'
    );
  });
});

// ------------------------------------------------------------------
// createTask
// ------------------------------------------------------------------

describe('createTask', () => {
  it('sends POST to /api/tasks with provided data', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ id: 'task-3' }));
    const result = await createTask({ task_name: 'My Task', topic: 'Testing' });
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/tasks');
    expect(opts.method).toBe('POST');
    expect(result.id).toBe('task-3');
  });
});

// ------------------------------------------------------------------
// listTasks
// ------------------------------------------------------------------

describe('listTasks', () => {
  it('calls /api/tasks with default limit and offset', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ tasks: [] }));
    await listTasks();
    const url = mockFetch.mock.calls[0][0];
    expect(url).toContain('limit=20');
    expect(url).toContain('offset=0');
  });

  it('appends status filter when provided', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ tasks: [] }));
    await listTasks(10, 0, 'pending');
    const url = mockFetch.mock.calls[0][0];
    expect(url).toContain('status=pending');
  });

  it('omits status when not provided', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ tasks: [] }));
    await listTasks(10, 0, null);
    const url = mockFetch.mock.calls[0][0];
    expect(url).not.toContain('status=');
  });
});

// ------------------------------------------------------------------
// getTaskById
// ------------------------------------------------------------------

describe('getTaskById', () => {
  it('calls /api/tasks/{id} with GET', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ id: 'task-1' }));
    const result = await getTaskById('task-1');
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/tasks/task-1'),
      expect.anything()
    );
    expect(result.id).toBe('task-1');
  });
});

// ------------------------------------------------------------------
// getTaskMetrics
// ------------------------------------------------------------------

describe('getTaskMetrics', () => {
  it('calls /api/tasks/metrics/summary', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ total: 100 }));
    await getTaskMetrics();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/tasks/metrics/summary'),
      expect.anything()
    );
  });
});

// ------------------------------------------------------------------
// sendChatMessage
// ------------------------------------------------------------------

describe('sendChatMessage', () => {
  it('sends POST to /api/chat with message/model/conversation_id', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ response: 'Hi there' }));
    await sendChatMessage('Hello', 'claude', 'conv-1');
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.message).toBe('Hello');
    expect(body.model).toBe('claude');
    expect(body.conversation_id).toBe('conv-1');
  });

  it('uses default model and conversationId when not provided', async () => {
    mockFetch.mockResolvedValueOnce(okJson({}));
    await sendChatMessage('Test');
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.model).toBe('openai-gpt4');
    expect(body.conversation_id).toBe('default');
  });
});

// ------------------------------------------------------------------
// getChatHistory / clearChatHistory / getAvailableModels
// ------------------------------------------------------------------

describe('getChatHistory', () => {
  it('GETs /api/chat/history/{conversationId}', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ messages: [] }));
    await getChatHistory('conv-1');
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/chat/history/conv-1'),
      expect.anything()
    );
  });

  it('uses "default" when no conversationId given', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ messages: [] }));
    await getChatHistory();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/chat/history/default'),
      expect.anything()
    );
  });
});

describe('clearChatHistory', () => {
  it('DELETEs /api/chat/history/{id}', async () => {
    mockFetch.mockResolvedValueOnce(noContent());
    await clearChatHistory('conv-1');
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/chat/history/conv-1');
    expect(opts.method).toBe('DELETE');
  });
});

describe('getAvailableModels', () => {
  it('GETs /api/chat/models', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ models: [] }));
    await getAvailableModels();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/chat/models'),
      expect.anything()
    );
  });
});

// ------------------------------------------------------------------
// OAuth
// ------------------------------------------------------------------

describe('getOAuthProviders', () => {
  it('calls /api/auth/providers', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ providers: ['github'] }));
    await getOAuthProviders();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/auth/providers'),
      expect.anything()
    );
  });
});

describe('getOAuthLoginURL', () => {
  it('returns login_url from response', async () => {
    mockFetch.mockResolvedValueOnce(
      okJson({ login_url: 'https://github.com/oauth' })
    );
    const url = await getOAuthLoginURL('github');
    expect(url).toBe('https://github.com/oauth');
  });
});

describe('handleOAuthCallback', () => {
  it('throws when code is missing', async () => {
    await expect(handleOAuthCallback('github', '', 'state')).rejects.toThrow(
      /Authorization code missing/
    );
  });

  it('calls provider callback endpoint with code and state', async () => {
    mockFetch.mockResolvedValueOnce(
      okJson({ token: 'tok', user: { id: '1' } })
    );
    await handleOAuthCallback('github', 'gh-code', 'csrf-state');
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.code).toBe('gh-code');
    expect(body.state).toBe('csrf-state');
  });
});

// ------------------------------------------------------------------
// getCurrentUser / getMetrics / publishBlogDraft
// ------------------------------------------------------------------

describe('getCurrentUser', () => {
  it('calls /api/auth/me', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ user: { id: '1' } }));
    await getCurrentUser();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/auth/me'),
      expect.anything()
    );
  });
});

describe('getMetrics', () => {
  it('calls /api/metrics', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ cpu: 50 }));
    await getMetrics();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/metrics'),
      expect.anything()
    );
  });
});

describe('publishBlogDraft', () => {
  it('PATCHes /api/tasks/{id}/publish with environment', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ published: true }));
    await publishBlogDraft('post-1', 'staging');
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/tasks/post-1/publish');
    expect(opts.method).toBe('PATCH');
    const body = JSON.parse(opts.body);
    expect(body.environment).toBe('staging');
    expect(body.status).toBe('published');
  });
});

// ------------------------------------------------------------------
// Agent / Orchestrator endpoints
// ------------------------------------------------------------------

describe('getAgentStatus', () => {
  it('calls /api/agents/{id}/status', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ status: 'idle' }));
    await getAgentStatus('content_agent');
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/agents/content_agent/status'),
      expect.anything()
    );
  });
});

describe('getAgentLogs', () => {
  it('calls /api/agents/{id}/logs with limit', async () => {
    mockFetch.mockResolvedValueOnce(okJson([]));
    await getAgentLogs('content_agent', 50);
    const url = mockFetch.mock.calls[0][0];
    expect(url).toContain('/api/agents/content_agent/logs');
    expect(url).toContain('limit=50');
  });
});

describe('processOrchestratorRequest', () => {
  it('POSTs to /api/orchestrator/process', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ task_id: 'orch-1' }));
    await processOrchestratorRequest(
      'Do X',
      { revenue: 100 },
      { speed: 'fast' }
    );
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/orchestrator/process');
    expect(opts.method).toBe('POST');
    const body = JSON.parse(opts.body);
    expect(body.request).toBe('Do X');
    expect(body.business_metrics).toEqual({ revenue: 100 });
  });
});

describe('getOrchestratorStatus', () => {
  it('GETs /api/orchestrator/status/{id}', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ status: 'running' }));
    await getOrchestratorStatus('orch-1');
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/orchestrator/status/orch-1'),
      expect.anything()
    );
  });
});

describe('approveOrchestratorResult', () => {
  it('POSTs to /api/orchestrator/approve/{id} with action', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ approved: true }));
    await approveOrchestratorResult('orch-1', { action: 'approve' });
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/orchestrator/approve/orch-1');
    expect(opts.method).toBe('POST');
  });
});

describe('getOrchestratorTools', () => {
  it('GETs /api/orchestrator/tools', async () => {
    mockFetch.mockResolvedValueOnce(okJson([]));
    await getOrchestratorTools();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/orchestrator/tools'),
      expect.anything()
    );
  });
});

describe('generateTaskImage', () => {
  it('POSTs to /api/tasks/{id}/generate-image with options', async () => {
    mockFetch.mockResolvedValueOnce(
      okJson({ image_url: 'http://img.example.com/1.png' })
    );
    const result = await generateTaskImage('task-1', {
      source: 'dall-e',
      topic: 'AI',
    });
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/tasks/task-1/generate-image');
    expect(opts.method).toBe('POST');
    expect(result.image_url).toBeDefined();
  });
});
