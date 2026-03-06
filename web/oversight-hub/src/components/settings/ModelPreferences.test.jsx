import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ModelPreferences from './ModelPreferences';
import * as settingsService from '../../services/settingsService';

// Mock the settings service
vi.mock('../../services/settingsService');

describe('ModelPreferences Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('renders the component with title', async () => {
    settingsService.getSetting.mockImplementation((key) => {
      if (key === 'fallback_llm_providers') {
        return Promise.resolve({ value: '["anthropic","openai","google"]' });
      }
      return Promise.resolve({ value: 'ollama' });
    });
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<ModelPreferences />);
    await waitFor(() => {
      expect(
        screen.getByText('Model Provider Preferences')
      ).toBeInTheDocument();
    });
  });

  test('loads model preferences on mount', async () => {
    settingsService.getSetting.mockResolvedValue({ value: 'ollama' });
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<ModelPreferences />);

    await waitFor(() => {
      expect(settingsService.getSetting).toHaveBeenCalledWith(
        'primary_llm_provider'
      );
    });
  });

  test('displays loading spinner while loading', () => {
    settingsService.getSetting.mockImplementation(
      () =>
        new Promise(() => {
          // Never resolves to keep component in loading state
        })
    );

    render(<ModelPreferences />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  test('uses default values when settings fail to load', async () => {
    // Each getSetting call has an inner .catch() returning defaults,
    // so individual failures are silently handled with defaults.
    settingsService.getSetting.mockRejectedValue(new Error('Network error'));

    render(<ModelPreferences />);

    await waitFor(() => {
      expect(
        screen.getByText('Model Provider Preferences')
      ).toBeInTheDocument();
    });
  });

  test('displays success message after saving', async () => {
    settingsService.getSetting.mockResolvedValue({ value: 'ollama' });
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<ModelPreferences />);

    await waitFor(() => {
      const saveButton = screen.getByText('Save Model Preferences');
      expect(saveButton).toBeInTheDocument();
    });

    const saveButton = screen.getByText('Save Model Preferences');
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(
        screen.getByText('Settings saved successfully!')
      ).toBeInTheDocument();
    });
  });

  test('disables save button while saving', async () => {
    settingsService.getSetting.mockResolvedValue({ value: 'ollama' });
    settingsService.createOrUpdateSetting.mockImplementation(
      () =>
        new Promise(() => {
          // Never resolves to keep component in saving state
        })
    );

    render(<ModelPreferences />);

    await waitFor(() => {
      const saveButton = screen.getByText('Save Model Preferences');
      expect(saveButton).toBeInTheDocument();
    });

    const saveButton = screen.getByText('Save Model Preferences');
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(saveButton).toBeDisabled();
    });
  });

  test('shows cost-optimized toggle', async () => {
    settingsService.getSetting.mockImplementation((key) => {
      if (key === 'fallback_llm_providers') {
        return Promise.resolve({ value: '["anthropic","openai","google"]' });
      }
      return Promise.resolve({ value: 'true' });
    });
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<ModelPreferences />);

    await waitFor(() => {
      expect(screen.getByText(/Cost-Optimized Mode/)).toBeInTheDocument();
    });
  });

  test('displays fallback providers as chips', async () => {
    settingsService.getSetting.mockImplementation((key) => {
      if (key === 'fallback_llm_providers') {
        return Promise.resolve({ value: '["anthropic","openai","google"]' });
      }
      return Promise.resolve({ value: '' });
    });
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<ModelPreferences />);

    await waitFor(() => {
      expect(screen.getByText('anthropic')).toBeInTheDocument();
      expect(screen.getByText('openai')).toBeInTheDocument();
      expect(screen.getByText('google')).toBeInTheDocument();
    });
  });
});
