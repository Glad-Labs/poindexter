/**
 * websocketService.js (Phase 4)
 *
 * WebSocket client for real-time updates
 * Manages connection lifecycle, event subscriptions, and automatic reconnection
 */

class WebSocketService {
  constructor() {
    this.ws = null;
    // WebSocket endpoint: /api/ws/ (note the trailing slash for root endpoint)
    this.url = `ws://${window.location.hostname}:8000/api/ws/`;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
    this.reconnectDelay = 3000; // 3 seconds
    this.isIntentionallyClosed = false;
    this.listeners = {}; // { eventName: [callback1, callback2, ...] }
    this.messageQueue = []; // Queue messages while disconnected
  }

  /**
   * Connect to WebSocket server
   * @returns {Promise<void>}
   */
  connect() {
    return new Promise((resolve, reject) => {
      try {
        this.isIntentionallyClosed = false;
        this.ws = new WebSocket(this.url);

        this.ws.onopen = () => {
          if (process.env.NODE_ENV === 'development') {
            console.log('WebSocket connected');
          }
          this.reconnectAttempts = 0;

          // Flush message queue
          while (this.messageQueue.length > 0) {
            const message = this.messageQueue.shift();
            this.ws.send(JSON.stringify(message));
          }

          this.emit('connected');
          resolve();
        };

        this.ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            // Emit event based on message type
            if (data.type) {
              this.emit(data.type, data);
              // Also emit namespaced events (e.g., 'task.progress')
              if (data.event) {
                this.emit(data.event, data);
              }
            }
          } catch (error) {
            if (process.env.NODE_ENV === 'development') {
              console.error('Failed to parse WebSocket message:', error);
            }
          }
        };

        this.ws.onerror = (error) => {
          if (process.env.NODE_ENV === 'development') {
            console.error('WebSocket error:', error);
          }
          this.emit('error', error);
          reject(error);
        };

        this.ws.onclose = () => {
          if (process.env.NODE_ENV === 'development') {
            console.log('WebSocket disconnected');
          }
          this.emit('disconnected');

          if (
            !this.isIntentionallyClosed &&
            this.reconnectAttempts < this.maxReconnectAttempts
          ) {
            this.attemptReconnect();
          }
        };
      } catch (error) {
        if (process.env.NODE_ENV === 'development') {
          console.error('Failed to create WebSocket:', error);
        }
        reject(error);
      }
    });
  }

  /**
   * Attempt to reconnect with exponential backoff
   */
  attemptReconnect() {
    if (this.isIntentionallyClosed) return;

    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);

    if (process.env.NODE_ENV === 'development') {
      console.log(
        `Attempting to reconnect (${this.reconnectAttempts}/${this.maxReconnectAttempts}) in ${delay}ms`
      );
    }

    setTimeout(() => {
      if (!this.isIntentionallyClosed) {
        this.connect().catch((error) => {
          if (process.env.NODE_ENV === 'development') {
            console.error('Reconnection failed:', error);
          }
        });
      }
    }, delay);
  }

  /**
   * Subscribe to an event
   * @param {string} eventName - Event to listen for
   * @param {Function} callback - Callback function
   * @returns {Function} Unsubscribe function
   */
  subscribe(eventName, callback) {
    if (!this.listeners[eventName]) {
      this.listeners[eventName] = [];
    }

    this.listeners[eventName].push(callback);

    // Return unsubscribe function
    return () => {
      const index = this.listeners[eventName].indexOf(callback);
      if (index > -1) {
        this.listeners[eventName].splice(index, 1);
      }
    };
  }

  /**
   * Emit an event to all subscribers
   * @param {string} eventName - Event name
   * @param {*} data - Event data
   */
  emit(eventName, data) {
    if (this.listeners[eventName]) {
      this.listeners[eventName].forEach((callback) => {
        try {
          callback(data);
        } catch (error) {
          if (process.env.NODE_ENV === 'development') {
            console.error(`Error in listener for ${eventName}:`, error);
          }
        }
      });
    }
  }

  /**
   * Send a message to the server
   * @param {string} event - Event name
   * @param {Object} data - Event data
   */
  send(event, data = {}) {
    const message = { event, ...data };

    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    } else {
      // Queue message if not connected
      this.messageQueue.push(message);
      if (process.env.NODE_ENV === 'development') {
        console.warn('WebSocket not connected, queueing message:', message);
      }
    }
  }

  /**
   * Subscribe to task progress updates
   * @param {string} taskId - Task ID
   * @param {Function} callback - Callback with task.progress data
   * @returns {Function} Unsubscribe function
   */
  subscribeToTaskProgress(taskId, callback) {
    return this.subscribe(`task.progress.${taskId}`, callback);
  }

  /**
   * Subscribe to workflow status updates
   * @param {string} workflowId - Workflow ID
   * @param {Function} callback - Callback with workflow.status data
   * @returns {Function} Unsubscribe function
   */
  subscribeToWorkflowStatus(workflowId, callback) {
    return this.subscribe(`workflow.status.${workflowId}`, callback);
  }

  /**
   * Subscribe to analytics updates
   * @param {Function} callback - Callback with analytics.update data
   * @returns {Function} Unsubscribe function
   */
  subscribeToAnalyticsUpdates(callback) {
    return this.subscribe('analytics.update', callback);
  }

  /**
   * Check if connected
   * @returns {boolean}
   */
  isConnected() {
    return this.ws && this.ws.readyState === WebSocket.OPEN;
  }

  /**
   * Disconnect gracefully
   */
  disconnect() {
    this.isIntentionallyClosed = true;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  /**
   * Clear all event listeners
   */
  clearListeners() {
    this.listeners = {};
  }
}

// Export single instance
export const websocketService = new WebSocketService();
