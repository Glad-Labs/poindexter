/**
 * NotificationCenter.jsx (Phase 4)
 *
 * Component for displaying real-time notifications
 * Shows push notifications as they arrive, maintains history
 */

import React, { useState, useEffect } from 'react';
import {
  Stack,
  Alert,
  AlertTitle,
  Snackbar,
  Box,
  Card,
  CardContent,
  Typography,
  Button,
  Badge,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material';
import {
  Close as CloseIcon,
  Delete as DeleteIcon,
  History as HistoryIcon,
  NotificationsActive as NotificationsIcon,
} from '@mui/icons-material';
import { notificationService } from '../../services/notificationService';
import { useWebSocket } from '../../context/WebSocketContext';

/**
 * NotificationCenter Component
 * Displays real-time notifications and maintains history
 */
export function NotificationCenter() {
  const [currentNotification, setCurrentNotification] = useState(null);
  const [notifications, setNotifications] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const { isConnected } = useWebSocket();

  useEffect(() => {
    // Subscribe to notification service
    const unsubscribe = notificationService.subscribe(
      ({ action, notification }) => {
        if (action === 'add') {
          setCurrentNotification(notification);
          setNotifications((prev) => [notification, ...prev].slice(0, 50)); // Keep history of 50
        } else if (action === 'remove') {
          if (currentNotification?.id === notification.id) {
            setCurrentNotification(null);
          }
        }
      }
    );

    return unsubscribe;
  }, [currentNotification]);

  const handleCloseNotification = () => {
    if (currentNotification) {
      notificationService.dismiss(currentNotification.id);
      setCurrentNotification(null);
    }
  };

  const handleClearHistory = () => {
    notificationService.clearAll();
    setNotifications([]);
    setShowHistory(false);
  };

  const getAlertSeverity = (type) => {
    switch (type) {
      case 'error':
        return 'error';
      case 'warning':
        return 'warning';
      case 'success':
        return 'success';
      default:
        return 'info';
    }
  };

  return (
    <>
      {/* Connection Status Indicator */}
      <Box
        sx={{
          position: 'fixed',
          bottom: 20,
          right: 20,
          zIndex: 1000,
        }}
      >
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            mb: 2,
            fontSize: '0.875rem',
            color: isConnected ? '#4caf50' : '#f44336',
          }}
        >
          {/* Use role=status + aria-live so status changes are announced.
              The emoji dot is aria-hidden — the text label provides the accessible name. */}
          <span
            role="status"
            aria-live="polite"
            aria-label={
              isConnected ? 'WebSocket connected' : 'WebSocket disconnected'
            }
          >
            <span aria-hidden="true">{isConnected ? '🟢' : '🔴'}</span>{' '}
            {isConnected ? 'Connected' : 'Disconnected'}
          </span>
        </Box>

        {/* Current Notification Snackbar */}
        <Snackbar
          open={!!currentNotification}
          autoHideDuration={currentNotification?.duration || 5000}
          onClose={handleCloseNotification}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        >
          <Alert
            role="alert"
            aria-live={
              currentNotification?.type === 'error' ? 'assertive' : 'polite'
            }
            onClose={handleCloseNotification}
            severity={getAlertSeverity(currentNotification?.type)}
            sx={{ minWidth: 300 }}
          >
            {currentNotification?.title && (
              <AlertTitle>{currentNotification.title}</AlertTitle>
            )}
            {currentNotification?.message}
          </Alert>
        </Snackbar>

        {/* Notification History Button */}
        <IconButton
          onClick={() => setShowHistory(true)}
          aria-label={`Notification history, ${notifications.length} notification${notifications.length !== 1 ? 's' : ''}`}
          sx={{
            backgroundColor: '#f5f5f5',
            '&:hover': {
              backgroundColor: '#e0e0e0',
            },
          }}
        >
          <Badge
            badgeContent={notifications.length}
            color="error"
            aria-hidden="true"
          >
            <HistoryIcon />
          </Badge>
        </IconButton>
      </Box>

      {/* Notification History Dialog */}
      <Dialog
        open={showHistory}
        onClose={() => setShowHistory(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <NotificationsIcon />
              <span>Notification History</span>
            </Box>
            <IconButton
              onClick={() => setShowHistory(false)}
              size="small"
              aria-label="Close notification history"
            >
              <CloseIcon />
            </IconButton>
          </Box>
        </DialogTitle>
        <DialogContent>
          {notifications.length === 0 ? (
            <Typography color="textSecondary" sx={{ py: 2 }}>
              No notifications yet
            </Typography>
          ) : (
            <Stack spacing={2} sx={{ mt: 2 }}>
              {notifications.map((notification) => (
                <Card key={notification.id} variant="outlined">
                  <CardContent sx={{ pb: 1 }}>
                    <Box
                      sx={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'flex-start',
                        gap: 1,
                      }}
                    >
                      <Box sx={{ flex: 1 }}>
                        {notification.title && (
                          <Typography
                            variant="subtitle2"
                            sx={{ fontWeight: 'bold', mb: 0.5 }}
                          >
                            {notification.title}
                          </Typography>
                        )}
                        <Typography variant="body2">
                          {notification.message}
                        </Typography>
                        <Typography
                          variant="caption"
                          color="textSecondary"
                          sx={{ display: 'block', mt: 1 }}
                        >
                          {notification.timestamp.toLocaleTimeString()}
                        </Typography>
                      </Box>
                      <Alert
                        severity={getAlertSeverity(notification.type)}
                        sx={{ mb: 'auto' }}
                        variant="standard"
                      >
                        {notification.type.charAt(0).toUpperCase() +
                          notification.type.slice(1)}
                      </Alert>
                    </Box>
                  </CardContent>
                </Card>
              ))}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setShowHistory(false)}>Close</Button>
          <Button
            onClick={handleClearHistory}
            startIcon={<DeleteIcon />}
            color="error"
            disabled={notifications.length === 0}
          >
            Clear History
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

export default NotificationCenter;
