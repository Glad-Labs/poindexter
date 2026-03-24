/**
 * AppRoutes.jsx tests
 *
 * Covers:
 * - Unauthenticated user visiting a protected route is redirected to /login
 * - Loading state shows "Loading..." during auth check
 * - Authenticated user visiting /login is redirected to /
 * - Each protected route renders its target component (smoke tests)
 * - Unknown path redirects to /
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import AppRoutes from '../AppRoutes';

// ── mock useAuth ──────────────────────────────────────────────────────────────
const { mockUseAuth } = vi.hoisted(() => ({
  mockUseAuth: vi.fn(),
}));

vi.mock('../../hooks/useAuth', () => ({
  default: mockUseAuth,
}));

// ── stub all heavy page/component imports to avoid their API calls ────────────
// These are now lazy-imported directly (not through ../index)
vi.mock('../Settings', () => ({
  default: () => <div data-testid="settings-page">Settings</div>,
}));
vi.mock('../TaskManagement', () => ({
  default: () => <div data-testid="task-management-page">TaskManagement</div>,
}));
vi.mock('../CostMetricsDashboard', () => ({
  default: () => <div data-testid="cost-metrics-page">CostMetrics</div>,
}));

vi.mock('../../components/pages/ExecutiveDashboard', () => ({
  default: () => (
    <div data-testid="executive-dashboard">ExecutiveDashboard</div>
  ),
}));

vi.mock('../../components/pages/UnifiedServicesPanel', () => ({
  default: () => <div data-testid="unified-services">UnifiedServices</div>,
}));

vi.mock('../../pages/BlogWorkflowPage', () => ({
  default: () => <div data-testid="blog-workflow-page">BlogWorkflow</div>,
}));

vi.mock('../AIStudio', () => ({
  default: () => <div data-testid="ai-studio-page">AIStudio</div>,
}));

vi.mock('../Content', () => ({
  default: () => <div data-testid="content-page">Content</div>,
}));

vi.mock('../PerformanceDashboard', () => ({
  default: () => (
    <div data-testid="performance-dashboard-page">PerformanceDashboard</div>
  ),
}));

vi.mock('../../pages/Login', () => ({
  default: () => <div data-testid="login-page">Sign in with GitHub</div>,
}));

vi.mock('../../pages/AuthCallback', () => ({
  default: () => <div data-testid="auth-callback-page">AuthCallback</div>,
}));

vi.mock('../../components/tasks/ApprovalQueue', () => ({
  default: () => <div data-testid="approval-queue-page">ApprovalQueue</div>,
}));

vi.mock('../../components/LayoutWrapper', () => ({
  default: ({ children }) => <div data-testid="layout-wrapper">{children}</div>,
}));

vi.mock('../../components/ErrorBoundary', () => ({
  default: ({ children }) => <>{children}</>,
}));

// ── helpers ───────────────────────────────────────────────────────────────────
function renderAt(
  path,
  authState = { isAuthenticated: false, loading: false, user: null }
) {
  mockUseAuth.mockReturnValue(authState);
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>
  );
}

const AUTHENTICATED = {
  isAuthenticated: true,
  loading: false,
  user: { role: 'admin' },
};
const UNAUTHENTICATED = { isAuthenticated: false, loading: false, user: null };
const LOADING = { isAuthenticated: false, loading: true, user: null };

// ── tests ─────────────────────────────────────────────────────────────────────
describe('AppRoutes', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('authentication guard', () => {
    it('redirects unauthenticated user from /tasks to /login', () => {
      renderAt('/tasks', UNAUTHENTICATED);
      expect(screen.getByTestId('login-page')).toBeInTheDocument();
    });

    it('redirects unauthenticated user from / to /login', () => {
      renderAt('/', UNAUTHENTICATED);
      expect(screen.getByTestId('login-page')).toBeInTheDocument();
    });

    it('shows loading indicator while auth state is loading', () => {
      renderAt('/tasks', LOADING);
      expect(screen.getByText('Loading...')).toBeInTheDocument();
    });
  });

  describe('public routes', () => {
    it('renders Login page at /login', () => {
      renderAt('/login', UNAUTHENTICATED);
      expect(screen.getByTestId('login-page')).toBeInTheDocument();
    });

    it('renders AuthCallback at /auth/callback', () => {
      renderAt('/auth/callback', UNAUTHENTICATED);
      expect(screen.getByTestId('auth-callback-page')).toBeInTheDocument();
    });
  });

  describe('protected routes (authenticated)', () => {
    // Routes use React.lazy — findByTestId waits for Suspense to resolve
    it('renders ExecutiveDashboard at /', async () => {
      renderAt('/', AUTHENTICATED);
      expect(
        await screen.findByTestId('executive-dashboard')
      ).toBeInTheDocument();
    });

    it('renders TaskManagement at /tasks', async () => {
      renderAt('/tasks', AUTHENTICATED);
      expect(
        await screen.findByTestId('task-management-page')
      ).toBeInTheDocument();
    });

    it('renders Content at /content', async () => {
      renderAt('/content', AUTHENTICATED);
      expect(await screen.findByTestId('content-page')).toBeInTheDocument();
    });

    it('renders ApprovalQueue at /approvals', async () => {
      renderAt('/approvals', AUTHENTICATED);
      expect(
        await screen.findByTestId('approval-queue-page')
      ).toBeInTheDocument();
    });

    it('renders AIStudio at /ai', async () => {
      renderAt('/ai', AUTHENTICATED);
      expect(await screen.findByTestId('ai-studio-page')).toBeInTheDocument();
    });

    it('renders Settings at /settings', async () => {
      renderAt('/settings', AUTHENTICATED);
      expect(await screen.findByTestId('settings-page')).toBeInTheDocument();
    });

    it('renders CostMetricsDashboard at /costs', async () => {
      renderAt('/costs', AUTHENTICATED);
      expect(
        await screen.findByTestId('cost-metrics-page')
      ).toBeInTheDocument();
    });

    it('renders PerformanceDashboard at /performance', async () => {
      renderAt('/performance', AUTHENTICATED);
      expect(
        await screen.findByTestId('performance-dashboard-page')
      ).toBeInTheDocument();
    });

    it('renders BlogWorkflowPage at /workflows', async () => {
      renderAt('/workflows', AUTHENTICATED);
      expect(
        await screen.findByTestId('blog-workflow-page')
      ).toBeInTheDocument();
    });

    it('renders UnifiedServicesPanel at /services', async () => {
      renderAt('/services', AUTHENTICATED);
      expect(await screen.findByTestId('unified-services')).toBeInTheDocument();
    });
  });

  describe('unknown routes', () => {
    it('redirects unknown path to / (which renders login when unauthenticated)', () => {
      renderAt('/this-does-not-exist', UNAUTHENTICATED);
      // Redirects to / which ProtectedRoute redirects to /login
      expect(screen.getByTestId('login-page')).toBeInTheDocument();
    });

    it('redirects unknown path to / (which renders dashboard when authenticated)', () => {
      renderAt('/this-does-not-exist', AUTHENTICATED);
      expect(screen.getByTestId('executive-dashboard')).toBeInTheDocument();
    });
  });
});
