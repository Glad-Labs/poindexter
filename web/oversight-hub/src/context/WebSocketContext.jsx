import logger from '@/lib/logger';
/**
 * WebSocketContext.jsx (Phase 4)
 *
 * React Context for providing WebSocket connection across entire app
 * Manages connection lifecycle and provides hooks for components
 */

import React, { createContext, useContext, useEffect, useState } from 'react';
import { websocketService } from '../services/websocketService';

const WebSocketContext = createContext(null);

/**
 * WebSocketProvider Component
 * Wraps app to provide WebSocket connection
 */
export function WebSocketProvider({ children }) {
  const [isConnected, setIsConnected] = useState(false);
  const [connectionError, setConnectionError] = useState(null);

  useEffect(() => {
    // Connect to WebSocket on mount
    const connectWebSocket = async () => {
      try {
        await websocketService.connect();
        setIsConnected(true);
        setConnectionError(null);
      } catch (error) {
        logger.error('Failed to connect WebSocket:', error);
        setConnectionError(error.message);
      }
    };

    // Subscribe to connection events
    const unsubscribeConnected = websocketService.subscribe('connected', () => {
      setIsConnected(true);
      setConnectionError(null);
    });

    const unsubscribeDisconnected = websocketService.subscribe(
      'disconnected',
      () => {
        setIsConnected(false);
      }
    );

    const unsubscribeError = websocketService.subscribe('error', (error) => {
      setConnectionError(error.message || 'WebSocket error');
    });

    connectWebSocket();

    // Cleanup on unmount
    return () => {
      unsubscribeConnected();
      unsubscribeDisconnected();
      unsubscribeError();
    };
  }, []);

  const value = {
    isConnected,
    connectionError,
    service: websocketService,
  };

  return (
    <WebSocketContext.Provider value={value}>
      {children}
    </WebSocketContext.Provider>
  );
}

/**
 * Hook to use WebSocket in components
 * @returns {Object} { isConnected, connectionError, service }
 */
export function useWebSocket() {
  const context = useContext(WebSocketContext);
  if (!context) {
    throw new Error('useWebSocket must be used within WebSocketProvider');
  }
  return context;
}

/**
 * Hook to subscribe to specific events
 * @param {string} eventName - Event name
 * @param {Function} callback - Callback function
 */
export function useWebSocketEvent(eventName, callback) {
  const { service } = useWebSocket();

  useEffect(() => {
    const unsubscribe = service.subscribe(eventName, callback);
    return unsubscribe;
  }, [service, eventName, callback]);
}

/**
 * Hook to subscribe to task progress
 * @param {string} taskId - Task ID (null to disable)
 * @param {Function} callback - Callback function
 */
export function useTaskProgress(taskId, callback) {
  const { service } = useWebSocket();

  useEffect(() => {
    if (!taskId) return;
    const unsubscribe = service.subscribeToTaskProgress(taskId, callback);
    return unsubscribe;
  }, [service, taskId, callback]);
}

/**
 * Hook to subscribe to workflow status
 * @param {string} workflowId - Workflow ID (null to disable)
 * @param {Function} callback - Callback function
 */
export function useWorkflowStatus(workflowId, callback) {
  const { service } = useWebSocket();

  useEffect(() => {
    if (!workflowId) return;
    const unsubscribe = service.subscribeToWorkflowStatus(workflowId, callback);
    return unsubscribe;
  }, [service, workflowId, callback]);
}

/**
 * Hook to subscribe to analytics updates
 * @param {Function} callback - Callback function
 */
export function useAnalyticsUpdates(callback) {
  const { service } = useWebSocket();

  useEffect(() => {
    const unsubscribe = service.subscribeToAnalyticsUpdates(callback);
    return unsubscribe;
  }, [service, callback]);
}
