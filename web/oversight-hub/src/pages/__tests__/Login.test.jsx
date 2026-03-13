/**
 * Login Page Tests
 *
 * Tests the GitHub OAuth login page:
 * - Renders the login button correctly
 * - Calls generateGitHubAuthURL on production login
 * - Uses mock auth URL in development mode when REACT_APP_USE_MOCK_AUTH=true
 * - Redirects when already authenticated
 * - Shows error state when error query param present
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// Hoisted mock variables (must be declared before vi.mock calls)
const { mockNavigate, mockUseAuth, mockGetEnv, mockGenerateGitHubAuthURL } =
  vi.hoisted(() => ({
    mockNavigate: vi.fn(),
    mockUseAuth: vi.fn(),
    mockGetEnv: vi.fn(),
    mockGenerateGitHubAuthURL: vi.fn(
      (clientId) =>
        `https://github.com/login/oauth/authorize?client_id=${clientId}`
    ),
  }));

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock('../../hooks/useAuth', () => ({
  default: mockUseAuth,
}));

vi.mock('../../config/apiConfig', () => ({
  getEnv: mockGetEnv,
}));

vi.mock('../../services/authService', () => ({
  generateGitHubAuthURL: mockGenerateGitHubAuthURL,
}));

// Login.css is a side-effect import — silence module resolution errors
vi.mock('../Login.css', () => ({}));

// Dynamic import of mockAuthService (only loaded in dev mode)
vi.mock('../../services/mockAuthService', () => ({
  generateMockGitHubAuthURL: vi.fn(
    (clientId) => `http://localhost:3001/auth/mock?client_id=${clientId}`
  ),
}));

import Login from '../Login';

const renderLogin = () =>
  render(
    <MemoryRouter>
      <Login />
    </MemoryRouter>
  );

describe('Login Page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default: authenticated=false, production mode, no mock auth
    mockUseAuth.mockReturnValue({ isAuthenticated: false, loading: false });
    mockGetEnv.mockImplementation((key) => {
      if (key === 'REACT_APP_GH_OAUTH_CLIENT_ID') return 'test-client-id';
      if (key === 'MODE' || key === 'NODE_ENV') return 'production';
      if (key === 'REACT_APP_USE_MOCK_AUTH') return 'false';
      return '';
    });
    // Store original window.location.href setter
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { href: '' },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('Rendering', () => {
    it('renders Glad Labs heading', () => {
      renderLogin();
      expect(screen.getByText('Glad Labs')).toBeInTheDocument();
    });

    it('renders Oversight Hub subheading', () => {
      renderLogin();
      expect(screen.getByText('Oversight Hub')).toBeInTheDocument();
    });

    it('renders Sign in with GitHub button in production mode', () => {
      renderLogin();
      expect(
        screen.getByRole('button', { name: /Sign in with GitHub/i })
      ).toBeInTheDocument();
    });

    it('renders mock sign-in button when REACT_APP_USE_MOCK_AUTH=true in dev mode', () => {
      mockGetEnv.mockImplementation((key) => {
        if (key === 'REACT_APP_GH_OAUTH_CLIENT_ID') return 'test-client-id';
        if (key === 'MODE' || key === 'NODE_ENV') return 'development';
        if (key === 'REACT_APP_USE_MOCK_AUTH') return 'true';
        return '';
      });
      renderLogin();
      expect(
        screen.getByRole('button', { name: /Mock - Dev Only/i })
      ).toBeInTheDocument();
    });

    it('does NOT render mock button in production even if REACT_APP_USE_MOCK_AUTH=true', () => {
      mockGetEnv.mockImplementation((key) => {
        if (key === 'REACT_APP_GH_OAUTH_CLIENT_ID') return 'test-client-id';
        if (key === 'MODE' || key === 'NODE_ENV') return 'production';
        if (key === 'REACT_APP_USE_MOCK_AUTH') return 'true';
        return '';
      });
      renderLogin();
      expect(
        screen.queryByRole('button', { name: /Mock - Dev Only/i })
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: /Sign in with GitHub/i })
      ).toBeInTheDocument();
    });
  });

  describe('Authentication redirect', () => {
    it('navigates to / when already authenticated', () => {
      mockUseAuth.mockReturnValue({ isAuthenticated: true, loading: false });
      renderLogin();
      expect(mockNavigate).toHaveBeenCalledWith('/');
    });

    it('does NOT navigate when not authenticated', () => {
      renderLogin();
      expect(mockNavigate).not.toHaveBeenCalled();
    });
  });

  describe('GitHub OAuth login', () => {
    it('redirects to GitHub OAuth URL on button click', () => {
      renderLogin();
      const btn = screen.getByRole('button', { name: /Sign in with GitHub/i });
      fireEvent.click(btn);
      expect(mockGenerateGitHubAuthURL).toHaveBeenCalledWith('test-client-id');
      expect(window.location.href).toContain('github.com');
    });

    it('shows alert when clientId is not configured', () => {
      const alertMock = vi.spyOn(window, 'alert').mockImplementation(() => {});
      mockGetEnv.mockImplementation((key) => {
        if (key === 'REACT_APP_GH_OAUTH_CLIENT_ID') return '';
        if (key === 'MODE' || key === 'NODE_ENV') return 'production';
        if (key === 'REACT_APP_USE_MOCK_AUTH') return 'false';
        return '';
      });
      renderLogin();
      const btn = screen.getByRole('button', { name: /Sign in with GitHub/i });
      fireEvent.click(btn);
      expect(alertMock).toHaveBeenCalledWith(
        expect.stringContaining('GitHub Client ID not configured')
      );
      expect(mockGenerateGitHubAuthURL).not.toHaveBeenCalled();
    });
  });

  describe('Debug info', () => {
    it('renders debug environment info block', async () => {
      renderLogin();
      await waitFor(() => {
        // The pre element is rendered with JSON debug info
        const pre = document.querySelector('pre');
        expect(pre).not.toBeNull();
        expect(pre.textContent).toContain('environment');
      });
    });
  });
});
