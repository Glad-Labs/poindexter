/**
 * statusConfig.test.js
 *
 * Unit tests for lib/statusConfig.js.
 *
 * Tests cover:
 * - STATUS_CONFIG — contains all expected statuses with required fields
 * - getStatusConfig — returns correct config for known status, defaults to pending for unknown
 * - getStatusColor — returns correct MUI color for known statuses, fallback for unknown
 * - getStatusLabel — returns human-readable label for known statuses, fallback for unknown
 * - getStatusIcon — returns icon for known statuses
 * - getStatusBackgroundColor — returns CSS color string
 * - getStatusBorderColor — returns CSS color string
 * - getAllStatuses — returns all keys from STATUS_CONFIG
 * - getStatusesByCategory — returns correct statuses per category, empty for unknown category
 *
 * No mocking needed — pure config and functions.
 */

import {
  STATUS_CONFIG,
  getStatusConfig,
  getStatusColor,
  getStatusLabel,
  getStatusIcon,
  getStatusBackgroundColor,
  getStatusBorderColor,
  getAllStatuses,
  getStatusesByCategory,
} from '../statusConfig';

const EXPECTED_STATUSES = [
  'pending',
  'in_progress',
  'awaiting_approval',
  'approved',
  'published',
  'failed',
  'rejected',
  'on_hold',
  'cancelled',
  'completed',
];

describe('STATUS_CONFIG', () => {
  it('contains all expected statuses', () => {
    for (const status of EXPECTED_STATUSES) {
      expect(STATUS_CONFIG).toHaveProperty(status);
    }
  });

  it('each status has required fields', () => {
    for (const [key, config] of Object.entries(STATUS_CONFIG)) {
      expect(config, `${key} missing color`).toHaveProperty('color');
      expect(config, `${key} missing label`).toHaveProperty('label');
      expect(config, `${key} missing icon`).toHaveProperty('icon');
      expect(config, `${key} missing backgroundColor`).toHaveProperty(
        'backgroundColor'
      );
    }
  });
});

describe('getStatusConfig', () => {
  it('returns config for known status', () => {
    const config = getStatusConfig('approved');
    expect(config.label).toBe('Approved');
    expect(config.color).toBe('success');
  });

  it('falls back to pending config for unknown status', () => {
    const config = getStatusConfig('nonexistent_status');
    expect(config.label).toBe('Pending');
  });

  it('falls back to pending config for undefined', () => {
    const config = getStatusConfig(undefined);
    expect(config.label).toBe('Pending');
  });
});

describe('getStatusColor', () => {
  it('returns success for approved', () => {
    expect(getStatusColor('approved')).toBe('success');
  });

  it('returns success for published', () => {
    expect(getStatusColor('published')).toBe('success');
  });

  it('returns error for failed', () => {
    expect(getStatusColor('failed')).toBe('error');
  });

  it('returns error for rejected', () => {
    expect(getStatusColor('rejected')).toBe('error');
  });

  it('returns warning for pending', () => {
    expect(getStatusColor('pending')).toBe('warning');
  });

  it('returns info for in_progress', () => {
    expect(getStatusColor('in_progress')).toBe('info');
  });

  it('falls back to warning (pending) for unknown status', () => {
    expect(getStatusColor('unknown')).toBe('warning');
  });
});

describe('getStatusLabel', () => {
  it('returns "Approved" for approved', () => {
    expect(getStatusLabel('approved')).toBe('Approved');
  });

  it('returns "In Progress" for in_progress', () => {
    expect(getStatusLabel('in_progress')).toBe('In Progress');
  });

  it('returns "Awaiting Approval" for awaiting_approval', () => {
    expect(getStatusLabel('awaiting_approval')).toBe('Awaiting Approval');
  });

  it('falls back to "Pending" label for unknown status', () => {
    expect(getStatusLabel('made_up')).toBe('Pending');
  });
});

describe('getStatusIcon', () => {
  it('returns icon emoji for approved', () => {
    expect(getStatusIcon('approved')).toBe('✅');
  });

  it('returns icon for cancelled', () => {
    expect(getStatusIcon('cancelled')).toBeTruthy();
  });

  it('returns fallback icon for unknown status', () => {
    expect(getStatusIcon('unknown')).toBeTruthy(); // falls back to pending icon
  });
});

describe('getStatusBackgroundColor', () => {
  it('returns a CSS color string for approved', () => {
    const color = getStatusBackgroundColor('approved');
    expect(typeof color).toBe('string');
    expect(color).toMatch(/^#[0-9a-f]+$/i);
  });

  it('falls back for unknown status', () => {
    const color = getStatusBackgroundColor('invalid');
    expect(typeof color).toBe('string');
    expect(color.length).toBeGreaterThan(0);
  });
});

describe('getStatusBorderColor', () => {
  it('returns a CSS color string for rejected', () => {
    const color = getStatusBorderColor('rejected');
    expect(typeof color).toBe('string');
    expect(color).toMatch(/^#[0-9a-f]+$/i);
  });
});

describe('getAllStatuses', () => {
  it('returns an array of all status keys', () => {
    const statuses = getAllStatuses();
    expect(Array.isArray(statuses)).toBe(true);
    expect(statuses.length).toBeGreaterThanOrEqual(EXPECTED_STATUSES.length);
  });

  it('includes all expected status keys', () => {
    const statuses = getAllStatuses();
    for (const expected of EXPECTED_STATUSES) {
      expect(statuses).toContain(expected);
    }
  });
});

describe('getStatusesByCategory', () => {
  it('returns pending category statuses', () => {
    const statuses = getStatusesByCategory('pending');
    expect(statuses).toContain('pending');
    expect(statuses).toContain('on_hold');
  });

  it('returns active category statuses', () => {
    const statuses = getStatusesByCategory('active');
    expect(statuses).toContain('in_progress');
    expect(statuses).toContain('awaiting_approval');
    expect(statuses).toContain('approved');
  });

  it('returns complete category statuses', () => {
    const statuses = getStatusesByCategory('complete');
    expect(statuses).toContain('published');
    expect(statuses).toContain('completed');
  });

  it('returns error category statuses', () => {
    const statuses = getStatusesByCategory('error');
    expect(statuses).toContain('failed');
    expect(statuses).toContain('rejected');
    expect(statuses).toContain('cancelled');
  });

  it('returns empty array for unknown category', () => {
    expect(getStatusesByCategory('nonexistent')).toEqual([]);
  });

  it('returns empty array for undefined', () => {
    expect(getStatusesByCategory(undefined)).toEqual([]);
  });
});
