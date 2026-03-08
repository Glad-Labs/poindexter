import {
  listSettings,
  getSetting,
  createOrUpdateSetting,
  deleteSetting,
  bulkUpdateSettings,
  getSettingWithDefault,
} from '../settingsService';
import * as cofounderAgentClient from '../cofounderAgentClient';

// Mock cofounderAgentClient
vi.mock('../cofounderAgentClient');

describe('settingsService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('listSettings', () => {
    test('fetches all settings', async () => {
      const mockSettings = {
        theme: 'dark',
        auto_refresh: true,
        mercury_api_key: 'key123',
      };

      cofounderAgentClient.makeRequest.mockResolvedValue(mockSettings);

      const result = await listSettings();

      expect(cofounderAgentClient.makeRequest).toHaveBeenCalledWith(
        '/api/settings',
        'GET'
      );

      expect(result).toEqual(mockSettings);
    });

    test('throws on API error', async () => {
      cofounderAgentClient.makeRequest.mockRejectedValue(
        new Error('API error')
      );

      await expect(listSettings()).rejects.toThrow('API error');
    });
  });

  describe('getSetting', () => {
    test('fetches a specific setting', async () => {
      const mockSetting = { theme: 'dark' };

      cofounderAgentClient.makeRequest.mockResolvedValue(mockSetting);

      const result = await getSetting('theme');

      expect(cofounderAgentClient.makeRequest).toHaveBeenCalledWith(
        '/api/settings/theme',
        'GET',
        null,
        false,
        null,
        30000,
        expect.objectContaining({
          shouldSuppressErrorLog: expect.any(Function),
        })
      );

      const suppressFn =
        cofounderAgentClient.makeRequest.mock.calls[0][6]
          .shouldSuppressErrorLog;
      expect(suppressFn({ status: 404 })).toBe(true);
      expect(suppressFn({ status: 500 })).toBe(false);

      expect(result).toEqual(mockSetting);
    });

    test('throws when setting not found', async () => {
      cofounderAgentClient.makeRequest.mockRejectedValue(
        new Error('Setting not found')
      );

      await expect(getSetting('nonexistent')).rejects.toThrow();
    });
  });

  describe('createOrUpdateSetting', () => {
    test('creates or updates a setting', async () => {
      const mockResponse = { key: 'theme', value: 'light' };

      cofounderAgentClient.makeRequest.mockResolvedValue(mockResponse);

      const result = await createOrUpdateSetting('theme', 'light');

      expect(cofounderAgentClient.makeRequest).toHaveBeenCalledWith(
        '/api/settings',
        'POST',
        {
          key: 'theme',
          value: 'light',
        }
      );

      expect(result).toEqual(mockResponse);
    });

    test('stringifies non-string values', async () => {
      cofounderAgentClient.makeRequest.mockResolvedValue({ success: true });

      await createOrUpdateSetting('auto_refresh', true);

      expect(cofounderAgentClient.makeRequest).toHaveBeenCalledWith(
        '/api/settings',
        'POST',
        {
          key: 'auto_refresh',
          value: 'true',
        }
      );
    });

    test('handles object values', async () => {
      const objectValue = { nested: { setting: 'value' } };

      cofounderAgentClient.makeRequest.mockResolvedValue({ success: true });

      await createOrUpdateSetting('complex_setting', objectValue);

      expect(cofounderAgentClient.makeRequest).toHaveBeenCalledWith(
        '/api/settings',
        'POST',
        {
          key: 'complex_setting',
          value: JSON.stringify(objectValue),
        }
      );
    });
  });

  describe('deleteSetting', () => {
    test('deletes a setting', async () => {
      const mockResponse = { success: true };

      cofounderAgentClient.makeRequest.mockResolvedValue(mockResponse);

      const result = await deleteSetting('theme');

      expect(cofounderAgentClient.makeRequest).toHaveBeenCalledWith(
        '/api/settings/theme',
        'DELETE'
      );

      expect(result).toEqual(mockResponse);
    });

    test('throws on deletion error', async () => {
      cofounderAgentClient.makeRequest.mockRejectedValue(
        new Error('Cannot delete')
      );

      await expect(deleteSetting('theme')).rejects.toThrow();
    });
  });

  describe('bulkUpdateSettings', () => {
    test('updates multiple settings', async () => {
      const settingsUpdate = {
        theme: 'light',
        auto_refresh: false,
      };

      const mockResponse = { updated: 2 };

      cofounderAgentClient.makeRequest.mockResolvedValue(mockResponse);

      const result = await bulkUpdateSettings(settingsUpdate);

      expect(cofounderAgentClient.makeRequest).toHaveBeenCalledWith(
        '/api/settings/bulk',
        'POST',
        settingsUpdate
      );

      expect(result).toEqual(mockResponse);
    });
  });

  describe('getSettingWithDefault', () => {
    test('returns setting value if found', async () => {
      cofounderAgentClient.makeRequest.mockResolvedValue({
        value: 'dark',
      });

      const result = await getSettingWithDefault('theme', 'light');

      expect(result).toBe('dark');
    });

    test('returns default value if setting not found', async () => {
      cofounderAgentClient.makeRequest.mockRejectedValue(
        new Error('Not found')
      );

      const result = await getSettingWithDefault('theme', 'light');

      expect(result).toBe('light');
    });

    test('returns null default if not provided', async () => {
      cofounderAgentClient.makeRequest.mockRejectedValue(
        new Error('Not found')
      );

      const result = await getSettingWithDefault('theme', null);

      expect(result).toBeNull();
    });

    test('logs warning when setting not found', async () => {
      console.warn = vi.fn();

      cofounderAgentClient.makeRequest.mockRejectedValue(
        new Error('Not found')
      );

      await getSettingWithDefault('theme', 'light');

      expect(console.warn).toHaveBeenCalled();
    });
  });
});
