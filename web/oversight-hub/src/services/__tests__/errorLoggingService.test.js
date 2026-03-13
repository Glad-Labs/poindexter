import {
  logError,
  logErrorToBackend,
  logErrorToSentry,
} from '../errorLoggingService';
import * as cofounderAgentClient from '../cofounderAgentClient';
import * as Sentry from '@sentry/react';

// Mock cofounderAgentClient
vi.mock('../cofounderAgentClient');

// Mock @sentry/react so captureException is a spy
vi.mock('@sentry/react', () => ({
  captureException: vi.fn(),
}));

describe('errorLoggingService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    console.error = vi.fn();
  });

  describe('logErrorToBackend', () => {
    test('sends error to backend via makeRequest', async () => {
      const mockError = new Error('Test error');
      cofounderAgentClient.makeRequest.mockResolvedValue({ success: true });

      const result = await logErrorToBackend(mockError, {
        componentStack: 'Component > Parent > Root',
      });

      expect(cofounderAgentClient.makeRequest).toHaveBeenCalledWith(
        '/api/errors',
        'POST',
        expect.objectContaining({
          type: 'client_error',
          message: 'Test error',
          componentStack: 'Component > Parent > Root',
        })
      );

      expect(result).toEqual({ success: true });
    });

    test('includes custom context in error payload', async () => {
      const mockError = new Error('Test error');
      cofounderAgentClient.makeRequest.mockResolvedValue({ success: true });

      await logErrorToBackend(mockError, {
        customContext: { userId: '123', feature: 'tasks' },
      });

      expect(cofounderAgentClient.makeRequest).toHaveBeenCalledWith(
        '/api/errors',
        'POST',
        expect.objectContaining({
          custom_context: { userId: '123', feature: 'tasks' },
        })
      );
    });

    test('sets severity level in payload', async () => {
      const mockError = new Error('Critical error');
      cofounderAgentClient.makeRequest.mockResolvedValue({ success: true });

      await logErrorToBackend(mockError, { severity: 'critical' });

      expect(cofounderAgentClient.makeRequest).toHaveBeenCalledWith(
        '/api/errors',
        'POST',
        expect.objectContaining({
          severity: 'critical',
        })
      );
    });

    test('returns null on failure without throwing', async () => {
      const mockError = new Error('Test error');
      cofounderAgentClient.makeRequest.mockRejectedValue(
        new Error('Network error')
      );

      const result = await logErrorToBackend(mockError);

      expect(result).toBeNull();
      expect(console.error).toHaveBeenCalled();
    });

    test('includes node environment in payload', async () => {
      const mockError = new Error('Test error');
      cofounderAgentClient.makeRequest.mockResolvedValue({ success: true });

      await logErrorToBackend(mockError);

      expect(cofounderAgentClient.makeRequest).toHaveBeenCalledWith(
        '/api/errors',
        'POST',
        expect.objectContaining({
          environment: process.env.NODE_ENV,
        })
      );
    });
  });

  describe('logErrorToSentry', () => {
    test('sends error to Sentry if available', () => {
      const mockError = new Error('Test error');

      logErrorToSentry(mockError, {
        componentStack: 'Component > Root',
      });

      expect(Sentry.captureException).toHaveBeenCalledWith(
        mockError,
        expect.objectContaining({
          contexts: expect.objectContaining({
            react: expect.objectContaining({
              componentStack: 'Component > Root',
            }),
          }),
        })
      );
    });

    test('does not throw if Sentry is not available', () => {
      const mockError = new Error('Test error');

      expect(() => {
        logErrorToSentry(mockError);
      }).not.toThrow();
    });

    test('includes custom context when sending to Sentry', () => {
      const mockError = new Error('Test error');
      const customContext = { userId: '123' };

      logErrorToSentry(mockError, { customContext });

      expect(Sentry.captureException).toHaveBeenCalledWith(
        mockError,
        expect.objectContaining({
          contexts: expect.objectContaining({
            custom: customContext,
          }),
        })
      );
    });
  });

  describe('logError', () => {
    test('calls both Sentry and backend logging', async () => {
      cofounderAgentClient.makeRequest.mockResolvedValue({ success: true });

      const mockError = new Error('Test error');

      await logError(mockError, {
        componentStack: 'Component > Root',
        severity: 'critical',
      });

      expect(Sentry.captureException).toHaveBeenCalled();
      expect(cofounderAgentClient.makeRequest).toHaveBeenCalled();
    });

    test('continues even if Sentry fails', async () => {
      Sentry.captureException.mockImplementationOnce(() => {
        throw new Error('Sentry failed');
      });
      cofounderAgentClient.makeRequest.mockResolvedValue({ success: true });

      const mockError = new Error('Test error');

      await logError(mockError);

      // Should not throw and should still call backend
      expect(cofounderAgentClient.makeRequest).toHaveBeenCalled();
    });

    test('continues even if backend fails', async () => {
      cofounderAgentClient.makeRequest.mockRejectedValue(
        new Error('Backend failed')
      );

      const mockError = new Error('Test error');

      const result = await logError(mockError);

      // Should not throw
      expect(Sentry.captureException).toHaveBeenCalled();
      expect(result).toBeNull();
    });
  });
});
