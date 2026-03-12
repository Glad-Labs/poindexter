/**
 * notificationService.test.js
 *
 * Unit tests for services/notificationService.js.
 *
 * Tests cover:
 * - notify — returns id, stores notification, defaults, notifies listeners, trims to maxNotifications, auto-dismiss
 * - dismiss — removes by id, calls listeners with action:remove, no-ops for unknown id
 * - subscribe — listener receives events, unsubscribe stops events
 * - getNotifications — returns current array
 * - clearAll — clears all notifications, notifies listeners per-notification
 *
 * No mocking needed — pure in-memory singleton logic, reset between tests.
 */

import { vi } from 'vitest';

// Import the singleton but reset it before each test by creating a fresh instance
import { notificationService } from '../notificationService';

beforeEach(() => {
  vi.useFakeTimers();
  // Reset the singleton's state manually
  notificationService.notifications = [];
  notificationService.notificationId = 0;
  notificationService.listeners = [];
});

afterEach(() => {
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// notify
// ---------------------------------------------------------------------------

describe('notify', () => {
  it('returns a string ID', () => {
    const id = notificationService.notify({ type: 'info', message: 'Hello' });
    expect(typeof id).toBe('string');
  });

  it('assigns sequential IDs starting from 0', () => {
    const id1 = notificationService.notify({ message: 'First' });
    const id2 = notificationService.notify({ message: 'Second' });
    expect(id1).toBe('0');
    expect(id2).toBe('1');
  });

  it('stores notification with all fields', () => {
    notificationService.notify({
      type: 'success',
      title: 'Done',
      message: 'All good',
      duration: 3000,
    });
    const notifications = notificationService.getNotifications();
    expect(notifications).toHaveLength(1);
    expect(notifications[0]).toMatchObject({
      type: 'success',
      title: 'Done',
      message: 'All good',
      duration: 3000,
    });
  });

  it('uses defaults for type, title, message, and duration', () => {
    notificationService.notify({});
    const n = notificationService.getNotifications()[0];
    expect(n.type).toBe('info');
    expect(n.title).toBe('');
    expect(n.message).toBe('');
    expect(n.duration).toBe(5000);
  });

  it('adds notification at front (newest first)', () => {
    notificationService.notify({ message: 'First' });
    notificationService.notify({ message: 'Second' });
    const notifications = notificationService.getNotifications();
    expect(notifications[0].message).toBe('Second');
    expect(notifications[1].message).toBe('First');
  });

  it('trims list to maxNotifications (10)', () => {
    for (let i = 0; i < 12; i++) {
      notificationService.notify({ message: `Notification ${i}` });
    }
    expect(notificationService.getNotifications()).toHaveLength(10);
  });

  it('notifies listeners with action:add', () => {
    const listener = vi.fn();
    notificationService.subscribe(listener);
    notificationService.notify({ type: 'warning', message: 'Watch out' });
    expect(listener).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'add',
        notification: expect.objectContaining({
          type: 'warning',
          message: 'Watch out',
        }),
      })
    );
  });

  it('auto-dismisses after duration expires', () => {
    notificationService.notify({ message: 'Brief', duration: 2000 });
    expect(notificationService.getNotifications()).toHaveLength(1);
    vi.advanceTimersByTime(2000);
    expect(notificationService.getNotifications()).toHaveLength(0);
  });

  it('does not auto-dismiss when duration is 0', () => {
    notificationService.notify({ message: 'Persistent', duration: 0 });
    vi.advanceTimersByTime(100000); // Advance a long time
    expect(notificationService.getNotifications()).toHaveLength(1);
  });

  it('includes a timestamp', () => {
    notificationService.notify({ message: 'Test' });
    const n = notificationService.getNotifications()[0];
    expect(n.timestamp).toBeInstanceOf(Date);
  });
});

// ---------------------------------------------------------------------------
// dismiss
// ---------------------------------------------------------------------------

