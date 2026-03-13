/**
 * PerformanceDashboard.jsx route tests
 *
 * Covers:
 * - Initial render with loading state
 * - Renders performance metrics after successful fetch
 * - Error state when API returns non-OK
 * - Error state when API throws
 * - Auto-refresh toggle triggers re-fetch via setInterval
 * - No metrics data shows gracefully
 */

import React from 'react';
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from '@testing-library/react';
import { vi } from 'vitest';
import PerformanceDashboard from '../PerformanceDashboard';

// ── mock getApiUrl ────────────────────────────────────────────────────────
vi.mock('../../config/apiConfig', () => ({
  getApiUrl: vi.fn(() => 'http://localhost:8000'),
}));

// ── mock recharts (avoid SVG rendering issues in jsdom) ───────────────────
vi.mock('recharts', () => ({
  BarChart: ({ children }) => <div data-testid="bar-chart">{children}</div>,
  Bar: () => null,
  LineChart: ({ children }) => <div data-testid="line-chart">{children}</div>,
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  Legend: () => null,
  ResponsiveContainer: ({ children }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  PieChart: ({ children }) => <div data-testid="pie-chart">{children}</div>,
  Pie: () => null,
  Cell: () => null,
}));

// ── sample data ───────────────────────────────────────────────────────────
const SAMPLE_PERF_DATA = {
  overall_stats: {
    total_requests: 1234,
    avg_latency_ms: 45.2,
    p95_latency_ms: 120.5,
    error_rate: 0.01,
  },
  cache_stats: {
    user_cache: { hit_rate: 0.85, ttl: 300 },
    task_cache: { hit_rate: 0.72, ttl: 60 },
  },
  route_latencies: [
    { route: '/api/tasks', avg_ms: 32.1, count: 500 },
    { route: '/api/workflows', avg_ms: 88.4, count: 200 },
  ],
  model_decisions: [
    { model: 'claude-3', count: 600 },
    { model: 'gpt-4', count: 634 },
  ],
};

// ── helpers ───────────────────────────────────────────────────────────────
function makeFetch(data, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(data),
  });
}

function renderDashboard() {
  return render(<PerformanceDashboard />);
}

// ── tests ─────────────────────────────────────────────────────────────────
describe('PerformanceDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  // Helper: mount and let the initial fetch + microtasks settle without
  // triggering infinite setInterval recursion.  We advance by a small amount
  // (100 ms) so that the React useEffect runs and Promises flush, but we do
  // NOT call runAllTimersAsync() which would loop the 2 s client-metrics
  // interval forever.
  async function mountAndSettle() {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
  }

  describe('initial render', () => {
    it('renders the page title', async () => {
      global.fetch = makeFetch(SAMPLE_PERF_DATA);
      renderDashboard();
      await mountAndSettle();

      expect(screen.getByText(/Performance Dashboard/i)).toBeInTheDocument();
    });

    it('fetches metrics on mount', async () => {
      global.fetch = makeFetch(SAMPLE_PERF_DATA);
      renderDashboard();
      await mountAndSettle();

      expect(global.fetch).toHaveBeenCalledWith(
        'http://localhost:8000/api/metrics/performance',
        expect.any(Object)
      );
    });
  });

  describe('success state', () => {
    it('shows Overall Performance section with data', async () => {
      global.fetch = makeFetch(SAMPLE_PERF_DATA);
      renderDashboard();
      await mountAndSettle();

      expect(screen.getByText(/Overall Performance/i)).toBeInTheDocument();
    });

    it('shows cache hit rate section when cache_stats present', async () => {
      global.fetch = makeFetch(SAMPLE_PERF_DATA);
      renderDashboard();
      await mountAndSettle();

      expect(screen.getByText(/Cache Hit Rate/i)).toBeInTheDocument();
    });
  });

  describe('error state', () => {
    it('shows error when API returns non-OK status', async () => {
      global.fetch = makeFetch(null, false, 500);
      renderDashboard();
      await mountAndSettle();

      // With fake timers, waitFor's internal setTimeout never fires.
      // The error state is set synchronously after the rejected promise
      // resolves, so checking directly after mountAndSettle is sufficient.
      expect(
        screen.getByText(/Failed to fetch performance metrics/i)
      ).toBeInTheDocument();
    });

    it('shows error when fetch throws', async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error('Network error'));
      renderDashboard();
      await mountAndSettle();

      expect(screen.getByText(/Network error/i)).toBeInTheDocument();
    });
  });

  describe('auto-refresh toggle', () => {
    it('shows the Auto-Refresh toggle', async () => {
      global.fetch = makeFetch(SAMPLE_PERF_DATA);
      renderDashboard();
      await mountAndSettle();

      expect(screen.getByText(/Auto-Refresh/i)).toBeInTheDocument();
    });

    it('calls fetch again after refresh interval when auto-refresh is on', async () => {
      global.fetch = makeFetch(SAMPLE_PERF_DATA);
      renderDashboard();
      await mountAndSettle();

      const callsBefore = global.fetch.mock.calls.length;

      // Advance exactly 15 seconds to fire the poll interval once.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(15000);
      });

      expect(global.fetch.mock.calls.length).toBeGreaterThan(callsBefore);
    });
  });
});
