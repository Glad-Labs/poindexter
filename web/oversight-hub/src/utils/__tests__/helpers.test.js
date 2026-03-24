/**
 * helpers.test.js
 *
 * Unit tests for lib/date.js formatTimestamp
 *
 * Tests cover:
 * - formatTimestamp — null/undefined, object with .seconds, object without .seconds
 */

import { formatTimestamp } from '../../lib/date';

describe('formatTimestamp', () => {
  it('returns N/A for null', () => {
    expect(formatTimestamp(null)).toBe('N/A');
  });

  it('returns N/A for undefined', () => {
    expect(formatTimestamp(undefined)).toBe('N/A');
  });

  it('returns locale string for object with .seconds property', () => {
    const ts = { seconds: 1741770000 }; // 2025-03-12T10:00:00Z approx
    const result = formatTimestamp(ts);
    expect(typeof result).toBe('string');
    expect(result).not.toBe('N/A');
    expect(result.length).toBeGreaterThan(0);
  });

  it('returns a locale-formatted string matching Date construction from .seconds', () => {
    const seconds = 1741770000;
    const ts = { seconds };
    const result = formatTimestamp(ts);
    const expected = new Date(seconds * 1000).toLocaleString();
    expect(result).toBe(expected);
  });

  it('returns Invalid Date string for object without .seconds', () => {
    // When .seconds is undefined, new Date(undefined * 1000) => Invalid Date
    const result = formatTimestamp({ foo: 'bar' });
    expect(result).toBe('Invalid Date');
  });
});
