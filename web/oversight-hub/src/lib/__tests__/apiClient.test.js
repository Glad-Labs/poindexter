/**
 * apiClient.test.js
 *
 * Unit tests for lib/apiClient.js — the central HTTP client.
 *
 * Covers:
 * - Request interceptor: auth token injection from Zustand persist storage and
 *   fallback to localStorage auth_token
 * - Response interceptor: 401 triggers clearPersistedAuthState + redirect
 * - Endpoint wrappers: listTasks, createTask, getTask, updateTask, pauseTask,
 *   resumeTask, cancelTask, listPosts, createPost, getPost, updatePost,
 *   publishPost, archivePost, deletePost, listCategories, listTags, getHealth,
 *   getMetrics, getTaskMetrics, getContentMetrics, listModels, testModel,
 *   getModelStatus, generateContent, getTaskResult, previewContent,
 *   publishTaskAsPost, getTasksBatch, exportTasks, getPostBySlug
 * - formatApiError: detail, statusText, message, fallback
 * - isRecoverableError: 5xx, network, 4xx
 * - retryWithBackoff: success, non-recoverable, exhaustion
 * - Default export object has all methods
 *
 * Closes #1018.
 */

import { vi } from 'vitest';

// ---------------------------------------------------------------------------
// Mocks (hoisted)
// ---------------------------------------------------------------------------

const {
  mockClearPersistedAuthState,
  mockLogErrorToSentry,
  mockGet,
  mockPost,
  mockPatch,
  mockDelete,
  requestInterceptors,
  responseInterceptors,
} = vi.hoisted(() => ({
  mockClearPersistedAuthState: vi.fn(),
  mockLogErrorToSentry: vi.fn(),
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockPatch: vi.fn(),
  mockDelete: vi.fn(),
  requestInterceptors: [],
  responseInterceptors: [],
}));

vi.mock('../../services/authService', () => ({
  clearPersistedAuthState: mockClearPersistedAuthState,
}));

vi.mock('../../services/errorLoggingService', () => ({
  logErrorToSentry: mockLogErrorToSentry,
}));

vi.mock('axios', () => {
  return {
    default: {
      create: vi.fn(() => {
        const instance = {
          get: mockGet,
          post: mockPost,
          patch: mockPatch,
          delete: mockDelete,
          interceptors: {
            request: {
              use: (fulfilled, rejected) => {
                requestInterceptors.push({ fulfilled, rejected });
              },
            },
            response: {
              use: (fulfilled, rejected) => {
                responseInterceptors.push({ fulfilled, rejected });
              },
            },
          },
        };
        return instance;
      }),
    },
  };
});

// Import after mocks are set up
import apiClientMethods, {
  listTasks,
  createTask,
  getTask,
  updateTask,
  pauseTask,
  resumeTask,
  cancelTask,
  listPosts,
  createPost,
  getPost,
  getPostBySlug,
  updatePost,
  publishPost,
  archivePost,
  deletePost,
  listCategories,
  listTags,
  getHealth,
  getMetrics,
  getTaskMetrics,
  getContentMetrics,
  listModels,
  testModel,
  getModelStatus,
  generateContent,
  getTaskResult,
  previewContent,
  publishTaskAsPost,
  getTasksBatch,
  exportTasks,
  formatApiError,
  isRecoverableError,
  retryWithBackoff,
} from '../../lib/apiClient';

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  mockGet.mockResolvedValue({ data: { ok: true } });
  mockPost.mockResolvedValue({ data: { ok: true } });
  mockPatch.mockResolvedValue({ data: { ok: true } });
  mockDelete.mockResolvedValue({ data: { ok: true } });
});

// ---------------------------------------------------------------------------
// Request interceptor
// ---------------------------------------------------------------------------

