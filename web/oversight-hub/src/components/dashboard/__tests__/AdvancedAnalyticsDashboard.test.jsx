/**
 * Tests for components/dashboard/AdvancedAnalyticsDashboard.jsx
 *
 * Covers:
 * - Loading state while data fetches
 * - Error state when API fails
 * - Renders Advanced Analytics heading
 * - Time range toggle buttons present
 * - KPI data rendered after successful fetch
 * - Error shown when fetch returns error property
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock logger
vi.mock('@/lib/logger', () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

// Mock analyticsService — vi.hoisted() required so variables are available in factory
const {
  mockGetKPIs,
  mockGetTaskMetrics,
  mockGetCostBreakdown,
  mockGetContentMetrics,
} = vi.hoisted(() => ({
  mockGetKPIs: vi.fn(),
  mockGetTaskMetrics: vi.fn(),
  mockGetCostBreakdown: vi.fn(),
  mockGetContentMetrics: vi.fn(),
}));

vi.mock('../../../services/analyticsService', () => ({
  getKPIs: mockGetKPIs,
  getTaskMetrics: mockGetTaskMetrics,
  getCostBreakdown: mockGetCostBreakdown,
  getContentMetrics: mockGetContentMetrics,
}));

import { AdvancedAnalyticsDashboard } from '../AdvancedAnalyticsDashboard';

const MOCK_KPI_DATA = {
  kpis: {
    revenue: { current: 12500, change: 15, currency: 'USD' },
    contentPublished: { current: 42, change: 8 },
    tasksCompleted: { current: 150, change: 12 },
    costSavings: { current: 3200, change: 20 },
  },
};

const MOCK_TASK_METRICS = {
  tasks: { completed: 150, failed: 5, success_rate: 96.7 },
};

const MOCK_COST_BREAKDOWN = {
  providers: [
    { name: 'anthropic', cost: 25.5, percentage: 67 },
    { name: 'openai', cost: 12.3, percentage: 33 },
  ],
  total_cost: 37.8,
};

const MOCK_CONTENT_METRICS = {
  content: { published: 42, drafts: 8 },
};

function setupHappyPath() {
  mockGetKPIs.mockResolvedValue(MOCK_KPI_DATA);
  mockGetTaskMetrics.mockResolvedValue(MOCK_TASK_METRICS);
  mockGetCostBreakdown.mockResolvedValue(MOCK_COST_BREAKDOWN);
  mockGetContentMetrics.mockResolvedValue(MOCK_CONTENT_METRICS);
}

describe('AdvancedAnalyticsDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows a loading spinner initially', () => {
    mockGetKPIs.mockImplementation(() => new Promise(() => {}));
    mockGetTaskMetrics.mockImplementation(() => new Promise(() => {}));
    mockGetCostBreakdown.mockImplementation(() => new Promise(() => {}));
    mockGetContentMetrics.mockImplementation(() => new Promise(() => {}));

    render(<AdvancedAnalyticsDashboard />);
    // MUI CircularProgress renders role="progressbar"
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows error alert when API call fails', async () => {
    mockGetKPIs.mockRejectedValue(new Error('Network error'));
    mockGetTaskMetrics.mockResolvedValue({});
    mockGetCostBreakdown.mockResolvedValue({});
    mockGetContentMetrics.mockResolvedValue({});

    render(<AdvancedAnalyticsDashboard />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load analytics/i)).toBeInTheDocument();
    });
  });

  // Helper: wait for the component to leave loading state
  // The component renders a single CircularProgress when loading; once done,
  // it renders the full page with the "Advanced Analytics" heading.
  async function waitForLoad() {
    await waitFor(() => {
      expect(screen.getByText('Advanced Analytics')).toBeInTheDocument();
    });
  }

  it('renders Advanced Analytics heading after load', async () => {
    setupHappyPath();
    render(<AdvancedAnalyticsDashboard />);
    await waitForLoad();
    expect(screen.getByText('Advanced Analytics')).toBeInTheDocument();
  });

  it('renders time range toggle buttons', async () => {
    setupHappyPath();
    render(<AdvancedAnalyticsDashboard />);
    await waitForLoad();

    expect(screen.getByText('7 Days')).toBeInTheDocument();
    expect(screen.getByText('30 Days')).toBeInTheDocument();
    expect(screen.getByText('90 Days')).toBeInTheDocument();
    expect(screen.getByText('All Time')).toBeInTheDocument();
  });

  it('renders KPI revenue card when kpiData is available', async () => {
    setupHappyPath();
    render(<AdvancedAnalyticsDashboard />);
    await waitForLoad();

    expect(screen.getByText('Total Revenue')).toBeInTheDocument();
    expect(screen.getByText('Content Published')).toBeInTheDocument();
    expect(screen.getByText('Tasks Completed')).toBeInTheDocument();
  });

  it('calls APIs again when time range changes', async () => {
    setupHappyPath();
    render(<AdvancedAnalyticsDashboard />);
    await waitForLoad();

    // Click 7 Days toggle button
    const sevenDayBtn = screen.getByText('7 Days');
    fireEvent.click(sevenDayBtn);

    await waitFor(() => {
      // Each API should have been called at least twice (initial 30d + after 7d change)
      expect(mockGetKPIs.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
    // Verify it was called with the new range
    const kpiCalls = mockGetKPIs.mock.calls.map((c) => c[0]);
    expect(kpiCalls).toContain('7d');
  });

  it('renders Refresh button', async () => {
    setupHappyPath();
    render(<AdvancedAnalyticsDashboard />);
    await waitForLoad();

    expect(
      screen.getByRole('button', { name: /Refresh/i })
    ).toBeInTheDocument();
  });

  it('clicking Refresh triggers re-fetch', async () => {
    setupHappyPath();
    render(<AdvancedAnalyticsDashboard />);
    await waitForLoad();

    const initialCallCount = mockGetKPIs.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: /Refresh/i }));

    await waitFor(() => {
      expect(mockGetKPIs.mock.calls.length).toBeGreaterThan(initialCallCount);
    });
  });
});
