/**
 * useNodeDragDrop.test.js
 *
 * Unit tests for the useNodeDragDrop hook.
 * Covers:
 * - Initial state (no dragged/dragover nodes)
 * - handlePhaseDragStart sets draggedNodeId and dataTransfer
 * - handlePhaseDragOver sets dragOverNodeId (only for different node)
 * - handlePhaseDrop calls onReorder with correct indices
 * - handlePhaseDrop clears drag state after drop
 * - Drop on same node is a no-op
 * - Drop with no source node clears state
 * - clearDragState resets both IDs
 * - Drop falls back to dataTransfer when draggedNodeId is null
 */

import { renderHook, act } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import useNodeDragDrop from '../useNodeDragDrop';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeDragEvent(overrides = {}) {
  return {
    preventDefault: vi.fn(),
    dataTransfer: {
      effectAllowed: '',
      dropEffect: '',
      setData: vi.fn(),
      getData: vi.fn(() => ''),
    },
    ...overrides,
  };
}

const NODES = [{ id: 'node-a' }, { id: 'node-b' }, { id: 'node-c' }];

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useNodeDragDrop', () => {
  let onReorder;

  beforeEach(() => {
    onReorder = vi.fn();
  });

  it('returns initial state with null drag IDs', () => {
    const { result } = renderHook(() => useNodeDragDrop({ onReorder }));

    expect(result.current.draggedNodeId).toBeNull();
    expect(result.current.dragOverNodeId).toBeNull();
    expect(typeof result.current.handlePhaseDragStart).toBe('function');
    expect(typeof result.current.handlePhaseDragOver).toBe('function');
    expect(typeof result.current.handlePhaseDrop).toBe('function');
    expect(typeof result.current.clearDragState).toBe('function');
  });

  it('works without onReorder callback', () => {
    const { result } = renderHook(() => useNodeDragDrop());

    expect(result.current.draggedNodeId).toBeNull();
  });

  it('handlePhaseDragStart sets draggedNodeId and configures dataTransfer', () => {
    const { result } = renderHook(() => useNodeDragDrop({ onReorder }));
    const event = makeDragEvent();

    act(() => {
      result.current.handlePhaseDragStart(event, 'node-a');
    });

    expect(result.current.draggedNodeId).toBe('node-a');
    expect(event.dataTransfer.effectAllowed).toBe('move');
    expect(event.dataTransfer.setData).toHaveBeenCalledWith(
      'text/plain',
      'node-a'
    );
  });

  it('handlePhaseDragOver sets dragOverNodeId for a different node', () => {
    const { result } = renderHook(() => useNodeDragDrop({ onReorder }));
    const startEvent = makeDragEvent();
    const overEvent = makeDragEvent();

    act(() => {
      result.current.handlePhaseDragStart(startEvent, 'node-a');
    });

    act(() => {
      result.current.handlePhaseDragOver(overEvent, 'node-b');
    });

    expect(result.current.dragOverNodeId).toBe('node-b');
    expect(overEvent.preventDefault).toHaveBeenCalled();
    expect(overEvent.dataTransfer.dropEffect).toBe('move');
  });

  it('handlePhaseDragOver does not set dragOverNodeId for the same node', () => {
    const { result } = renderHook(() => useNodeDragDrop({ onReorder }));

    act(() => {
      result.current.handlePhaseDragStart(makeDragEvent(), 'node-a');
    });

    act(() => {
      result.current.handlePhaseDragOver(makeDragEvent(), 'node-a');
    });

    expect(result.current.dragOverNodeId).toBeNull();
  });

  it('handlePhaseDrop calls onReorder with source and target indices', () => {
    const { result } = renderHook(() => useNodeDragDrop({ onReorder }));
    const dropEvent = makeDragEvent();

    // Start drag from node-a
    act(() => {
      result.current.handlePhaseDragStart(makeDragEvent(), 'node-a');
    });

    // Drop on node-c
    act(() => {
      result.current.handlePhaseDrop(dropEvent, 'node-c', NODES);
    });

    expect(dropEvent.preventDefault).toHaveBeenCalled();
    expect(onReorder).toHaveBeenCalledWith(0, 2); // node-a=0, node-c=2
  });

  it('handlePhaseDrop clears drag state after drop', () => {
    const { result } = renderHook(() => useNodeDragDrop({ onReorder }));

    act(() => {
      result.current.handlePhaseDragStart(makeDragEvent(), 'node-a');
    });

    act(() => {
      result.current.handlePhaseDrop(makeDragEvent(), 'node-b', NODES);
    });

    expect(result.current.draggedNodeId).toBeNull();
    expect(result.current.dragOverNodeId).toBeNull();
  });

  it('handlePhaseDrop is a no-op when dropping on the same node', () => {
    const { result } = renderHook(() => useNodeDragDrop({ onReorder }));

    act(() => {
      result.current.handlePhaseDragStart(makeDragEvent(), 'node-a');
    });

    act(() => {
      result.current.handlePhaseDrop(makeDragEvent(), 'node-a', NODES);
    });

    expect(onReorder).not.toHaveBeenCalled();
    expect(result.current.draggedNodeId).toBeNull();
  });

  it('handlePhaseDrop clears state when no source node found', () => {
    const { result } = renderHook(() => useNodeDragDrop({ onReorder }));
    const dropEvent = makeDragEvent({
      dataTransfer: {
        effectAllowed: '',
        dropEffect: '',
        setData: vi.fn(),
        getData: vi.fn(() => ''),
      },
    });

    // No drag start, so draggedNodeId is null and getData returns ''
    act(() => {
      result.current.handlePhaseDrop(dropEvent, 'node-b', NODES);
    });

    expect(onReorder).not.toHaveBeenCalled();
    expect(result.current.draggedNodeId).toBeNull();
  });

  it('handlePhaseDrop falls back to dataTransfer getData when draggedNodeId is null', () => {
    const { result } = renderHook(() => useNodeDragDrop({ onReorder }));
    const dropEvent = makeDragEvent({
      dataTransfer: {
        effectAllowed: '',
        dropEffect: '',
        setData: vi.fn(),
        getData: vi.fn(() => 'node-a'),
      },
    });

    // No handlePhaseDragStart, but dataTransfer has the ID
    act(() => {
      result.current.handlePhaseDrop(dropEvent, 'node-c', NODES);
    });

    expect(onReorder).toHaveBeenCalledWith(0, 2);
  });

  it('handlePhaseDrop does not call onReorder when source node is not in nodes list', () => {
    const { result } = renderHook(() => useNodeDragDrop({ onReorder }));

    act(() => {
      result.current.handlePhaseDragStart(makeDragEvent(), 'node-x');
    });

    act(() => {
      result.current.handlePhaseDrop(makeDragEvent(), 'node-b', NODES);
    });

    // sourceIndex would be -1
    expect(onReorder).not.toHaveBeenCalled();
  });

  it('handlePhaseDrop does not call onReorder when no callback provided', () => {
    const { result } = renderHook(() => useNodeDragDrop());

    act(() => {
      result.current.handlePhaseDragStart(makeDragEvent(), 'node-a');
    });

    // Should not throw
    act(() => {
      result.current.handlePhaseDrop(makeDragEvent(), 'node-b', NODES);
    });

    expect(result.current.draggedNodeId).toBeNull();
  });

  it('clearDragState resets both drag IDs', () => {
    const { result } = renderHook(() => useNodeDragDrop({ onReorder }));

    act(() => {
      result.current.handlePhaseDragStart(makeDragEvent(), 'node-a');
    });

    act(() => {
      result.current.handlePhaseDragOver(makeDragEvent(), 'node-b');
    });

    expect(result.current.draggedNodeId).toBe('node-a');
    expect(result.current.dragOverNodeId).toBe('node-b');

    act(() => {
      result.current.clearDragState();
    });

    expect(result.current.draggedNodeId).toBeNull();
    expect(result.current.dragOverNodeId).toBeNull();
  });
});