describe('request interceptor', () => {
  it('adds Bearer token from Zustand persist storage', () => {
    localStorage.setItem(
      'oversight-hub-storage',
      JSON.stringify({ state: { accessToken: 'ztok-123' } })
    );

    const interceptor = requestInterceptors[0]?.fulfilled;
    expect(interceptor).toBeDefined();

    const config = { headers: {} };
    const result = interceptor(config);
    expect(result.headers.Authorization).toBe('Bearer ztok-123');
  });

  it('falls back to auth_token key in localStorage', () => {
    localStorage.setItem('auth_token', 'legacy-tok');

    const interceptor = requestInterceptors[0]?.fulfilled;
    const config = { headers: {} };
    const result = interceptor(config);
    expect(result.headers.Authorization).toBe('Bearer legacy-tok');
  });

  it('does not set Authorization when no token is found', () => {
    const interceptor = requestInterceptors[0]?.fulfilled;
    const config = { headers: {} };
    const result = interceptor(config);
    expect(result.headers.Authorization).toBeUndefined();
  });

  it('prefers Zustand token over legacy localStorage', () => {
    localStorage.setItem(
      'oversight-hub-storage',
      JSON.stringify({ state: { accessToken: 'zustand-tok' } })
    );
    localStorage.setItem('auth_token', 'legacy-tok');

    const interceptor = requestInterceptors[0]?.fulfilled;
    const config = { headers: {} };
    const result = interceptor(config);
    expect(result.headers.Authorization).toBe('Bearer zustand-tok');
  });

  it('handles malformed JSON in persist storage gracefully', () => {
    localStorage.setItem('oversight-hub-storage', 'not-json');
    localStorage.setItem('auth_token', 'fallback-tok');

    const interceptor = requestInterceptors[0]?.fulfilled;
    const config = { headers: {} };
    const result = interceptor(config);
    // Should fall back to legacy token
    expect(result.headers.Authorization).toBe('Bearer fallback-tok');
  });
});

// ---------------------------------------------------------------------------
// Response interceptor
// ---------------------------------------------------------------------------

describe('response interceptor', () => {
  // Save and restore window.location safely using Object.defineProperty
  let origLocationDescriptor;

  beforeEach(() => {
    origLocationDescriptor = Object.getOwnPropertyDescriptor(
      window,
      'location'
    );
    Object.defineProperty(window, 'location', {
      configurable: true,
      writable: true,
      value: {
        href: '',
        assign: vi.fn(),
        replace: vi.fn(),
        reload: vi.fn(),
      },
    });
  });

  afterEach(() => {
    if (origLocationDescriptor) {
      Object.defineProperty(window, 'location', origLocationDescriptor);
    }
  });

  it('passes through successful responses', () => {
    const interceptor = responseInterceptors[0]?.fulfilled;
    const response = { status: 200, data: { ok: true } };
    expect(interceptor(response)).toEqual(response);
  });

  it('clears auth and redirects on 401', async () => {
    const interceptor = responseInterceptors[0]?.rejected;
    const error = { response: { status: 401 } };

    await expect(interceptor(error)).rejects.toEqual(error);
    expect(mockClearPersistedAuthState).toHaveBeenCalled();
    expect(window.location.href).toBe('/login');
  });

  it('does not redirect on non-401 errors', async () => {
    const interceptor = responseInterceptors[0]?.rejected;
    const error = { response: { status: 500 } };

    await expect(interceptor(error)).rejects.toEqual(error);
    expect(mockClearPersistedAuthState).not.toHaveBeenCalled();
    expect(window.location.href).toBe('');
  });
});

// ---------------------------------------------------------------------------
// Task endpoints
// ---------------------------------------------------------------------------

