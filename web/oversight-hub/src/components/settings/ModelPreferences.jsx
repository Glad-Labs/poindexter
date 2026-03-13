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
  Chip,
  FormControlLabel,
  Switch,
} from '@mui/material';
import {
  createOrUpdateSetting,
  getSetting,
} from '../../services/settingsService';

/**
 * ModelPreferences Component (Phase 1.3)
 *
 * Manages LLM model provider preferences:
 * - Default model provider selection
 * - Model fallback chain preferences
 * - Cost-vs-quality tradeoffs
 */
export const ModelPreferences = () => {
  const [settings, setSettings] = useState({
    primaryProvider: 'ollama',
    fallbackProviders: ['anthropic', 'openai', 'google'],
    costOptimized: true,
    preferredModels: {
      ollama: 'mistral',
      anthropic: 'claude-3-5-sonnet',
      openai: 'gpt-4-turbo',
      google: 'gemini-2.0-flash',
    },
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
      const [primary, fallback, costOpt, models] = await Promise.all([
        getSetting('primary_llm_provider').catch(() => ({ value: 'ollama' })),
        getSetting('fallback_llm_providers').catch(() => ({
          value: '["anthropic","openai","google"]',
        })),
        getSetting('cost_optimized').catch(() => ({ value: 'true' })),
        getSetting('preferred_models').catch(() => ({
          value: '{}',
        })),
      ]);

      setSettings({
        primaryProvider: primary?.value || 'ollama',
        fallbackProviders: (() => {
          try {
            return JSON.parse(
              fallback?.value || '["anthropic","openai","google"]'
            );
          } catch {
            return ['anthropic', 'openai', 'google'];
          }
        })(),
        costOptimized: costOpt?.value === 'true' || costOpt?.value === true,
        preferredModels: (() => {
          try {
            return JSON.parse(models?.value || '{}');
          } catch {
            return {};
          }
        })(),
      });
    } catch (err) {
      logger.error('Failed to load model preferences:', err);
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
        createOrUpdateSetting('primary_llm_provider', settings.primaryProvider),
        createOrUpdateSetting(
          'fallback_llm_providers',
          JSON.stringify(settings.fallbackProviders)
        ),
        createOrUpdateSetting(
          'cost_optimized',
          settings.costOptimized.toString()
        ),
        createOrUpdateSetting(
          'preferred_models',
          JSON.stringify(settings.preferredModels)
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
      <CardHeader title="Model Provider Preferences" />
      <CardContent>
        <Stack spacing={3}>
          {error && <Alert severity="error">{error}</Alert>}
          {success && (
            <Alert severity="success">Settings saved successfully!</Alert>
          )}

          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Primary LLM Provider
            </Typography>
            <TextField
              label="Primary LLM Provider"
              select
              value={settings.primaryProvider}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  primaryProvider: e.target.value,
                })
              }
              fullWidth
              size="small"
              disabled={saving}
              SelectProps={{
                native: true,
              }}
            >
              <option value="ollama">Ollama (Local, Free)</option>
              <option value="anthropic">Anthropic (Claude)</option>
              <option value="openai">OpenAI (GPT-4)</option>
              <option value="google">Google (Gemini)</option>
            </TextField>
            <Typography variant="caption" color="textSecondary">
              This provider will be used first for all requests
            </Typography>
          </Box>

          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Fallback Providers (in order)
            </Typography>
            <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
              {settings.fallbackProviders.map((provider) => (
                <Chip
                  key={provider}
                  label={provider}
                  onDelete={() =>
                    setSettings({
                      ...settings,
                      fallbackProviders: settings.fallbackProviders.filter(
                        (p) => p !== provider
                      ),
                    })
                  }
                  disabled={saving}
                />
              ))}
            </Stack>
            <Typography variant="caption" color="textSecondary">
              These providers are used in order if primary fails
            </Typography>
          </Box>

          <Box>
            <FormControlLabel
              control={
                <Switch
                  checked={settings.costOptimized}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      costOptimized: e.target.checked,
                    })
                  }
                  disabled={saving}
                />
              }
              label="Cost-Optimized Mode (prefer cheaper models)"
            />
            <Typography variant="caption" color="textSecondary" display="block">
              When enabled, the system prefers lower-cost models and providers
            </Typography>
          </Box>

          <Button
            variant="contained"
            color="primary"
            onClick={handleSaveSettings}
            disabled={saving}
            fullWidth
          >
            {saving ? <CircularProgress size={24} /> : 'Save Model Preferences'}
          </Button>
        </Stack>
      </CardContent>
    </Card>
  );
};

export default ModelPreferences;
