/**
 * Tests for WebSocketContext.jsx — WebSocket connection provider (Issue #573)
 *
 * Covers:
 * - useWebSocket() hook exposes isConnected, connectionError, service
 * - useWebSocket() throws when used outside WebSocketProvider
 * - WebSocketProvider connects when authenticated
 * - WebSocketProvider does NOT connect when unauthenticated
 * - WebSocketProvider does NOT connect while auth is still loading
 * - 'connected' event sets isConnected=true
 * - 'disconnected' event sets isConnected=false
 * - 'error' event sets connectionError
 * - Cleanup unsubscribes all event listeners on unmount
 * - useWebSocketEvent subscribes and unsubscribes correctly
 * - useTaskProgress subscribes when taskId is provided
 * - useTaskProgress skips when taskId is null/undefined
 */

import React from 'react';
import { render, act, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import {
  WebSocketProvider,
  useWebSocket,
  useWebSocketEvent,
  useTaskProgress,
} from '../WebSocketContext';
import { AuthContext } from '../AuthContext';

vi.mock('@/lib/logger', () => ({
  default: {
    log: vi.fn(),
    error: vi.fn(),
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
  },
}));

// ----- Mock websocketService -----
const mockConnect = vi.fn();
const mockSubscribe = vi.fn();
const mockSubscribeToTaskProgress = vi.fn();
const mockSubscribeToWorkflowStatus = vi.fn();
const mockSubscribeToAnalyticsUpdates = vi.fn();

// subscribe() returns an unsubscribe function — default to a noop
mockSubscribe.mockReturnValue(vi.fn());
mockSubscribeToTaskProgress.mockReturnValue(vi.fn());
mockSubscribeToWorkflowStatus.mockReturnValue(vi.fn());
mockSubscribeToAnalyticsUpdates.mockReturnValue(vi.fn());

vi.mock('../../services/websocketService', () => ({
  websocketService: {
    connect: (...args) => mockConnect(...args),
    subscribe: (...args) => mockSubscribe(...args),
    subscribeToTaskProgress: (...args) => mockSubscribeToTaskProgress(...args),
    subscribeToWorkflowStatus: (...args) =>
      mockSubscribeToWorkflowStatus(...args),
    subscribeToAnalyticsUpdates: (...args) =>
      mockSubscribeToAnalyticsUpdates(...args),
  },
}));

// Helper: build an AuthContext value
function makeAuthValue({ isAuthenticated = true, loading = false } = {}) {
  return { isAuthenticated, loading };
}

// Helper: wrap with AuthContext + WebSocketProvider
function makeWrapper({ isAuthenticated = true, loading = false } = {}) {
  return ({ children }) => (
    <AuthContext.Provider value={makeAuthValue({ isAuthenticated, loading })}>
      <WebSocketProvider>{children}</WebSocketProvider>
    </AuthContext.Provider>
  );
}

describe('useWebSocket — outside provider', () => {
  it('throws when used outside WebSocketProvider', () => {
    // Suppress the expected error output
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => {
      renderHook(() => useWebSocket());
    }).toThrow('useWebSocket must be used within WebSocketProvider');
    spy.mockRestore();
  });
});

describe('WebSocketProvider — initial state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSubscribe.mockReturnValue(vi.fn());
    mockConnect.mockResolvedValue(undefined);
  });

  it('exposes isConnected=false initially', async () => {
    const { result } = renderHook(() => useWebSocket(), {
      wrapper: makeWrapper({ isAuthenticated: false }),
    });
    expect(result.current.isConnected).toBe(false);
  });

  it('exposes connectionError=null initially', async () => {
    const { result } = renderHook(() => useWebSocket(), {
      wrapper: makeWrapper({ isAuthenticated: false }),
    });
    expect(result.current.connectionError).toBeNull();
  });

  it('exposes service object', async () => {
    const { result } = renderHook(() => useWebSocket(), {
      wrapper: makeWrapper({ isAuthenticated: false }),
    });
    expect(result.current.service).toBeDefined();
  });
});

describe('WebSocketProvider — connection gating', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSubscribe.mockReturnValue(vi.fn());
    mockConnect.mockResolvedValue(undefined);
  });

  it('does NOT attempt connect when auth is still loading', async () => {
    renderHook(() => useWebSocket(), {
      wrapper: makeWrapper({ loading: true, isAuthenticated: false }),
    });
    // Wait a tick — no async connect should occur
    await act(async () => {});
    expect(mockConnect).not.toHaveBeenCalled();
  });

  it('does NOT attempt connect when not authenticated', async () => {
    renderHook(() => useWebSocket(), {
      wrapper: makeWrapper({ loading: false, isAuthenticated: false }),
    });
    await act(async () => {});
    expect(mockConnect).not.toHaveBeenCalled();
  });

  it('connects when authenticated and not loading', async () => {
    renderHook(() => useWebSocket(), {
      wrapper: makeWrapper({ loading: false, isAuthenticated: true }),
    });
    await act(async () => {});
    expect(mockConnect).toHaveBeenCalled();
  });

  it('subscribes to connected, disconnected, and error events', async () => {
    renderHook(() => useWebSocket(), {
      wrapper: makeWrapper({ loading: false, isAuthenticated: true }),
    });
    await act(async () => {});
    const subscribedEvents = mockSubscribe.mock.calls.map((c) => c[0]);
    expect(subscribedEvents).toContain('connected');
    expect(subscribedEvents).toContain('disconnected');
    expect(subscribedEvents).toContain('error');
  });
});

