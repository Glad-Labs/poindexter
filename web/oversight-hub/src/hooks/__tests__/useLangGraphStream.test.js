/**
 * useLangGraphStream.test.js
 *
 * Unit tests for the useLangGraphStream hook.
 * Covers:
 * - Initial state (no requestId)
 * - WebSocket connection lifecycle
 * - Progress messages update phase state
 * - Complete messages mark all phases done
 * - Error messages set error state
 * - JSON parse errors handled gracefully
 * - WebSocket error events
 * - Cleanup closes open WebSocket on unmount
 * - Phase index mapping
 */

import { renderHook, act } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

// Mock logger
vi.mock('@/lib/logger', () => ({
  default: { log: vi.fn(), error: vi.fn(), warn: vi.fn() },
}));

// Mock apiConfig
vi.mock('../../config/apiConfig', () => ({
  getWebSocketUrl: vi.fn(() => 'ws://localhost:8000'),
}));

// ---------------------------------------------------------------------------
// WebSocket mock
// ---------------------------------------------------------------------------

class MockWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 1; // OPEN
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    this.onclose = null;
    this.close = vi.fn(() => {
      this.readyState = 3; // CLOSED
    });

    // Store instance for test access
    MockWebSocket._lastInstance = this;
  }
}

// Static constants matching the real WebSocket API
MockWebSocket.OPEN = 1;
MockWebSocket.CLOSED = 3;
MockWebSocket._lastInstance = null;

// ---------------------------------------------------------------------------
// Import hook AFTER mocks are set up
// ---------------------------------------------------------------------------