describe('task endpoints', () => {
  it('listTasks calls GET /api/tasks with params', async () => {
    await listTasks(5, 20, 'pending');
    expect(mockGet).toHaveBeenCalledWith(
      expect.stringContaining('/api/tasks?')
    );
    const url = mockGet.mock.calls[0][0];
    expect(url).toContain('skip=5');
    expect(url).toContain('limit=20');
    expect(url).toContain('status=pending');
  });

  it('listTasks omits status param when null', async () => {
    await listTasks(0, 10, null);
    const url = mockGet.mock.calls[0][0];
    expect(url).not.toContain('status=');
  });

  it('createTask calls POST /api/tasks', async () => {
    const data = { task_name: 'Test', topic: 'AI' };
    await createTask(data);
    expect(mockPost).toHaveBeenCalledWith('/api/tasks', data);
  });

  it('getTask calls GET /api/tasks/{id}', async () => {
    await getTask('task-123');
    expect(mockGet).toHaveBeenCalledWith('/api/tasks/task-123');
  });

  it('updateTask calls PATCH /api/tasks/{id}', async () => {
    await updateTask('task-123', { status: 'completed' });
    expect(mockPatch).toHaveBeenCalledWith('/api/tasks/task-123', {
      status: 'completed',
    });
  });

  it('pauseTask delegates to updateTask with paused status', async () => {
    await pauseTask('task-1');
    expect(mockPatch).toHaveBeenCalledWith('/api/tasks/task-1', {
      status: 'paused',
    });
  });

  it('resumeTask delegates to updateTask with in_progress status', async () => {
    await resumeTask('task-1');
    expect(mockPatch).toHaveBeenCalledWith('/api/tasks/task-1', {
      status: 'in_progress',
    });
  });

  it('cancelTask delegates to updateTask with cancelled status', async () => {
    await cancelTask('task-1');
    expect(mockPatch).toHaveBeenCalledWith('/api/tasks/task-1', {
      status: 'cancelled',
    });
  });

  it('listTasks logs error to Sentry on failure', async () => {
    const err = new Error('network');
    mockGet.mockRejectedValue(err);
    await expect(listTasks()).rejects.toThrow('network');
    expect(mockLogErrorToSentry).toHaveBeenCalledWith(
      err,
      expect.objectContaining({
        context: expect.stringContaining('Error listing tasks'),
      })
    );
  });
});

// ---------------------------------------------------------------------------
// Post endpoints
// ---------------------------------------------------------------------------

describe('post endpoints', () => {
  it('listPosts calls GET /api/posts', async () => {
    await listPosts();
    expect(mockGet).toHaveBeenCalledWith(
      '/api/posts',
      expect.objectContaining({
        params: { skip: 0, limit: 20, published_only: true },
      })
    );
  });

  it('createPost calls POST /api/posts', async () => {
    await createPost({ title: 'Test' });
    expect(mockPost).toHaveBeenCalledWith('/api/posts', { title: 'Test' });
  });

  it('getPost calls GET /api/posts/{id}', async () => {
    await getPost('p1');
    expect(mockGet).toHaveBeenCalledWith('/api/posts/p1');
  });

  it('getPostBySlug calls GET /api/posts with slug param', async () => {
    mockGet.mockResolvedValue({ data: { data: [{ slug: 'test' }] } });
    const result = await getPostBySlug('test');
    expect(mockGet).toHaveBeenCalledWith('/api/posts', {
      params: { slug: 'test' },
    });
    expect(result).toEqual({ slug: 'test' });
  });

  it('getPostBySlug returns null when no data', async () => {
    mockGet.mockResolvedValue({ data: {} });
    const result = await getPostBySlug('missing');
    expect(result).toBeNull();
  });

  it('updatePost calls PATCH /api/posts/{id}', async () => {
    await updatePost('p1', { title: 'Updated' });
    expect(mockPatch).toHaveBeenCalledWith('/api/posts/p1', {
      title: 'Updated',
    });
  });

  it('publishPost calls updatePost with published status', async () => {
    await publishPost('p1');
    expect(mockPatch).toHaveBeenCalledWith(
      '/api/posts/p1',
      expect.objectContaining({ status: 'published' })
    );
  });

  it('archivePost calls updatePost with archived status', async () => {
    await archivePost('p1');
    expect(mockPatch).toHaveBeenCalledWith('/api/posts/p1', {
      status: 'archived',
    });
  });

  it('deletePost calls DELETE /api/posts/{id}', async () => {
    await deletePost('p1');
    expect(mockDelete).toHaveBeenCalledWith('/api/posts/p1');
  });
});

