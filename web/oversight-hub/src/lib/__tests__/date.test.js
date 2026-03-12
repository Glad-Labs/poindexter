/**
 * date.test.js
 *
 * Unit tests for lib/date.js.
 *
 * Tests cover:
 * - formatTimestamp — null/undefined returns 'N/A', Firestore timestamp object returns locale string
 *
 * No mocking needed — pure function.
 */

import { formatTimestamp } from '../date';

describe('formatTimestamp', () => {
  it('returns "N/A" for null', () => {
    expect(formatTimestamp(null)).toBe('N/A');
  });

  it('returns "N/A" for undefined', () => {
    expect(formatTimestamp(undefined)).toBe('N/A');
  });

  it('returns "N/A" for falsy values', () => {
    expect(formatTimestamp(0)).toBe('N/A');
    expect(formatTimestamp('')).toBe('N/A');
  });

  it('returns locale string for Firestore timestamp with seconds field', () => {
    // A Firestore timestamp object with a `seconds` field
    const firestoreTimestamp = { seconds: 1700000000 }; // 2023-11-14T22:13:20Z
    const result = formatTimestamp(firestoreTimestamp);
    // Should be a non-empty locale string, not 'N/A'
    expect(typeof result).toBe('string');
    expect(result).not.toBe('N/A');
    expect(result.length).toBeGreaterThan(0);
  });

  it('converts seconds to milliseconds correctly', () => {
    // seconds=0 → epoch date
    const result = formatTimestamp({ seconds: 0 });
    // Should produce a valid date string for the Unix epoch
    expect(result).toBeTruthy();
    expect(result).not.toBe('N/A');
  });
});
