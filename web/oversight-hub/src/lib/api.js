import logger from '@/lib/logger';
import axios from 'axios';
import { getApiUrl } from '../config/apiConfig';

const api = axios.create({
  baseURL: getApiUrl(), // Validated API URL (no localhost fallback)
  timeout: 10000, // 10 second timeout
  headers: {
    'Content-Type': 'application/json',
  },
});

// ===== ORCHESTRATOR API METHODS =====

/**
 * Submit an orchestrator command for execution
 * @param {Object} payload - Command payload from OrchestratorChatHandler
 * @returns {Promise<Object>} Execution response with executionId and phases
 */
export const submitOrchestratorCommand = async (payload) => {
  try {
    const response = await api.post('/api/orchestrator/process', payload);
    return response.data;
  } catch (error) {
    logger.error('Error submitting orchestrator command:', error);
    throw error;
  }
};

/**
 * Get current status of an orchestrator execution
 * @param {string} executionId - Execution ID to check
 * @returns {Promise<Object>} Status object with phase data, progress, etc.
 */
export const getOrchestratorStatus = async (executionId) => {
  try {
    const response = await api.get(`/api/orchestrator/status/${executionId}`);
    return response.data;
  } catch (error) {
    logger.error('Error getting orchestrator status:', error);
    throw error;
  }
};

/**
 * Approve an orchestrator result and proceed to next phase
 * @param {string} executionId - Execution ID
 * @param {Object} feedback - User feedback and approval data
 * @returns {Promise<Object>} Updated execution state
 */
export const approveOrchestratorResult = async (executionId, feedback = {}) => {
  try {
    const response = await api.post(
      `/api/orchestrator/approve/${executionId}`,
      { feedback }
    );
    return response.data;
  } catch (error) {
    logger.error('Error approving orchestrator result:', error);
    throw error;
  }
};

/**
 * Reject an orchestrator result and request changes
 * @param {string} executionId - Execution ID
 * @param {Object} feedback - Rejection reason and feedback
 * @returns {Promise<Object>} Updated execution state
 */
export const rejectOrchestratorResult = async (executionId, feedback = {}) => {
  try {
    const response = await api.post(`/api/orchestrator/reject/${executionId}`, {
      feedback,
    });
    return response.data;
  } catch (error) {
    logger.error('Error rejecting orchestrator result:', error);
    throw error;
  }
};

/**
 * Export execution data for training or analysis
 * @param {string} executionId - Execution ID
 * @param {Object} options - Export options (format, includeMetadata, etc.)
 * @returns {Promise<Blob>} Exported data file
 */
export const exportTrainingData = async (executionId, options = {}) => {
  try {
    const response = await api.get(`/api/orchestrator/export/${executionId}`, {
      params: options,
      responseType: 'blob',
    });
    return response.data;
  } catch (error) {
    logger.error('Error exporting training data:', error);
    throw error;
  }
};

/**
 * Connect to real-time status updates via WebSocket
 * @param {string} executionId - Execution ID to listen for
 * @param {Function} onUpdate - Callback function for status updates
 * @param {Function} onError - Callback function for errors
 * @returns {Function} Cleanup function to disconnect
 */
export const connectToStatusUpdates = (
  executionId,
  onUpdate,
  onError = null
) => {
  // Determine WebSocket URL from API base URL
  const wsBaseUrl =
    import.meta.env.VITE_WS_BASE_URL ||
    (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000')
      .replace('http://', 'ws://')
      .replace('https://', 'wss://');

  const wsUrl = `${wsBaseUrl}/api/orchestrator/subscribe/${executionId}`;

  let ws = null;

  try {
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      logger.log(`Connected to status updates for execution ${executionId}`);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (onUpdate) {
          onUpdate(data);
        }
      } catch (parseError) {
        logger.error('Error parsing WebSocket message:', parseError);
        if (onError) {
          onError(parseError);
        }
      }
    };

    ws.onerror = (error) => {
      logger.error('WebSocket error:', error);
      if (onError) {
        onError(error);
      }
    };

    ws.onclose = () => {
      logger.log(
        `Disconnected from status updates for execution ${executionId}`
      );
    };
  } catch (error) {
    logger.error('Error establishing WebSocket connection:', error);
    if (onError) {
      onError(error);
    }
  }

  // Return cleanup function
  return () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.close();
    }
  };
};

/**
 * Polling fallback for status updates (if WebSocket unavailable)
 * @param {string} executionId - Execution ID to poll
 * @param {Function} onUpdate - Callback function for status updates
 * @param {number} intervalMs - Poll interval in milliseconds (default 1000)
 * @returns {Function} Cleanup function to stop polling
 */
export const pollOrchestratorStatus = (
  executionId,
  onUpdate,
  intervalMs = 1000
) => {
  const pollInterval = setInterval(async () => {
    try {
      const status = await getOrchestratorStatus(executionId);
      if (onUpdate) {
        onUpdate(status);
      }
      // Stop polling if execution is complete or failed
      if (
        status.status === 'completed' ||
        status.status === 'failed' ||
        status.status === 'idle'
      ) {
        clearInterval(pollInterval);
      }
    } catch (error) {
      logger.error('Error polling orchestrator status:', error);
    }
  }, intervalMs);

  // Return cleanup function
  return () => {
    clearInterval(pollInterval);
  };
};

export default api;
