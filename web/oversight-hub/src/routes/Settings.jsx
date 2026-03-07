import logger from '@/lib/logger';
import React, { useState, useEffect } from 'react';
import {
  Container,
  Alert,
  CircularProgress,
  Snackbar,
  Box,
  Divider,
} from '@mui/material';
import WritingStyleManager from '../components/WritingStyleManager';
import GeneralSettings from '../components/settings/GeneralSettings';
import ModelPreferences from '../components/settings/ModelPreferences';
import AlertSettings from '../components/settings/AlertSettings';
import {
  listSettings,
  createOrUpdateSetting,
} from '../services/settingsService';
import './Settings.css';

function Settings() {
  // State for API-backed settings
  const [theme, setTheme] = useState('dark');
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [desktopNotifications, setDesktopNotifications] = useState(false);
  const [apiKeys, setApiKeys] = useState({
    mercury: '',
    gcp: '',
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  // Load settings from API on mount
  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      setLoading(true);
      setError(null);
      const settings = await listSettings();

      // Parse settings from API response
      if (settings) {
        setTheme(settings.theme || 'dark');
        setAutoRefresh(
          settings.auto_refresh === 'true' || settings.auto_refresh === true
        );
        setDesktopNotifications(
          settings.desktop_notifications === 'true' ||
            settings.desktop_notifications === true
        );
        setApiKeys({
          mercury: settings.mercury_api_key || '',
          gcp: settings.gcp_api_key || '',
        });
      }
    } catch (err) {
      logger.error('Failed to load settings:', err);
      setError(`Failed to load settings: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const toggleTheme = async () => {
    const newTheme = theme === 'light' ? 'dark' : 'light';
    try {
      setSaving(true);
      await createOrUpdateSetting('theme', newTheme);
      setTheme(newTheme);
      setSuccess(true);
    } catch (err) {
      setError(`Failed to update theme: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const toggleAutoRefresh = async () => {
    const newValue = !autoRefresh;
    try {
      setSaving(true);
      await createOrUpdateSetting('auto_refresh', newValue);
      setAutoRefresh(newValue);
      setSuccess(true);
    } catch (err) {
      setError(`Failed to update auto-refresh: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const toggleDesktopNotifications = async () => {
    const newValue = !desktopNotifications;
    try {
      setSaving(true);
      await createOrUpdateSetting('desktop_notifications', newValue);
      setDesktopNotifications(newValue);
      setSuccess(true);
    } catch (err) {
      setError(`Failed to update notifications: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleApiKeyChange = async (e) => {
    const { name, value } = e.target;
    const newApiKeys = { ...apiKeys, [name]: value };
    setApiKeys(newApiKeys);

    // Save to API after user stops typing (debounced)
    try {
      setSaving(true);
      const settingKey = name === 'mercury' ? 'mercury_api_key' : 'gcp_api_key';
      await createOrUpdateSetting(settingKey, value);
      setSuccess(true);
    } catch (err) {
      setError(`Failed to update API key: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="settings-container">
      <div className="dashboard-header">
        <h1 className="dashboard-title">Settings</h1>
        <p className="dashboard-subtitle">Customize your experience</p>
      </div>

      {/* Loading State */}
      {loading && (
        <Container maxWidth="md" sx={{ py: 3, textAlign: 'center' }}>
          <CircularProgress />
          <p>Loading settings...</p>
        </Container>
      )}

      {/* Error State */}
      {error && (
        <Container maxWidth="md" sx={{ py: 2 }}>
          <Alert severity="error" onClose={() => setError(null)}>
            {error}
          </Alert>
        </Container>
      )}

      {/* Writing Style Manager */}
      {!loading && (
        <Container maxWidth="md" sx={{ py: 3 }}>
          <WritingStyleManager />
        </Container>
      )}

      {/* Phase 1.3 Settings Extensions */}
      {!loading && (
        <>
          <Container maxWidth="md" sx={{ py: 3 }}>
            <GeneralSettings />
          </Container>

          <Container maxWidth="md" sx={{ py: 3 }}>
            <ModelPreferences />
          </Container>

          <Container maxWidth="md" sx={{ py: 3 }}>
            <AlertSettings />
          </Container>

          <Divider sx={{ my: 4 }} />
        </>
      )}

      {!loading && (
        <>
          <div className="settings-section">
            <h2>Appearance</h2>
            <p>Customize how the application looks and feels.</p>

            <div className="setting-item">
              <div className="setting-info">
                <h3>Theme</h3>
                <p>Choose between light and dark mode</p>
              </div>
              <button
                className="theme-toggle-btn"
                onClick={toggleTheme}
                disabled={saving}
                aria-label={`Switch to ${
                  theme === 'light' ? 'dark' : 'light'
                } theme`}
              >
                {saving
                  ? '...'
                  : theme === 'light'
                    ? 'Light Mode'
                    : 'Dark Mode'}
              </button>
            </div>
          </div>

          <div className="settings-section">
            <h2>System</h2>
            <p>Application and performance settings.</p>

            <div className="setting-item">
              <div className="setting-info">
                <h3>Auto-refresh</h3>
                <p>Automatically refresh data every 30 seconds</p>
              </div>
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={autoRefresh}
                  onChange={toggleAutoRefresh}
                  disabled={saving}
                />
                <span className="slider"></span>
              </label>
            </div>

            <div className="setting-item">
              <div className="setting-info">
                <h3>Notifications</h3>
                <p>Receive desktop notifications for important updates</p>
              </div>
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={desktopNotifications}
                  onChange={toggleDesktopNotifications}
                  disabled={saving}
                />
                <span className="slider"></span>
              </label>
            </div>
          </div>

          <div className="settings-section">
            <h2>API Keys</h2>
            <p>Manage your API keys for third-party services.</p>

            <div className="setting-item">
              <div className="setting-info">
                <h3>Mercury API Key</h3>
                <p>Used for financial data integration.</p>
                <input
                  type="password"
                  name="mercury"
                  className="api-key-input"
                  value={apiKeys.mercury}
                  onChange={handleApiKeyChange}
                  disabled={saving}
                  placeholder="Enter your Mercury API key"
                />
              </div>
            </div>

            <div className="setting-item">
              <div className="setting-info">
                <h3>GCP Billing API Key</h3>
                <p>Used for Google Cloud Platform cost analysis.</p>
                <input
                  type="password"
                  name="gcp"
                  className="api-key-input"
                  value={apiKeys.gcp}
                  onChange={handleApiKeyChange}
                  disabled={saving}
                  placeholder="Enter your GCP Billing API key"
                />
              </div>
            </div>
          </div>
        </>
      )}

      {/* Success Toast */}
      <Snackbar
        open={success}
        autoHideDuration={3000}
        onClose={() => setSuccess(false)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        message="✓ Settings saved successfully"
      />
    </div>
  );
}

export default Settings;
