/**
 * naturalLanguageComposerService.test.js
 *
 * Unit tests for services/naturalLanguageComposerService.js.
 *
 * Tests cover:
 * - composeTaskFromNaturalLanguage — success, options (autoExecute/saveTask), not-ok response throws,
 *   JSON error detail extracted, network error re-wrapped
 * - composeAndExecuteTask — success, not-ok response throws
 * - detectTaskRequest — short message returns false, action verbs return true, question patterns return true,
 *   domain words return true, confidence calculation, multiple matches increase confidence
 * - formatCompositionResult — success:false shows error, no task_definition shows explanation,
 *   full result with steps, execution_id appended
 *
 * global.fetch is mocked; no network calls.
 */

import { vi } from 'vitest';

vi.mock('../config/apiConfig', () => ({
  getApiUrl: () => 'http://localhost:8000',
}));

vi.mock('@/lib/logger', () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

import {
  composeTaskFromNaturalLanguage,
  composeAndExecuteTask,
  detectTaskRequest,
  formatCompositionResult,
} from '../naturalLanguageComposerService';

const _mockFetch = ({
  ok = true,
  json = {},
  status = 200,
  statusText = 'OK',
} = {}) => {
  global.fetch = vi.fn().mockResolvedValue({
    ok,
    status,
    statusText,
    json: async () => json,
  });
};

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// composeTaskFromNaturalLanguage
// ---------------------------------------------------------------------------

describe('composeTaskFromNaturalLanguage', () => {
  it('returns composition result on success', async () => {
    _mockFetch({
      json: { success: true, task_definition: { name: 'Blog Post' } },
    });
    const result = await composeTaskFromNaturalLanguage(
      'Write a blog post about AI'
    );
    expect(result.success).toBe(true);
  });

  it('sends request body with defaults autoExecute=false, saveTask=true', async () => {
    _mockFetch({ json: {} });
    await composeTaskFromNaturalLanguage('Create a blog post');
    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.request).toBe('Create a blog post');
    expect(body.auto_execute).toBe(false);
    expect(body.save_task).toBe(true);
  });

  it('passes autoExecute and saveTask options to request body', async () => {
    _mockFetch({ json: {} });
    await composeTaskFromNaturalLanguage('Write article', {
      autoExecute: true,
      saveTask: false,
    });
    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.auto_execute).toBe(true);
    expect(body.save_task).toBe(false);
  });

  it('throws with detail when response not ok and has detail field', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      statusText: 'Unprocessable Entity',
      json: async () => ({ detail: 'Validation error' }),
    });
    await expect(composeTaskFromNaturalLanguage('Request')).rejects.toThrow(
      'Failed to compose task: Validation error'
    );
  });

  it('throws with error when response not ok and has error field', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: async () => ({ error: 'Server crashed' }),
    });
    await expect(composeTaskFromNaturalLanguage('Request')).rejects.toThrow(
      'Failed to compose task: Server crashed'
    );
  });

  it('throws with HTTP status when response not ok and no detail/error', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      statusText: 'Service Unavailable',
      json: async () => ({}),
    });
    await expect(composeTaskFromNaturalLanguage('Request')).rejects.toThrow(
      'Failed to compose task: HTTP 503: Service Unavailable'
    );
  });

  it('re-wraps network error', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('Network failure'));
    await expect(composeTaskFromNaturalLanguage('Request')).rejects.toThrow(
      'Failed to compose task: Network failure'
    );
  });

  it('calls POST /api/tasks/capability/compose-from-natural-language', async () => {
    _mockFetch({ json: { success: true } });
    await composeTaskFromNaturalLanguage('Write email');
    expect(global.fetch.mock.calls[0][0]).toContain(
      '/api/tasks/capability/compose-from-natural-language'
    );
    expect(global.fetch.mock.calls[0][1].method).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// composeAndExecuteTask
// ---------------------------------------------------------------------------

describe('composeAndExecuteTask', () => {
  it('returns execution result on success', async () => {
    _mockFetch({ json: { success: true, execution_id: 'exec-1' } });
    const result = await composeAndExecuteTask('Generate blog post');
    expect(result.execution_id).toBe('exec-1');
  });

  it('sends auto_execute: true always', async () => {
    _mockFetch({ json: {} });
    await composeAndExecuteTask('Generate newsletter');
    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.auto_execute).toBe(true);
  });

  it('saveTask defaults to true', async () => {
    _mockFetch({ json: {} });
    await composeAndExecuteTask('Generate newsletter');
    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.save_task).toBe(true);
  });

  it('saveTask can be overridden to false', async () => {
    _mockFetch({ json: {} });
    await composeAndExecuteTask('Generate newsletter', { saveTask: false });
    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.save_task).toBe(false);
  });

  it('throws wrapped error when response not ok', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      statusText: 'Unavailable',
      json: async () => ({ detail: 'LLM unreachable' }),
    });
    await expect(composeAndExecuteTask('Request')).rejects.toThrow(
      'Failed to execute task'
    );
  });

  it('calls POST /api/tasks/capability/compose-and-execute', async () => {
    _mockFetch({ json: {} });
    await composeAndExecuteTask('Do something');
    expect(global.fetch.mock.calls[0][0]).toContain(
      '/api/tasks/capability/compose-and-execute'
    );
  });
});

