/**
 * Tests for lib/error-handling.js
 *
 * Covers all exported functions:
 * - logError
 * - getErrorType
 * - getErrorMessage
 * - createErrorBoundaryData
 * - withRetry
 * - isRetryableError
 * - sleep
 * - safeJsonParse
 * - safeJsonStringify
 * - validateData
 * - ErrorLogger (class)
 */

// Mock Sentry to avoid real reporting
jest.mock('@sentry/nextjs', () => ({
  captureException: jest.fn(),
}));

// Mock logger to suppress console noise
jest.mock('../logger', () => ({
  error: jest.fn(),
  warn: jest.fn(),
  info: jest.fn(),
}));

import * as Sentry from '@sentry/nextjs';
import logger from '../logger';
import {
  logError,
  getErrorType,
  getErrorMessage,
  createErrorBoundaryData,
  withRetry,
  isRetryableError,
  sleep,
  safeJsonParse,
  safeJsonStringify,
  validateData,
  ErrorLogger,
} from '../error-handling';

beforeEach(() => {
  jest.clearAllMocks();
});

// ---------------------------------------------------------------------------
// getErrorType
// ---------------------------------------------------------------------------

describe('getErrorType()', () => {
  test('returns unknown for null', () => {
    expect(getErrorType(null)).toBe('unknown');
  });

  test('returns not-found for status 404', () => {
    expect(getErrorType({ status: 404 })).toBe('not-found');
  });

  test('returns forbidden for status 403', () => {
    expect(getErrorType({ status: 403 })).toBe('forbidden');
  });

  test('returns server-error for status 500', () => {
    expect(getErrorType({ status: 500 })).toBe('server-error');
  });

  test('returns server-error for status 503', () => {
    expect(getErrorType({ status: 503 })).toBe('server-error');
  });

  test('returns network for message containing fetch', () => {
    expect(getErrorType({ message: 'Failed to fetch resource' })).toBe(
      'network'
    );
  });

  test('returns network for message containing network', () => {
    expect(getErrorType({ message: 'network error occurred' })).toBe('network');
  });

  test('returns timeout for message containing timeout', () => {
    expect(getErrorType({ message: 'Request timeout exceeded' })).toBe(
      'timeout'
    );
  });

  test('returns parse-error for message containing parse', () => {
    expect(getErrorType({ message: 'Failed to parse JSON' })).toBe(
      'parse-error'
    );
  });

  test('returns unknown for unrecognized error', () => {
    expect(getErrorType({ message: 'Something weird happened' })).toBe(
      'unknown'
    );
  });

  test('statusCode is also checked for status', () => {
    expect(getErrorType({ statusCode: 404 })).toBe('not-found');
  });
});

// ---------------------------------------------------------------------------
// getErrorMessage
// ---------------------------------------------------------------------------

describe('getErrorMessage()', () => {
  test('returns not-found message for 404 error', () => {
    const msg = getErrorMessage({ status: 404 });
    expect(msg).toContain("doesn't exist");
  });

  test('returns forbidden message for 403 error', () => {
    const msg = getErrorMessage({ status: 403 });
    expect(msg).toContain('permission');
  });

  test('returns server-error message for 500 error', () => {
    const msg = getErrorMessage({ status: 500 });
    expect(msg).toContain('server');
  });

  test('returns network message for network error', () => {
    const msg = getErrorMessage({ message: 'Failed to fetch' });
    expect(msg).toContain('Network');
  });

  test('returns unknown message as fallback', () => {
    const msg = getErrorMessage({});
    expect(msg).toContain('unexpected');
  });

  test('accepts explicit errorType override', () => {
    const msg = getErrorMessage({}, 'timeout');
    expect(msg).toContain('too long');
  });
});

// ---------------------------------------------------------------------------
// logError
// ---------------------------------------------------------------------------

