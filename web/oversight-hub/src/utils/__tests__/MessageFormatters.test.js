/**
 * MessageFormatters.test.js
 *
 * Unit tests for all exported formatting utilities in MessageFormatters.js.
 *
 * Tests cover:
 * - truncateText — null/undefined, too short, at limit, over limit
 * - truncateTextCustom — default ellipsis, custom suffix
 * - capitalizeFirst — empty, single char, normal string
 * - formatWordCount — negative, zero, below 1k, at 1k, above 1k
 * - formatCost — negative, zero, positive values
 * - formatQualityScore — non-number, 0-1 scale, 0-100 scale
 * - formatPercentage — non-number, 0, 0.5, 1
 * - formatExecutionTime — negative, 0, <60s, <60min, hours
 * - formatTimestamp — null/undefined, valid date, invalid date
 * - formatRelativeTime — null, just now, minutes ago, hours ago, days ago
 * - formatEstimatedTime — negative, zero, positive
 * - formatPhaseStatus — known statuses, unknown status
 * - formatCommandParameters — null, empty, with internal fields filtered, normal params
 * - formatErrorSeverity — error/warning/info, unknown
 * - formatPhaseLabel — with emoji map, without emoji map, missing emoji
 * - formatProgress — negative, 0, 50, 100, over 100
 * - formatExecutionSummary — null execution, normal execution
 * - formatResultMetadata — null, normal metadata
 * - isFormattable — null, undefined, empty string, valid values
 * - safeFormat — valid value, null value, formatter throws
 */

import {
  truncateText,
  truncateTextCustom,
  capitalizeFirst,
  formatWordCount,
  formatCost,
  formatQualityScore,
  formatPercentage,
  formatExecutionTime,
  formatTimestamp,
  formatRelativeTime,
  formatEstimatedTime,
  formatPhaseStatus,
  formatCommandParameters,
  formatErrorSeverity,
  formatPhaseLabel,
  formatProgress,
  formatExecutionSummary,
  formatResultMetadata,
  isFormattable,
  safeFormat,
} from '../MessageFormatters';

// ---------------------------------------------------------------------------
// truncateText
// ---------------------------------------------------------------------------

