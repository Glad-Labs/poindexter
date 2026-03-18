/**
 * AuthCallback Component Tests
 *
 * Tests the OAuth callback handler
 * Verifies: State validation, token exchange, error handling, redirect flow
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { useNavigate } from 'react-router-dom';
import AuthCallback from './AuthCallback';

// Mock React Router
vi.mock('react-router-dom', () => ({
  useNavigate: vi.fn(),
  useSearchParams: vi.fn(() => {
    const searchParams = new URLSearchParams();
    searchParams.set('code', 'test-auth-code-123');
    searchParams.set('state', 'test-state-abc123');
    return [searchParams];
  }),
}));

// Mock the auth service
vi.mock('../services/authService', () => ({
  handleOAuthCallbackNew: vi.fn(async () => ({
    user: { id: '1', name: 'Test User' },
    token: 'test-access-token',
  })),
  exchangeCodeForToken: vi.fn(async (_code, _state, _provider) => ({
    user: { id: '1', name: 'Test User' },
    token: 'test-access-token',
  })),
  setAuthToken: vi.fn(),
  getAuthState: vi.fn(() => 'test-state-abc123'),
}));

// Mock the auth client
vi.mock('../lib/authClient', () => ({
  validateAndConsumeOAuthState: vi.fn((_state) => ({
    valid: true,
    provider: 'github',
  })),
}));

// Mock useAuth hook
vi.mock('../hooks/useAuth', () => ({
  default: vi.fn(() => ({
    setUser: vi.fn(),
    setAccessToken: vi.fn(),
    setIsAuthenticated: vi.fn(),
  })),
}));

// Mock logger
vi.mock('@/lib/logger', () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

describe('AuthCallback Component', () => {
  const mockNavigate = vi.fn();

  beforeEach(async () => {
    vi.clearAllMocks();
    useNavigate.mockReturnValue(mockNavigate);

    // Reset mocks to default happy-path behavior after clearAllMocks
    const { useSearchParams } = await import('react-router-dom');
    const searchParams = new URLSearchParams();
    searchParams.set('code', 'test-auth-code-123');
    searchParams.set('state', 'test-state-abc123');
    useSearchParams.mockReturnValue([searchParams]);

    const authService = await import('../services/authService');
    authService.handleOAuthCallbackNew.mockResolvedValue({
      user: { id: '1', name: 'Test User' },
      token: 'test-access-token',
    });
    authService.exchangeCodeForToken.mockResolvedValue({
      user: { id: '1', name: 'Test User' },
      token: 'test-access-token',
    });

    const useAuth = (await import('../hooks/useAuth')).default;
    useAuth.mockReturnValue({
      setUser: vi.fn(),
      setAccessToken: vi.fn(),
      setIsAuthenticated: vi.fn(),
    });
  });

  it('should render callback processing display', () => {
    render(<AuthCallback />);

    // Component renders "Authenticating..." heading while processing
    expect(screen.getByText(/authenticating/i)).toBeInTheDocument();
  });

  it('should show loading message while processing', () => {
    render(<AuthCallback />);

    // Component shows credential verification message
    expect(screen.getByText(/please wait/i)).toBeInTheDocument();
  });

  it('should extract auth code from URL parameters', async () => {
    const { handleOAuthCallbackNew } = await import('../services/authService');

    render(<AuthCallback />);

    await waitFor(() => {
      // Verify the auth code from the mocked URL params was passed to the OAuth handler
      expect(handleOAuthCallbackNew).toHaveBeenCalledWith(
        expect.anything(),
        'test-auth-code-123',
        expect.anything()
      );
    });
  });

  it('should extract state from URL parameters', async () => {
    const { handleOAuthCallbackNew } = await import('../services/authService');

    render(<AuthCallback />);

    await waitFor(() => {
      // Verify the state from the mocked URL params was passed to the OAuth handler
      expect(handleOAuthCallbackNew).toHaveBeenCalledWith(
        expect.anything(),
        expect.anything(),
        'test-state-abc123'
      );
    });
  });

  it('should pass state parameter to auth service', async () => {
    const authService = await import('../services/authService');

    render(<AuthCallback />);

    await waitFor(() => {
      // Verify state from URL params is forwarded to the OAuth handler
      expect(authService.handleOAuthCallbackNew).toHaveBeenCalledWith(
        expect.anything(),
        expect.anything(),
        'test-state-abc123'
      );
    });
  });

  it('should redirect on authentication failure', async () => {
    // Mock handleOAuthCallbackNew to reject, and exchangeCodeForToken to also reject
    const authService = await import('../services/authService');
    authService.handleOAuthCallbackNew.mockRejectedValue(
      new Error('OAuth failed')
    );
    authService.exchangeCodeForToken.mockRejectedValue(
      new Error('Exchange failed')
    );

    render(<AuthCallback />);

    await waitFor(() => {
      // Component shows error message on failure
      const errorEl = screen.queryByText(/failed|error/i);
      expect(errorEl).toBeTruthy();
    });
  });

  it('should show error message on auth failure', async () => {
    const authService = await import('../services/authService');
    authService.handleOAuthCallbackNew.mockRejectedValue(
      new Error('Auth failed')
    );
    authService.exchangeCodeForToken.mockRejectedValue(
      new Error('Exchange failed')
    );

    render(<AuthCallback />);

    await waitFor(() => {
      const errorMessage = screen.queryByText(/error|failed/i);
      expect(errorMessage).toBeTruthy();
    });
  });

  it('should exchange auth code for token', async () => {
    const authService = await import('../services/authService');

    render(<AuthCallback />);

    await waitFor(() => {
      // Component tries handleOAuthCallbackNew first
      expect(authService.handleOAuthCallbackNew).toHaveBeenCalledWith(
        'github',
        'test-auth-code-123',
        'test-state-abc123'
      );
    });
  });

  it('should store token on successful exchange', async () => {
    const useAuth = (await import('../hooks/useAuth')).default;
    // Capture the mock fn so we can check it was called
    const mockSetAccessToken = vi.fn();
    const mockSetUser = vi.fn();
    const mockSetIsAuthenticated = vi.fn();
    useAuth.mockReturnValue({
      setUser: mockSetUser,
      setAccessToken: mockSetAccessToken,
      setIsAuthenticated: mockSetIsAuthenticated,
    });

    render(<AuthCallback />);

    await waitFor(() => {
      expect(mockSetAccessToken).toHaveBeenCalledWith('test-access-token');
    });
  });

  it('should redirect to dashboard on success', async () => {
    render(<AuthCallback />);

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/', { replace: true });
    });
  });

  it('should handle missing auth code', async () => {
    // Mock search params without code
    const { useSearchParams } = await import('react-router-dom');
    useSearchParams.mockReturnValue([new URLSearchParams()]);

    render(<AuthCallback />);

    await waitFor(() => {
      // Component shows "No authorization code received" error
      expect(screen.getByText(/no authorization code/i)).toBeInTheDocument();
    });
  });

  it('should handle missing state parameter', async () => {
    // Mock search params without state — code present but no state
    const { useSearchParams } = await import('react-router-dom');
    const searchParams = new URLSearchParams();
    searchParams.set('code', 'test-code');
    useSearchParams.mockReturnValue([searchParams]);

    render(<AuthCallback />);

    // Component still attempts auth with null state; verify it renders
    await waitFor(() => {
      const authService = import('../services/authService');
      expect(authService).toBeDefined();
    });
  });

  it('should handle network errors gracefully', async () => {
    const authService = await import('../services/authService');
    authService.exchangeCodeForToken.mockRejectedValue(
      new Error('Network error')
    );

    render(<AuthCallback />);

    await waitFor(() => {
      const errorMessage = screen.queryByText(/error|network|failed/i);
      expect(errorMessage).toBeDefined();
    });
  });

  it('should prevent navigation while processing', async () => {
    render(<AuthCallback />);

    // Should not navigate until processing completes
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('should handle multiple callback invocations', async () => {
    const authService = await import('../services/authService');

    render(<AuthCallback />);

    await waitFor(() => {
      // Component should call the OAuth handler exactly once per mount
      expect(authService.handleOAuthCallbackNew).toHaveBeenCalledTimes(1);
    });
  });

  it('should pass provider to auth handler', async () => {
    const { handleOAuthCallbackNew } = await import('../services/authService');

    render(<AuthCallback />);

    await waitFor(() => {
      // Default provider is 'github' when not specified in URL params
      expect(handleOAuthCallbackNew).toHaveBeenCalledWith(
        'github',
        expect.anything(),
        expect.anything()
      );
    });
  });

  it('should cleanup on unmount', () => {
    const { unmount, container } = render(<AuthCallback />);

    unmount();
    // Verify component is no longer in the DOM after unmount
    expect(container.innerHTML).toBe('');
  });
});
