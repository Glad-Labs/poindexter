/**
 * useNodeDragDrop
 *
 * Encapsulates drag-and-drop state and event handlers for the phase-ordering
 * list inside WorkflowCanvas.  Consumers call onDrop with a reorder callback
 * to apply the new phase order.
 *
 * Extracted from WorkflowCanvas.jsx (#295).
 */
import { useState, useCallback } from 'react';

/**
 * @param {object} params
 * @param {Function} params.onReorder - called with (sourceIndex, targetIndex)
 *   when a valid drop occurs; caller is responsible for updating node order.
 */
const useNodeDragDrop = ({ onReorder } = {}) => {
  const [draggedNodeId, setDraggedNodeId] = useState(null);
  const [dragOverNodeId, setDragOverNodeId] = useState(null);

  const clearDragState = useCallback(() => {
    setDraggedNodeId(null);
    setDragOverNodeId(null);
  }, []);

  const handlePhaseDragStart = useCallback((event, nodeId) => {
    setDraggedNodeId(nodeId);
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', nodeId);
  }, []);

  const handlePhaseDragOver = useCallback(
    (event, nodeId) => {
      event.preventDefault();
      if (nodeId !== draggedNodeId) {
        setDragOverNodeId(nodeId);
      }
      event.dataTransfer.dropEffect = 'move';
    },
    [draggedNodeId]
  );

  const handlePhaseDrop = useCallback(
    (event, targetNodeId, nodes) => {
      event.preventDefault();

      const sourceNodeId =
        draggedNodeId || event.dataTransfer.getData('text/plain');

      if (!sourceNodeId || sourceNodeId === targetNodeId) {
        clearDragState();
        return;
      }

      const sourceIndex = nodes.findIndex((node) => node.id === sourceNodeId);
      const targetIndex = nodes.findIndex((node) => node.id === targetNodeId);

      if (sourceIndex >= 0 && targetIndex >= 0 && onReorder) {
        onReorder(sourceIndex, targetIndex);
      }

      clearDragState();
    },
    [draggedNodeId, clearDragState, onReorder]
  );

  return {
    draggedNodeId,
    dragOverNodeId,
    handlePhaseDragStart,
    handlePhaseDragOver,
    handlePhaseDrop,
    clearDragState,
  };
};

export default useNodeDragDrop;
