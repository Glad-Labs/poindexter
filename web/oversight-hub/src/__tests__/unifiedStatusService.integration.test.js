/**
 * Integration Tests for Unified Status Service
 */

import {
  STATUS_ENUM,
  STATUS_ENUM_LEGACY,
  isValidStatusTransition,
  getValidTransitions,
} from '../Constants/statusEnums';

describe('Status Enums and Mappings', () => {
  test('should have 10 new status values', () => {
    // FAILED_REVISIONS_REQUESTED was added as the 10th status
    expect(Object.keys(STATUS_ENUM).length).toBe(10);
  });

  test('should have 5 legacy status values', () => {
    expect(Object.keys(STATUS_ENUM_LEGACY).length).toBe(5);
  });
});

describe('Status Transition Validation', () => {
  test('should allow pending → in_progress', () => {
    expect(
      isValidStatusTransition(STATUS_ENUM.PENDING, STATUS_ENUM.IN_PROGRESS)
    ).toBe(true);
  });

  test('should allow awaiting_approval → approved', () => {
    expect(
      isValidStatusTransition(
        STATUS_ENUM.AWAITING_APPROVAL,
        STATUS_ENUM.APPROVED
      )
    ).toBe(true);
  });

  test('should NOT allow invalid transitions', () => {
    expect(
      isValidStatusTransition(STATUS_ENUM.PUBLISHED, STATUS_ENUM.PENDING)
    ).toBe(false);
  });

  test('should return valid transitions for a status', () => {
    const transitions = getValidTransitions(STATUS_ENUM.PENDING);
    expect(Array.isArray(transitions)).toBe(true);
    expect(transitions.length).toBeGreaterThan(0);
    expect(transitions).toContain(STATUS_ENUM.IN_PROGRESS);
  });
});

describe('Status Constants', () => {
  test('APPROVED status exists in new enum', () => {
    expect(STATUS_ENUM.APPROVED).toBe('approved');
  });

  test('AWAITING_APPROVAL status exists in new enum', () => {
    expect(STATUS_ENUM.AWAITING_APPROVAL).toBe('awaiting_approval');
  });

  test('PENDING_APPROVAL status exists in legacy enum', () => {
    expect(STATUS_ENUM_LEGACY.PENDING_APPROVAL).toBe('pending_approval');
  });

  test('all statuses are lowercase strings', () => {
    Object.values(STATUS_ENUM).forEach((status) => {
      expect(status).toBe(status.toLowerCase());
      expect(typeof status).toBe('string');
    });
  });
});