describe('dismiss', () => {
  it('removes notification by id', () => {
    const id = notificationService.notify({ message: 'Remove me' });
    notificationService.dismiss(id);
    expect(notificationService.getNotifications()).toHaveLength(0);
  });

  it('notifies listeners with action:remove', () => {
    const listener = vi.fn();
    notificationService.subscribe(listener);
    const id = notificationService.notify({ message: 'Remove me' });
    listener.mockClear(); // Clear the 'add' event
    notificationService.dismiss(id);
    expect(listener).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'remove',
        notification: expect.objectContaining({ id }),
      })
    );
  });

  it('does nothing for unknown id (no-op)', () => {
    notificationService.notify({ message: 'Keep me' });
    const listener = vi.fn();
    notificationService.subscribe(listener);
    notificationService.dismiss('nonexistent-id');
    expect(notificationService.getNotifications()).toHaveLength(1);
    expect(listener).not.toHaveBeenCalled();
  });

  it('only removes the matching notification when multiple exist', () => {
    const id1 = notificationService.notify({ message: 'Keep' });
    const id2 = notificationService.notify({ message: 'Remove' });
    notificationService.dismiss(id2);
    const remaining = notificationService.getNotifications();
    expect(remaining).toHaveLength(1);
    expect(remaining[0].id).toBe(id1);
  });
});

// ---------------------------------------------------------------------------
// subscribe
// ---------------------------------------------------------------------------

describe('subscribe', () => {
  it('returns an unsubscribe function', () => {
    const unsubscribe = notificationService.subscribe(vi.fn());
    expect(typeof unsubscribe).toBe('function');
  });

  it('listener receives add events', () => {
    const listener = vi.fn();
    notificationService.subscribe(listener);
    notificationService.notify({ message: 'Event' });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('unsubscribe stops listener from receiving events', () => {
    const listener = vi.fn();
    const unsubscribe = notificationService.subscribe(listener);
    unsubscribe();
    notificationService.notify({ message: 'After unsubscribe' });
    expect(listener).not.toHaveBeenCalled();
  });

  it('multiple listeners all receive events', () => {
    const l1 = vi.fn();
    const l2 = vi.fn();
    notificationService.subscribe(l1);
    notificationService.subscribe(l2);
    notificationService.notify({ message: 'Broadcast' });
    expect(l1).toHaveBeenCalled();
    expect(l2).toHaveBeenCalled();
  });

  it('unsubscribing one listener does not affect others', () => {
    const l1 = vi.fn();
    const l2 = vi.fn();
    const unsubscribe1 = notificationService.subscribe(l1);
    notificationService.subscribe(l2);
    unsubscribe1();
    notificationService.notify({ message: 'Selective' });
    expect(l1).not.toHaveBeenCalled();
    expect(l2).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// getNotifications
// ---------------------------------------------------------------------------

describe('getNotifications', () => {
  it('returns empty array initially', () => {
    expect(notificationService.getNotifications()).toEqual([]);
  });

  it('returns all current notifications', () => {
    notificationService.notify({ message: 'A' });
    notificationService.notify({ message: 'B' });
    expect(notificationService.getNotifications()).toHaveLength(2);
  });

  it('returns the same array reference (not a copy)', () => {
    const notifications = notificationService.getNotifications();
    notificationService.notify({ message: 'New' });
    // Same reference should now contain the new item
    expect(notifications).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// clearAll
// ---------------------------------------------------------------------------

describe('clearAll', () => {
  it('removes all notifications', () => {
    notificationService.notify({ message: 'A' });
    notificationService.notify({ message: 'B' });
    notificationService.clearAll();
    expect(notificationService.getNotifications()).toHaveLength(0);
  });

  it('notifies listeners for each cleared notification', () => {
    const listener = vi.fn();
    notificationService.notify({ message: 'A' });
    notificationService.notify({ message: 'B' });
    notificationService.subscribe(listener);
    notificationService.clearAll();
    // Should be called once per notification removed
    expect(listener).toHaveBeenCalledTimes(2);
    expect(listener).toHaveBeenCalledWith(
      expect.objectContaining({ action: 'remove' })
    );
  });

  it('is a no-op when already empty', () => {
    const listener = vi.fn();
    notificationService.subscribe(listener);
    notificationService.clearAll();
    expect(listener).not.toHaveBeenCalled();
    expect(notificationService.getNotifications()).toHaveLength(0);
  });
});