// ---------------------------------------------------------------------------
// Category & tag endpoints
// ---------------------------------------------------------------------------

describe('category & tag endpoints', () => {
  it('listCategories calls GET /api/categories', async () => {
    await listCategories();
    expect(mockGet).toHaveBeenCalledWith('/api/categories');
  });

  it('listTags calls GET /api/tags', async () => {
    await listTags();
    expect(mockGet).toHaveBeenCalledWith('/api/tags');
  });
});

// ---------------------------------------------------------------------------
// System endpoints
// ---------------------------------------------------------------------------

describe('system endpoints', () => {
  it('getHealth calls GET /api/health', async () => {
    await getHealth();
    expect(mockGet).toHaveBeenCalledWith('/api/health');
  });

  it('getMetrics calls GET /api/metrics', async () => {
    await getMetrics();
    expect(mockGet).toHaveBeenCalledWith('/api/metrics');
  });

  it('getTaskMetrics calls GET /api/tasks/metrics', async () => {
    await getTaskMetrics();
    expect(mockGet).toHaveBeenCalledWith('/api/tasks/metrics');
  });

  it('getContentMetrics calls GET /api/metrics', async () => {
    await getContentMetrics();
    expect(mockGet).toHaveBeenCalledWith('/api/metrics');
  });
});

// ---------------------------------------------------------------------------
// Model endpoints
// ---------------------------------------------------------------------------

describe('model endpoints', () => {
  it('listModels calls GET /api/models', async () => {
    await listModels();
    expect(mockGet).toHaveBeenCalledWith('/api/models');
  });

  it('testModel calls POST /api/models/test', async () => {
    await testModel('openai', 'gpt-4');
    expect(mockPost).toHaveBeenCalledWith('/api/models/test', {
      provider: 'openai',
      model: 'gpt-4',
    });
  });

  it('getModelStatus calls GET /api/models/status', async () => {
    await getModelStatus();
    expect(mockGet).toHaveBeenCalledWith('/api/models/status');
  });
});

// ---------------------------------------------------------------------------
// Content generation endpoints
// ---------------------------------------------------------------------------

describe('content generation endpoints', () => {
  it('generateContent calls POST /api/tasks/{id}/generate', async () => {
    await generateContent('t1');
    expect(mockPost).toHaveBeenCalledWith('/api/tasks/t1/generate');
  });

  it('getTaskResult calls GET /api/tasks/{id}/result', async () => {
    await getTaskResult('t1');
    expect(mockGet).toHaveBeenCalledWith('/api/tasks/t1/result');
  });

  it('previewContent calls GET /api/tasks/{id}/preview', async () => {
    await previewContent('t1');
    expect(mockGet).toHaveBeenCalledWith('/api/tasks/t1/preview');
  });

  it('publishTaskAsPost calls POST /api/tasks/{id}/publish', async () => {
    await publishTaskAsPost('t1', { category: 'tech' });
    expect(mockPost).toHaveBeenCalledWith('/api/tasks/t1/publish', {
      category: 'tech',
    });
  });
});

// ---------------------------------------------------------------------------
// Batch endpoints
// ---------------------------------------------------------------------------

describe('batch endpoints', () => {
  it('getTasksBatch calls POST /api/tasks/batch', async () => {
    await getTasksBatch(['t1', 't2']);
    expect(mockPost).toHaveBeenCalledWith('/api/tasks/batch', {
      ids: ['t1', 't2'],
    });
  });

  it('exportTasks calls GET /api/tasks/export with format param', async () => {
    await exportTasks({ status: 'completed' }, 'json');
    expect(mockGet).toHaveBeenCalledWith('/api/tasks/export', {
      params: { format: 'json', status: 'completed' },
      responseType: 'blob',
    });
  });
});

// ---------------------------------------------------------------------------
// formatApiError
// ---------------------------------------------------------------------------

