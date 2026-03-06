/**
 * Integration tests for settingsService and errorLoggingService
 *
 * These tests verify that the React services correctly call the backend API endpoints
 * Tests should be run against a running backend server (e.g., http://localhost:8000)
 *
 * Environment setup:
 * - REACT_APP_API_URL should point to backend server
 * - Backend must have /api/settings and /api/logs endpoints
 *
 * Run with: npm test -- settingsService.integration.test.js
 */

import {
  listSettings,
  getSetting,
  createOrUpdateSetting,
  deleteSetting,
  bulkUpdateSettings,
  getSettingWithDefault,
} from '../settingsService';

import {
  logError,
  logWarning,
  logInfo,
  getErrorLogs,
  deleteErrorLog,
  clearAllLogs,
} from '../errorLoggingService';

// Mock for integration tests - replace with real auth token when testing against actual backend
const TEST_AUTH_TOKEN = process.env.REACT_APP_TEST_AUTH_TOKEN || null;
const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

/**
 * Helper to set auth token for tests
 */
function setAuthToken(token) {
  // Store in localStorage as the real service expects
  if (token) {
    localStorage.setItem('authToken', token);
  } else {
    localStorage.removeItem('authToken');
  }
}

describe('settingsService Integration Tests', () => {
  beforeAll(() => {
    if (!TEST_AUTH_TOKEN) {
      console.warn(
        '⚠️  REACT_APP_TEST_AUTH_TOKEN not set. Some tests will be skipped.'
      );
    }
    if (!API_URL.includes('localhost') && !API_URL.includes('127.0.0.1')) {
      console.warn(
        '⚠️  Tests configured for remote API. Make sure backend is running.'
      );
    }
  });

  beforeEach(() => {
    // Clear any existing auth token
    localStorage.clear();
  });

  describe('Settings CRUD Operations', () => {
    test.skipIf(!TEST_AUTH_TOKEN)(
      'should fetch all settings from backend',
      async () => {
        setAuthToken(TEST_AUTH_TOKEN);

        const settings = await listSettings();

        expect(settings).toBeDefined();
        expect(typeof settings).toBe('object');
      }
    );

    test.skipIf(!TEST_AUTH_TOKEN)(
      'should fetch specific setting by key',
      async () => {
        setAuthToken(TEST_AUTH_TOKEN);

        const setting = await getSetting('theme');

        expect(setting).toBeDefined();
        expect(setting.key).toBe('theme');
      }
    );

    test.skipIf(!TEST_AUTH_TOKEN)('should create a new setting', async () => {
      setAuthToken(TEST_AUTH_TOKEN);
      const testKey = `test_setting_${Date.now()}`;
      const testValue = 'test_value_' + Math.random();

      const created = await createOrUpdateSetting(testKey, testValue);

      expect(created).toBeDefined();
      expect(created.key).toBe(testKey);
      expect(created.value).toBe(testValue);
    });

    test.skipIf(!TEST_AUTH_TOKEN)(
      'should update existing setting',
      async () => {
        setAuthToken(TEST_AUTH_TOKEN);
        const updatedValue = 'updated_' + Date.now();

        const updated = await createOrUpdateSetting('theme', updatedValue);

        expect(updated.value).toBe(updatedValue);
      }
    );

    test.skipIf(!TEST_AUTH_TOKEN)(
      'should handle missing settings gracefully',
      async () => {
        setAuthToken(TEST_AUTH_TOKEN);

        const setting = await getSettingWithDefault(
          'nonexistent_key',
          'default_value'
        );

        expect(setting).toBe('default_value');
      }
    );

    test.skipIf(!TEST_AUTH_TOKEN)('should delete setting', async () => {
      setAuthToken(TEST_AUTH_TOKEN);
      const testKey = `delete_test_${Date.now()}`;

      // Create first
      await createOrUpdateSetting(testKey, 'test_value');

      // Then delete
      const result = await deleteSetting(testKey);
      expect(result).toBeTruthy();

      // Verify it's deleted by checking with default
      const setting = await getSettingWithDefault(testKey, null);
      expect(setting).toBeNull();
    });
  });

  describe('Bulk Settings Operations', () => {
    test.skipIf(!TEST_AUTH_TOKEN)(
      'should update multiple settings at once',
      async () => {
        setAuthToken(TEST_AUTH_TOKEN);
        const updates = {
          theme: 'dark_' + Date.now(),
          auto_refresh: false,
          batch_size: 25,
        };

        const result = await bulkUpdateSettings(updates);

        expect(result).toBeDefined();
        expect(result.updated_count).toBe(Object.keys(updates).length);
      }
    );
  });

  describe('Error Logging', () => {
    test.skipIf(!TEST_AUTH_TOKEN)('should log error to backend', async () => {
      setAuthToken(TEST_AUTH_TOKEN);
      const timestamp = Date.now();

      const result = await logError(
        new Error(`Test error ${timestamp}`),
        'test_component',
        { context: 'test context' }
      );

      expect(result).toBeDefined();
    });

    test.skipIf(!TEST_AUTH_TOKEN)('should log warning to backend', async () => {
      setAuthToken(TEST_AUTH_TOKEN);

      const result = await logWarning('Test warning', 'test_component', {
        context: 'test context',
      });

      expect(result).toBeDefined();
    });

    test.skipIf(!TEST_AUTH_TOKEN)('should log info to backend', async () => {
      setAuthToken(TEST_AUTH_TOKEN);

      const result = await logInfo('Test info message', 'test_component', {
        context: 'test context',
      });

      expect(result).toBeDefined();
    });

    test.skipIf(!TEST_AUTH_TOKEN)(
      'should retrieve error logs from backend',
      async () => {
        setAuthToken(TEST_AUTH_TOKEN);

        // First log an error
        await logError(new Error('Integration test error'), 'test_component');

        // Then retrieve logs
        const logs = await getErrorLogs();

        expect(Array.isArray(logs)).toBeTruthy();
        // Verify our error is in the logs
        const ourLog = logs.find((log) =>
          log.message.includes('Integration test error')
        );
        expect(ourLog).toBeDefined();
      }
    );

    test.skipIf(!TEST_AUTH_TOKEN)(
      'should delete specific error log',
      async () => {
        setAuthToken(TEST_AUTH_TOKEN);

        // First log an error
        const logResult = await logError(
          new Error('Error to delete'),
          'test_component'
        );

        // Assuming the result contains an ID or timestamp
        if (logResult && logResult.id) {
          const deleteResult = await deleteErrorLog(logResult.id);
          expect(deleteResult).toBeTruthy();
        }
      }
    );

    test.skipIf(!TEST_AUTH_TOKEN)('should clear all logs', async () => {
      setAuthToken(TEST_AUTH_TOKEN);

      // Log some errors first
      await logError(new Error('Error 1'), 'test');
      await logError(new Error('Error 2'), 'test');

      // Clear all
      const result = await clearAllLogs();
      expect(result).toBeTruthy();

      // Verify logs are cleared
      const logs = await getErrorLogs();
      expect(logs.length).toBe(0);
    });
  });

  describe('Error Handling', () => {
    test('should handle API errors gracefully', async () => {
      // Don't set auth token - should fail with 401

      try {
        await listSettings();
        fail('Should have thrown an error');
      } catch (error) {
        expect(error).toBeDefined();
        expect(
          error.message.includes('401') ||
            error.message.includes('Unauthorized') ||
            error.message.includes('Not authenticated') ||
            error.message.includes('authentication') ||
            error.message.includes('auth')
        ).toBe(true);
      }
    });

    test('should handle network errors', async () => {
      // Try to connect to non-existent server
      const backupUrl = process.env.REACT_APP_API_URL;
      process.env.REACT_APP_API_URL =
        'http://invalid-url-xyz-12345.example.com:9999';

      try {
        await listSettings();
        fail('Should have thrown a network error');
      } catch (error) {
        expect(error).toBeDefined();
        // Should be a network error or timeout
      } finally {
        process.env.REACT_APP_API_URL = backupUrl;
      }
    });
  });

  describe('Response Validation', () => {
    test.skipIf(!TEST_AUTH_TOKEN)(
      'should validate settings response schema',
      async () => {
        setAuthToken(TEST_AUTH_TOKEN);

        const settings = await listSettings();

        // Each setting should have key and value
        if (settings && typeof settings === 'object') {
          Object.entries(settings).forEach(([key, value]) => {
            expect(typeof key).toBe('string');
            expect(value).toBeDefined();
          });
        }
      }
    );

    test.skipIf(!TEST_AUTH_TOKEN)(
      'should validate error log schema',
      async () => {
        setAuthToken(TEST_AUTH_TOKEN);

        const logs = await getErrorLogs();

        if (Array.isArray(logs) && logs.length > 0) {
          logs.forEach((log) => {
            expect(log.message).toBeDefined();
            expect(log.timestamp).toBeDefined();
            expect(log.level).toMatch(/error|warning|info/i);
          });
        }
      }
    );
  });
});

/**
 * Performance Tests (optional)
 * Verify that service calls complete within reasonable time
 */
describe('Performance Tests', () => {
  const MAX_RESPONSE_TIME_MS = 5000; // 5 seconds

  test.skipIf(!TEST_AUTH_TOKEN)(
    'settings fetch should complete within 5 seconds',
    async () => {
      setAuthToken(TEST_AUTH_TOKEN);
      const start = Date.now();

      await listSettings();

      const duration = Date.now() - start;
      expect(duration).toBeLessThan(MAX_RESPONSE_TIME_MS);
    }
  );

  test.skipIf(!TEST_AUTH_TOKEN)('error logging should be fast', async () => {
    setAuthToken(TEST_AUTH_TOKEN);
    const start = Date.now();

    await logError(new Error('Test'), 'perf_test');

    const duration = Date.now() - start;
    expect(duration).toBeLessThan(MAX_RESPONSE_TIME_MS);
  });
});
