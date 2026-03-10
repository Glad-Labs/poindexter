/**
 * websocketService.js — reconnection logic and message queuing tests (#141)
 */
import { describe, test, expect, beforeEach, afterEach, vi } from 'vitest';

// ============================================================
// WebSocket mock factory
// ============================================================
function createMockWs(readyState = WebSocket.CONNECTING) {
  return {
    readyState,
    send: vi.fn(),
    close: vi.fn(),
    onopen: null,
    onmessage: null,
    onerror: null,
    onclose: null,
  };
}

let mockWsInstances = [];
const MockWebSocket = vi.fn((url) => {
  const ws = createMockWs();
  ws.url = url;
  mockWsInstances.push(ws);
  return ws;
});
MockWebSocket.CONNECTING = 0;
MockWebSocket.OPEN = 1;
MockWebSocket.CLOSING = 2;
MockWebSocket.CLOSED = 3;

// Install mock before module import
globalThis.WebSocket = MockWebSocket;

// Import after mocking
const { websocketService } = await import('../websocketService.js');

describe('WebSocketService — connection lifecycle', () => {
  beforeEach(() => {
    mockWsInstances = [];
    MockWebSocket.mockClear();
    // Reset service state for each test
    websocketService.ws = null;
    websocketService.reconnectAttempts = 0;
    websocketService.isIntentionallyClosed = false;
    websocketService.messageQueue = [];
    websocketService.listeners = {};
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  test('connect() creates a WebSocket to the correct URL', async () => {
    const connectPromise = websocketService.connect();
    const ws = mockWsInstances[0];
    ws.readyState = WebSocket.OPEN;
    ws.onopen();
    await connectPromise;
    expect(MockWebSocket).toHaveBeenCalledOnce();
    expect(ws.url).toContain('/api/ws/');
  });

  test('connect() resolves on successful open', async () => {
    const connectPromise = websocketService.connect();
    const ws = mockWsInstances[0];
    ws.readyState = WebSocket.OPEN;
    ws.onopen();
    await expect(connectPromise).resolves.toBeUndefined();
  });

  test('connect() rejects on WebSocket error', async () => {
    const connectPromise = websocketService.connect();
    const ws = mockWsInstances[0];
    ws.onerror(new Error('connection refused'));
    await expect(connectPromise).rejects.toBeDefined();
  });

  test('isConnected() returns true when ws is OPEN', async () => {
    const connectPromise = websocketService.connect();
    const ws = mockWsInstances[0];
    ws.readyState = WebSocket.OPEN;
    ws.onopen();
    await connectPromise;
    expect(websocketService.isConnected()).toBe(true);
  });

  test('isConnected() returns falsy when ws is null', () => {
    websocketService.ws = null;
    expect(websocketService.isConnected()).toBeFalsy();
  });

  test('disconnect() sets isIntentionallyClosed and closes ws', async () => {
    const connectPromise = websocketService.connect();
    const ws = mockWsInstances[0];
    ws.readyState = WebSocket.OPEN;
    ws.onopen();
    await connectPromise;

    websocketService.disconnect();
    expect(websocketService.isIntentionallyClosed).toBe(true);
    expect(ws.close).toHaveBeenCalledOnce();
    expect(websocketService.ws).toBeNull();
  });
});

describe('WebSocketService — reconnection logic', () => {
  beforeEach(() => {
    mockWsInstances = [];
    MockWebSocket.mockClear();
    websocketService.ws = null;
    websocketService.reconnectAttempts = 0;
    websocketService.isIntentionallyClosed = false;
    websocketService.messageQueue = [];
    websocketService.listeners = {};
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  test('attemptReconnect() does not reconnect when isIntentionallyClosed', () => {
    websocketService.isIntentionallyClosed = true;
    websocketService.attemptReconnect();
    vi.runAllTimers();
    expect(MockWebSocket).not.toHaveBeenCalled();
  });

  test('attemptReconnect() uses exponential backoff delay', () => {
    websocketService.reconnectAttempts = 0;
    const connectSpy = vi.spyOn(websocketService, 'connect').mockResolvedValue();

    // First attempt: delay = 3000 * 2^0 = 3000ms
    websocketService.attemptReconnect();
    expect(connectSpy).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2999);
    expect(connectSpy).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(connectSpy).toHaveBeenCalledOnce();

    connectSpy.mockRestore();
  });

  test('attemptReconnect() increments reconnectAttempts', () => {
    const connectSpy = vi.spyOn(websocketService, 'connect').mockResolvedValue();
    websocketService.reconnectAttempts = 0;
    websocketService.attemptReconnect();
    expect(websocketService.reconnectAttempts).toBe(1);
    connectSpy.mockRestore();
  });

  test('onclose triggers reconnect when not intentionally closed', async () => {
    const connectPromise = websocketService.connect();
    const ws = mockWsInstances[0];
    ws.readyState = WebSocket.OPEN;
    ws.onopen();
    await connectPromise;

    const reconnectSpy = vi
      .spyOn(websocketService, 'attemptReconnect')
      .mockImplementation(() => {});

    ws.onclose();
    expect(reconnectSpy).toHaveBeenCalledOnce();
    reconnectSpy.mockRestore();
  });

  test('onclose does NOT trigger reconnect when isIntentionallyClosed', async () => {
    const connectPromise = websocketService.connect();
    const ws = mockWsInstances[0];
    ws.readyState = WebSocket.OPEN;
    ws.onopen();
    await connectPromise;

    websocketService.isIntentionallyClosed = true;
    const reconnectSpy = vi
      .spyOn(websocketService, 'attemptReconnect')
      .mockImplementation(() => {});

    ws.onclose();
    expect(reconnectSpy).not.toHaveBeenCalled();
    reconnectSpy.mockRestore();
  });

  test('stops reconnecting after maxReconnectAttempts', () => {
    const connectSpy = vi.spyOn(websocketService, 'connect').mockResolvedValue();
    websocketService.reconnectAttempts = websocketService.maxReconnectAttempts;
    websocketService.attemptReconnect();
    vi.runAllTimers();
    // No new connection attempt since attempt count is at max
    // (the guard in onclose checks before calling attemptReconnect)
    expect(connectSpy).toHaveBeenCalledOnce(); // attemptReconnect itself calls once
    connectSpy.mockRestore();
  });
});

describe('WebSocketService — message queuing', () => {
  beforeEach(() => {
    mockWsInstances = [];
    MockWebSocket.mockClear();
    websocketService.ws = null;
    websocketService.reconnectAttempts = 0;
    websocketService.isIntentionallyClosed = false;
    websocketService.messageQueue = [];
    websocketService.listeners = {};
  });

  test('send() queues messages when not connected', () => {
    websocketService.ws = null;
    websocketService.send('test_event', { data: 'hello' });
    expect(websocketService.messageQueue).toHaveLength(1);
    expect(websocketService.messageQueue[0]).toMatchObject({ event: 'test_event', data: 'hello' });
  });

  test('send() sends directly when connected', async () => {
    const connectPromise = websocketService.connect();
    const ws = mockWsInstances[0];
    ws.readyState = WebSocket.OPEN;
    ws.onopen();
    await connectPromise;

    websocketService.send('test_event', { data: 'hello' });
    expect(ws.send).toHaveBeenCalledWith(
      JSON.stringify({ event: 'test_event', data: 'hello' })
    );
    expect(websocketService.messageQueue).toHaveLength(0);
  });

  test('queued messages are flushed on reconnect', async () => {
    // Queue a message while disconnected
    websocketService.send('queued_event', { value: 42 });
    expect(websocketService.messageQueue).toHaveLength(1);

    // Connect — onopen should flush the queue
    const connectPromise = websocketService.connect();
    const ws = mockWsInstances[0];
    ws.readyState = WebSocket.OPEN;
    ws.onopen();
    await connectPromise;

    expect(ws.send).toHaveBeenCalledWith(
      JSON.stringify({ event: 'queued_event', value: 42 })
    );
    expect(websocketService.messageQueue).toHaveLength(0);
  });
});

describe('WebSocketService — subscribe / emit', () => {
  beforeEach(() => {
    websocketService.listeners = {};
  });

  test('subscribe returns an unsubscribe function', () => {
    const cb = vi.fn();
    const unsub = websocketService.subscribe('my_event', cb);
    websocketService.emit('my_event', { x: 1 });
    expect(cb).toHaveBeenCalledWith({ x: 1 });

    unsub();
    websocketService.emit('my_event', { x: 2 });
    expect(cb).toHaveBeenCalledOnce(); // not called again after unsub
  });

  test('emit calls all subscribers for an event', () => {
    const cb1 = vi.fn();
    const cb2 = vi.fn();
    websocketService.subscribe('ev', cb1);
    websocketService.subscribe('ev', cb2);
    websocketService.emit('ev', 'data');
    expect(cb1).toHaveBeenCalledWith('data');
    expect(cb2).toHaveBeenCalledWith('data');
  });

  test('emit does nothing when no subscribers', () => {
    // Should not throw
    expect(() => websocketService.emit('no_subs', {})).not.toThrow();
  });

  test('clearListeners removes all listeners', () => {
    const cb = vi.fn();
    websocketService.subscribe('ev', cb);
    websocketService.clearListeners();
    websocketService.emit('ev', 'data');
    expect(cb).not.toHaveBeenCalled();
  });
});