// ---------------------------------------------------------------------------
// detectTaskRequest
// ---------------------------------------------------------------------------

describe('detectTaskRequest', () => {
  it('returns isTaskRequest: false for short messages', () => {
    const result = detectTaskRequest('Hi');
    expect(result.isTaskRequest).toBe(false);
    expect(result.confidence).toBe(0);
  });

  it('returns isTaskRequest: false for null/empty input', () => {
    expect(detectTaskRequest(null).isTaskRequest).toBe(false);
    expect(detectTaskRequest('').isTaskRequest).toBe(false);
  });

  it('returns isTaskRequest: true for action verbs', () => {
    const result = detectTaskRequest(
      'Write a detailed blog post about machine learning trends'
    );
    expect(result.isTaskRequest).toBe(true);
    expect(result.confidence).toBeGreaterThanOrEqual(0.3);
  });

  it('returns isTaskRequest: true for question patterns', () => {
    const result = detectTaskRequest(
      'Can you create a newsletter for our subscribers?'
    );
    expect(result.isTaskRequest).toBe(true);
  });

  it('returns isTaskRequest: true for domain-specific words', () => {
    const result = detectTaskRequest(
      'I need help with a blog post about the latest tech trends'
    );
    expect(result.isTaskRequest).toBe(true);
  });

  it('returns isTaskRequest: true for deploy/publish verbs', () => {
    const result = detectTaskRequest(
      'Please publish the scheduled newsletter for our users'
    );
    expect(result.isTaskRequest).toBe(true);
  });

  it('caps confidence at 1.0 even with many matches', () => {
    const longRequest =
      'Can you please create and generate a blog post article newsletter about social media content?';
    const result = detectTaskRequest(longRequest);
    expect(result.confidence).toBeLessThanOrEqual(1.0);
  });

  it('returns task_composition intent when isTaskRequest is true', () => {
    const result = detectTaskRequest(
      'Create a blog post about AI trends for our readers'
    );
    expect(result.intent).toBe('task_composition');
  });

  it('returns null intent when isTaskRequest is false', () => {
    const result = detectTaskRequest('Hello, how are you doing today?');
    expect(result.intent).toBeNull();
  });

  it('increases confidence with multiple keyword matches', () => {
    const singleMatch = detectTaskRequest('Write a report about financials');
    const multiMatch = detectTaskRequest(
      'Can you please write and create a blog post article?'
    );
    expect(multiMatch.confidence).toBeGreaterThanOrEqual(
      singleMatch.confidence
    );
  });
});

// ---------------------------------------------------------------------------
// formatCompositionResult
// ---------------------------------------------------------------------------

describe('formatCompositionResult', () => {
  it('shows error message when success is false', () => {
    const result = formatCompositionResult({
      success: false,
      error: 'LLM failed',
    });
    expect(result).toContain('LLM failed');
  });

  it('shows "Unknown error" when success is false and no error message', () => {
    const result = formatCompositionResult({ success: false });
    expect(result).toContain('Unknown error');
  });

  it('shows explanation when success is true but no task_definition', () => {
    const result = formatCompositionResult({
      success: true,
      task_definition: null,
      explanation: 'Could not parse intent',
    });
    expect(result).toContain('Could not parse intent');
  });

  it('formats full result with steps list', () => {
    const compositionResult = {
      success: true,
      confidence: 0.9,
      explanation: 'Task composed successfully',
      task_definition: {
        name: 'Blog Pipeline',
        description: 'Writes a blog post',
        steps: [
          { capability_name: 'research' },
          { capability_name: 'blog_writer' },
        ],
      },
    };
    const result = formatCompositionResult(compositionResult);
    expect(result).toContain('Blog Pipeline');
    expect(result).toContain('1. research');
    expect(result).toContain('2. blog_writer');
    expect(result).toContain('Task composed successfully');
  });

  it('appends execution_id when present', () => {
    const compositionResult = {
      success: true,
      confidence: 0.85,
      explanation: 'Done',
      task_definition: {
        name: 'Test',
        description: 'Desc',
        steps: [{ capability_name: 'step1' }],
      },
      execution_id: 'exec-abc-123',
    };
    const result = formatCompositionResult(compositionResult);
    expect(result).toContain('exec-abc-123');
  });

  it('does not append execution_id line when execution_id is absent', () => {
    const compositionResult = {
      success: true,
      confidence: 0.8,
      explanation: 'Done',
      task_definition: {
        name: 'Test',
        description: 'Desc',
        steps: [{ capability_name: 'step1' }],
      },
    };
    const result = formatCompositionResult(compositionResult);
    expect(result).not.toContain('auto-executed');
  });

  it('uses correct emoji for confidence > 0.8', () => {
    const result = formatCompositionResult({
      success: true,
      confidence: 0.9,
      explanation: 'Done',
      task_definition: {
        name: 'T',
        description: 'D',
        steps: [{ capability_name: 's' }],
      },
    });
    expect(result).toContain('✅');
  });

  it('uses warning emoji for confidence between 0.6 and 0.8', () => {
    const result = formatCompositionResult({
      success: true,
      confidence: 0.7,
      explanation: 'Done',
      task_definition: {
        name: 'T',
        description: 'D',
        steps: [{ capability_name: 's' }],
      },
    });
    expect(result).toContain('⚠️');
  });
});
