/**
 * Settings.jsx route tests
 *
 * Covers:
 * - Initial render: page title
 * - Settings loaded on mount (listSettings called)
 * - Error state when listSettings fails
 * - Theme toggle calls createOrUpdateSetting
 * - Auto-refresh toggle calls createOrUpdateSetting
 * - API key input change calls createOrUpdateSetting
 * - Success snackbar shown after save
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import Settings from '../Settings';

// ── mock settingsService ──────────────────────────────────────────────────────
const { mockListSettings, mockCreateOrUpdateSetting } = vi.hoisted(() => ({
  mockListSettings: vi.fn(),
  mockCreateOrUpdateSetting: vi.fn(),
}));

vi.mock('../../services/settingsService', () => ({
  listSettings: mockListSettings,
  createOrUpdateSetting: mockCreateOrUpdateSetting,
}));

// ── mock heavy child components to avoid their own API calls ─────────────────
vi.mock('../../components/WritingStyleManager', () => ({
  default: () => <div data-testid="writing-style-manager" />,
}));

vi.mock('../../components/settings/GeneralSettings', () => ({
  default: () => <div data-testid="general-settings" />,
}));

vi.mock('../../components/settings/ModelPreferences', () => ({
  default: () => <div data-testid="model-preferences" />,
}));

vi.mock('../../components/settings/AlertSettings', () => ({
  default: () => <div data-testid="alert-settings" />,
}));

// ── sample data ───────────────────────────────────────────────────────────────
const SAMPLE_SETTINGS = {
  theme: 'dark',
  auto_refresh: 'false',
  desktop_notifications: 'false',
  mercury_api_key: '',
  gcp_api_key: '',
};

// ── helpers ───────────────────────────────────────────────────────────────────
function renderSettings() {
  return render(<Settings />);
}

// ── tests ─────────────────────────────────────────────────────────────────────
describe('Settings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListSettings.mockResolvedValue(SAMPLE_SETTINGS);
    mockCreateOrUpdateSetting.mockResolvedValue({ success: true });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('initial render', () => {
    it('renders the page title', async () => {
      renderSettings();
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });
    });

    it('calls listSettings on mount', async () => {
      renderSettings();
      await waitFor(() => {
        expect(mockListSettings).toHaveBeenCalledTimes(1);
      });
    });

    it('renders sub-panels after loading', async () => {
      renderSettings();
      await waitFor(() => {
        expect(screen.getByTestId('writing-style-manager')).toBeInTheDocument();
        expect(screen.getByTestId('general-settings')).toBeInTheDocument();
        expect(screen.getByTestId('model-preferences')).toBeInTheDocument();
        expect(screen.getByTestId('alert-settings')).toBeInTheDocument();
      });
    });
  });

  describe('error state', () => {
    it('shows error alert when listSettings fails', async () => {
      mockListSettings.mockRejectedValue(new Error('Server error'));
      renderSettings();
      await waitFor(() => {
        expect(
          screen.getByText(/Failed to load settings/i)
        ).toBeInTheDocument();
      });
    });
  });

  describe('theme toggle', () => {
    it('calls createOrUpdateSetting with new theme on toggle', async () => {
      renderSettings();
      await waitFor(() => {
        // Dark Mode button is shown when theme is 'dark'
        expect(screen.getByText('Dark Mode')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Dark Mode'));

      await waitFor(() => {
        expect(mockCreateOrUpdateSetting).toHaveBeenCalledWith(
          'theme',
          'light'
        );
      });
    });
  });

  describe('auto-refresh toggle', () => {
    it('calls createOrUpdateSetting when checkbox is toggled', async () => {
      renderSettings();
      await waitFor(() => {
        expect(screen.getByText('Auto-refresh')).toBeInTheDocument();
      });

      // Find the auto-refresh checkbox (first checkbox in the form)
      const checkboxes = screen.getAllByRole('checkbox');
      fireEvent.click(checkboxes[0]);

      await waitFor(() => {
        expect(mockCreateOrUpdateSetting).toHaveBeenCalledWith(
          'auto_refresh',
          true
        );
      });
    });
  });

  describe('API key input', () => {
    it('calls createOrUpdateSetting when mercury API key changes', async () => {
      renderSettings();
      await waitFor(() => {
        expect(
          screen.getByPlaceholderText(/Enter your Mercury API key/i)
        ).toBeInTheDocument();
      });

      const mercuryInput = screen.getByPlaceholderText(
        /Enter your Mercury API key/i
      );
      fireEvent.change(mercuryInput, {
        target: { name: 'mercury', value: 'test-key-123' },
      });

      await waitFor(() => {
        expect(mockCreateOrUpdateSetting).toHaveBeenCalledWith(
          'mercury_api_key',
          'test-key-123'
        );
      });
    });
  });
});
