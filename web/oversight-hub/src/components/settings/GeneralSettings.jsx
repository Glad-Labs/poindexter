import logger from '@/lib/logger';
import React, { useState, useEffect } from 'react';
import {
  Box,
  Card,
  CardContent,
  CardHeader,
  TextField,
  Switch,
  FormControlLabel,
  Button,
  CircularProgress,
  Alert,
  Stack,
  Typography,
} from '@mui/material';
import {
  createOrUpdateSetting,
  getSetting,
} from '../../services/settingsService';

/**
 * GeneralSettings Component (Phase 1.3)
 *
 * Manages general application settings:
 * - Auto-refresh interval (configurable, default 30s)
 * - Task table row limit per page
 * - Default task quality preference
 * - Enable/disable notifications
 */
export const GeneralSettings = () => {
  const [settings, setSettings] = useState({
    autoRefreshInterval: 30,
    taskTableRowLimit: 10,
    enableNotifications: true,
    defaultTaskQualityPreference: 'balanced',
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
      const [interval, rowLimit, notifications, quality] = await Promise.all([
        getSetting('auto_refresh_interval').catch(() => ({ value: '30' })),
        getSetting('task_table_row_limit').catch(() => ({ value: '10' })),
        getSetting('enable_notifications').catch(() => ({ value: 'true' })),
        getSetting('default_task_quality').catch(() => ({ value: 'balanced' })),
      ]);

      setSettings({
        autoRefreshInterval: parseInt(interval?.value || '30', 10),
        taskTableRowLimit: parseInt(rowLimit?.value || '10', 10),
        enableNotifications:
          notifications?.value === 'true' || notifications?.value === true,
        defaultTaskQualityPreference: quality?.value || 'balanced',
      });
    } catch (err) {
      logger.error('Failed to load general settings:', err);
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
          'auto_refresh_interval',
          settings.autoRefreshInterval.toString()
        ),
        createOrUpdateSetting(
          'task_table_row_limit',
          settings.taskTableRowLimit.toString()
        ),
        createOrUpdateSetting(
          'enable_notifications',
          settings.enableNotifications.toString()
        ),
        createOrUpdateSetting(
          'default_task_quality',
          settings.defaultTaskQualityPreference
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
      <CardHeader title="General Settings" />
      <CardContent>
        <Stack spacing={3}>
          {error && <Alert severity="error">{error}</Alert>}
          {success && (
            <Alert severity="success">Settings saved successfully!</Alert>
          )}

          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Auto-refresh Interval (seconds)
            </Typography>
            <TextField
              label="Auto-refresh Interval (seconds)"
              type="number"
              value={settings.autoRefreshInterval}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  autoRefreshInterval: parseInt(e.target.value, 10),
                })
              }
              inputProps={{ min: 5, max: 300, step: 5 }}
              fullWidth
              size="small"
              disabled={saving}
              helperText="How often to refresh data. Range: 5-300 seconds"
            />
          </Box>

          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Task Table Rows Per Page
            </Typography>
            <TextField
              label="Task Table Rows Per Page"
              type="number"
              value={settings.taskTableRowLimit}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  taskTableRowLimit: parseInt(e.target.value, 10),
                })
              }
              inputProps={{ min: 5, max: 100, step: 5 }}
              fullWidth
              size="small"
              disabled={saving}
              helperText="Number of tasks to display per page. Range: 5-100"
            />
          </Box>

          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Default Task Quality Preference
            </Typography>
            <TextField
              label="Default Task Quality Preference"
              select
              value={settings.defaultTaskQualityPreference}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  defaultTaskQualityPreference: e.target.value,
                })
              }
              fullWidth
              size="small"
              disabled={saving}
              SelectProps={{
                native: true,
              }}
            >
              <option value="ultra_cheap">Ultra Cheap (Ollama)</option>
              <option value="cheap">Cheap (Gemini)</option>
              <option value="balanced">Balanced (Claude 3.5 Sonnet)</option>
              <option value="premium">Premium (Claude 3 Opus)</option>
            </TextField>
          </Box>

          <Box>
            <FormControlLabel
              control={
                <Switch
                  checked={settings.enableNotifications}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      enableNotifications: e.target.checked,
                    })
                  }
                  disabled={saving}
                />
              }
              label="Enable Desktop Notifications for Task Updates"
            />
          </Box>

          <Button
            variant="contained"
            color="primary"
            onClick={handleSaveSettings}
            disabled={saving}
            fullWidth
          >
            {saving ? <CircularProgress size={24} /> : 'Save General Settings'}
          </Button>
        </Stack>
      </CardContent>
    </Card>
  );
};

export default GeneralSettings;
