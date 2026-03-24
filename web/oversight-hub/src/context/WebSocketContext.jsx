import logger from '@/lib/logger';
/**
 * WebSocketContext.jsx (Phase 4)
 *
 * React Context for providing WebSocket connection across entire app
 * Manages connection lifecycle and provides hooks for components
 */

import React, {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import { websocketService } from '../services/websocketService';
import { AuthContext } from './AuthContext';

const WebSocketContext = createContext(null);

/**
 * WebSocketProvider Component
 * Wraps app to provide WebSocket connection.
 *
 * Waits for AuthContext initialization before attempting to connect so that:
 *  1. The auth token is available in memory before the WS handshake.
 *  2. Unauthenticated page loads do not produce noisy "Not authenticated"
 *     errors in the console.
 *  3. The connection is torn down automatically when the user logs out.
 */
export function WebSocketProvider({ children }) {
  const [isConnected, setIsConnected] = useState(false);
  // 'idle' | 'connecting' | 'connected' | 'disconnected'
  const [connectionStatus, setConnectionStatus] = useState('idle');
  const [connectionError, setConnectionError] = useState(null);

  // Consume auth state — WebSocketProvider is always rendered inside AuthProvider.
  const auth = useContext(AuthContext);
  const authLoading = auth?.loading ?? true;
  const isAuthenticated = auth?.isAuthenticated ?? false;

  useEffect(() => {
    // Do not attempt to connect while auth is still initializing or when the
    // user is not authenticated.  This prevents "Not authenticated" errors on
    // cold page loads and avoids opening a WS connection for anonymous visitors.
    if (authLoading || !isAuthenticated) {
      return;
    }

    // Connect to WebSocket on mount
    const connectWebSocket = async () => {
      try {
        setConnectionStatus('connecting');
        await websocketService.connect();
        setIsConnected(true);
        setConnectionStatus('connected');
        setConnectionError(null);
      } catch (error) {
        logger.error('Failed to connect WebSocket:', error);
        setConnectionError(error.message);
        setConnectionStatus('disconnected');
      }
    };

    // Subscribe to connection events
    const unsubscribeConnected = websocketService.subscribe('connected', () => {
      setIsConnected(true);
      setConnectionStatus('connected');
      setConnectionError(null);
    });

    const unsubscribeDisconnected = websocketService.subscribe(
      'disconnected',
      () => {
        setIsConnected(false);
        setConnectionStatus('disconnected');
      }
    );

    const unsubscribeError = websocketService.subscribe('error', (error) => {
      setConnectionError(error.message || 'WebSocket error');
    });

    connectWebSocket();

    // Cleanup on unmount or when auth state changes (e.g., logout)
    return () => {
      unsubscribeConnected();
      unsubscribeDisconnected();
      unsubscribeError();
    };
  }, [authLoading, isAuthenticated]);

  const value = {
    isConnected,
    connectionStatus,
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
 * Hook to subscribe to specific events.
 *
 * Uses a stable ref for the callback so that callers can pass inline functions
 * without causing the effect to re-run (and re-subscribe) on every render.
 * The subscription is only re-created when the eventName or service changes.
 *
 * @param {string} eventName - Event name
 * @param {Function} callback - Callback function (may be an inline function)
 */
export function useWebSocketEvent(eventName, callback) {
  const { service } = useWebSocket();
  // Keep a stable ref to the latest callback so we don't need it in the dep array
  const callbackRef = useRef(callback);
  useEffect(() => {
    callbackRef.current = callback;
  });

  useEffect(() => {
    const stableCallback = (...args) => callbackRef.current(...args);
    const unsubscribe = service.subscribe(eventName, stableCallback);
    return unsubscribe;
  }, [service, eventName]);
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
