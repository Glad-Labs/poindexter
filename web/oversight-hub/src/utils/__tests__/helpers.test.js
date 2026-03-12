/**
 * helpers.test.js
 *
 * Unit tests for utils/helpers.js
 *
 * Tests cover:
 * - formatTimestamp — Firestore Timestamp-like object with .toDate(), plain object without .toDate()
 */

import { formatTimestamp } from '../helpers';

describe('formatTimestamp', () => {
  it('returns N/A for null', () => {
    expect(formatTimestamp(null)).toBe('N/A');
  });

  it('returns N/A for undefined', () => {
    expect(formatTimestamp(undefined)).toBe('N/A');
  });

  it('returns N/A for plain object without toDate method', () => {
    expect(formatTimestamp({ seconds: 1234567890 })).toBe('N/A');
  });

  it('calls .toDate() and returns locale string for Firestore Timestamp-like object', () => {
    const mockTimestamp = {
      toDate: () => new Date('2026-03-12T10:00:00Z'),
    };
    const result = formatTimestamp(mockTimestamp);
    expect(typeof result).toBe('string');
    expect(result).not.toBe('N/A');
    // Result should contain something date-like
    expect(result.length).toBeGreaterThan(0);
  });

  it('returns a locale-formatted string from the .toDate() result', () => {
    const fixedDate = new Date('2026-03-12T10:00:00Z');
    const mockTimestamp = { toDate: () => fixedDate };
    const result = formatTimestamp(mockTimestamp);
    // Should match the locale string of the date
    expect(result).toBe(fixedDate.toLocaleString());
  });
});
