/**
 * notificationService.js (Phase 4)
 *
 * Service for managing real-time notifications
 * Handles notification creation, storage, and lifecycle
 */

class NotificationService {
  constructor() {
    this.notifications = [];
    this.notificationId = 0;
    this.listeners = [];
    this.maxNotifications = 10; // Keep max 10 in history
  }

  /**
   * Show a notification
   * @param {Object} notification - Notification object
   * @param {string} notification.type - 'success', 'error', 'warning', 'info'
   * @param {string} notification.title - Notification title
   * @param {string} notification.message - Notification message
   * @param {number} notification.duration - Duration in ms (0 = persistent)
   * @returns {string} Notification ID
   */
  notify({ type = 'info', title = '', message = '', duration = 5000 }) {
    const id = String(this.notificationId++);
    const notification = {
      id,
      type,
      title,
      message,
      duration,
      timestamp: new Date(),
    };

    this.notifications.unshift(notification);

    // Keep only recent notifications
    if (this.notifications.length > this.maxNotifications) {
      this.notifications.pop();
    }

    // Notify listeners
    this.listeners.forEach((listener) => {
      listener({
        action: 'add',
        notification,
      });
    });

    // Auto-dismiss if duration specified
    if (duration > 0) {
      setTimeout(() => {
        this.dismiss(id);
      }, duration);
    }

    return id;
  }

  /**
   * Dismiss a notification
   * @param {string} id - Notification ID
   */
  dismiss(id) {
    const index = this.notifications.findIndex((n) => n.id === id);
    if (index > -1) {
      const notification = this.notifications.splice(index, 1)[0];
      this.listeners.forEach((listener) => {
        listener({
          action: 'remove',
          notification,
        });
      });
    }
  }

  /**
   * Subscribe to notification changes
   * @param {Function} listener - Callback function
   * @returns {Function} Unsubscribe function
   */
  subscribe(listener) {
    this.listeners.push(listener);
    return () => {
      const index = this.listeners.indexOf(listener);
      if (index > -1) {
        this.listeners.splice(index, 1);
      }
    };
  }

  /**
   * Get all notifications
   * @returns {Array} Notifications array
   */
  getNotifications() {
    return this.notifications;
  }

  /**
   * Clear all notifications
   */
  clearAll() {
    const cleared = this.notifications.splice(0, this.notifications.length);
    cleared.forEach((notification) => {
      this.listeners.forEach((listener) => {
        listener({
          action: 'remove',
          notification,
        });
      });
    });
  }
}

export const notificationService = new NotificationService();