describe('logError()', () => {
  test('returns errorInfo object', () => {
    const error = new Error('test error');
    const result = logError(error);
    expect(result.message).toBe('test error');
    expect(result.timestamp).toBeTruthy();
    expect(result.context).toEqual({});
  });

  test('includes context in errorInfo', () => {
    const error = new Error('oops');
    const result = logError(error, { page: '/home' });
    expect(result.context).toEqual({ page: '/home' });
  });

  test('handles null error gracefully', () => {
    const result = logError(null);
    expect(result.message).toBe('Unknown error');
  });

  test('calls logger.error', () => {
    logError(new Error('test'));
    expect(logger.error).toHaveBeenCalled();
  });

  test('does NOT call Sentry.captureException in test (non-production) env', () => {
    logError(new Error('test'));
    expect(Sentry.captureException).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// createErrorBoundaryData
// ---------------------------------------------------------------------------

describe('createErrorBoundaryData()', () => {
  test('includes error, reset, type, message, timestamp', () => {
    const error = new Error('boundary test');
    const reset = jest.fn();
    const data = createErrorBoundaryData(error, reset);
    expect(data.error).toBe(error);
    expect(data.reset).toBe(reset);
    expect(data.type).toBeTruthy();
    expect(data.message).toBeTruthy();
    expect(data.timestamp).toBeTruthy();
  });

  test('type is derived from error', () => {
    const error = { status: 404 };
    const data = createErrorBoundaryData(error, null);
    expect(data.type).toBe('not-found');
  });
});

// ---------------------------------------------------------------------------
// isRetryableError
// ---------------------------------------------------------------------------

describe('isRetryableError()', () => {
  test('returns true for null error (network error)', () => {
    expect(isRetryableError(null)).toBe(true);
  });

  test('returns true for error with no status (network error)', () => {
    expect(isRetryableError({ message: 'network fail' })).toBe(true);
  });

  test('returns true for 500 error', () => {
    expect(isRetryableError({ status: 500 })).toBe(true);
  });

  test('returns true for 503 error', () => {
    expect(isRetryableError({ status: 503 })).toBe(true);
  });

  test('returns true for 408 timeout', () => {
    expect(isRetryableError({ status: 408 })).toBe(true);
  });

  test('returns true for 429 rate limited', () => {
    expect(isRetryableError({ status: 429 })).toBe(true);
  });

  test('returns false for 400 bad request', () => {
    expect(isRetryableError({ status: 400 })).toBe(false);
  });

  test('returns false for 404', () => {
    expect(isRetryableError({ status: 404 })).toBe(false);
  });

  test('returns false for 403', () => {
    expect(isRetryableError({ status: 403 })).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// sleep
// ---------------------------------------------------------------------------

describe('sleep()', () => {
  test('resolves after specified ms', async () => {
    const start = Date.now();
    await sleep(10);
    expect(Date.now() - start).toBeGreaterThanOrEqual(5);
  });

  test('returns a Promise', () => {
    const result = sleep(1);
    expect(result).toBeInstanceOf(Promise);
    return result;
  });
});

// ---------------------------------------------------------------------------
// withRetry
// ---------------------------------------------------------------------------

describe('withRetry()', () => {
  test('returns result on first success', async () => {
    const fn = jest.fn().mockResolvedValue('ok');
    const result = await withRetry(fn, { maxRetries: 3, delayMs: 1 });
    expect(result).toBe('ok');
    expect(fn).toHaveBeenCalledTimes(1);
  });

  test('retries on retryable error then succeeds', async () => {
    const fn = jest
      .fn()
      .mockRejectedValueOnce({ message: 'network error' })
      .mockResolvedValueOnce('success');
    const result = await withRetry(fn, { maxRetries: 2, delayMs: 1 });
    expect(result).toBe('success');
    expect(fn).toHaveBeenCalledTimes(2);
  });

  test('throws after exhausting retries', async () => {
    const fn = jest.fn().mockRejectedValue({ message: 'server fail' });
    await expect(withRetry(fn, { maxRetries: 2, delayMs: 1 })).rejects.toEqual({
      message: 'server fail',
    });
    expect(fn).toHaveBeenCalledTimes(3); // initial + 2 retries
  });

  test('does not retry on 404 (non-retryable)', async () => {
    const fn = jest.fn().mockRejectedValue({ status: 404 });
    await expect(
      withRetry(fn, { maxRetries: 3, delayMs: 1 })
    ).rejects.toMatchObject({
      status: 404,
    });
    expect(fn).toHaveBeenCalledTimes(1);
  });

  test('calls onRetry callback with attempt info', async () => {
    const onRetry = jest.fn();
    const fn = jest
      .fn()
      .mockRejectedValueOnce({ message: 'network' })
      .mockResolvedValue('done');
    await withRetry(fn, { maxRetries: 2, delayMs: 1, onRetry });
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onRetry.mock.calls[0][0]).toBe(1); // attempt number
  });
});

// ---------------------------------------------------------------------------
// safeJsonParse
// ---------------------------------------------------------------------------

describe('safeJsonParse()', () => {
  test('parses valid JSON object', () => {
    expect(safeJsonParse('{"a":1}')).toEqual({ a: 1 });
  });

  test('parses valid JSON array', () => {
    expect(safeJsonParse('[1,2,3]')).toEqual([1, 2, 3]);
  });

  test('returns null fallback on invalid JSON', () => {
    expect(safeJsonParse('invalid json')).toBeNull();
  });

  test('returns custom fallback on invalid JSON', () => {
    expect(safeJsonParse('bad', [])).toEqual([]);
  });

  test('returns null for null input', () => {
    expect(safeJsonParse(null)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// safeJsonStringify
// ---------------------------------------------------------------------------

describe('safeJsonStringify()', () => {
  test('stringifies plain object', () => {
    expect(safeJsonStringify({ a: 1 })).toBe('{"a":1}');
  });

  test('returns {} fallback for circular reference', () => {
    const obj = {};
    obj.self = obj; // circular
    expect(safeJsonStringify(obj)).toBe('{}');
  });

  test('returns custom fallback on error', () => {
    const obj = {};
    obj.self = obj;
    expect(safeJsonStringify(obj, '[]')).toBe('[]');
  });
});

// ---------------------------------------------------------------------------
// validateData
// ---------------------------------------------------------------------------

describe('validateData()', () => {
  test('returns valid=true for passing data', () => {
    const result = validateData(
      { name: 'Alice', email: 'alice@example.com' },
      { name: { required: true }, email: { required: true } }
    );
    expect(result.valid).toBe(true);
    expect(result.errors).toHaveLength(0);
  });

  test('required field missing returns error', () => {
    const result = validateData({ name: '' }, { name: { required: true } });
    expect(result.valid).toBe(false);
    expect(result.errors).toContain('name is required');
  });

  test('wrong type returns error', () => {
    const result = validateData({ age: 'thirty' }, { age: { type: 'number' } });
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes('type'))).toBe(true);
  });

  test('minLength violation returns error', () => {
    const result = validateData({ bio: 'hi' }, { bio: { minLength: 10 } });
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes('at least'))).toBe(true);
  });

  test('maxLength violation returns error', () => {
    const result = validateData(
      { title: 'A'.repeat(201) },
      { title: { maxLength: 200 } }
    );
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes('at most'))).toBe(true);
  });

  test('pattern violation returns error', () => {
    const result = validateData(
      { slug: 'Invalid Slug!' },
      { slug: { pattern: /^[a-z0-9-]+$/ } }
    );
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes('format is invalid'))).toBe(
      true
    );
  });

  test('enum violation returns error', () => {
    const result = validateData(
      { status: 'deleted' },
      { status: { enum: ['draft', 'published'] } }
    );
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes('must be one of'))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// ErrorLogger (class)
// ---------------------------------------------------------------------------

describe('ErrorLogger', () => {
  test('constructor sets default service name', () => {
    const el = new ErrorLogger();
    expect(el.serviceName).toBe('Glad Labs');
  });

  test('constructor accepts custom service name', () => {
    const el = new ErrorLogger('MyService');
    expect(el.serviceName).toBe('MyService');
  });

  test('log adds entry to errors array', () => {
    const el = new ErrorLogger();
    el.log(new Error('test'));
    expect(el.errors).toHaveLength(1);
  });

  test('log returns errorEntry with message and timestamp', () => {
    const el = new ErrorLogger();
    const entry = el.log(new Error('oops'));
    expect(entry.message).toBe('oops');
    expect(entry.timestamp).toBeTruthy();
    expect(entry.service).toBe('Glad Labs');
  });

  test('log includes context', () => {
    const el = new ErrorLogger();
    el.log(new Error('err'), { page: '/blog' });
    expect(el.errors[0].context).toEqual({ page: '/blog' });
  });

  test('clear() empties errors array', () => {
    const el = new ErrorLogger();
    el.log(new Error('a'));
    el.log(new Error('b'));
    el.clear();
    expect(el.errors).toHaveLength(0);
  });

  test('getErrors() returns copy of errors array', () => {
    const el = new ErrorLogger();
    el.log(new Error('x'));
    const result = el.getErrors();
    expect(result).toHaveLength(1);
    // Mutating copy does not affect internal state
    result.pop();
    expect(el.errors).toHaveLength(1);
  });

  test('export() returns JSON string', () => {
    const el = new ErrorLogger();
    el.log(new Error('export test'));
    const json = el.export();
    expect(() => JSON.parse(json)).not.toThrow();
    const parsed = JSON.parse(json);
    expect(parsed).toHaveLength(1);
  });

  test('caps errors at 100 entries', () => {
    const el = new ErrorLogger();
    for (let i = 0; i < 105; i++) {
      el.log(new Error(`error ${i}`));
    }
    expect(el.errors.length).toBeLessThanOrEqual(100);
  });

  test('log handles non-Error input (string)', () => {
    const el = new ErrorLogger();
    const entry = el.log('plain string error');
    expect(entry.message).toBe('plain string error');
  });
});
