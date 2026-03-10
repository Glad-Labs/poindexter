/**
 * serviceStatus.js — connectivity tracker unit tests
 */
import { describe, test, expect, beforeEach, vi } from 'vitest';

// Import a fresh instance for each test by resetting module cache
let serviceStatus;

beforeEach(async () => {
  // Re-import to get fresh instance
  vi.resetModules();
  const mod = await import('../serviceStatus.js');
  serviceStatus = mod.serviceStatus;
});

describe('serviceStatus — offline detection', () => {
  test('starts online (offline = false)', () => {
    expect(serviceStatus.offline).toBe(false);
  });

  test('markOffline() sets offline to true', () => {
    serviceStatus.markOffline();
    expect(serviceStatus.offline).toBe(true);
  });

  test('markOffline() returns true on first call (new event)', () => {
    const result = serviceStatus.markOffline();
    expect(result).toBe(true);
  });

  test('markOffline() returns false within dedup window (second call)', () => {
    serviceStatus.markOffline();
    const result = serviceStatus.markOffline();
    expect(result).toBe(false);
  });

  test('markOnline() clears offline state', () => {
    serviceStatus.markOffline();
    serviceStatus.markOnline();
    expect(serviceStatus.offline).toBe(false);
  });

  test('markOnline() is a no-op when already online', () => {
    const listener = vi.fn();
    serviceStatus.subscribe(listener);
    serviceStatus.markOnline(); // already online
    expect(listener).not.toHaveBeenCalled();
  });
});

describe('serviceStatus — subscribers', () => {
  test('subscribe notifies listener on markOffline()', () => {
    const listener = vi.fn();
    serviceStatus.subscribe(listener);
    serviceStatus.markOffline();
    expect(listener).toHaveBeenCalledWith({ offline: true });
  });

  test('subscribe notifies listener on markOnline()', () => {
    const listener = vi.fn();
    serviceStatus.subscribe(listener);
    serviceStatus.markOffline();
    listener.mockClear();
    serviceStatus.markOnline();
    expect(listener).toHaveBeenCalledWith({ offline: false });
  });

  test('unsubscribe stops notifications', () => {
    const listener = vi.fn();
    const unsub = serviceStatus.subscribe(listener);
    unsub();
    serviceStatus.markOffline();
    expect(listener).not.toHaveBeenCalled();
  });

  test('multiple listeners all receive notifications', () => {
    const l1 = vi.fn();
    const l2 = vi.fn();
    serviceStatus.subscribe(l1);
    serviceStatus.subscribe(l2);
    serviceStatus.markOffline();
    expect(l1).toHaveBeenCalledOnce();
    expect(l2).toHaveBeenCalledOnce();
  });

  test('faulty listener does not break other listeners', () => {
    const bad = vi.fn(() => { throw new Error('oops'); });
    const good = vi.fn();
    serviceStatus.subscribe(bad);
    serviceStatus.subscribe(good);
    expect(() => serviceStatus.markOffline()).not.toThrow();
    expect(good).toHaveBeenCalledOnce();
  });
});
