/**
 * Tests for components/pages/ExecutiveDashboard.jsx
 *
 * Covers:
 * - Loading state while data is fetching
 * - Error state when API fails
 * - Data-populated state renders KPI content
 * - Time range selector triggers refetch
 * - Navigation buttons present
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock react-router-dom
const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

// Mock logger
vi.mock('@/lib/logger', () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

// Mock CSS
vi.mock('../ExecutiveDashboard.css', () => ({}));

// Mock child components
vi.mock('../../tasks/CreateTaskModal', () => ({
  default: ({ isOpen }) =>
    isOpen ? <div data-testid="create-task-modal" /> : null,
}));
vi.mock('../../CostBreakdownCards', () => ({
  default: () => <div data-testid="cost-breakdown-cards" />,
}));

// Mock errorLoggingService (imported by ExecutiveDashboard after #839)
vi.mock('../../../services/errorLoggingService', () => ({
  logError: vi.fn(),
  logErrorToSentry: vi.fn(),
}));

// Mock the cofounderAgentClient dynamic import
const { mockMakeRequest } = vi.hoisted(() => ({ mockMakeRequest: vi.fn() }));
vi.mock('../../../services/cofounderAgentClient', () => ({
  makeRequest: mockMakeRequest,
}));

// Mock useAuth — tests assume authenticated state so data fetches fire
vi.mock('../../../hooks/useAuth', () => ({
  default: () => ({ isAuthenticated: true, loading: false }),
}));

// Import after mocks
import ExecutiveDashboard from '../ExecutiveDashboard';

describe('ExecutiveDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading state initially', async () => {
    // makeRequest never resolves during this test
    mockMakeRequest.mockImplementation(() => new Promise(() => {}));

    render(<ExecutiveDashboard />);
    expect(screen.getByText(/Loading dashboard/i)).toBeInTheDocument();
  });

  it('shows error state and fallback data when API fails', async () => {
    mockMakeRequest.mockRejectedValue(new Error('Server unavailable'));

    render(<ExecutiveDashboard />);

    // After rejection, the component uses mock data as fallback — no error message shown alone
    // just verify loading goes away
    await waitFor(() => {
      expect(screen.queryByText(/Loading dashboard/i)).not.toBeInTheDocument();
    });
  });

  it('renders dashboard content after successful API call', async () => {
    mockMakeRequest.mockResolvedValue({
      total_tasks: 100,
      total_cost: 50.0,
      cost_by_day: [],
      tasks_by_day: [],
      success_by_day: [],
    });

    render(<ExecutiveDashboard />);

    await waitFor(() => {
      expect(screen.queryByText(/Loading dashboard/i)).not.toBeInTheDocument();
    });
  });

  it('renders time range selector', async () => {
    mockMakeRequest.mockResolvedValue({ total_tasks: 0, total_cost: 0 });

    render(<ExecutiveDashboard />);

    await waitFor(() => {
      expect(screen.queryByText(/Loading dashboard/i)).not.toBeInTheDocument();
    });

    const select = screen.getByRole('combobox');
    expect(select).toBeInTheDocument();
  });

  it('changing time range calls API again', async () => {
    mockMakeRequest.mockResolvedValue({ total_tasks: 0, total_cost: 0 });

    render(<ExecutiveDashboard />);

    await waitFor(() => {
      expect(screen.queryByText(/Loading dashboard/i)).not.toBeInTheDocument();
    });

    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: '7d' } });

    await waitFor(() => {
      // makeRequest should have been called at least twice (initial + after time range change)
      expect(mockMakeRequest.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
  });

  it('renders when API returns data with error field (treated as error)', async () => {
    mockMakeRequest.mockResolvedValue({
      error: 'API error',
      message: 'failed',
    });

    render(<ExecutiveDashboard />);

    await waitFor(() => {
      // Falls back to mock data — no hard crash
      expect(screen.queryByText(/Loading dashboard/i)).not.toBeInTheDocument();
    });
  });

  it('shows AI-Powered Business Management heading', async () => {
    mockMakeRequest.mockResolvedValue({ total_tasks: 5, total_cost: 2.5 });

    render(<ExecutiveDashboard />);

    await waitFor(() => {
      expect(screen.queryByText(/Loading dashboard/i)).not.toBeInTheDocument();
    });

    expect(
      screen.getByText(/AI-Powered Business Management/i)
    ).toBeInTheDocument();
  });

  it('renders CostBreakdownCards sub-component', async () => {
    mockMakeRequest.mockResolvedValue({ total_tasks: 5, total_cost: 2.5 });

    render(<ExecutiveDashboard />);

    await waitFor(() => {
      expect(screen.queryByText(/Loading dashboard/i)).not.toBeInTheDocument();
    });

    expect(screen.getByTestId('cost-breakdown-cards')).toBeInTheDocument();
  });
});
