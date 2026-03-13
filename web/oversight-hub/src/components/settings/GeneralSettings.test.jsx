// @vitest-environment jsdom
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, test, expect, beforeEach, vi } from 'vitest';

vi.mock('@/lib/logger', () => ({
  default: {
    log: vi.fn(),
    error: vi.fn(),
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
  },
}));

import GeneralSettings from './GeneralSettings';
import * as settingsService from '../../services/settingsService';

// Mock the settings service
vi.mock('../../services/settingsService');

describe('GeneralSettings Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('renders the component with title', async () => {
    settingsService.getSetting.mockResolvedValue({ value: '30' });
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<GeneralSettings />);

    await waitFor(() => {
      expect(screen.getByText('General Settings')).toBeInTheDocument();
    });
  });

  test('loads settings on mount', async () => {
    settingsService.getSetting.mockResolvedValue({ value: '30' });
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<GeneralSettings />);

    await waitFor(() => {
      expect(settingsService.getSetting).toHaveBeenCalledWith(
        'auto_refresh_interval'
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

    render(<GeneralSettings />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  test('uses default values when individual settings fail to load', async () => {
    // Each getSetting call has an inner .catch() that returns defaults,
    // so individual failures are silently handled.
    settingsService.getSetting.mockRejectedValue(new Error('Network error'));
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<GeneralSettings />);

    // Component renders normally with default values
    await waitFor(() => {
      expect(screen.getByText('General Settings')).toBeInTheDocument();
    });
  });

  test('displays error message when saving fails', async () => {
    settingsService.getSetting.mockResolvedValue({ value: '30' });
    settingsService.createOrUpdateSetting.mockRejectedValue(
      new Error('Failed to save settings')
    );

    render(<GeneralSettings />);

    await waitFor(() => {
      const saveButton = screen.getByText('Save General Settings');
      fireEvent.click(saveButton);
    });

    await waitFor(() => {
      expect(screen.getByText(/Failed to save settings/)).toBeInTheDocument();
    });
  });

  test('displays success message after saving', async () => {
    settingsService.getSetting.mockResolvedValue({ value: '30' });
    settingsService.createOrUpdateSetting.mockResolvedValue({});

    render(<GeneralSettings />);

    await waitFor(() => {
      expect(screen.getByText('Save General Settings')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Save General Settings'));

    await waitFor(() => {
      expect(
        screen.getByText('Settings saved successfully!')
      ).toBeInTheDocument();
    });
  });

  test('disables save button while saving', async () => {
    settingsService.getSetting.mockResolvedValue({ value: '30' });
    settingsService.createOrUpdateSetting.mockImplementation(
      () =>
        new Promise(() => {
          // Never resolves to keep component in saving state
        })
    );

    render(<GeneralSettings />);

    await waitFor(() => {
      expect(screen.getByText('Save General Settings')).toBeInTheDocument();
    });

    const saveButton = screen.getByText('Save General Settings');
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(saveButton).toBeDisabled();
    });
  });
});

// ---------------------------------------------------------------------------
// a11y — issue #769: TextField select elements have label props
// ---------------------------------------------------------------------------

describe('GeneralSettings — a11y: TextField label associations (issue #769)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    settingsService.getSetting.mockResolvedValue({ value: '30' });
    settingsService.createOrUpdateSetting.mockResolvedValue({});
  });

  it('Auto-refresh Interval field has accessible label', async () => {
    render(<GeneralSettings />);
    await waitFor(() => {
      const field = screen.getByLabelText(/Auto-refresh Interval/i);
      expect(field).toBeInTheDocument();
    });
  });

  it('Task Table Rows Per Page field has accessible label', async () => {
    render(<GeneralSettings />);
    await waitFor(() => {
      const field = screen.getByLabelText(/Task Table Rows Per Page/i);
      expect(field).toBeInTheDocument();
    });
  });

  it('Default Task Quality Preference select has accessible label', async () => {
    render(<GeneralSettings />);
    await waitFor(() => {
      const field = screen.getByLabelText(/Default Task Quality Preference/i);
      expect(field).toBeInTheDocument();
    });
  });
});