import { useLangGraphStream } from '../useLangGraphStream';

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useLangGraphStream', () => {
  beforeEach(() => {
    MockWebSocket._lastInstance = null;
    vi.stubGlobal('WebSocket', MockWebSocket);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns initial state when no requestId is provided', () => {
    const { result } = renderHook(() => useLangGraphStream(null));

    expect(result.current.phase).toBe('pending');
    expect(result.current.progress).toBe(0);
    expect(result.current.status).toBe('waiting');
    expect(result.current.content).toBe('');
    expect(result.current.quality).toBe(0);
    expect(result.current.refinements).toBe(0);
    expect(result.current.error).toBeNull();
    expect(result.current.phases).toHaveLength(5);
    expect(result.current.phases.every((p) => !p.completed)).toBe(true);
    // No WebSocket should be created
    expect(MockWebSocket._lastInstance).toBeNull();
  });

  it('does not create WebSocket for undefined requestId', () => {
    renderHook(() => useLangGraphStream(undefined));
    expect(MockWebSocket._lastInstance).toBeNull();
  });

  it('does not create WebSocket for empty string requestId', () => {
    renderHook(() => useLangGraphStream(''));
    expect(MockWebSocket._lastInstance).toBeNull();
  });

  it('opens a WebSocket when requestId is provided', () => {
    renderHook(() => useLangGraphStream('req-123'));

    expect(MockWebSocket._lastInstance).not.toBeNull();
    expect(MockWebSocket._lastInstance.url).toBe(
      'ws://localhost:8000/api/content/langgraph/ws/blog-posts/req-123'
    );
  });

  it('updates state on progress message', () => {
    const { result } = renderHook(() => useLangGraphStream('req-1'));
    const ws = MockWebSocket._lastInstance;

    act(() => {
      ws.onmessage({
        data: JSON.stringify({
          type: 'progress',
          node: 'draft',
          progress: 55,
          current_content_preview: 'Draft content...',
          quality_score: 72,
          refinement_count: 2,
        }),
      });
    });

    expect(result.current.phase).toBe('draft');
    expect(result.current.progress).toBe(55);
    expect(result.current.status).toBe('in_progress');
    expect(result.current.content).toBe('Draft content...');
    expect(result.current.quality).toBe(72);
    expect(result.current.refinements).toBe(2);
    // Research and Outline should be completed (indices 0,1 < draft index 2)
    expect(result.current.phases[0].completed).toBe(true); // Research
    expect(result.current.phases[1].completed).toBe(true); // Outline
    expect(result.current.phases[2].completed).toBe(false); // Draft (current)
    expect(result.current.phases[3].completed).toBe(false);
    expect(result.current.phases[4].completed).toBe(false);
  });

  it('preserves previous content/quality/refinements when not in message', () => {
    const { result } = renderHook(() => useLangGraphStream('req-2'));
    const ws = MockWebSocket._lastInstance;

    // First message sets content
    act(() => {
      ws.onmessage({
        data: JSON.stringify({
          type: 'progress',
          node: 'research',
          progress: 20,
          current_content_preview: 'Research data',
          quality_score: 50,
          refinement_count: 1,
        }),
      });
    });

    // Second message without those fields should keep them
    act(() => {
      ws.onmessage({
        data: JSON.stringify({
          type: 'progress',
          node: 'outline',
          progress: 40,
        }),
      });
    });

    expect(result.current.content).toBe('Research data');
    expect(result.current.quality).toBe(50);
    expect(result.current.refinements).toBe(1);
  });

  it('marks all phases complete on complete message', () => {
    const { result } = renderHook(() => useLangGraphStream('req-3'));
    const ws = MockWebSocket._lastInstance;

    act(() => {
      ws.onmessage({
        data: JSON.stringify({ type: 'complete' }),
      });
    });

    expect(result.current.phase).toBe('complete');
    expect(result.current.progress).toBe(100);
    expect(result.current.status).toBe('completed');
    expect(result.current.phases.every((p) => p.completed)).toBe(true);
  });

  it('sets error state on error message', () => {
    const { result } = renderHook(() => useLangGraphStream('req-4'));
    const ws = MockWebSocket._lastInstance;

    act(() => {
      ws.onmessage({
        data: JSON.stringify({ type: 'error', error: 'LLM quota exceeded' }),
      });
    });

    expect(result.current.status).toBe('error');
    expect(result.current.error).toBe('LLM quota exceeded');
  });

  it('handles JSON parse errors gracefully', () => {
    const { result } = renderHook(() => useLangGraphStream('req-5'));
    const ws = MockWebSocket._lastInstance;

    act(() => {
      ws.onmessage({ data: 'not-valid-json{' });
    });

    expect(result.current.status).toBe('error');
    expect(result.current.error).toBe('Failed to parse server response');
  });

  it('sets error state on WebSocket error event', () => {
    const { result } = renderHook(() => useLangGraphStream('req-6'));
    const ws = MockWebSocket._lastInstance;

    act(() => {
      ws.onerror(new Event('error'));
    });

    expect(result.current.status).toBe('error');
    expect(result.current.error).toBe('Connection failed');
  });

  it('closes WebSocket on unmount when open', () => {
    const { unmount } = renderHook(() => useLangGraphStream('req-7'));
    const ws = MockWebSocket._lastInstance;
    ws.readyState = 1; // OPEN

    unmount();

    expect(ws.close).toHaveBeenCalled();
  });

  it('does not close WebSocket on unmount when already closed', () => {
    const { unmount } = renderHook(() => useLangGraphStream('req-8'));
    const ws = MockWebSocket._lastInstance;
    ws.readyState = 3; // CLOSED

    unmount();

    expect(ws.close).not.toHaveBeenCalled();
  });

  it('reconnects when requestId changes', () => {
    const { rerender } = renderHook(({ id }) => useLangGraphStream(id), {
      initialProps: { id: 'req-A' },
    });

    const firstWs = MockWebSocket._lastInstance;
    firstWs.readyState = 1; // OPEN

    rerender({ id: 'req-B' });

    expect(firstWs.close).toHaveBeenCalled();
    const secondWs = MockWebSocket._lastInstance;
    expect(secondWs).not.toBe(firstWs);
    expect(secondWs.url).toContain('req-B');
  });

  it('maps phase names to correct indices for completed marking', () => {
    const { result } = renderHook(() => useLangGraphStream('req-9'));
    const ws = MockWebSocket._lastInstance;

    // assess = index 3, so phases 0,1,2 should be completed
    act(() => {
      ws.onmessage({
        data: JSON.stringify({
          type: 'progress',
          node: 'assess',
          progress: 80,
        }),
      });
    });

    expect(result.current.phases[0].completed).toBe(true); // Research
    expect(result.current.phases[1].completed).toBe(true); // Outline
    expect(result.current.phases[2].completed).toBe(true); // Draft
    expect(result.current.phases[3].completed).toBe(false); // Quality Check (current)
    expect(result.current.phases[4].completed).toBe(false); // Finalization
  });

  it('defaults unknown phase to index 0', () => {
    const { result } = renderHook(() => useLangGraphStream('req-10'));
    const ws = MockWebSocket._lastInstance;

    act(() => {
      ws.onmessage({
        data: JSON.stringify({
          type: 'progress',
          node: 'unknown_phase',
          progress: 10,
        }),
      });
    });

    // All phases should be not completed (index 0 means none before it)
    expect(result.current.phases.every((p) => !p.completed)).toBe(true);
    expect(result.current.phase).toBe('unknown_phase');
  });

  it('ignores messages with unrecognized type', () => {
    const { result } = renderHook(() => useLangGraphStream('req-11'));
    const ws = MockWebSocket._lastInstance;

    act(() => {
      ws.onmessage({
        data: JSON.stringify({ type: 'heartbeat', timestamp: 123 }),
      });
    });

    // State should remain at initial values
    expect(result.current.status).toBe('waiting');
    expect(result.current.phase).toBe('pending');
  });
});
