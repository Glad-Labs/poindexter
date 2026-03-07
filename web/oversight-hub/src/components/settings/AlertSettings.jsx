import logger from '@/lib/logger';
import React, { useState, useEffect } from 'react';
import {
  Box,
  Card,
  CardContent,
  CardHeader,
  TextField,
  Button,
  CircularProgress,
  Alert,
  Stack,
  Typography,
  FormControlLabel,
  Switch,
} from '@mui/material';
import {
  createOrUpdateSetting,
  getSetting,
} from '../../services/settingsService';

/**
 * AlertSettings Component (Phase 1.3)
 *
 * Manages cost and notification alert preferences:
 * - Cost alert thresholds
 * - Notification delivery methods
 * - Alert severity levels
 */
export const AlertSettings = () => {
  const [settings, setSettings] = useState({
    costAlertThreshold: 10,
    enableCostAlerts: true,
    enableEmailNotifications: false,
    enableDesktopNotifications: true,
    enableInAppNotifications: true,
    notificationThreshold: 5,
  });

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  // Load settings on mount
  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    setLoading(true);
    setError(null);
    try {
      const [
        costAlert,
        enableCost,
        enableEmail,
        enableDesktop,
        enableInApp,
        notifThreshold,
      ] = await Promise.all([
        getSetting('cost_alert_threshold').catch(() => ({ value: '10' })),
        getSetting('enable_cost_alerts').catch(() => ({ value: 'true' })),
        getSetting('enable_email_notifications').catch(() => ({
          value: 'false',
        })),
        getSetting('enable_desktop_notifications').catch(() => ({
          value: 'true',
        })),
        getSetting('enable_inapp_notifications').catch(() => ({
          value: 'true',
        })),
        getSetting('notification_threshold').catch(() => ({ value: '5' })),
      ]);

      setSettings({
        costAlertThreshold: parseFloat(costAlert?.value || '10') || 10,
        enableCostAlerts:
          enableCost?.value === 'true' || enableCost?.value === true,
        enableEmailNotifications:
          enableEmail?.value === 'true' || enableEmail?.value === true,
        enableDesktopNotifications:
          enableDesktop?.value === 'true' || enableDesktop?.value === true,
        enableInAppNotifications:
          enableInApp?.value === 'true' || enableInApp?.value === true,
        notificationThreshold: parseFloat(notifThreshold?.value || '5') || 5,
      });
    } catch (err) {
      logger.error('Failed to load alert settings:', err);
      setError(`Failed to load settings: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveSettings = async () => {
    setSaving(true);
    setError(null);

    try {
      await Promise.all([
        createOrUpdateSetting(
          'cost_alert_threshold',
          settings.costAlertThreshold.toString()
        ),
        createOrUpdateSetting(
          'enable_cost_alerts',
          settings.enableCostAlerts.toString()
        ),
        createOrUpdateSetting(
          'enable_email_notifications',
          settings.enableEmailNotifications.toString()
        ),
        createOrUpdateSetting(
          'enable_desktop_notifications',
          settings.enableDesktopNotifications.toString()
        ),
        createOrUpdateSetting(
          'enable_inapp_notifications',
          settings.enableInAppNotifications.toString()
        ),
        createOrUpdateSetting(
          'notification_threshold',
          settings.notificationThreshold.toString()
        ),
      ]);

      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      setError(`Failed to save settings: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Card>
      <CardHeader title="Alert & Notification Settings" />
      <CardContent>
        <Stack spacing={3}>
          {error && <Alert severity="error">{error}</Alert>}
          {success && (
            <Alert severity="success">Settings saved successfully!</Alert>
          )}

          <Box>
            <FormControlLabel
              control={
                <Switch
                  checked={settings.enableCostAlerts}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      enableCostAlerts: e.target.checked,
                    })
                  }
                  disabled={saving}
                />
              }
              label="Enable Cost Alerts"
            />
            <Typography variant="caption" color="textSecondary" display="block">
              Alert when LLM API usage exceeds threshold
            </Typography>
          </Box>

          {settings.enableCostAlerts && (
            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Cost Alert Threshold ($)
              </Typography>
              <TextField
                type="number"
                value={settings.costAlertThreshold}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    costAlertThreshold: parseFloat(e.target.value) || 0,
                  })
                }
                fullWidth
                size="small"
                disabled={saving}
                inputProps={{ min: 0, step: 0.01 }}
              />
              <Typography variant="caption" color="textSecondary">
                Alert when daily LLM costs exceed this amount
              </Typography>
            </Box>
          )}

          <Box sx={{ borderTop: '1px solid #e0e0e0', pt: 3 }}>
            <Typography variant="subtitle1" gutterBottom>
              Notification Methods
            </Typography>

            <FormControlLabel
              control={
                <Switch
                  checked={settings.enableDesktopNotifications}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      enableDesktopNotifications: e.target.checked,
                    })
                  }
                  disabled={saving}
                />
              }
              label="Desktop Notifications"
            />
            <Typography
              variant="caption"
              color="textSecondary"
              display="block"
              sx={{ mb: 2 }}
            >
              Browser notifications for alerts
            </Typography>

            <FormControlLabel
              control={
                <Switch
                  checked={settings.enableInAppNotifications}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      enableInAppNotifications: e.target.checked,
                    })
                  }
                  disabled={saving}
                />
              }
              label="In-App Notifications"
            />
            <Typography
              variant="caption"
              color="textSecondary"
              display="block"
              sx={{ mb: 2 }}
            >
              Toast messages within the dashboard
            </Typography>

            <FormControlLabel
              control={
                <Switch
                  checked={settings.enableEmailNotifications}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      enableEmailNotifications: e.target.checked,
                    })
                  }
                  disabled={saving}
                />
              }
              label="Email Notifications"
            />
            <Typography variant="caption" color="textSecondary" display="block">
              Email alerts for critical events
            </Typography>
          </Box>

          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Notification Threshold (minutes)
            </Typography>
            <TextField
              type="number"
              value={settings.notificationThreshold}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  notificationThreshold: parseFloat(e.target.value) || 0,
                })
              }
              fullWidth
              size="small"
              disabled={saving}
              inputProps={{ min: 0, step: 1 }}
            />
            <Typography variant="caption" color="textSecondary">
              Minimum time between duplicate alerts (prevents spam)
            </Typography>
          </Box>

          <Button
            variant="contained"
            color="primary"
            onClick={handleSaveSettings}
            disabled={saving}
            fullWidth
          >
            {saving ? <CircularProgress size={24} /> : 'Save Alert Settings'}
          </Button>
        </Stack>
      </CardContent>
    </Card>
  );
};

export default AlertSettings;
