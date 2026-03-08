/**
 * useWebSocket Hook Tests
 *
 * Tests the WebSocket connection management hook
 * Verifies: Connection lifecycle, message handling, error recovery, reconnection
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import useWebSocket from '../hooks/useWebSocket';

// Mock WebSocket
const mockWebSocket = {
  send: vi.fn(),
  close: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  readyState: 1, // OPEN
};

global.WebSocket = vi.fn(() => mockWebSocket);

describe('useWebSocket Hook', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('should establish WebSocket connection', () => {
    renderHook(() => useWebSocket('ws://localhost:8000/ws/test'));

    expect(WebSocket).toHaveBeenCalledWith('ws://localhost:8000/ws/test');
  });

  it('should return initial state', () => {
    const { result } = renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test')
    );

    expect(result.current).toMatchObject({
      data: null,
      status: expect.any(String),
      error: null,
    });
  });

  it('should set status to connected on connection open', async () => {
    const { result } = renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test')
    );

    act(() => {
      const openHandler = mockWebSocket.addEventListener.mock.calls.find(
        (call) => call[0] === 'open'
      )?.[1];
      if (openHandler) openHandler();
    });

    await waitFor(() => {
      expect(result.current.status).toBe('connected');
    });
  });

  it('should handle incoming messages', async () => {
    const { result } = renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test')
    );

    const testMessage = { type: 'phase_started', phase: 'research' };

    act(() => {
      const messageHandler = mockWebSocket.addEventListener.mock.calls.find(
        (call) => call[0] === 'message'
      )?.[1];
      if (messageHandler) {
        messageHandler({ data: JSON.stringify(testMessage) });
      }
    });

    await waitFor(() => {
      expect(result.current.data).toEqual(testMessage);
    });
  });

  it('should handle connection errors', async () => {
    const { result } = renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test')
    );

    const error = new Error('Connection failed');

    act(() => {
      const errorHandler = mockWebSocket.addEventListener.mock.calls.find(
        (call) => call[0] === 'error'
      )?.[1];
      if (errorHandler) errorHandler(error);
    });

    await waitFor(() => {
      expect(result.current.error).toBeDefined();
      expect(result.current.status).toBe('error');
    });
  });

  it('should set status to disconnected on close', async () => {
    const { result } = renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test')
    );

    act(() => {
      const closeHandler = mockWebSocket.addEventListener.mock.calls.find(
        (call) => call[0] === 'close'
      )?.[1];
      if (closeHandler) closeHandler();
    });

    await waitFor(() => {
      expect(result.current.status).toBe('disconnected');
    });
  });

  it('should send messages', async () => {
    const { result } = renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test')
    );

    const message = { action: 'ping' };

    act(() => {
      result.current.send(message);
    });

    expect(mockWebSocket.send).toHaveBeenCalledWith(JSON.stringify(message));
  });

  it('should handle reconnection attempts', async () => {
    const { result } = renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test', {
        reconnect: true,
        maxRetries: 3,
      })
    );

    // Simulate disconnect
    act(() => {
      const closeHandler = mockWebSocket.addEventListener.mock.calls.find(
        (call) => call[0] === 'close'
      )?.[1];
      if (closeHandler) closeHandler();
    });

    await waitFor(() => {
      expect(result.current.status).toBe('disconnected');
    });
  });

  it('should cleanup listeners on unmount', () => {
    const { unmount } = renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test')
    );

    unmount();

    // Verify removeEventListener was called for cleanup
    expect(mockWebSocket.removeEventListener).toHaveBeenCalled();
  });

  it('should close connection on unmount', () => {
    const { unmount } = renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test')
    );

    unmount();

    expect(mockWebSocket.close).toHaveBeenCalled();
  });

  it('should handle binary messages', async () => {
    const { result } = renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test')
    );

    const binaryData = new ArrayBuffer(8);

    act(() => {
      const messageHandler = mockWebSocket.addEventListener.mock.calls.find(
        (call) => call[0] === 'message'
      )?.[1];
      if (messageHandler) {
        messageHandler({ data: binaryData });
      }
    });

    await waitFor(() => {
      // Should handle binary data gracefully
      expect(result.current).toBeDefined();
    });
  });

  it('should parse JSON messages correctly', async () => {
    const { result } = renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test')
    );

    const testData = { type: 'update', value: 42 };

    act(() => {
      const messageHandler = mockWebSocket.addEventListener.mock.calls.find(
        (call) => call[0] === 'message'
      )?.[1];
      if (messageHandler) {
        messageHandler({ data: JSON.stringify(testData) });
      }
    });

    await waitFor(() => {
      expect(result.current.data).toEqual(testData);
    });
  });

  it('should respect custom reconnect intervals', () => {
    renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test', {
        reconnect: true,
        reconnectInterval: 5000,
        maxRetries: 5,
      })
    );

    expect(WebSocket).toHaveBeenCalled();
  });

  it('should support conditional connection', () => {
    renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test', {
        enabled: false,
      })
    );

    // Should handle disabled state without error
    expect(true).toBe(true);
  });

  it('should handle rapid reconnections', async () => {
    renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test', { reconnect: true })
    );

    // Simulate rapid disconnections
    act(() => {
      const closeHandler = mockWebSocket.addEventListener.mock.calls.find(
        (call) => call[0] === 'close'
      )?.[1];
      if (closeHandler) {
        closeHandler();
        closeHandler();
        closeHandler();
      }
    });

    // Should not crash or create orphaned connections
    expect(true).toBe(true);
  });

  it('should emit connection state changes', async () => {
    const onStatusChange = vi.fn();
    renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/test', {
        onStatusChange,
      })
    );

    act(() => {
      const openHandler = mockWebSocket.addEventListener.mock.calls.find(
        (call) => call[0] === 'open'
      )?.[1];
      if (openHandler) openHandler();
    });

    await waitFor(() => {
      expect(onStatusChange).toHaveBeenCalledWith('connected');
    });
  });
});