describe('WebSocketProvider — event handling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockConnect.mockResolvedValue(undefined);
  });

  it('sets isConnected=true when connected event fires', async () => {
    let connectedHandler;
    mockSubscribe.mockImplementation((event, handler) => {
      if (event === 'connected') connectedHandler = handler;
      return vi.fn();
    });

    const { result } = renderHook(() => useWebSocket(), {
      wrapper: makeWrapper({ loading: false, isAuthenticated: true }),
    });
    await act(async () => {});

    act(() => {
      connectedHandler?.();
    });

    expect(result.current.isConnected).toBe(true);
  });

  it('sets isConnected=false when disconnected event fires', async () => {
    let connectedHandler, disconnectedHandler;
    mockSubscribe.mockImplementation((event, handler) => {
      if (event === 'connected') connectedHandler = handler;
      if (event === 'disconnected') disconnectedHandler = handler;
      return vi.fn();
    });

    const { result } = renderHook(() => useWebSocket(), {
      wrapper: makeWrapper({ loading: false, isAuthenticated: true }),
    });
    await act(async () => {});

    act(() => {
      connectedHandler?.();
    });
    expect(result.current.isConnected).toBe(true);

    act(() => {
      disconnectedHandler?.();
    });
    expect(result.current.isConnected).toBe(false);
  });

  it('sets connectionError when error event fires', async () => {
    let errorHandler;
    mockSubscribe.mockImplementation((event, handler) => {
      if (event === 'error') errorHandler = handler;
      return vi.fn();
    });

    const { result } = renderHook(() => useWebSocket(), {
      wrapper: makeWrapper({ loading: false, isAuthenticated: true }),
    });
    await act(async () => {});

    act(() => {
      errorHandler?.({ message: 'Connection refused' });
    });

    expect(result.current.connectionError).toBe('Connection refused');
  });

  it('sets isConnected=false when connect() rejects', async () => {
    mockConnect.mockRejectedValue(new Error('Not authenticated'));

    const { result } = renderHook(() => useWebSocket(), {
      wrapper: makeWrapper({ loading: false, isAuthenticated: true }),
    });
    await act(async () => {});

    expect(result.current.isConnected).toBe(false);
    expect(result.current.connectionError).toBe('Not authenticated');
  });
});

describe('WebSocketProvider — cleanup', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockConnect.mockResolvedValue(undefined);
  });

  it('calls all unsubscribe functions when unmounted', async () => {
    const unsubConnected = vi.fn();
    const unsubDisconnected = vi.fn();
    const unsubError = vi.fn();

    mockSubscribe.mockImplementation((event) => {
      if (event === 'connected') return unsubConnected;
      if (event === 'disconnected') return unsubDisconnected;
      if (event === 'error') return unsubError;
      return vi.fn();
    });

    const { unmount } = renderHook(() => useWebSocket(), {
      wrapper: makeWrapper({ loading: false, isAuthenticated: true }),
    });
    await act(async () => {});

    unmount();

    expect(unsubConnected).toHaveBeenCalled();
    expect(unsubDisconnected).toHaveBeenCalled();
    expect(unsubError).toHaveBeenCalled();
  });
});

describe('useWebSocketEvent', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockConnect.mockResolvedValue(undefined);
    mockSubscribe.mockReturnValue(vi.fn());
  });

  it('subscribes to the specified event', async () => {
    const callback = vi.fn();
    renderHook(() => useWebSocketEvent('task_update', callback), {
      wrapper: makeWrapper({ loading: false, isAuthenticated: false }),
    });

    await act(async () => {});
    const subscribedEvents = mockSubscribe.mock.calls.map((c) => c[0]);
    expect(subscribedEvents).toContain('task_update');
  });

  it('calls unsubscribe on unmount', async () => {
    const unsubscribeFn = vi.fn();
    mockSubscribe.mockReturnValue(unsubscribeFn);

    const { unmount } = renderHook(
      () => useWebSocketEvent('some_event', vi.fn()),
      {
        wrapper: makeWrapper({ loading: false, isAuthenticated: false }),
      }
    );
    await act(async () => {});
    unmount();

    expect(unsubscribeFn).toHaveBeenCalled();
  });
});

describe('useTaskProgress', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockConnect.mockResolvedValue(undefined);
    mockSubscribe.mockReturnValue(vi.fn());
    mockSubscribeToTaskProgress.mockReturnValue(vi.fn());
  });

  it('subscribes to task progress when taskId is provided', async () => {
    const callback = vi.fn();
    renderHook(() => useTaskProgress('task-abc', callback), {
      wrapper: makeWrapper({ loading: false, isAuthenticated: false }),
    });
    await act(async () => {});

    expect(mockSubscribeToTaskProgress).toHaveBeenCalledWith(
      'task-abc',
      callback
    );
  });

  it('does not subscribe when taskId is null', async () => {
    renderHook(() => useTaskProgress(null, vi.fn()), {
      wrapper: makeWrapper({ loading: false, isAuthenticated: false }),
    });
    await act(async () => {});

    expect(mockSubscribeToTaskProgress).not.toHaveBeenCalled();
  });

  it('calls unsubscribe on unmount', async () => {
    const unsubFn = vi.fn();
    mockSubscribeToTaskProgress.mockReturnValue(unsubFn);

    const { unmount } = renderHook(() => useTaskProgress('task-xyz', vi.fn()), {
      wrapper: makeWrapper({ loading: false, isAuthenticated: false }),
    });
    await act(async () => {});
    unmount();

    expect(unsubFn).toHaveBeenCalled();
  });
});
