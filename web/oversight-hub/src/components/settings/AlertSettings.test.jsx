import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import AlertSettings from './AlertSettings';
import * as settingsService from '../../services/settingsService';

// Mock the settings service
vi.mock('../../services/settingsService');

describe('AlertSettings Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('renders the component with title', async () => {
    settingsService.getSetting.mockResolvedValue({ value: '10' });
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<AlertSettings />);
    await waitFor(() => {
      expect(
        screen.getByText('Alert & Notification Settings')
      ).toBeInTheDocument();
    });
  });

  test('loads alert settings on mount', async () => {
    settingsService.getSetting.mockResolvedValue({ value: '10' });
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<AlertSettings />);

    await waitFor(() => {
      expect(settingsService.getSetting).toHaveBeenCalledWith(
        'cost_alert_threshold'
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

    render(<AlertSettings />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  test('uses default values when settings fail to load', async () => {
    // Each getSetting call has an inner .catch() returning defaults,
    // so individual failures are silently handled with defaults.
    settingsService.getSetting.mockRejectedValue(new Error('Network error'));

    render(<AlertSettings />);

    await waitFor(() => {
      expect(
        screen.getByText('Alert & Notification Settings')
      ).toBeInTheDocument();
    });
  });

  test('displays error message when saving fails', async () => {
    settingsService.getSetting.mockResolvedValue({ value: '10' });
    settingsService.createOrUpdateSetting.mockRejectedValue(
      new Error('Failed to save settings')
    );

    render(<AlertSettings />);

    await waitFor(() => {
      const saveButton = screen.getByText('Save Alert Settings');
      fireEvent.click(saveButton);
    });

    await waitFor(() => {
      expect(screen.getByText(/Failed to save settings/)).toBeInTheDocument();
    });
  });

  test('displays success message after saving', async () => {
    settingsService.getSetting.mockResolvedValue({ value: '10' });
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<AlertSettings />);

    await waitFor(() => {
      const saveButton = screen.getByText('Save Alert Settings');
      expect(saveButton).toBeInTheDocument();
    });

    const saveButton = screen.getByText('Save Alert Settings');
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(
        screen.getByText('Settings saved successfully!')
      ).toBeInTheDocument();
    });
  });

  test('disables save button while saving', async () => {
    settingsService.getSetting.mockResolvedValue({ value: '10' });
    settingsService.createOrUpdateSetting.mockImplementation(
      () =>
        new Promise(() => {
          // Never resolves to keep component in saving state
        })
    );

    render(<AlertSettings />);

    await waitFor(() => {
      const saveButton = screen.getByText('Save Alert Settings');
      expect(saveButton).toBeInTheDocument();
    });

    const saveButton = screen.getByText('Save Alert Settings');
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(saveButton).toBeDisabled();
    });
  });

  test('shows notification method toggles', async () => {
    settingsService.getSetting.mockResolvedValue({ value: 'true' });
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<AlertSettings />);

    await waitFor(() => {
      expect(screen.getByText('Desktop Notifications')).toBeInTheDocument();
      expect(screen.getByText('In-App Notifications')).toBeInTheDocument();
      expect(screen.getByText('Email Notifications')).toBeInTheDocument();
    });
  });

  test('shows cost alert threshold input when cost alerts enabled', async () => {
    settingsService.getSetting.mockResolvedValue({ value: 'true' });
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<AlertSettings />);

    await waitFor(() => {
      expect(screen.getByText('Cost Alert Threshold ($)')).toBeInTheDocument();
    });
  });

  test('shows notification threshold input', async () => {
    settingsService.getSetting.mockResolvedValue({ value: '5' });
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<AlertSettings />);

    await waitFor(() => {
      expect(
        screen.getByText(/Notification Threshold \(minutes\)/)
      ).toBeInTheDocument();
    });
  });
});