describe('truncateText', () => {
  it('returns empty string for null', () => {
    expect(truncateText(null)).toBe('');
  });

  it('returns empty string for undefined', () => {
    expect(truncateText(undefined)).toBe('');
  });

  it('returns empty string for non-string', () => {
    expect(truncateText(42)).toBe('');
  });

  it('returns string unchanged when shorter than limit', () => {
    expect(truncateText('hello', 10)).toBe('hello');
  });

  it('returns string unchanged at exactly the limit', () => {
    expect(truncateText('hello', 5)).toBe('hello');
  });

  it('truncates with ellipsis when over limit', () => {
    expect(truncateText('hello world', 5)).toBe('hello...');
  });

  it('uses default limit of 500', () => {
    const long = 'a'.repeat(600);
    const result = truncateText(long);
    expect(result).toHaveLength(503); // 500 + '...'
    expect(result.endsWith('...')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// truncateTextCustom
// ---------------------------------------------------------------------------

describe('truncateTextCustom', () => {
  it('returns empty string for non-string', () => {
    expect(truncateTextCustom(null)).toBe('');
  });

  it('uses default ellipsis suffix', () => {
    expect(truncateTextCustom('hello world', 5)).toBe('hello...');
  });

  it('uses custom suffix when provided', () => {
    expect(truncateTextCustom('hello world', 5, ' [more]')).toBe(
      'hello [more]'
    );
  });

  it('returns unchanged string below limit', () => {
    expect(truncateTextCustom('hi', 10)).toBe('hi');
  });
});

// ---------------------------------------------------------------------------
// capitalizeFirst
// ---------------------------------------------------------------------------

describe('capitalizeFirst', () => {
  it('returns empty string for falsy input', () => {
    expect(capitalizeFirst('')).toBe('');
    expect(capitalizeFirst(null)).toBe('');
  });

  it('capitalizes first letter', () => {
    expect(capitalizeFirst('hello')).toBe('Hello');
  });

  it('single character', () => {
    expect(capitalizeFirst('a')).toBe('A');
  });

  it('preserves rest of string', () => {
    expect(capitalizeFirst('hELLO')).toBe('HELLO');
  });

  it('already capitalized string unchanged', () => {
    expect(capitalizeFirst('Hello')).toBe('Hello');
  });
});

// ---------------------------------------------------------------------------
// formatWordCount
// ---------------------------------------------------------------------------

describe('formatWordCount', () => {
  it('returns 0 for negative', () => {
    expect(formatWordCount(-5)).toBe('0');
  });

  it('returns 0 for non-number', () => {
    expect(formatWordCount('many')).toBe('0');
  });

  it('returns plain number for values under 1000', () => {
    expect(formatWordCount(500)).toBe('500');
    expect(formatWordCount(0)).toBe('0');
  });

  it('formats 1000 as 1.0K', () => {
    expect(formatWordCount(1000)).toBe('1.0K');
  });

  it('formats 2500 as 2.5K', () => {
    expect(formatWordCount(2500)).toBe('2.5K');
  });
});

// ---------------------------------------------------------------------------
// formatCost
// ---------------------------------------------------------------------------

describe('formatCost', () => {
  it('returns $0.00 for negative', () => {
    expect(formatCost(-1)).toBe('$0.00');
  });

  it('returns $0.00 for non-number', () => {
    expect(formatCost('free')).toBe('$0.00');
  });

  it('formats zero', () => {
    expect(formatCost(0)).toBe('$0.000');
  });

  it('formats to 3 decimal places', () => {
    expect(formatCost(0.025)).toBe('$0.025');
  });

  it('formats larger cost', () => {
    expect(formatCost(1.234)).toBe('$1.234');
  });
});

// ---------------------------------------------------------------------------
// formatQualityScore
// ---------------------------------------------------------------------------

describe('formatQualityScore', () => {
  it('returns N/A for non-number', () => {
    expect(formatQualityScore('high')).toBe('N/A');
    expect(formatQualityScore(null)).toBe('N/A');
  });

  it('normalizes 0-1 scale to 0-100', () => {
    expect(formatQualityScore(0.85)).toBe('85/100');
  });

  it('uses 0-100 scale directly for values > 1', () => {
    expect(formatQualityScore(85)).toBe('85/100');
  });

  it('rounds to nearest integer', () => {
    expect(formatQualityScore(0.856)).toBe('86/100');
  });

  it('handles zero', () => {
    expect(formatQualityScore(0)).toBe('0/100');
  });
});

// ---------------------------------------------------------------------------
// formatPercentage
// ---------------------------------------------------------------------------

describe('formatPercentage', () => {
  it('returns 0% for non-number', () => {
    expect(formatPercentage('high')).toBe('0%');
  });

  it('returns 0% for zero', () => {
    expect(formatPercentage(0)).toBe('0%');
  });

  it('returns 50% for 0.5', () => {
    expect(formatPercentage(0.5)).toBe('50%');
  });

  it('returns 100% for 1', () => {
    expect(formatPercentage(1)).toBe('100%');
  });

  it('rounds to nearest integer', () => {
    expect(formatPercentage(0.856)).toBe('86%');
  });
});

// ---------------------------------------------------------------------------
// formatExecutionTime
// ---------------------------------------------------------------------------

describe('formatExecutionTime', () => {
  it('returns 0s for negative', () => {
    expect(formatExecutionTime(-10)).toBe('0s');
  });

  it('returns 0s for non-number', () => {
    expect(formatExecutionTime('fast')).toBe('0s');
  });

  it('formats 0 as 0s', () => {
    expect(formatExecutionTime(0)).toBe('0s');
  });

  it('formats 30 seconds', () => {
    expect(formatExecutionTime(30)).toBe('30s');
  });

  it('formats 59 seconds', () => {
    expect(formatExecutionTime(59)).toBe('59s');
  });

  it('formats 90 seconds as 1m 30s', () => {
    expect(formatExecutionTime(90)).toBe('1m 30s');
  });

  it('formats exactly 60 seconds as 1m', () => {
    expect(formatExecutionTime(60)).toBe('1m');
  });

  it('formats 3600 seconds as 1h', () => {
    expect(formatExecutionTime(3600)).toBe('1h');
  });

  it('formats 3660 seconds as 1h 1m', () => {
    expect(formatExecutionTime(3660)).toBe('1h 1m');
  });
});

// ---------------------------------------------------------------------------
// formatTimestamp
// ---------------------------------------------------------------------------

describe('formatTimestamp', () => {
  it('returns N/A for null', () => {
    expect(formatTimestamp(null)).toBe('N/A');
  });

  it('returns N/A for undefined', () => {
    expect(formatTimestamp(undefined)).toBe('N/A');
  });

  it('returns a string for valid date', () => {
    const result = formatTimestamp('2026-03-12T10:00:00Z');
    expect(typeof result).toBe('string');
    expect(result).not.toBe('N/A');
  });

  it('returns locale string for Date object', () => {
    const result = formatTimestamp(new Date('2026-03-12'));
    expect(typeof result).toBe('string');
    expect(result.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// formatRelativeTime
// ---------------------------------------------------------------------------

describe('formatRelativeTime', () => {
  const FIXED_NOW = new Date('2026-01-15T12:00:00.000Z').getTime();

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(FIXED_NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns N/A for null', () => {
    expect(formatRelativeTime(null)).toBe('N/A');
  });

  it('returns just now for very recent timestamps', () => {
    // 10 seconds ago
    const result = formatRelativeTime(
      new Date(FIXED_NOW - 10_000).toISOString()
    );
    expect(result).toBe('just now');
  });

  it('returns minutes ago for timestamps ~5 min ago', () => {
    const past = new Date(FIXED_NOW - 5 * 60 * 1000).toISOString();
    const result = formatRelativeTime(past);
    expect(result).toBe('5m ago');
  });

  it('returns hours ago for timestamps ~2h ago', () => {
    const past = new Date(FIXED_NOW - 2 * 60 * 60 * 1000).toISOString();
    const result = formatRelativeTime(past);
    expect(result).toBe('2h ago');
  });

  it('returns days ago for timestamps ~3d ago', () => {
    const past = new Date(FIXED_NOW - 3 * 24 * 60 * 60 * 1000).toISOString();
    const result = formatRelativeTime(past);
    expect(result).toBe('3d ago');
  });

  it('returns locale date for timestamps > 7 days old', () => {
    const past = new Date(FIXED_NOW - 10 * 24 * 60 * 60 * 1000).toISOString();
    const result = formatRelativeTime(past);
    // Should return a date string, not "Xd ago"
    expect(result).not.toMatch(/ago$/);
  });
});

// ---------------------------------------------------------------------------
// formatEstimatedTime
// ---------------------------------------------------------------------------

describe('formatEstimatedTime', () => {
  it('returns 0 min for negative', () => {
    expect(formatEstimatedTime(-1)).toBe('0 min');
  });

  it('returns 0 min for non-number', () => {
    expect(formatEstimatedTime('soon')).toBe('0 min');
  });

  it('returns ~0 min for 0 phases', () => {
    expect(formatEstimatedTime(0)).toBe('~0 min');
  });

  it('uses default avgPerPhase of 2', () => {
    expect(formatEstimatedTime(3)).toBe('~6 min');
  });

  it('respects custom avgPerPhase', () => {
    expect(formatEstimatedTime(3, 5)).toBe('~15 min');
  });
});

// ---------------------------------------------------------------------------
// formatPhaseStatus
// ---------------------------------------------------------------------------

describe('formatPhaseStatus', () => {
  it('formats complete status', () => {
    expect(formatPhaseStatus('complete')).toBe('✓ Done');
  });

  it('formats current status', () => {
    expect(formatPhaseStatus('current')).toBe('⏳ Running');
  });

  it('formats pending status', () => {
    expect(formatPhaseStatus('pending')).toBe('⏸ Waiting');
  });

  it('returns raw status for unknown', () => {
    expect(formatPhaseStatus('unknown_status')).toBe('unknown_status');
  });
});

// ---------------------------------------------------------------------------
// formatCommandParameters
// ---------------------------------------------------------------------------

describe('formatCommandParameters', () => {
  it('returns empty string for null', () => {
    expect(formatCommandParameters(null)).toBe('');
  });

  it('returns empty string for non-object', () => {
    expect(formatCommandParameters('string')).toBe('');
  });

  it('filters out internal fields', () => {
    const params = {
      topic: 'AI',
      commandType: 'blog',
      rawInput: 'raw input here',
      additionalInstructions: 'some instructions',
    };
    const result = formatCommandParameters(params);
    expect(result).toContain('Topic: AI');
    expect(result).not.toContain('commandType');
    expect(result).not.toContain('rawInput');
  });

  it('joins parameters with bullet separator', () => {
    const params = { topic: 'AI', style: 'professional' };
    const result = formatCommandParameters(params);
    expect(result).toContain(' • ');
  });

  it('filters out empty values', () => {
    const params = { topic: 'AI', category: '' };
    const result = formatCommandParameters(params);
    expect(result).not.toContain('category');
    expect(result).toContain('Topic: AI');
  });

  it('returns empty string for empty object', () => {
    expect(formatCommandParameters({})).toBe('');
  });
});

// ---------------------------------------------------------------------------
// formatErrorSeverity
// ---------------------------------------------------------------------------

describe('formatErrorSeverity', () => {
  it('formats error severity', () => {
    expect(formatErrorSeverity('error')).toBe('❌ Error');
  });

  it('formats warning severity', () => {
    expect(formatErrorSeverity('warning')).toContain('Warning');
  });

  it('formats info severity', () => {
    expect(formatErrorSeverity('info')).toContain('Info');
  });

  it('returns raw severity for unknown', () => {
    expect(formatErrorSeverity('critical')).toBe('critical');
  });
});

// ---------------------------------------------------------------------------
// formatPhaseLabel
// ---------------------------------------------------------------------------

describe('formatPhaseLabel', () => {
  const emojis = { research: '🔍', draft: '✍️' };

  it('uses emoji from provided map', () => {
    expect(formatPhaseLabel('research', emojis)).toBe('🔍 research');
  });

  it('uses default emoji when phase not in map', () => {
    expect(formatPhaseLabel('assess', emojis)).toBe('⏳ assess');
  });

  it('uses default emoji when no emoji map provided', () => {
    expect(formatPhaseLabel('research', null)).toBe('⏳ research');
  });

  it('uses default emoji when emoji map is undefined', () => {
    expect(formatPhaseLabel('draft')).toBe('⏳ draft');
  });
});

// ---------------------------------------------------------------------------
// formatProgress
// ---------------------------------------------------------------------------

describe('formatProgress', () => {
  it('returns 0% for negative', () => {
    expect(formatProgress(-10)).toBe('0%');
  });

  it('returns 0% for non-number', () => {
    expect(formatProgress('high')).toBe('0%');
  });

  it('returns 0% for zero', () => {
    expect(formatProgress(0)).toBe('0%');
  });

  it('returns 50% for 50', () => {
    expect(formatProgress(50)).toBe('50%');
  });

  it('returns 100% for 100', () => {
    expect(formatProgress(100)).toBe('100%');
  });

  it('returns 0% for value over 100', () => {
    expect(formatProgress(150)).toBe('0%');
  });

  it('rounds to nearest integer', () => {
    expect(formatProgress(45.6)).toBe('46%');
  });
});

// ---------------------------------------------------------------------------
// formatExecutionSummary
// ---------------------------------------------------------------------------

describe('formatExecutionSummary', () => {
  it('returns no execution data for null', () => {
    expect(formatExecutionSummary(null)).toBe('No execution data');
  });

  it('formats execution with status, phases, and duration', () => {
    const execution = {
      status: 'completed',
      currentPhaseIndex: 2,
      totalPhases: 6,
      totalDuration: 90,
    };
    const result = formatExecutionSummary(execution);
    expect(result).toContain('completed');
    expect(result).toContain('phases');
    expect(result).toContain('m'); // duration contains 1m 30s
  });

  it('handles missing fields gracefully', () => {
    const result = formatExecutionSummary({});
    expect(typeof result).toBe('string');
    expect(result).toContain('unknown');
  });
});

// ---------------------------------------------------------------------------
// formatResultMetadata
// ---------------------------------------------------------------------------

describe('formatResultMetadata', () => {
  it('returns empty object for null', () => {
    expect(formatResultMetadata(null)).toEqual({});
  });

  it('formats all metadata fields', () => {
    const metadata = {
      wordCount: 1500,
      qualityScore: 0.85,
      cost: 0.025,
      executionTime: 90,
      model: 'claude-3-sonnet',
      provider: 'anthropic',
    };
    const result = formatResultMetadata(metadata);
    expect(result.words).toBe('1.5K');
    expect(result.quality).toBe('85/100');
    expect(result.cost).toBe('$0.025');
    expect(result.model).toBe('claude-3-sonnet');
    expect(result.provider).toBe('anthropic');
  });

  it('uses Unknown for missing model/provider', () => {
    const result = formatResultMetadata({});
    expect(result.model).toBe('Unknown');
    expect(result.provider).toBe('Unknown');
  });
});

// ---------------------------------------------------------------------------
// isFormattable
// ---------------------------------------------------------------------------

describe('isFormattable', () => {
  it('returns false for null', () => {
    expect(isFormattable(null)).toBe(false);
  });

  it('returns false for undefined', () => {
    expect(isFormattable(undefined)).toBe(false);
  });

  it('returns false for empty string', () => {
    expect(isFormattable('')).toBe(false);
  });

  it('returns true for 0', () => {
    expect(isFormattable(0)).toBe(true);
  });

  it('returns true for non-empty string', () => {
    expect(isFormattable('hello')).toBe(true);
  });

  it('returns true for object', () => {
    expect(isFormattable({})).toBe(true);
  });

  it('returns true for false boolean', () => {
    expect(isFormattable(false)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// safeFormat
// ---------------------------------------------------------------------------

describe('safeFormat', () => {
  it('calls formatter with valid value', () => {
    const formatter = (v) => `formatted: ${v}`;
    expect(safeFormat(formatter, 'hello')).toBe('formatted: hello');
  });

  it('returns default for null value', () => {
    const formatter = (v) => `formatted: ${v}`;
    expect(safeFormat(formatter, null)).toBe('N/A');
  });

  it('returns default for undefined value', () => {
    const formatter = (v) => `formatted: ${v}`;
    expect(safeFormat(formatter, undefined)).toBe('N/A');
  });

  it('returns custom default', () => {
    const formatter = (v) => `formatted: ${v}`;
    expect(safeFormat(formatter, null, 'No data')).toBe('No data');
  });

  it('returns default if formatter throws', () => {
    const formatter = () => {
      throw new Error('Formatter failed');
    };
    expect(safeFormat(formatter, 'value')).toBe('N/A');
  });
});