describe('formatApiError', () => {
  it('returns response.data.detail when present', () => {
    const err = {
      response: { data: { detail: 'Not found' }, statusText: 'Not Found' },
    };
    expect(formatApiError(err)).toBe('Not found');
  });

  it('falls back to statusText', () => {
    const err = { response: { data: {}, statusText: 'Bad Gateway' } };
    expect(formatApiError(err)).toBe('Bad Gateway');
  });

  it('falls back to error.message', () => {
    const err = { message: 'Network Error' };
    expect(formatApiError(err)).toBe('Network Error');
  });

  it('returns generic fallback when nothing else is available', () => {
    expect(formatApiError({})).toBe('An unexpected error occurred');
  });
});

// ---------------------------------------------------------------------------
// isRecoverableError
// ---------------------------------------------------------------------------

describe('isRecoverableError', () => {
  it('returns true for 500 error', () => {
    expect(isRecoverableError({ response: { status: 500 } })).toBe(true);
  });

  it('returns true for 503 error', () => {
    expect(isRecoverableError({ response: { status: 503 } })).toBe(true);
  });

  it('returns false for 400 error', () => {
    expect(isRecoverableError({ response: { status: 400 } })).toBe(false);
  });

  it('returns false for 404 error', () => {
    expect(isRecoverableError({ response: { status: 404 } })).toBe(false);
  });

  it('returns true for network error (no status)', () => {
    expect(isRecoverableError({})).toBe(true);
  });

  it('returns true for ECONNABORTED', () => {
    expect(
      isRecoverableError({ code: 'ECONNABORTED', response: { status: 408 } })
    ).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// retryWithBackoff
// ---------------------------------------------------------------------------

describe('retryWithBackoff', () => {
  // Use fake timers to avoid real exponential backoff delays (1s, 2s, ...)
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns result on first success', async () => {
    const fn = vi.fn().mockResolvedValue('ok');
    const result = await retryWithBackoff(fn, 3);
    expect(result).toBe('ok');
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('retries on recoverable error and succeeds', async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce({ response: { status: 500 } })
      .mockResolvedValueOnce('recovered');

    const promise = retryWithBackoff(fn, 3);
    // Run all pending timers (the exponential backoff setTimeout)
    await vi.runAllTimersAsync();
    const result = await promise;
    expect(result).toBe('recovered');
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('throws immediately on non-recoverable error', async () => {
    const err = { response: { status: 400 } };
    const fn = vi.fn().mockRejectedValue(err);

    await expect(retryWithBackoff(fn, 3)).rejects.toEqual(err);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('throws after exhausting all retries', async () => {
    const err = { response: { status: 500 } };
    const fn = vi.fn().mockRejectedValue(err);

    // Attach the catch handler immediately to avoid unhandled rejection
    const promise = retryWithBackoff(fn, 2).catch((e) => e);
    // Run all pending timers (backoff delays between retries)
    await vi.runAllTimersAsync();
    const result = await promise;
    expect(result).toEqual(err);
    expect(fn).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// Default export
// ---------------------------------------------------------------------------

describe('default export', () => {
  it('contains all expected method names', () => {
    const expected = [
      'listTasks',
      'createTask',
      'getTask',
      'updateTask',
      'pauseTask',
      'resumeTask',
      'cancelTask',
      'listPosts',
      'createPost',
      'getPost',
      'getPostBySlug',
      'updatePost',
      'publishPost',
      'archivePost',
      'deletePost',
      'listCategories',
      'listTags',
      'getHealth',
      'getMetrics',
      'getTaskMetrics',
      'getContentMetrics',
      'listModels',
      'testModel',
      'getModelStatus',
      'generateContent',
      'getTaskResult',
      'previewContent',
      'publishTaskAsPost',
      'getTasksBatch',
      'exportTasks',
      'formatApiError',
      'isRecoverableError',
      'retryWithBackoff',
    ];

    for (const name of expected) {
      expect(typeof apiClientMethods[name]).toBe('function');
    }
  });
});
