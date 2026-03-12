/**
 * extractApiError.test.js
 *
 * Unit tests for lib/extractApiError.js.
 *
 * Tests cover:
 * - Falsy input (null, undefined, 0, false) → 'Unknown error'
 * - String input — passes through, empty string → 'Unknown error'
 * - Error instances — message returned, network errors get friendly message
 * - Error with empty message → 'Unknown error'
 * - Plain objects — detail field, response.detail, message field, response.message
 * - Object with non-string detail → JSON-stringified
 * - Object with no known field → 'Unknown error'
 *
 * No mocking needed — pure function with no imports.
 */

import { extractApiError } from '../extractApiError';

describe('extractApiError — falsy input', () => {
  it('returns Unknown error for null', () => {
    expect(extractApiError(null)).toBe('Unknown error');
  });

  it('returns Unknown error for undefined', () => {
    expect(extractApiError(undefined)).toBe('Unknown error');
  });

  it('returns Unknown error for 0', () => {
    expect(extractApiError(0)).toBe('Unknown error');
  });

  it('returns Unknown error for false', () => {
    expect(extractApiError(false)).toBe('Unknown error');
  });
});

describe('extractApiError — string input', () => {
  it('returns the string as-is', () => {
    expect(extractApiError('Something went wrong')).toBe(
      'Something went wrong'
    );
  });

  it('returns Unknown error for empty string', () => {
    expect(extractApiError('')).toBe('Unknown error');
  });
});

describe('extractApiError — Error instances', () => {
  it('returns err.message for regular errors', () => {
    expect(extractApiError(new Error('Request failed'))).toBe('Request failed');
  });

  it('returns friendly message for "Failed to fetch"', () => {
    const result = extractApiError(new Error('Failed to fetch'));
    expect(result).toContain('Cannot reach backend service');
  });

  it('returns friendly message for NetworkError', () => {
    const result = extractApiError(
      new Error('NetworkError when attempting to fetch resource')
    );
    expect(result).toContain('Cannot reach backend service');
  });

  it('returns friendly message for ERR_CONNECTION errors', () => {
    const result = extractApiError(new Error('net::ERR_CONNECTION_REFUSED'));
    expect(result).toContain('Cannot reach backend service');
  });

  it('returns Unknown error when Error.message is empty', () => {
    const err = new Error();
    err.message = '';
    // Error() with empty string gives '' but constructing with '' gives 'Unknown error'
    expect(extractApiError(err)).toBe('Unknown error');
  });
});

describe('extractApiError — plain objects', () => {
  it('returns err.detail when present as string', () => {
    expect(extractApiError({ detail: 'Not authorized' })).toBe(
      'Not authorized'
    );
  });

  it('returns err.response.detail when present', () => {
    expect(extractApiError({ response: { detail: 'Token expired' } })).toBe(
      'Token expired'
    );
  });

  it('returns stringified err.detail when it is an object', () => {
    const err = { detail: { code: 422, message: 'Validation failed' } };
    const result = extractApiError(err);
    expect(result).toContain('Validation failed');
  });

  it('returns err.message when detail is absent', () => {
    expect(extractApiError({ message: 'Service unavailable' })).toBe(
      'Service unavailable'
    );
  });

  it('returns err.response.message when present', () => {
    expect(extractApiError({ response: { message: 'Rate limited' } })).toBe(
      'Rate limited'
    );
  });

  it('returns Unknown error when no known field', () => {
    expect(extractApiError({ code: 500, unknown: true })).toBe('Unknown error');
  });

  it('prefers detail over message when both present', () => {
    expect(
      extractApiError({ detail: 'Detail msg', message: 'Message msg' })
    ).toBe('Detail msg');
  });
});
